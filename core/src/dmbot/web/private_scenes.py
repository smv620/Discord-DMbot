"""The website's "Private scenes" list (#4102, the web half of #2996 rule 5): the times two player
characters were alone together while the party was split, for the DM only. Two database doors
(0439) list them after the consent re-check and mark one "Not private". Names, a date, about
how long and how many lines: never a word of what was said (the store keeps none) and never who
spoke."""

from __future__ import annotations

import re
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")


async def listed(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{items: [{id, first, second, sessionStartedAt, minutes, lines}]}`, newest first. A
    non-DM is refused (`not_allowed`)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_private_scenes(%s, %s, %s) AS r",
            (guild_id, campaign_id, TERMS_VERSION),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {
        "items": [
            {
                "id": str(s["id"]),
                "first": str(s["first"]),
                "second": str(s["second"]),
                "sessionStartedAt": int(s["sessionStartedAt"]),
                "minutes": int(s["minutes"]),
                "lines": int(s["lines"]),
            }
            for s in got
        ]
    }


async def not_private(db: Database, session: Session, ref: str, scene_id: str) -> dict[str, str]:
    """Mark one scene "Not private": DMbot never counts it again. Returns the two names for the
    page's line. Refusals: `not_found` (a malformed id), `not_allowed` (not a DM) and `gone`
    (already marked, or not there)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.fullmatch(scene_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_private_scene_not_private(%s, %s, %s) AS r",
            (guild_id, campaign_id, scene_id),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return {"first": str(got["first"]), "second": str(got["second"])}
