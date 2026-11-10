"""The lineage and class questions waiting for the DM, for the website (#3946, the web half of
#3926): what the lore said about a character the campaign already has, and what DMbot has for them
now. One database door (`dmbot_web_trait_offers`, 0410) answers it for a DM of the campaign; anyone
else gets "not found". The quote and the upload's label are the DM's own words: DM only."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


async def waiting(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{offers: [...]}`: each with the character (`entity`, `who`), the trait (`kind` species or
    class, `name`, `known`, `edition`), what DMbot has now (`current` lineage or null, `classes`),
    the upload's `upload` label and the reader's `quote`. Empty when nothing waits."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_trait_offers(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return {"offers": list(got)}
