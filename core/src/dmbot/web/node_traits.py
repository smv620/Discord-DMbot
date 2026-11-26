"""A character's lineage and class on the website (#3925, #2959 item 3): the lines Discord's name
card shows the DM ("🧬 Lineage: Dwarf · Class: Cleric"), for a DM of the campaign only. One database
door (`dmbot_web_node_traits`, migration 0587) answers NULL to anyone else, so a player or a
stranger gets "not allowed" and never a word. Names and flags only; the page words the line."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

NODE_ID = re.compile(r"^[0-9a-f]{32}$")


async def read(db: Database, session: Session, ref: str, node: str) -> dict[str, Any]:
    """`{traits: [{kind: "species" | "class", name, known, edition}]}`; `not_allowed` for anyone
    who is not a DM of the campaign, `not_found` for an id that is not an entry's."""
    if not NODE_ID.fullmatch(node):
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_node_traits(%s, %s, %s) AS r", (guild_id, campaign_id, node)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {
        "traits": [
            {
                "kind": str(t["kind"]),
                "name": str(t["name"]),
                "known": bool(t["known"]),
                "edition": str(t["edition"]),
            }
            for t in got
        ]
    }
