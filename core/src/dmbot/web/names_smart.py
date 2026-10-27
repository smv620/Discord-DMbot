"""The Names page's search by meaning (#2733, #2664): "ice goddess" finds Auril the Frostmaiden,
"friend of Brannoc" finds the names connected to Brannoc as a friend.

The website's server does the one thing the database cannot: the DM's search words go to Voyage AI
and come back as numbers (`voyage-3.5`). A database door (`dmbot_web_names_near`, schema.py) then
finds the campaign's names whose kept numbers are closest, and `rerank-2.5` puts the best of them
in order by reading the text each was made from. The rows come back in the same shape as the
page's usual list (`all_names.read_page`), in that order, at most `ROWS_MAX`.

Who may ask: a DM of the campaign, the same as the door (anyone else gets "not found", a server
manager who is not a DM included: the text searched holds descriptions the page does not show them).
The search words are the DM's own typing; they go to Voyage AI only while meaning search is on and
the key is set, and they are never logged: the log holds counts. When the search cannot run (off,
no credit left, Voyage AI not answering) the answer says so plainly with `available: false`, so the
page can fall back to the letters search, which is always there.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from dmbot.ai import AIOutOfFunds
from dmbot.db import Database
from dmbot.memory.entity_text import compose
from dmbot.memory.meaning import MIN_SCORE, Voyager
from dmbot.memory.voyage import ProviderError
from dmbot.web import all_names
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

log = logging.getLogger(__name__)

WORDS_MAX = 200  # characters of a search: "who is the ice goddess" is far short of this
ROWS_MAX = 50  # names shown for one search
POOL = 60  # names compared by meaning before the reranker orders the best ROWS_MAX of them


class SmartNames:
    """What the route needs from Voyage AI: the client, the model the kept numbers were made with
    and a hook that counts each call (a tally of calls, nothing of what was sent)."""

    def __init__(
        self,
        voyage: Voyager,
        model: str,
        count_call: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.voyage = voyage
        self.model = model
        self.count_call = count_call


def _words(text: str) -> str:
    return " ".join(text.split())[:WORDS_MAX]


async def _near(
    db: Database, session: Session, guild_id: int, campaign_id: str, model: str, vector: list[float]
) -> list[dict[str, Any]] | None:
    """The door's answer: the nearest names with the parts of their text, or None for anyone but
    a DM."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_names_near(%s, %s, %s, %s::real[], %s) AS r",
            (guild_id, campaign_id, model, vector, POOL),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    return None if got is None else list(got)


async def search(
    db: Database, session: Session, ref: str, words: str, smart: SmartNames | None
) -> dict[str, Any]:
    """The campaign's names closest in meaning to `words`, best first. "not_found" for anyone who
    is not a DM of the campaign (found out before any word leaves for Voyage AI); "bad_text" for
    nothing to search for. Otherwise `available` says whether the search could run and `why` is
    one of "off", "no_credit" or "unreachable" when it could not."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    text = _words(words)
    if not text:
        raise RecapError("bad_text")
    # Who is asking, before anything is spent: a one-number "search" reaches the door, which
    # answers an empty list to a DM and nothing to anyone else.
    model = smart.model if smart is not None else ""
    if await _near(db, session, guild_id, campaign_id, model, [1.0]) is None:
        raise RecapError("not_found")
    if smart is None:
        return _empty(False, "off")
    try:
        if smart.count_call is not None:
            await smart.count_call()
        (query,) = await smart.voyage.embed([text], query=True)
        close = await _near(db, session, guild_id, campaign_id, model, query)
        if close is None:  # (stopped being a DM while the words were away)
            raise RecapError("not_found")
        if not close:
            return _empty(True, None)
        if smart.count_call is not None:
            await smart.count_call()
        ranked = await smart.voyage.rerank(
            text, [compose(c["parts"]) for c in close], top_k=ROWS_MAX
        )
    except AIOutOfFunds:
        log.warning("Names search by meaning: no credit left")
        return _empty(False, "no_credit")
    except ProviderError as exc:
        log.warning("Names search by meaning: a call failed (%s)", exc.describe())
        return _empty(False, "unreachable")
    order = [
        str(close[r.index]["id"]) for r in ranked if r.score >= MIN_SCORE and r.index < len(close)
    ]
    log.info("Names search by meaning: %d candidates, %d names", len(close), len(order))
    if not order:
        return _empty(True, None)
    page = await all_names.read_page(
        db, session, ref, size=200, only=",".join(order)
    )  # (the rows' usual shape; the door keeps what a DM may see)
    rows = {str(n["id"]): n for n in page["names"]}
    return {
        "available": True,
        "why": None,
        "campaign": page["campaign"],
        "canEdit": page["canEdit"],
        "names": [rows[i] for i in order if i in rows],
    }


def _empty(available: bool, why: str | None) -> dict[str, Any]:
    return {"available": available, "why": why, "names": []}
