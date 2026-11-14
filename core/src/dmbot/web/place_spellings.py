"""The proposed spellings of places, for the website's "Is this the same place?" question (#3533).

A new spelling of a place the campaign already has waits as a proposed other name (#3532). The list
comes from a database door (`dmbot_web_place_spellings`) that works out the DM check itself, so a
player or another campaign gets nothing. The answers (Yes: confirm the other name; No: turn it
down for good) are queued like any name action (`spelling-yes`, `spelling-no`, see `all_names`) and
applied by the bot. Ids, the place's name and the proposed spelling only.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


async def read_place_spellings(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{"spellings": [{id, placeId, place, spelling}]}` for a DM of the campaign; `not_allowed`
    for anyone else who is in the server, `not_found` for a campaign that is not there."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_place_spellings(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
        got = found["r"] if found else None
        if got is None:
            cur = await conn.execute(
                "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s", (guild_id, campaign_id)
            )
            if await cur.fetchone() is None:
                raise RecapError("not_found")
            raise RecapError("not_allowed")
    return {"spellings": got}
