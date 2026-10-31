"""A campaign's session notes on the website (#3027, #3026), for its game masters alone.

The routes are `GET /me/campaigns/{ref}/session-notes` (which sessions have notes) and
`GET /me/campaigns/{ref}/session-notes/{at}` (one session's numbered beats).

A session's notes are its beat sheet, kept for good: they survive the deletion of transcript lines
and go only with the campaign. They are the campaign's own story notes, so only its DMs see them: a
player, a peek, or someone whose campaign it is not gets `not_allowed`, and nothing of them is in
any players' view. The website's database role has no right on `session_notes`, so everything
comes from two doors (`dmbot_web_session_notes_list`, `dmbot_web_session_notes`, migration 0308).
They send words only: a beat's number, what happened, who was in it, where, and its kind. The
pointers to transcript lines and PlotBot's notes about why a beat matters never leave the database.
This module adds the session's number (the transcripts page's own) and nothing else. Read-only.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.report import _parse_at
from dmbot.web.sessions import Session
from dmbot.web.transcripts import numbered_sessions


async def _as_dm(conn: Any, session: Session, guild_id: int, campaign_id: str) -> None:
    cur = await conn.execute(
        "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
        (guild_id, campaign_id, session.user_id),
    )
    if await cur.fetchone() is None:
        raise RecapError("not_allowed")  # a player, or a campaign that is not theirs


async def read_notes_list(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The sessions that have notes, newest first: `{sessionAt, sessionNumber, beats, writtenAt}`.
    `sessionNumber` is the transcripts page's number for that session, or null when it has none
    (the transcript's lines are all gone, or it was added by date). DM only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        await _as_dm(conn, session, guild_id, campaign_id)
        numbers = {
            int(s["startedAt"]): int(s["number"])
            for s in await numbered_sessions(conn, guild_id, campaign_id)
        }
        cur = await conn.execute(
            "SELECT dmbot_web_session_notes_list(%s, %s) AS r", (guild_id, campaign_id)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    return {
        "sessions": [
            {
                "sessionAt": int(s["at"]),
                "sessionNumber": numbers.get(int(s["at"])),
                "beats": int(s["beats"]),
                "writtenAt": int(s["writtenAt"]),
            }
            for s in got["sessions"]
        ]
    }


async def read_notes(db: Database, session: Session, ref: str, at: object) -> dict[str, Any]:
    """One session's notes, the session that began at `at` (Unix seconds): `{sessionAt,
    sessionNumber, writtenAt, beats: [{number, what, who, where, kind}]}`. A session with no notes
    is a `not_found`. DM only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    when = _parse_at(at)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        await _as_dm(conn, session, guild_id, campaign_id)
        numbers = {
            int(s["startedAt"]): int(s["number"])
            for s in await numbered_sessions(conn, guild_id, campaign_id)
        }
        cur = await conn.execute(
            "SELECT dmbot_web_session_notes(%s, %s, %s) AS r", (guild_id, campaign_id, when)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    if got.get("status") != "ok":
        raise RecapError("not_found")
    return {
        "sessionAt": int(got["at"]),
        "sessionNumber": numbers.get(int(got["at"])),
        "writtenAt": int(got["writtenAt"]),
        "beats": [
            {
                "number": int(b["number"]),
                "what": str(b["what"]),
                "who": [str(w) for w in b["who"]],
                "where": str(b["where"]),
                "kind": str(b["kind"]),
            }
            for b in got["beats"]
        ],
    }
