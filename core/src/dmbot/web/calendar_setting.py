"""The website's Game calendar panel (#5364 slice 3a): what the campaign's calendar is now, and
what a calendar the DM is typing would say, both worded by core so the page never counts a date.

Game masters only (anyone else gets `not_found`, as for the clock door they read through). The read
goes through the DM-only clock door (`dmbot_web_game_clock`); the preview reads nothing and stores
nothing, and logs nothing: a definition is the DM's own words. Saving is the queue's (3b).
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.timebot import calendar as cal
from dmbot.timebot import calendar_setting as setting
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


def shape(calendar: cal.Calendar, *, has_clock: bool) -> dict[str, Any]:
    """The panel's answer: `kind`, the definition (a calendar of its own only), where Day 1 falls,
    the preview line, and whether there is a game clock to save onto (pure, so it is tested with
    no database)."""
    return {
        "kind": calendar.kind,
        "def": cal.def_of(calendar)["layout"] if calendar.kind == cal.CUSTOM else None,
        "dayOne": setting.day_one_of(calendar),
        "line": setting.line_of(calendar),
        "hasClock": has_clock,
    }


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_game_clock(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = (await cur.fetchone() or {}).get("r")
    if not isinstance(found, dict):
        raise RecapError("not_found")  # (not a DM of the campaign, or not in the server)
    # `{}` is a campaign with no game clock yet: Day N, and nothing to save onto
    return shape(cal.from_door(found), has_clock=found.get("minute") is not None)


async def preview(db: Database, session: Session, ref: str, body: object) -> dict[str, Any]:
    """`{"line": "Day 1 is …"}` for a calendar the body names, or `{"problems": [words]}`. The
    caller must be a DM of the campaign (the clock door says so); nothing is stored."""
    await read(db, session, ref)  # (the same DM check as the read: not_found for anyone else)
    try:
        calendar = setting.from_body(body)
    except setting.CalendarProblem as bad:
        return {"problems": bad.problems}
    return {"line": setting.line_of(calendar)}
