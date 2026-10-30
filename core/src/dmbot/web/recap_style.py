"""Recap style on the website (#2985): the example recaps and standing notes that show how a
campaign's recaps sound, read and changed by the campaign's DMs.

The website's role cannot write the style tables, so a change is queued
(`dmbot_web_recap_style_add`, migration 0290) and the bot applies it with the same store calls
as Discord's Recap style panel, then the page polls its answer. The DM's examples are their own
pasted recaps and may name secrets, so every call here is DM only: anyone else gets
`not_allowed`, and no player ever sees them.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

ACTIONS = ("add_example", "remove_example", "use_defaults", "add_note", "remove_note")
# Discord's limits (recaps/style.py); the door and the bot check the same numbers. Web code cannot
# import them (they pull in the Discord side), so a test keeps these in step.
EXAMPLE_CHARS = 8000
NOTE_CHARS = 300


def _check(body: object) -> tuple[str, str]:
    """The action and its words or id, or `bad_arg`; never a guess at a stray field."""
    if not isinstance(body, dict):
        raise RecapError("bad_arg")
    action, arg = body.get("action"), body.get("arg", "")
    if action not in ACTIONS or not isinstance(arg, str):
        raise RecapError("bad_action" if action not in ACTIONS else "bad_arg")
    # Postgres text cannot hold a NUL and psycopg cannot send a lone surrogate: refuse, not a 500.
    if "\x00" in arg or any(0xD800 <= ord(ch) <= 0xDFFF for ch in arg):
        raise RecapError("bad_arg")
    if len(arg) > EXAMPLE_CHARS:
        raise RecapError("too_long")
    if action in ("remove_example", "remove_note") and not re.fullmatch(r"[0-9]{1,18}", arg):
        raise RecapError("bad_arg")
    return str(action), arg


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The campaign's style: `{"examples": [{id, text}], "notes": [{id, text}], "defaults": bool}`.
    DM only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_recap_style(%s, %s) AS r", (guild_id, campaign_id)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    return dict(got)


async def add(db: Database, session: Session, ref: str, body: object) -> dict[str, Any]:
    """Queue one change; answers `{"id": ...}` to poll. `bad_arg`, `bad_action` and `too_long` for
    a shape the door refuses, `too_soon` with ten open at once, `not_allowed` for a non-DM."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    action, arg = _check(body)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_recap_style_add(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, action, arg),
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return {"id": str(got["id"])}


async def action(db: Database, session: Session, ref: str, action_id: str) -> dict[str, Any]:
    """Where one queued change stands (state, error word, times; never its words). DM only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not re.fullmatch(r"[0-9a-f]{32}", action_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_recap_style_action(%s, %s, %s) AS r",
            (guild_id, campaign_id, action_id),
        )
        got = (await cur.fetchone() or {}).get("r")
        if got is None:
            cur = await conn.execute(
                "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s",
                (guild_id, campaign_id, session.user_id),
            )
            if await cur.fetchone() is None:
                raise RecapError("not_allowed")
            raise RecapError("not_found")
    return dict(got)
