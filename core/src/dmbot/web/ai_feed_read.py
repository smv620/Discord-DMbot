"""The lore and import pages' live windows on what DMbot asked the AI (#3710), for a campaign's DMs.

One database door (`dmbot_web_ai_feed`) answers a DM or game master of the campaign and no one
else. What it returns was cut to 500 characters before it was ever written, and it is shown as
plain text. Nothing here logs a prompt or a reply, and a word of either never leaves this module
except in the answer to that DM."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

KINDS = ("lore", "import")
JOB = re.compile(r"[0-9a-f]{32}")


async def read_feed(
    db: Database, session: Session, ref: str, kind: str, job: str, after: int
) -> dict[str, Any]:
    """The calls of one lore upload or import since `after` (a Unix second), those still waiting,
    and how many were not written. A kind or job that is not shaped right is "not found"; someone
    who is not a DM of the campaign is "not allowed"."""
    if kind not in KINDS or not JOB.fullmatch(job) or not 0 <= after < 10**11:
        raise RecapError("not_found")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_ai_feed(%s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, kind, job, after),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {
        "lost": int(got["lost"]),
        "entries": [
            {
                "job": str(e["job"]),
                "n": int(e["n"]),
                "at": int(e["at"]),
                "service": str(e["service"]),
                "model": str(e["model"]),
                "step": str(e["step"]),
                "prompt": str(e["prompt"]),
                "reply": None if e["reply"] is None else str(e["reply"]),
                "state": str(e["state"]),
                "reason": None if e["reason"] is None else str(e["reason"]),
            }
            for e in got["entries"]
        ],
    }
