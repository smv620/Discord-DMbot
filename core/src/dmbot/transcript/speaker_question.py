""" "Who was speaking just now?" for the DM (docs/PLAN.md; #53). Pure: no Discord, no
database.

When the AI isn't sure who the DM was speaking as, the line gets no label and DMbot asks
in the DM screen only: "❓ **Who was speaking just now?** [Narrating] [Brother Hiro]
[Not sure]". The answer is saved on that line. The buttons offer only names a label may
take (`speaker_ai.speakers`), and a typed name is checked the same way, so a secret name
can never be written.

Not flooding the DM screen, like "Did they mean…?": at most one question is open at a
time, a line is asked about once, a question nobody answers expires after a few minutes,
and a pause follows every question. Questions live with the running session, in memory.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from enum import Enum

from dmbot.transcript.models import NARRATING

NARRATING_PICK, TYPE, SKIP = "narrating", "type", "skip"
QUESTION_TTL_S = 120.0  # an unanswered question expires after this long
COOLDOWN_S = 150.0  # after a question closes, however it closed, before the next
MAX_OPTIONS = 3  # NPC names on the buttons
LABEL_MAX = 25  # names on buttons: fits a phone
CONTEXT_MAX = 70  # the bit of the line shown with the question
TYPED_MAX = 60

EXPIRED = "This question has closed. That line stays unlabelled."
ONLY_DM = "Only the DM can answer this. Ask your DM."
BUSY = "Already saving an answer to this. One moment."
GONE = "This question is closed. That person stopped being recorded."
CANT_CHECK = "DMbot can't check the names right now. Try again in a moment."
NOT_ALLOWED = (
    "That name can't go in the transcript (it may be secret, or not in this campaign). "
    "Pick a button, or ignore this question."
)
FORM_TITLE = "Who was speaking?"
FORM_FIELD = "NPC name"
FORM_HINT = "As written in your campaign"
TYPE_LABEL = "Type it…"
NARRATING_LABEL = "Narrating"
SKIP_LABEL = "Not sure"
SKIPPED = "Okay. That line stays unlabelled."

STOPPED, ENDED, NOT_POSTED, EXPIRED_WHY, ANSWERED = (
    "stopped",
    "ended",
    "not-posted",
    "expired",
    "answered",
)


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class Asked:
    """The open question: the line (by speaker and start), a bit of it to show, and the
    NPC names offered (at most `MAX_OPTIONS`; narrating, typing and "not sure" always)."""

    id: str
    speaker: int
    started_ms: int
    context: str
    options: tuple[str, ...]
    asked_at: float


class Begin(Enum):
    OK = "ok"
    GONE = "gone"
    BUSY = "busy"


@dataclass(slots=True)
class SpeakerBook:
    """The session's question about who was speaking: one open at a time."""

    ttl_s: float = QUESTION_TTL_S
    cooldown_s: float = COOLDOWN_S
    open: Asked | None = None
    answering: bool = False
    closed_at: float | None = None
    asked_lines: set[tuple[int, int]] = field(default_factory=set)
    closed_why: dict[str, str] = field(default_factory=dict)

    def offer(
        self, speaker: int, started_ms: int, line: str, options: list[str], now: float
    ) -> Asked | None:
        """Open a question about this line, or None: another is open, the last closed too
        recently, or this line was asked about already."""
        if self.open is not None or (key := (speaker, started_ms)) in self.asked_lines:
            return None
        if self.closed_at is not None and now - self.closed_at < self.cooldown_s:
            return None
        self.asked_lines.add(key)
        self.open = Asked(
            id=secrets.token_hex(4),
            speaker=speaker,
            started_ms=started_ms,
            context=_short(line, CONTEXT_MAX),
            options=tuple(options[:MAX_OPTIONS]),
            asked_at=now,
        )
        return self.open

    def expire(self, now: float) -> Asked | None:
        """The open question, closed now if nobody answered in time (returned, so its
        message can say so). Never one whose answer is being saved."""
        if self.open is None or self.answering or now - self.open.asked_at < self.ttl_s:
            return None
        return self.close(EXPIRED_WHY, now)

    def begin(self, question_id: str) -> Begin:
        if self.open is None or self.open.id != question_id:
            return Begin.GONE
        if self.answering:
            return Begin.BUSY
        self.answering = True
        return Begin.OK

    def failed(self, question_id: str) -> None:
        """Saving failed: open again for another try."""
        if self.open is not None and self.open.id == question_id:
            self.answering = False

    def is_open(self, question_id: str) -> bool:
        return self.open is not None and self.open.id == question_id

    def close(self, why: str, now: float) -> Asked | None:
        asked, self.open, self.answering = self.open, None, False
        if asked is not None:
            self.closed_why[asked.id] = why
            if why != NOT_POSTED:
                self.closed_at = now
            else:
                self.asked_lines.discard((asked.speaker, asked.started_ms))
        return asked

    def why_closed(self, question_id: str) -> str | None:
        return self.closed_why.get(question_id)

    def drop_speaker(self, speaker: int, now: float) -> Asked | None:
        """They stopped being recorded: their open question goes, even mid-answer."""
        if self.open is not None and self.open.speaker == speaker:
            return self.close(STOPPED, now)
        return None


def pick_to_name(asked: Asked, pick: str) -> str | None:
    """The name a button stands for: NARRATING, an offered NPC (`pick` is its place), or
    None for anything else."""
    if pick == NARRATING_PICK:
        return NARRATING
    if pick.isdigit() and int(pick) < len(asked.options):
        return asked.options[int(pick)]
    return None


def option_label(name: str) -> str:
    return _short(name, LABEL_MAX)


def question_text(context: str) -> str:
    """The question; `context` must already be safe to show."""
    line = f' ("…{_short(context, CONTEXT_MAX)}…")' if context else ""
    return (
        f"❓ **Who was speaking just now?**{line}\n"
        "Not sure? Ignore this and that line stays unlabelled."
    )


def answered_text(label: str) -> str:
    """After an answer (`label` already escaped)."""
    return f"✅ Got it: that line is marked **{_short(label, TYPED_MAX)}**."


def not_answered_text() -> str:
    return "No answer. That line stays unlabelled."
