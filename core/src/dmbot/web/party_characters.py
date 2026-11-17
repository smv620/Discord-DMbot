"""The party's player characters on the website (#4680, the web half of #4534), for a campaign's DM
only: the picker's list for 👥 Some know. One database door (`dmbot_web_party_characters`,
migration 0535) answers NULL to anyone else, so a player or a stranger gets "not allowed" and no
name. The same people Discord's picker offers: the campaign's confirmed player characters, A to Z.
Names and the entries' own ids only (the answer needs the id)."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{characters: [{id, name}]}`, A to Z. `not_allowed` for anyone who is not a DM of the
    campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_party_characters(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {"characters": [{"id": str(r["id"]), "name": str(r["name"])} for r in got]}
