"""Stored transcripts' data, and the lines waiting to be saved (#41, #125). Pure: no
Discord, no database."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

ENTITY_MAX = 120  # the database refuses a longer entity, which would stick a whole batch
MAX_UNSAVED = 5_000  # lines kept while saving keeps failing; the oldest go first


@dataclass(frozen=True, slots=True)
class TranscriptSession:
    id: str
    guild_id: int
    campaign_id: str
    started_at: int  # Unix seconds
    ended_at: int | None  # None while it's running (or if DMbot stopped unexpectedly)
    lines: int = 0
    speakers: tuple[int, ...] = ()
    number: int = 0  # Session 1, 2, … in its campaign (only sessions with lines count)
    engines: tuple[str, ...] = ()  # "engine model host" of each speech-to-text used
    deleted_at: int | None = None  # the retention job deleted its transcript (#1533)
    # Each speaker's Discord display name when DMbot kept it (#2337: at the session, or for an older
    # session when it was filled in); "" when they were not in the server. Only people with lines.
    speaker_names: Mapping[int, str] = field(default_factory=dict)


def line_ref(session_id: str, started_ms: int, user_id: int) -> str:
    """How story memory points at one stored line (`memory_mentions.line_ref`, #1532): the
    session, when the speech began and who said it, which is what names a line (and what
    "Delete my past transcripts" matches to remove the mentions of a deleted line). Anything
    that writes a mention of a transcript line uses this form."""
    return f"{session_id}:{int(started_ms)}:{int(user_id)}"


NARRATING = "narrating"  # a line's `entity` when the DM was narrating (#53)
SIDEBAR_QUESTION, SIDEBAR_ANSWER = "question", "answer"
# A player's question to DMbot (#1187), apart from the DM's: it has its own tag in the raw
# transcript. DMbot's answer to either is a SIDEBAR_ANSWER line.
SIDEBAR_TABLE_QUESTION = "table_question"
QUESTION_KINDS = (SIDEBAR_QUESTION, SIDEBAR_TABLE_QUESTION)
VIA_VOICE, VIA_TYPED, VIA_TABLE = "voice-memo", "typed", "table-trigger"
# Typed in the table's chat (#1118): not speech, so no speech-to-text in its lineage.
VIA_TABLE_CHAT = "table-chat"
# Typed in the campaign's DMbot chat channel (#1187).
VIA_CHAT = "chat"
VIAS = (VIA_VOICE, VIA_TYPED, VIA_TABLE, VIA_TABLE_CHAT, VIA_CHAT)


@dataclass(frozen=True, slots=True)
class Lineage:
    """Where a DM sidebar line came from (owner decision on #933, 2026-10-09): stored as
    columns, shown in the raw transcript's tag.

    A question has `ref` (its short id), `via` and, for speech, `stt` ("engine model host").
    DMbot's reply has `reply_to` (the question's `ref`), the AI `model`, the `prompt`
    version and the `sources` it used (rules entries, house rule numbers, memory facts,
    transcript span).
    """

    ref: str = ""
    reply_to: str = ""
    via: str = ""
    stt: str = ""
    model: str = ""
    prompt: str = ""
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.via and self.via not in VIAS:
            raise ValueError(f"unknown way in {self.via!r}")


NO_LINEAGE = Lineage()


@dataclass(frozen=True, slots=True)
class Line:
    started_ms: int  # Unix milliseconds, when the speech started
    user_id: int
    heard: str  # exactly what speech-to-text wrote
    text: str  # cleaned: misheard names fixed (dmbot.transcript.cleaner)
    duration_ms: int = 0  # how long the speech lasted (for "[1m 22s … skipped]")
    # game, table_talk or off_topic (#52; dmbot.transcript.topics): only off_topic is
    # hidden, in the cleaned transcript. Until the filter says, a line is game talk.
    topic: str = "game"
    # "" for table speech. A DM sidebar line (docs/PLAN.md, "DM sidebar"; #935): the DM's
    # question, or DMbot's answer (saved under the DM's ID, so a consent stop removes both,
    # and shown as DMbot). Only in the as-heard download, never the cleaned one.
    sidebar: str = ""
    lineage: Lineage = NO_LINEAGE
    # Who the line was said as (#53): "" for nobody named (the player's character shows),
    # NARRATING, or the confirmed, non-secret name of an NPC the DM was voicing. Never a
    # secret name: whatever sets it checks (the transcript is open to the whole server).
    entity: str = ""
    # Typed in the table's chat rather than said (#1118): tagged "(typed)", in both versions; the
    # cleaned one leaves out typing the off-topic filter labelled off_topic (#2447).
    typed: bool = False
    # The Discord message a typed line came from, so deleting it takes the line back while it
    # is still unsaved (#1118). 0 for speech. Never stored.
    message_id: int = 0

    def __post_init__(self) -> None:
        if self.sidebar not in ("", SIDEBAR_ANSWER, *QUESTION_KINDS):  # not at the database
            raise ValueError(f"unknown sidebar kind {self.sidebar!r}")
        tidy = " ".join(self.entity.split())[:ENTITY_MAX].strip()
        if tidy != self.entity:  # one line, never blank, never too long for the database
            object.__setattr__(self, "entity", tidy)

    def is_at(self, user_id: int, started_ms: int) -> bool:
        """The speech this person began at this moment (never a sidebar line)."""
        return (self.user_id, self.started_ms) == (user_id, started_ms) and not self.sidebar


@dataclass(slots=True)
class TranscriptBuffer:
    """Lines written down but not saved yet, for one session. Pure: no database.

    Consent is checked again as each batch is taken (`take`): someone who presses Stop
    never has another line saved, even one already waiting.
    """

    _waiting: list[Line] = field(default_factory=list)
    dropped: int = 0  # thrown away because saving kept failing

    def __len__(self) -> int:
        return len(self._waiting)

    def add(self, line: Line) -> None:
        if not line.heard.strip():
            return
        self._waiting.append(line)
        self._trim()

    def drop_message(self, message_id: int) -> None:
        """A typed message was deleted before its line was saved: the line goes too."""
        self._waiting = [
            w for w in self._waiting if not (w.message_id and w.message_id == message_id)
        ]

    def drop_speaker(self, user_id: int) -> None:
        self._waiting = [w for w in self._waiting if w.user_id != user_id]

    def take(self, allowed: Callable[[int], bool]) -> list[Line]:
        """Everything waiting from people still allowed; nothing is left behind."""
        batch = [w for w in self._waiting if allowed(w.user_id)]
        self._waiting = []
        return batch

    def lines(self) -> list[Line]:
        """The lines waiting now (a copy), for a re-check of the session after an answer."""
        return list(self._waiting)

    def relabel(self, user_id: int, started_ms: int, text: str) -> bool:
        """A waiting line's cleaned words changed (an Undo or an answer, #296, #503);
        True if it was here."""
        for i, waiting in enumerate(self._waiting):
            if (waiting.user_id, waiting.started_ms) == (
                user_id,
                started_ms,
            ) and not waiting.sidebar:
                self._waiting[i] = replace(waiting, text=text)
                return True
        return False

    def set_topic(self, user_id: int, started_ms: int, topic: str, *, typed: bool = False) -> bool:
        """A waiting line's topic, from the off-topic filter (#52); True if it was here. `typed`:
        a typed line and a spoken one of the same person can share a millisecond (#2447)."""
        for i, waiting in enumerate(self._waiting):
            if (
                (waiting.user_id, waiting.started_ms) == (user_id, started_ms)
                and not waiting.sidebar
                and waiting.typed == typed
            ):
                self._waiting[i] = replace(waiting, topic=topic)
                return True
        return False

    def set_entity(self, user_id: int, started_ms: int, entity: str) -> bool:
        """A waiting line's entity (#53); True if it was here."""
        for i, waiting in enumerate(self._waiting):
            if (waiting.user_id, waiting.started_ms) == (
                user_id,
                started_ms,
            ) and not waiting.sidebar:
                self._waiting[i] = replace(waiting, entity=entity)
                return True
        return False

    def put_back(self, batch: list[Line]) -> None:
        """Saving failed: keep the batch, ahead of anything newer."""
        self._waiting = batch + self._waiting
        self._trim()

    def _trim(self) -> None:
        extra = len(self._waiting) - MAX_UNSAVED
        if extra > 0:
            self._waiting = self._waiting[extra:]
            self.dropped += extra
