"""When a person last opened a campaign on the website (#5037): the account page lists campaigns by
last use, and their own visit is one of the three times that order comes from. One database door
(`dmbot_web_campaign_visit`, migration 0596) writes the time on the person's own row, at most once
a minute for one campaign, and only for a server in their session's list and a campaign that exists
there. A time only: no page, no content. It is quiet on purpose: a visit that cannot be recorded is
no reason to refuse the page."""

from __future__ import annotations

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import _guild_of, _scoped
from dmbot.web.sessions import Session


async def record(db: Database, session: Session, ref: str) -> bool:
    """Notes that this person opened campaign `ref` now. True when a time was written: the door
    writes once a minute at most, and nothing for a campaign outside their session's servers."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_campaign_visit(%s, %s) AS wrote", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    return bool(found and found["wrote"])
