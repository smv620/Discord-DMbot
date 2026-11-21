"""The website's "Things the characters learned" list (#3863 step 2): the reveal proposals waiting
for the DM, in words, and Keep or Not this on each.

The words need the knowledge gate and the consent re-check, which only the bot has, so the page
first asks the bot (`reveals-ask` on the queue, no AI call: it only reads), which keeps them for
this DM in `web_reveals` (migration 0586, swept after an hour) and `read` gives them back through
the DM-only door `dmbot_web_reveals`. Asking again replaces the set, so the page asks before every
read, also after each answer, and never sees a proposal twice. A press (`queue_answer`) goes
through the queue to `RevealProposals.keep` / `dismiss`, which check everything again, so a stale
page can never keep more than the bot would now; the Undo of a keep is the existing `join-undo`.
Campaign words the DM may read on the card, shown to a DM only, never logged."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")


async def read(db: Database, session: Session, ref: str) -> list[dict[str, Any]]:
    """`[{id, who, what, how, player, guessing}]` in the card's order, as the bot last worked them
    out for this DM (empty until it has). `not_allowed` for anyone who is not a DM."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute("SELECT dmbot_web_reveals(%s, %s) AS r", (guild_id, campaign_id))
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return [
        {
            "id": str(r["id"]),
            "who": str(r["who"]),
            "what": str(r["what"]),
            "how": str(r["how"]),
            "player": bool(r["player"]),
            "guessing": bool(r["guessing"]),
        }
        for r in got
    ]


async def queue_ask(db: Database, session: Session, ref: str) -> str:
    """Have the bot work the list out and keep it for this DM. The id of the answer the page
    polls (it is done when the list is kept; then `read` has it)."""
    return await relationships._queue(
        db, session, ref, entity=None, action="reveals-ask", target="", text="", flag=False
    )


async def queue_answer(db: Database, session: Session, ref: str, body: object) -> str:
    """`{"id": <the proposal>, "answer": "keep" | "not"}`. The id of the answer the page polls."""
    pid = body.get("id") if isinstance(body, dict) else None
    answer = body.get("answer") if isinstance(body, dict) else None
    if not (isinstance(pid, str) and _ID.fullmatch(pid)) or answer not in ("keep", "not"):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action=f"reveals-{answer}", target="", text=pid,
        flag=False,
    )  # fmt: skip
