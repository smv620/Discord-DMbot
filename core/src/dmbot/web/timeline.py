"""The Story timeline page (#2462): the campaign's sessions in order, each with its number, the
day it was played, its length, its recap and the names shown to the players at the table.

The sessions, their numbers and lengths are the transcripts page's own list (`transcripts.
list_sessions`), so a number is never counted twice and the same plan rule applies as to the
transcripts. One more door (`dmbot_web_story_timeline`, schema.py) adds the recaps and the names
shown at the table; it answers for anyone in the server, and the game masters see more: every
approved recap, where a player sees only posted ones, and none of a draft. A recap links to its
page only for a game master, because that page is theirs; a player reads the title.

A recap belongs to the session that started at its `session_at`, as the bot matches them. A name
shown at the table belongs to the session that was running, or ended in the last hour, when DMbot
showed it (it looks right after a session ends): one shown some other time, such as when an old
session was imported, is left out rather than put under the wrong session.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web import transcripts
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session
from dmbot.web.transcripts import _parse

# DMbot looks for names the table said right after a session ends; give it this long.
SCAN_WINDOW = 3600


def _session_of(sessions: list[dict[str, Any]], at: int, now: int) -> int | None:
    """The number of the session a name shown at `at` belongs to, else None. The session that was
    running then wins (the latest to have started, if two overlap); failing that, the latest one
    that ended in the hour before. A session with no end yet (still listening, or one that stopped
    early) runs until the next one starts, or until `now` if it is the last."""
    spans = []
    for i, s in enumerate(sessions):
        start = int(s["startedAt"])
        if s["endedAt"] is not None:
            end = int(s["endedAt"])
        else:
            end = int(sessions[i + 1]["startedAt"]) if i + 1 < len(sessions) else max(now, start)
        spans.append((start, end, int(s["number"])))
    running = [sp for sp in spans if sp[0] <= at <= sp[1]]
    if running:
        return max(running)[2]
    after = [sp for sp in spans if sp[1] < at <= sp[1] + SCAN_WINDOW]
    return max(after)[2] if after else None


async def read_timeline(
    db: Database, session: Session, ref: str, *, now: int, enforce_plans: bool
) -> dict[str, Any]:
    """The campaign's name and its sessions, oldest first, each with its recap (if any) and the
    names shown at the table. Raises `not_found` for anyone outside the server or a campaign that
    isn't there, and `not_in_plan` as the transcripts page does."""
    listed = await transcripts.list_sessions(db, session, ref, now=now, enforce_plans=enforce_plans)
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_story_timeline(%s, %s) AS found", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    if found is None:
        raise RecapError("not_found")
    is_dm = bool(found["dm"])
    by_start = {int(r["sessionAt"]): r for r in found["recaps"]}
    sessions = sorted(listed["sessions"], key=lambda s: s["number"])
    shown: dict[int, list[str]] = {}
    for reveal in found["reveals"]:
        number = _session_of(sessions, int(reveal["at"]), now)
        if number is not None:
            names = shown.setdefault(number, [])
            # A name shown twice (two scans) is listed once.
            names.extend(n for n in (str(x) for x in reveal["names"]) if n not in names)
    out: list[dict[str, Any]] = []
    for s in sessions:
        recap = by_start.get(int(s["startedAt"]))
        out.append(
            {
                "number": int(s["number"]),
                "startedAt": int(s["startedAt"]),
                "endedAt": s["endedAt"],
                "recap": None
                if recap is None
                else {
                    "title": str(recap["title"]),
                    "posted": recap["status"] == "posted",
                    # The recap's own page, for a game master only.
                    "ref": f"{guild_id}.{campaign_id}.{int(s['startedAt'])}" if is_dm else None,
                },
                "revealed": shown.get(int(s["number"]), []),
            }
        )
    return {"ref": f"{guild_id}.{campaign_id}", "name": listed["name"], "sessions": out}
