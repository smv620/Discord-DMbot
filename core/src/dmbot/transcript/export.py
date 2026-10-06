"""Transcript downloads as text files (#41, #125). Pure: no Discord, no database.

A download is a plain .txt file anyone can open: a short header, then one line per piece
of speech with the time since the session started, `0:42:10 Mia: …`. Speaker names are
display names at download time (DMbot doesn't store names). Until the Transcript
Cleaner exists (Phase 2b) the only version is "as heard". Sessions are named by number
("Session 7"), since a date in UTC can look like the wrong day to an evening table.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Iterable, Mapping

from dmbot.transcript.models import Line, TranscriptSession

UNKNOWN_SPEAKER = "Someone"
NAME_MAX = 80
FILE_NAME_MAX = 60

AS_HEARD_NOTE = (
    "As heard: what DMbot wrote down, with no fixes. Some words and names may be "
    "misheard. Only people who agreed were recorded."
)


def clean_name(raw: str | None) -> str:
    """A name fit for a file: no invisible characters that flip how a line reads, no
    line breaks, never empty."""
    name = " ".join((raw or "").split())  # line breaks and tabs become spaces
    name = "".join(c for c in name if unicodedata.category(c) not in ("Cf", "Cc"))
    return name.strip()[:NAME_MAX] or UNKNOWN_SPEAKER


def clock(seconds: float) -> str:
    """Time since the session started: 0:42:10."""
    s = max(0, int(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def duration(seconds: int) -> str:
    """'2 h 14 min', '35 min', 'under a minute'."""
    minutes = max(0, seconds) // 60
    if minutes == 0:
        return "under a minute"
    hours, minutes = divmod(minutes, 60)
    if hours == 0:
        return f"{minutes} min"
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def _day(session: TranscriptSession) -> str:
    return time.strftime("%b %d", time.gmtime(session.started_at)).replace(" 0", " ")


def session_label(session: TranscriptSession, *, running: bool = False) -> str:
    """For menus: 'Session 7 · 2 h 14 min · Oct 6 (UTC)'."""
    if running:
        how_long = "🔴 recording now"
    elif session.ended_at is None:
        how_long = "stopped early"
    else:
        how_long = duration(session.ended_at - session.started_at)
    return f"Session {session.number} · {how_long} · {_day(session)} (UTC)"


def file_name(campaign_name: str, session: TranscriptSession) -> str:
    """'rime-of-the-frostmaiden-session-7-as-heard.txt': safe on every system."""
    slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.lower()).strip("-")[:FILE_NAME_MAX]
    return f"{slug or 'campaign'}-session-{session.number}-as-heard.txt"


def render(
    campaign_name: str,
    session: TranscriptSession,
    lines: Iterable[Line],
    names: Mapping[int, str],
    *,
    running: bool = False,
) -> str:
    """The whole file. `names`: speaker ID → display name (missing ones show as
    'Someone'). `running`: DMbot is still recording this session."""
    start_ms = session.started_at * 1000
    shown: dict[int, str] = {}  # each name cleaned once
    body = []
    for line in lines:
        if line.user_id not in shown:
            shown[line.user_id] = clean_name(names.get(line.user_id))
        when = clock((line.started_ms - start_ms) / 1000)
        body.append(f"{when} {shown[line.user_id]}: {' '.join(line.heard.split())}")
    started = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(session.started_at))
    ran = (
        f", ran {duration(session.ended_at - session.started_at)}"
        if session.ended_at is not None and not running
        else ""
    )
    header = [
        f"DMbot transcript: {clean_name(campaign_name)}, session {session.number}",
        f"Started {started}{ran}",
        AS_HEARD_NOTE,
    ]
    if running:
        header.append(
            "DMbot is still recording, so this file stops here"
            + (f", at {body[-1].split(' ', 1)[0]}." if body else ".")
        )
    return "\n".join([*header, "", *body]) + "\n"
