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
from collections.abc import Collection, Iterable, Mapping, Sequence

from dmbot.transcript.models import (
    QUESTION_KINDS,
    SIDEBAR_TABLE_QUESTION,
    Line,
    TranscriptSession,
)
from dmbot.transcript.topics import GAME, OFF_TOPIC, TABLE_TALK, Spoken, collapse

UNKNOWN_SPEAKER = "Someone"
_BRACKETS = str.maketrans("", "", "()[]{}")
NAME_MAX = 80
FILE_NAME_MAX = 60

AS_HEARD_NOTE = (
    "As heard: every word exactly as DMbot heard it, off-topic chat included, before it "
    'fixed any names, so some names may be misheard. The "Cleaned" version (/transcript) '
    "has the names fixed and leaves out clearly off-topic chat. Only people who agreed "
    "were recorded."
)
CLEANED_NOTE = (
    "Cleaned: DMbot fixed the spelling of names it was sure about; a few may still be "
    "wrong. Chat clearly not about the game is left out, and a short note says how long "
    'it was spoken. The "As heard" version (/transcript) still has every word. Only people '
    "who agreed were recorded."
)
SIDEBAR_NOTE = (
    "[DM Sidebar] lines are the DM's questions to DMbot and its answers, with where each "
    "came from. They are left out of the Cleaned version."
)
DMBOT_NAME = "DMbot"
TABLE_Q_NAME = "Table Q"  # a player's question to DMbot (#1187), apart from the DM's sidebar
TYPED_TAG = "(typed)"
TYPED_NOTE = (
    "(typed) lines were typed in the table's chat, not said. In the Cleaned version, DMbot leaves "
    "out chat that is clearly not about the game."
)
HOW_TO_READ = (
    "Each line: [time since start] (person) {their character}: what they said. A person marked "
    "DM is the Dungeon Master. {narrating} means the DM is describing the scene; an NPC's name "
    "in braces means the DM is speaking as that character. {table_talk} is out-of-character talk "
    'about the rules or running the game, like "roll initiative" or "what\'s my AC?"; '
    "{non-game_content} is chat that isn't about the game (these two are not characters). "
    "The cleaned file replaces off-topic chat with a note of how long it lasted; the "
    "as-heard file keeps every word."
)
# What a line is when it is not the person's character speaking (#53).
TOPIC_TAGS = {TABLE_TALK: "table_talk", OFF_TOPIC: "non-game_content"}

AS_HEARD, CLEANED = "as-heard", "cleaned"
VERSIONS = (CLEANED, AS_HEARD)


def clean_name(raw: str | None) -> str:
    """A name fit for a file: no invisible characters that flip how a line reads, no
    line breaks, never empty."""
    name = " ".join((raw or "").split())  # line breaks and tabs become spaces
    name = "".join(c for c in name if unicodedata.category(c) not in ("Cf", "Cc"))
    return name.strip()[:NAME_MAX] or UNKNOWN_SPEAKER


class Label(str):
    """A speaker's label made by `distinct_names`: already safe, so its "(2)" is kept as it is."""


def _is_comma(char: str) -> bool:
    # every comma there is (full-width, small, Arabic…) and the look-alikes that read as one: the
    # low quotation marks U+201A and U+201E, the ideographic comma U+3001, the low accent U+02CF,
    # the Greek numeral sign U+0375 and the cedilla (U+00B8, which folds to a combining U+0327)
    return (
        "COMMA" in unicodedata.name(char, "")
        or char in "\u201a\u201e\u3001\u02cf\u0375\u00b8\u0327"
    )


def _is_bracket(char: str) -> bool:
    # every opening and closing bracket of any script (Unicode Ps and Pe), not only ()[]{}: the
    # medium and flattened ornament parentheses and the angle brackets read as the "(2)" that tells
    # two people with one name apart
    return unicodedata.category(char) in ("Ps", "Pe")


def person_name(raw: str | None) -> str:
    """A speaker's name as a file shows it, made safe to stand in a "(Name, DM)" label (#2337): no
    brackets (so it cannot look like another line's label or the "(2)" that tells two people with
    one name apart) and no comma of any look (so only the DM path ever writes ", DM"). Compatible
    forms are folded first, so a full-width look-alike goes the same way."""
    name = unicodedata.normalize("NFKC", clean_name(raw))
    name = "".join(" " if _is_comma(c) else c for c in name if not _is_bracket(c))
    return " ".join(name.split()) or UNKNOWN_SPEAKER


def distinct_names(names: Mapping[int, str | None]) -> dict[int, Label]:
    """Labels for these people, in the order given: a name safe for a label (`person_name`), and
    for a second person with the same name (any case) " (2)", a third " (3)" (#2337). The
    brackets are taken out of every name first, so no chosen name can be, or collide with, a
    suffix made here."""
    seen: dict[str, int] = {}
    out: dict[int, Label] = {}
    for user_id, raw in names.items():
        name = person_name(raw)
        key = name.casefold()
        seen[key] = seen.get(key, 0) + 1
        out[user_id] = Label(name if seen[key] == 1 else f"{name} ({seen[key]})")
    return out


def _hidden_typing(line: Line) -> bool:
    """Typed chat clearly not about the game: left out of the Cleaned version, as talk like it is
    (#2447, item 6)."""
    return line.typed and line.topic == OFF_TOPIC


def speaker_order(lines: Iterable[Line], version: str = AS_HEARD) -> list[int]:
    """The people a file of this version names, in the order each first speaks in it. DMbot's own
    answers are not a person; the cleaned version leaves out typed chat and sidebar lines, so
    someone who only typed or asked DMbot is not listed there (a gap in the numbers or a
    "(2)" would hint at a hidden person). One order for the website and the Discord download, so
    a person has the same label in both."""
    order: list[int] = []
    for line in sorted(lines, key=lambda x: x.started_ms):
        if line.sidebar and line.sidebar not in QUESTION_KINDS:
            continue
        if version == CLEANED and (line.sidebar or _hidden_typing(line)):
            continue
        if line.user_id not in order:
            order.append(line.user_id)
    return order


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


# Whether the voices left DMbot's computer matters most to players: every line says so.
_ENGINE_WORDS = {
    "deepgram": "Deepgram, an online service",
    "cloud": "an online service",
    "whisper-local": "Whisper, on DMbot's own computer",
}


def written_by(engines: Sequence[str]) -> str:
    """Which speech-to-text wrote it down, in plain words: "Deepgram, an online service
    (model nova-3)". The endpoint's host stays in the database: the file is for
    everyone in the server. The store keeps each engine once, in the order first used
    (it skips one the session already has), so a switch back shows only once."""
    words: list[str] = []
    for source in engines:
        engine, _, rest = source.partition(" ")
        model = rest.split(" ")[0] if rest else ""
        name = _ENGINE_WORDS.get(engine, engine)
        word = f"{name} (model {model})" if model else name
        if not words or words[-1] != word:
            words.append(word)
    return "; then ".join(words)


def _value(text: str) -> str:
    """One word for a tag: no spaces, brackets or equals signs to confuse a reader."""
    return "-".join(text.translate(_BRACKETS).replace("=", "-").split()) or "unknown"


def sidebar_tag(line: Line) -> str:
    """`[DM Sidebar id=a1b2c3 via=voice-memo stt=deepgram/nova-3]` on a question, and
    `[DM Sidebar reply-to=a1b2c3 model=… prompt=… sources=…]` on DMbot's reply: where
    each line came from (owner, #933). Only what is known is shown."""
    info = line.lineage
    parts: list[tuple[str, str]]
    if line.sidebar in QUESTION_KINDS:
        engine, _, rest = info.stt.partition(" ")  # the company's host stays private
        stt = f"{engine}/{rest.split(' ')[0]}" if rest else engine
        parts = [("id", info.ref), ("via", info.via), ("stt", stt)]
    else:
        parts = [
            ("reply-to", info.reply_to),
            ("model", info.model),
            ("prompt", info.prompt),
            ("sources", ";".join(_value(x) for x in info.sources)),
        ]
    shown = " ".join(
        f"{key}={_value(value) if key != 'sources' else value}" for key, value in parts if value
    )
    name = TABLE_Q_NAME if line.sidebar == SIDEBAR_TABLE_QUESTION else "DM Sidebar"
    return f"[{name} {shown}]" if shown else f"[{name}]"


def label(
    when: str,
    speaker: str,
    character: str | None = None,
    topic: str = GAME,
    entity: str = "",
    *,
    safe: bool = False,
) -> str:
    """`[0:42:10] (Mia) {Cerric}`, or `[0:42:10] (Sam)` without a character. Table talk and
    off-topic talk say so instead of the character: `[0:42:10] (Mia) {table_talk}`. A line
    said as someone else (the DM narrating, or voicing an NPC, #53) shows that instead of
    the speaker's own character: `[0:42:10] (Sam) {narrating}`, `(Sam) {Brother Hiro}`. `safe`: the
    speaker is a label `render` made (it may hold the "(2)" and ", DM" it wrote)."""
    speaker = speaker if safe else _plain(speaker)
    character = _plain(character) if character else None
    if entity:
        character = _plain(entity)
    character = TOPIC_TAGS.get(topic, character)
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
    dms: Collection[int] = (),
    running: bool = False,
    version: str = AS_HEARD,
) -> str:
    """The whole file. `names`: speaker ID → display name (missing ones show as
    'Someone'); `characters`: speaker ID → the character they play; `dms`: the speakers who are
    DMs of the campaign, shown as "(Name, DM)" (#2337). `running`: DMbot
    is still recording this session. `version`: CLEANED or AS_HEARD. The DM sidebar lines
    (#935) are in the as-heard version, for everyone who may read it, and never in the
    cleaned one (owner, #933)."""
    _check(version)
    start_ms = session.started_at * 1000
    playing = characters or {}

    def person(user_id: int) -> str:
        raw = names.get(user_id)
        # A label from `distinct_names` is safe as it is; any other name is made safe here, with
        # no comma left in it: only a DM is marked, and nobody can pass as one by their name.
        name = str(raw) if isinstance(raw, Label) else person_name(raw)
        return f"{name}, DM" if user_id in dms else name

    shown: dict[int, tuple[str, str | None]] = {}  # each name cleaned once
    timed: list[tuple[int, str]] = []  # (when it started, the line)
    # The cleaned version hides clearly off-topic talk (#52): each run of it becomes one
    # marker with how long it lasted. The as-heard version keeps everything.
    everything = list(lines)
    # Typed chat (#1118) is only in the as-heard version, tagged "(typed)"; the cleaned one is
    # what was said.
    typed_lines = [line for line in everything if line.typed and not line.sidebar]
    table_lines = [line for line in everything if not line.sidebar and not line.typed]
    shown_typed = (
        typed_lines if version == AS_HEARD else [x for x in typed_lines if not _hidden_typing(x)]
    )
    shown_sidebar = [line for line in everything if line.sidebar] if version == AS_HEARD else []
    spoken = [
        Spoken(
            line.user_id,
            line.started_ms,
            line.duration_ms / 1000,
            line.text if version == CLEANED else line.heard,
            line.topic if version == CLEANED else GAME,
            line.topic,
            line.entity,
        )
        for line in table_lines
    ]
    for item in collapse(spoken):
        if item.speaker not in shown:
            character = playing.get(item.speaker)
            shown[item.speaker] = (
                person(item.speaker),
                clean_name(character) if character else None,
            )
        when = clock((item.started_ms - start_ms) / 1000)
        who = (
            label(when, shown[item.speaker][0], safe=True)
            if item.skipped
            else label(when, *shown[item.speaker], item.said_as, item.entity, safe=True)
        )  # a skipped note says what it is, with no character
        if item.skipped:  # "[0:12:04] (Mia) [1m 22s of off-topic chat skipped]"
            timed.append((item.started_ms, f"{who} {item.text}"))
        else:
            timed.append((item.started_ms, f"{who}: {' '.join(item.text.split())}"))
    for line in shown_sidebar:  # "[0:12:04] (Sam) [DM Sidebar]: …", "[0:12:09] (DMbot) …"
        asker = DMBOT_NAME if line.sidebar not in QUESTION_KINDS else person(line.user_id)
        when = clock((line.started_ms - start_ms) / 1000)
        said = " ".join(line.heard.split())
        tag = sidebar_tag(line)
        timed.append((line.started_ms, f"[{when}] ({asker}) {tag}: {said}"))
    for line in shown_typed:  # "[0:12:06] (Mia) {Cerric} (typed): we take a long rest"
        when = clock((line.started_ms - start_ms) / 1000)
        said = " ".join((line.text if version == CLEANED else line.heard).split())
        who = label(
            when,
            person(line.user_id),
            clean_name(playing[line.user_id]) if line.user_id in playing else None,
            safe=True,
        )
        timed.append((line.started_ms, f"{who} {TYPED_TAG}: {said}"))
    timed.sort(key=lambda t: t[0])  # stable: a sidebar line follows table speech at the same ms
    body = [text for _, text in timed]
    started = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(session.started_at))
    ran = (
        f", ran {duration(session.ended_at - session.started_at)}"
        if session.ended_at is not None and not running
        else ""
    )
    header = [
        f"DMbot transcript: {clean_name(campaign_name)}, session {session.number}",
        f"Started {started}{ran}",
        *([f"Speech to text: {written_by(session.engines)}"] if session.engines else []),
        CLEANED_NOTE if version == CLEANED else AS_HEARD_NOTE,
        HOW_TO_READ,
        *([SIDEBAR_NOTE] if shown_sidebar else []),
        *([TYPED_NOTE] if typed_lines else []),  # (also when the Cleaned one hides them all)
    ]
    if running:
        header.append(
            "DMbot is still recording, so this file stops here"
            + (f", at {body[-1][1:].split(']', 1)[0]}." if body else ".")
        )
    return "\n".join([*header, "", *body]) + "\n"
