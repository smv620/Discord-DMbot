"""The website's Settings page, the part that changes things (#2463): a DM's choice for who can see
the DM screen, how much DMbot says, and how long transcripts are kept.

The website's database role has no right on the campaign tables, so a choice is queued through one
database door (`dmbot_web_setting_add`, schema.py), which checks the signed-in person is a DM of the
campaign; the bot applies it the way Discord's ⚙️ Settings buttons do (dm_screen/web_settings.py)
and the page reads where it stands. A server manager who is not a DM, a player and anyone else get
"not allowed" or "not found", the same as for the other pages. Not here: game masters, hand over,
pause and delete (the page sends the DM to Discord for those).

A campaign is named by `<server>.<campaign>`, as for the recap and transcript pages.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

# What each setting accepts: the choices Discord offers, nothing else.
CHOICES: dict[str, tuple[str, ...]] = {
    "visibility": ("private", "peek", "open"),
    "level": ("quiet", "normal"),
    "retention": ("forever", "365", "90", "30"),
}


def _arguments(body: object) -> tuple[str, str, bool]:
    """(setting, value, confirmed) for the database door, or the refusal word."""
    if not isinstance(body, dict):
        raise RecapError("bad_setting")
    setting = body.get("setting")
    if not isinstance(setting, str) or setting not in CHOICES:
        raise RecapError("bad_setting")
    value = body.get("value")
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)  # (a page may send 90 for "90 days")
    if not isinstance(value, str) or value not in CHOICES[setting]:
        raise RecapError("bad_value")
    return setting, value, body.get("confirm") is True


async def queue_setting(db: Database, session: Session, ref: str, body: object) -> str:
    """Queue one choice of the signed-in DM for the bot and return its id. A server the person is
    not in is "not found"; a member who is not a DM of the campaign is "not allowed". A shorter
    transcript limit deletes old sessions, so the page asks first and sends `confirm`; the bot
    refuses it without."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    setting, value, confirmed = _arguments(body)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_setting_add(%s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, setting, value, confirmed),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


async def read_setting(db: Database, session: Session, ref: str, answer: str) -> dict[str, Any]:
    """Where one of the caller's own choices stands: `state` (waiting, working, done, failed), the
    `error` word when it failed, and `note`, the words Discord would say (for a rule the DM can
    fix, or what the change did)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not ID_PATTERN.match(answer):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_setting_read(%s, %s, %s) AS r", (guild_id, campaign_id, answer)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return {
        "state": got["state"],
        "error": got["error"],
        "setting": got["setting"],
        "value": got["value"],
        "note": got["note"],
    }
