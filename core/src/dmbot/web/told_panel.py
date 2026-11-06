"""Who holds and who was told each secret other name, for the website's "Tell a character" panel
(#3542). The list comes from a database door (`dmbot_web_told_panel`) that works out the DM check
itself, so a player or another campaign gets nothing. Telling and taking back are queued like any
name action (`told-add`, `told-remove`, see `all_names`) and applied by the bot. Ids and names only,
shown to the DM.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


async def read_told_panel(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{"names": [{id, entryId, holders: [{id, name}], told: [{id, name}]}]}` for a DM of the
    campaign: `holders` hold the secret name and were not told, `told` were. `not_allowed` for
    anyone else in the server, `not_found` for a campaign that is not there."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_told_panel(%s, %s) AS r", (guild_id, campaign_id)
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
    return {"names": got}
