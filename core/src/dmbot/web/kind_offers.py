"""The words DMbot stopped asking about, for the website (#3577, the website half of #3033).

After a session DMbot may ask whether words the table keeps using for a connection should become a
kind of connection. A DM's Never or Not now is kept (`memory_kind_offers`); this lists those answers
for the campaign's DMs from a database door that works out the DM check itself, so a player
or another campaign gets nothing. Lifting one is a queued `offer-clear` action (`all_names`),
applied by the bot through `clear_kind_offer`. The words are the table's own, shown to the DM
only.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


async def read_kind_offers(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{"offers": [{words, state}]}` for a DM of the campaign (`state` is "never" or "later");
    `not_allowed` for anyone else in the server, `not_found` for a campaign that is not there."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_kind_offers(%s, %s) AS r", (guild_id, campaign_id)
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
    return {"offers": got}
