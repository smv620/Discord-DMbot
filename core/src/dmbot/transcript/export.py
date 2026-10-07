"""Transcript downloads as text files (#41, #125). Pure: no Discord, no database.

A download is a plain .txt file anyone can open: a short header, then one line per piece
of speech (#53): the time since the session started, the speaker, and the character
they play, `[0:42:10] (Mia) {Cerric}: …` (no `{…}` for someone without one, such as the
DM). Speaker names are display names at download time (DMbot doesn't store names);
characters are the campaign's player characters at download time. Each session comes
in two versions (#296): "cleaned" (names spelled right, what the transcript channel
showed) and "as heard" (exactly what speech-to-text wrote). Sessions are named by number
("Session 7"), since a date in UTC can look like the wrong day to an evening table.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Iterable, Mapping, Sequence

from dmbot.transcript.models import Line, TranscriptSession

UNKNOWN_SPEAKER = "Someone"
_BRACKETS = str.maketrans("", "", "()[]{}")
NAME_MAX = 80
FILE_NAME_MAX = 60

AS_HEARD_NOTE = (
    "As heard: the words exactly as DMbot heard them, before it fixed any names, so "
    'some names may be misheard. The "Cleaned" version (/transcript) has the names '
    "fixed. Only people who agreed were recorded."
)
CLEANED_NOTE = (
    "Cleaned: DMbot fixed the spelling of names it was sure about; a few may still be "
    'wrong. For the exact words, download the "As heard" version with /transcript. '
    "Only people who agreed were recorded."
)
HOW_TO_READ = "Each line: [time since start] (person) {their character}: what they said."

AS_HEARD, CLEANED = "as-heard", "cleaned"
VERSIONS = (CLEANED, AS_HEARD)


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


def file_name(campaign_name: str, session: TranscriptSession, version: str = AS_HEARD) -> str:
    """'rime-of-the-frostmaiden-session-7-cleaned.txt': safe on every system."""
    _check(version)
    slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.lower()).strip("-")[:FILE_NAME_MAX]
    return f"{slug or 'campaign'}-session-{session.number}-{version}.txt"


def _check(version: str) -> None:
    if version not in VERSIONS:
        raise ValueError(f"Unknown transcript version: {version!r}")


_ENGINE_WORDS = {
    "deepgram": "Deepgram",
    "cloud": "a speech-to-text company",
    "whisper-local": "Whisper, on DMbot's own computer",
}


def written_by(engines: Sequence[str]) -> str:
    """Which speech-to-text wrote it down, in plain words: "Deepgram (nova-3)". The
    endpoint's host stays in the database: the file is for everyone in the server."""
    words = []
    for source in engines:
        engine, _, rest = source.partition(" ")
        model = rest.split(" ")[0] if rest else ""
        name = _ENGINE_WORDS.get(engine, engine)
        words.append(f"{name} ({model})" if model else name)
    return ", then ".join(dict.fromkeys(words))


def label(when: str, speaker: str, character: str | None = None) -> str:
    """`[0:42:10] (Mia) {Cerric}`, or `[0:42:10] (Sam)` without a character."""
    speaker, character = _plain(speaker), _plain(character) if character else None
    return f"[{when}] ({speaker}) {{{character}}}" if character else f"[{when}] ({speaker})"


def _plain(name: str) -> str:
    """No brackets of any kind, so a name can't make a line look like someone else's."""
    return " ".join(name.translate(_BRACKETS).split()) or UNKNOWN_SPEAKER


def render(
    campaign_name: str,
    session: TranscriptSession,
    lines: Iterable[Line],
    names: Mapping[int, str],
    *,
    characters: Mapping[int, str] | None = None,
    running: bool = False,
    version: str = AS_HEARD,
) -> str:
    """The whole file. `names`: speaker ID → display name (missing ones show as
    'Someone'); `characters`: speaker ID → the character they play. `running`: DMbot
    is still recording this session. `version`: CLEANED or AS_HEARD."""
    _check(version)
    start_ms = session.started_at * 1000
    playing = characters or {}
    shown: dict[int, tuple[str, str | None]] = {}  # each name cleaned once
    body = []
    for line in lines:
        if line.user_id not in shown:
            character = playing.get(line.user_id)
            shown[line.user_id] = (
                clean_name(names.get(line.user_id)),
                clean_name(character) if character else None,
            )
        when = clock((line.started_ms - start_ms) / 1000)
        said = line.text if version == CLEANED else line.heard
        body.append(f"{label(when, *shown[line.user_id])}: {' '.join(said.split())}")
    started = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(session.started_at))
    ran = (
        f", ran {duration(session.ended_at - session.started_at)}"
        if session.ended_at is not None and not running
        else ""
    )
    header = [
        f"DMbot transcript: {clean_name(campaign_name)}, session {session.number}",
        f"Started {started}{ran}",
        *([f"Written down by {written_by(session.engines)}"] if session.engines else []),
        CLEANED_NOTE if version == CLEANED else AS_HEARD_NOTE,
        HOW_TO_READ,
    ]
    if running:
        header.append(
            "DMbot is still recording, so this file stops here"
            + (f", at {body[-1][1:].split(']', 1)[0]}." if body else ".")
        )
    return "\n".join([*header, "", *body]) + "\n"
