"""The website's World notes (#4285, slice 1c's door; the website half): DMbot's guesses about
places and languages, for a DM of the campaign only. One database door (`dmbot_web_world_notes`,
migration 0481) answers NULL to anyone else, so a player or a stranger gets "not allowed" and never
a line. This module shapes the door's lines, and queues the three actions (`world-keep`,
`world-not`, `world-undo`) through the one queue door as the connection edits do: the bot applies
each in one write, so each is one Undo. Words, ids and counts only: no story text."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

LINE_ID = re.compile(r"^(place|language):[0-9a-f]{32}$")
KEEP_AT_MOST = 25  # Discord's menu limit: Keep all only keeps lines the DM was shown
_BATCH = re.compile(r"^[0-9]{1,15}$")


def _line(kind: str, r: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(r["id"]),
        "entityId": str(r["entityId"]),
        "name": str(r["name"]),
        "from": str(r.get("from") or ""),
        "fromSession": int(r["fromSession"]) if r.get("fromSession") else None,
    }
    if kind == "language":
        # the door names a language line's word `value` (a place's is its land or climate)
        out["language"] = str(r.get("language") or r.get("value") or "")
        return out
    out["layer"] = str(r["layer"])
    out["value"] = str(r["value"])
    out["words"] = str(r.get("words") or "")  # the DM's own words, when the value is "other"
    out["how"] = str(r["how"]) if r.get("how") else None
    out["rulerId"] = str(r["rulerId"]) if r.get("rulerId") else None
    out["ruler"] = str(r["ruler"]) if r.get("ruler") else None
    return out


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{places: [...], languages: [...]}`, the lines of Discord's 🌍 World notes list.
    `not_allowed` for anyone who is not a DM of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_world_notes(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    by_kind = {str(g["kind"]): g.get("lines") or [] for g in got.get("groups", [])}
    return {
        "places": [_line("place", r) for r in by_kind.get("place", [])],
        "languages": [_line("language", r) for r in by_kind.get("language", [])],
    }


async def queue_keep(db: Database, session: Session, ref: str, body: object) -> str:
    """✅ Keep: the ids of the lines the DM was shown (one is a single line, many is Keep all)."""
    ids = body.get("ids") if isinstance(body, dict) else None
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= KEEP_AT_MOST
        or not all(isinstance(i, str) and LINE_ID.fullmatch(i) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="world-keep", target="", text=" ".join(ids),
        flag=False,
    )  # fmt: skip


async def queue_not(db: Database, session: Session, ref: str, body: object) -> str:
    """🚫 Not right: one line."""
    line = body.get("id") if isinstance(body, dict) else None
    if not isinstance(line, str) or not LINE_ID.fullmatch(line):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="world-not", target="", text=line, flag=False
    )


async def queue_undo(db: Database, session: Session, ref: str, batch: str) -> str:
    """↩ Undo of a ✅ or 🚫 made from the page, by the change number its answer carried. The bot
    reads the number again in the caller's own done actions before it undoes anything."""
    if not _BATCH.fullmatch(batch):
        raise RecapError("not_found")
    return await relationships._queue(
        db, session, ref, entity=None, action="world-undo", target="", text=batch, flag=False
    )
