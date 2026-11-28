"""The website's Game calendar panel (#5364 slice 3a): what the campaign's calendar is now, and
what a calendar the DM is typing would say, both worded by core so the page never counts a date.

Game masters only (anyone else gets `not_found`, as for the clock door they read through). The read
goes through the DM-only clock door (`dmbot_web_game_clock`); the preview reads nothing and stores
nothing, and logs nothing: a definition is the DM's own words. Saving is the queue's (3b).
"""

from __future__ import annotations

import re
from typing import Any

from psycopg.types.json import Jsonb

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


async def _door(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The DM-only clock door's answer (`{}` for a campaign with no game clock), or `not_found`."""
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
    return found


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    found = await _door(db, session, ref)
    # `{}` is a campaign with no game clock yet: Day N, and nothing to save onto
    return shape(cal.from_door(found), has_clock=found.get("minute") is not None)


async def preview(db: Database, session: Session, ref: str, body: object) -> dict[str, Any]:
    """`{"line": "Day 1 is …", "today": "Today is …"|None}` for a calendar the body names, or
    `{"problems": [words]}`; with no game clock yet the answer adds a `note` saying it can be saved
    once the clock is started. The caller must be a DM of the campaign (the clock door says so);
    nothing is stored."""
    found = await _door(db, session, ref)  # (not_found for anyone who is not a DM)
    minute = found.get("minute")
    try:
        calendar = setting.from_body(body)
    except setting.CalendarProblem as bad:
        return {"problems": bad.problems}
    out: dict[str, Any] = {
        "line": setting.line_of(calendar),
        "extra": setting.leap_line(calendar),  # ("Shieldmeet comes every 4 years, after …")
        "today": setting.today_of(calendar, minute if type(minute) is int else None),
    }
    if minute is None:
        out["note"] = setting.NO_CLOCK_NOTE
    return out


ANSWER_ID = re.compile(r"^[0-9a-f]{32}$")
STALLED_AFTER_S = 120  # a save nobody has moved for this long says so (the wait rule)
# What each ending says, for the page (nothing here is the DM's own words).
ENDINGS = {
    "bad_calendar": setting.PROBLEMS["layout"],
    "no_clock": "Start the game clock first, then save the calendar.",
    "not_allowed": "Only a game master can change the calendar.",
    "failed": "DMbot couldn't save the calendar. Try again.",
}
BUSY = "Another save of this calendar is still going. Wait a moment, then try again."


async def save(db: Database, session: Session, ref: str, body: object) -> dict[str, Any]:
    """Queue the calendar `body` names (the preview's body), for the bot to save: `{"answer": id}`;
    `{"problems": [words]}` for a body core refuses (nothing queued); `{"error": "no_clock"|"busy",
    "words": …}` when it cannot be saved now. A game master only (`not_found` for anyone else)."""
    found = await _door(db, session, ref)
    try:
        calendar = setting.from_body(body)
    except setting.CalendarProblem as bad:
        return {"problems": bad.problems}
    if found.get("minute") is None:
        return {"error": "no_clock", "words": ENDINGS["no_clock"]}
    assert isinstance(body, dict)  # (from_body took it)
    clean: dict[str, Any] = {"kind": calendar.kind}
    if calendar.kind != cal.DAYS:
        clean["dayOne"] = body["dayOne"]
    if calendar.kind == cal.CUSTOM:
        clean["def"] = body["def"]
    guild_id, campaign_id = _parse(ref)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_calendar_save(%s, %s, %s) AS r",
            (guild_id, campaign_id, Jsonb(clean)),
        )
        queued = (await cur.fetchone() or {}).get("r")
    if not isinstance(queued, dict):
        raise RecapError("not_found")
    status = queued.get("status")
    if status == "ok":
        return {"answer": str(queued["id"])}
    if status == "busy":
        return {"error": "busy", "words": BUSY}
    return {"problems": [setting.PROBLEMS["big"]]}  # (too big, or not an object)


async def read_answer(db: Database, session: Session, ref: str, answer: str) -> dict[str, Any]:
    """How one save went, for the DM who asked: the state, `error` and its `words`, and the wait
    (stage, seconds waited, when it last moved) as every wait shows it. Times and words only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not ANSWER_ID.match(answer):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_calendar_save_read(%s, %s, %s) AS r", (guild_id, campaign_id, answer)
        )
        got = (await cur.fetchone() or {}).get("r")
    if not isinstance(got, dict) or got.get("missing"):
        raise RecapError("not_found")
    state, error = str(got["state"]), str(got["error"])
    created, moved, now = int(got["createdAt"]), int(got["updatedAt"]), int(got["serverNow"])
    return {
        "state": state,
        "error": error,
        "words": ENDINGS.get(error, ENDINGS["failed"]) if error else "",
        "waits": {
            "stage": {"waiting": "queued", "working": "saving"}.get(state, state),
            "waitedSeconds": max(0, now - created),
            "lastMovedAt": moved,
            "now": now,
            "stalled": state in ("waiting", "working") and now - moved > STALLED_AFTER_S,
        },
    }
