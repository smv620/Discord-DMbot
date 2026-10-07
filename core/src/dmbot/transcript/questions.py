""" "Did they mean…?" for the DM (docs/PLAN.md, "Transcript Cleaner"; #296). Pure: no
Discord, no database.

When a word sounds like two or three confirmed names, the Transcript Cleaner leaves it
as heard and DMbot asks the DM which one it was, in the DM screen only:
"❓ **DMbot heard Mia say "Beleros".** Did they mean… [Belleros] [Bellaros]
[Keep "Beleros"]". The answer is saved for the campaign (a fixed spelling, or "keep as
heard"), so the same words are handled silently from then on, and it can be undone.

Not flooding the DM screen: at most one question is open at a time, each word is asked
about at most once per session, and a question nobody answers expires after a few
minutes, so the next one can be asked. Questions live with the running session, in
memory; when it ends they expire and the line stays as heard.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from enum import Enum

from dmbot.memory.models import name_key
from dmbot.transcript.cleaner import Question

KEEP = "keep"  # the button choice that keeps the words as heard
QUESTION_TTL_S = 300.0  # an unanswered question expires after this long
LABEL_MAX = 25  # names on buttons: fits a phone (the full name is in the answer)
NAME_MAX = 60  # a name or heard words in a message, shortened past this
EXPIRED = "That question is closed, so the words stay as heard."
NOT_ANSWERED = "Not answered, so the words stay as heard."
ONLY_DM = "Only the DM can answer this."
BUSY = "Already saving an answer to this. One moment."
NAME_GONE = "That name isn't in the campaign anymore, so the words stay as heard."
# The speaker stopped being recorded before the DM answered: their words aren't repeated.
GONE = "This question is closed: that person stopped being recorded."
UNDONE = "↩️ Undone: that answer no longer counts. DMbot may ask about those words again."
UNDO_FAILED = "Couldn't undo: that was changed again since."


@dataclass(frozen=True, slots=True)
class Asked:
    """A question posted (or about to be) to the DM screen."""

    id: str  # short, for the buttons
    speaker: int
    heard: str
    options: tuple[tuple[str, str], ...]  # (entity ID, name)
    asked_at: float  # monotonic seconds


class Begin(Enum):
    OK = "ok"  # this press saves the answer
    BUSY = "busy"  # another press is saving it now
    GONE = "gone"  # no such open question (answered, expired, or another session's)


@dataclass(slots=True)
class QuestionBook:
    """This session's questions: which words were asked about, and the open one. While
    an answer is being saved the question stays open ("answering"), so it can still be
    taken down if its speaker stops being recorded, and is reopened if saving fails."""

    ttl_s: float = QUESTION_TTL_S
    asked_keys: set[str] = field(default_factory=set)
    open: Asked | None = None
    answering: bool = False

    def offer(self, speaker: int, questions: tuple[Question, ...], now: float) -> Asked | None:
        """The question to post now, if any: none while one is open, and never the same
        words twice in a session. Call `expire` first."""
        if self.open is not None:
            return None
        for question in questions:
            key = name_key(question.heard)
            if key in self.asked_keys:
                continue
            self.asked_keys.add(key)
            self.open = Asked(secrets.token_hex(4), speaker, question.heard, question.options, now)
            return self.open
        return None

    def expire(self, now: float) -> Asked | None:
        """The open question, closed now if nobody answered it in time (returned, so its
        message can say so). Never one whose answer is being saved."""
        if self.open is None or self.answering or now - self.open.asked_at < self.ttl_s:
            return None
        return self.close()

    def begin(self, question_id: str) -> Begin:
        if self.open is None or self.open.id != question_id:
            return Begin.GONE
        if self.answering:
            return Begin.BUSY
        self.answering = True
        return Begin.OK

    def failed(self, question_id: str) -> None:
        """Saving failed: the question is open again for another try."""
        if self.open is not None and self.open.id == question_id:
            self.answering = False

    def is_open(self, question_id: str) -> bool:
        return self.open is not None and self.open.id == question_id

    def close(self) -> Asked | None:
        asked, self.open, self.answering = self.open, None, False
        return asked

    def drop_speaker(self, speaker: int) -> Asked | None:
        """They stopped being recorded: their open question goes, even mid-answer
        (returned, so its message can be taken down)."""
        if self.open is not None and self.open.speaker == speaker:
            return self.close()
        return None


def _short(text: str, limit: int = NAME_MAX) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def question_text(speaker: str, heard: str) -> str:
    """The question; `speaker` and `heard` must already be safe to show (escaped)."""
    return (
        f'❓ **DMbot heard {speaker} say "{_short(heard)}".** Did they mean…\n'
        "Not sure? Ignore this and it stays as heard."
    )


def option_label(name: str) -> str:
    return _short(name, LABEL_MAX)


def keep_label(heard: str) -> str:
    return f'Keep "{_short(heard, LABEL_MAX - 7)}"'


def fixed_text(heard: str, name: str) -> str:
    """After the DM picked a name (both already escaped)."""
    return (
        f'✅ Got it: from now on, "{_short(heard)}" is written **{_short(name)}** in this campaign.'
    )


def kept_text(heard: str) -> str:
    return f'✅ Got it: DMbot won\'t change "{_short(heard)}" in this campaign.'
