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

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg

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


CALL_WAIT_S = 10  # the longest a call to Voyage AI, or waiting for a scan slot, may take
SEARCHES_AT_ONCE = 2  # database scans at one time in this process: a scan is a core's worth of CPU


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
        self.scans = asyncio.Semaphore(SEARCHES_AT_ONCE)


class NotAvailable(RecapError):
    """The search could not run: the page is told "not_available" and falls back to the letters
    search. `why` is for the log and the tests: "off", "no_credit", "unreachable" or "slow"."""

    def __init__(self, why: str) -> None:
        super().__init__("not_available")
        self.why = why


def _words(text: str) -> str:
    return " ".join(text.split())[:WORDS_MAX]


async def _near(
    db: Database, session: Session, guild_id: int, campaign_id: str, model: str, vector: list[float]
) -> tuple[list[dict[str, Any]], bool] | None:
    """The door's answer: the nearest names with the parts of their text, and whether the campaign
    has more numbers than one search compares; None for anyone but a DM."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_names_near(%s, %s, %s, %s::real[], %s) AS r",
            (guild_id, campaign_id, model, vector, POOL),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    return None if got is None else (list(got["rows"]), bool(got["capped"]))


async def search(
    db: Database, session: Session, ref: str, words: str, smart: SmartNames | None
) -> dict[str, Any]:
    """The campaign's names closest in meaning to `words`, best first: `{names, capped}`.
    "not_found" for anyone who is not a DM of the campaign (found out before any word leaves for
    Voyage AI); "bad_text" for nothing to search for; "not_available" (a `NotAvailable`, with
    `why`) when the search could not run. `capped`: the campaign has more numbers than one search
    compares (the newest 10,000), so an old name may not be found."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    text = _words(words)
    if not text:
        raise RecapError("bad_text")
    # Who is asking, before anything is spent: an empty "search" reaches the door's checks and
    # stops there (no numbers are read), so it answers nothing to anyone but a DM.
    model = smart.model if smart is not None else ""
    if await _near(db, session, guild_id, campaign_id, model, []) is None:
        raise RecapError("not_found")
    if smart is None:
        raise NotAvailable("off")
    try:
        if smart.count_call is not None:
            await smart.count_call()
        async with asyncio.timeout(CALL_WAIT_S):
            (query,) = await smart.voyage.embed([text], query=True)
        near = await _scan(db, session, smart, guild_id, campaign_id, query)
        if near is None:  # (stopped being a DM while the words were away)
            raise RecapError("not_found")
        close, capped = near
        if not close:
            return _answer([], capped)
        if smart.count_call is not None:
            await smart.count_call()
        async with asyncio.timeout(CALL_WAIT_S):
            ranked = await smart.voyage.rerank(
                text, [compose(c["parts"]) for c in close], top_k=ROWS_MAX
            )
    except AIOutOfFunds:
        log.warning("Names search by meaning: no credit left")
        raise NotAvailable("no_credit") from None
    except (ProviderError, TimeoutError) as exc:
        log.warning(
            "Names search by meaning: a call failed (%s)",
            exc.describe() if isinstance(exc, ProviderError) else "too slow",
        )
        raise NotAvailable("unreachable") from None
    order = [
        str(close[r.index]["id"]) for r in ranked if r.score >= MIN_SCORE and r.index < len(close)
    ]
    log.info("Names search by meaning: %d candidates, %d names", len(close), len(order))
    if not order:
        return _answer([], capped)
    page = await all_names.read_page(
        db, session, ref, size=200, only=",".join(order)
    )  # (the rows' usual shape; the door keeps what a DM may see)
    rows = {str(n["id"]): n for n in page["names"]}
    return _answer([rows[i] for i in order if i in rows], capped)


async def _scan(
    db: Database,
    session: Session,
    smart: SmartNames,
    guild_id: int,
    campaign_id: str,
    query: list[float],
) -> tuple[list[dict[str, Any]], bool] | None:
    """The comparing, in one of the few slots a process has for it (a scan is a core's worth of
    CPU). Waiting for a slot is limited too, and a scan that outlasts the website's own limit on a
    statement is "slow", not an error."""
    try:
        async with asyncio.timeout(CALL_WAIT_S):
            await smart.scans.acquire()
    except TimeoutError:
        log.warning("Names search by meaning: no scan slot free in time")
        raise NotAvailable("slow") from None
    try:
        return await _near(db, session, guild_id, campaign_id, smart.model, query)
    except psycopg.errors.QueryCanceled:
        log.warning("Names search by meaning: the scan ran out of time")
        raise NotAvailable("slow") from None
    finally:
        smart.scans.release()


def _answer(names: list[dict[str, Any]], capped: bool) -> dict[str, Any]:
    return {"names": names, "capped": capped}
