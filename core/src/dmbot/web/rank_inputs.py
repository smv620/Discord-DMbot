"""What the story summary's importance rank is counted from (#3236): ids, kinds, roles, statuses,
the plain connections, each entry's newest assessment, the sessions it was heard in and the entries
said in a kept private scene (`private`, #4110). Counts and dates only. The API ranks with it in
Python and sends none of it to a browser."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


async def read_rank_inputs(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The door's answer for one campaign, for any member of its server (the same reach as the graph
    source). `not_found` for anyone else or a campaign that is not there."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_rank_inputs(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got["status"] != "ok":
        raise RecapError("not_found")
    return dict(got)
