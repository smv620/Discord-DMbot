"""A campaign's past sessions with what each one's recap is (#2610).

The route is `GET /me/campaigns/{ref}/past-sessions`.

The recap page for a DM lists every session of a campaign, newest first, so the DM can make a
recap for one that has none, make it again, or add one someone wrote (#2608, #2609). Only the
campaign's DMs are told anything: a player, or someone asking about a campaign that is not theirs,
gets `not_allowed`. Nothing new is read from the database: the sessions are the transcripts page's
own list (the same numbers), the recaps are read through the same row-level security as the recap
list, and the held ones come from the recap list's own door. Nothing of a transcript or a recap's
words leaves this module, only counts, states and who wrote a recap.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.recaps.notify import job_ref, parse_ref
from dmbot.web.recaps import (
    _COLUMNS,
    _FROM,
    RecapError,
    _guild_of,
    _holds,
    _scoped,
    _view,
    session_facts,
)
from dmbot.web.sessions import Session

LIMIT = 500  # the most sessions one answer lists; a campaign has far fewer


def _parse(ref: str) -> tuple[int, str]:
    parsed = parse_ref(f"{ref}.0")
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


def recap_of(view: dict[str, Any]) -> dict[str, Any]:
    """What the page needs of one recap: its state, whether it is being made, approved or written by
    someone else, and whether it may be made again."""
    return {
        "state": view["state"],
        "status": view["status"],
        "working": bool(view["working"]),
        "approved": bool((view.get("approval") or {}).get("approved")),
        "writtenBy": view.get("writtenBy"),
        "canMakeAgain": bool(view.get("canMakeAgain", False)),
    }


async def read_past_sessions(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The campaign's sessions, newest first. Each is `{ref, sessionAt, sessionNumber,
    sessionSeconds, hasTranscript, held, recap}`: `ref` is what "make it again" takes, `held` says a
    recap waits for the DM's answers about names, and `recap` is null when the session has none.
    A recap whose session has no transcript row (one a DM added by date) is listed too."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
            (guild_id, campaign_id, session.user_id),
        )
        if await cur.fetchone() is None:
            raise RecapError("not_allowed")  # a player, or a campaign that is not theirs
        cur = await conn.execute(
            "SELECT dmbot_web_transcript_sessions(%s, %s) AS found", (guild_id, campaign_id)
        )
        found = (await cur.fetchone() or {}).get("found") or {}
        listed: list[dict[str, Any]] = list(found.get("sessions", []))
        cur = await conn.execute(
            f"SELECT {_COLUMNS} {_FROM} WHERE r.guild_id = %s AND r.campaign_id = %s"
            " ORDER BY r.session_at DESC LIMIT %s",
            (guild_id, campaign_id, LIMIT),
        )
        recaps = {int(r["session_at"]): _view(r) for r in await cur.fetchall()}
        holds = {
            int(h["sessionAt"])
            for h in await _holds(conn, guild_id)
            if h.get("campaignId") == campaign_id
        }
    sessions: list[dict[str, Any]] = []
    seen: set[int] = set()
    for s in listed[:LIMIT]:
        at = int(s["startedAt"])
        seen.add(at)
        number, seconds = session_facts(listed, at)
        sessions.append(
            {
                "ref": job_ref(guild_id, campaign_id, at),
                "sessionAt": at,
                "sessionNumber": number,
                "sessionSeconds": seconds,
                # lines kept: not erased by "Keep transcripts for", and something was said
                "hasTranscript": s.get("transcriptsDeletedAt") is None
                and int(s.get("lines") or 0) > 0,
                "held": at in holds and at not in recaps,
                "recap": recap_of(recaps[at]) if at in recaps else None,
            }
        )
    for at, view in recaps.items():
        if at in seen:
            continue
        sessions.append(
            {
                "ref": job_ref(guild_id, campaign_id, at),
                "sessionAt": at,
                "sessionNumber": None,
                "sessionSeconds": None,
                "hasTranscript": False,
                "held": False,
                "recap": recap_of(view),
            }
        )
    sessions.sort(key=lambda x: (-int(x["sessionAt"]), str(x["ref"])))
    return {"sessions": sessions[:LIMIT]}
