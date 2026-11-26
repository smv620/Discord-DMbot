"""The Shared story adventure on the website (#3694; the Discord side is the Shared story card,
`dm_screen/adventure_card.py`).

Slice A: what DMbot holds of the DM's adventure, read-only. Each file given, how far its read is,
and what the read found, for a DM of the campaign only, from one door (`dmbot_web_adventure_list`,
migration 0634). Counts and the DM's own file names; never a word of the adventure, which is only
ever shown on the DM's own pages and never put in a log. The same numbers as Discord's card
(`story_plan`), so the page can only say what the card says.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory.adventure import CAMPAIGN_MAX_CHARS, DOCUMENTS_MAX
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


async def held(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{mode, documents: [{id, name, characters, restored, addedAt, read, found}], documentsMax,
    charactersMax, charactersHeld}`. `read` is null until a read began, else `{status, nextChunk,
    totalChunks, stopReason, skippedParts, updatedAt}` (`status`: reading, done or stopped).
    `found` is `{chapters, places, people, links}`. `restored`: back from a backup, with no text
    until the file is given again. A DM of the campaign only: anyone else is "not found"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_list(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    documents = list(got["documents"])
    return {
        "mode": got["mode"],
        "documents": documents,
        "documentsMax": DOCUMENTS_MAX,
        "charactersMax": CAMPAIGN_MAX_CHARS,
        "charactersHeld": sum(int(d["characters"]) for d in documents),
    }


PAGE = 50  # the door's own cap too


async def read_nodes(db: Database, session: Session, ref: str, offset: int = 0) -> dict[str, Any]:
    """Slice B: a page of what was read, as Discord's "See what was read" lists it: `{total,
    offset, items: [{kind, name, summary, quote}]}`, `kind` being chapter, place, person or link.
    This is the book's own content: a DM of the campaign only (anyone else is "not found"), never
    logged, never cached here, and one page per call."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    start = max(0, offset)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_plan_nodes(%s, %s, %s, %s) AS r", (guild_id, campaign_id, start, PAGE)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return {"total": got["total"], "offset": start, "items": got["items"]}


_ID = re.compile(r"^[0-9a-f]{32}$")


async def remove(db: Database, session: Session, ref: str, document: str) -> str:
    """Slice C: queue the removal of one file, as Discord's 🗑 does it (the bot calls the same
    `AdventureStore.remove`). Returns the removal's id for `removal`. A DM of the campaign only;
    "not found" for anyone else, a malformed id and a file that is not there, so the page can't
    tell the cases apart."""
    if not _ID.match(document):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_remove(%s, %s, %s) AS r", (guild_id, campaign_id, document)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got["status"] != "ok":
        raise RecapError("not_found")
    return str(got["id"])


async def removal(db: Database, session: Session, ref: str, removal_id: str) -> dict[str, Any]:
    """How a queued removal went: `{state: waiting|working|done|failed, error, updatedAt}`, where
    `error` is a code (`not_yours`, `failed`) the page turns into words."""
    if not _ID.match(removal_id):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_adventure_remove_read(%s, %s, %s) AS r",
            (guild_id, campaign_id, removal_id),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got.get("missing"):
        raise RecapError("not_found")
    return {"id": got["id"], "state": got["state"], "error": got["error"],
            "updatedAt": got["updatedAt"]}  # fmt: skip
