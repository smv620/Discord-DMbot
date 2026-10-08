""" "Did they mean…?" for the DM (docs/PLAN.md, "Transcript Cleaner"; #296). Pure: no
Discord, no database.

When a word sounds like two or three confirmed names, the Transcript Cleaner leaves it
as heard and DMbot asks the DM which one it was, in the DM screen only:
"❓ **DMbot heard Mia say "Beleros".** Did they mean… [Belleros] [Bellaros]
[Keep "Beleros"]". The answer is saved for the campaign (a fixed spelling, or "keep as
heard"), so the same words are handled silently from then on, and it can be undone.
The DM may also type the name (**Type it…**); the line that was asked about is fixed
too, and Undo puts it back (#503).

Not flooding the DM screen: at most one question is open at a time, each word is asked
about at most once per session, and a question nobody answers expires after a few
minutes, so the next one can be asked. Questions live with the running session, in
memory; when it ends they expire and the line stays as heard.
"""

from __future__ import annotations

import secrets
from collections.abc import Collection
from dataclasses import dataclass, field
from enum import Enum

from dmbot.memory.models import name_key
from dmbot.transcript.cleaner import Fix, Question

KEEP = "keep"  # the button choice that keeps the words as heard
TYPE = "type"  # the button that opens a form to type the name
QUESTION_TTL_S = 300.0  # an unanswered question expires after this long
COOLDOWN_S = 150.0  # after a question closes, however it closed, before the next
LABEL_MAX = 25  # names on buttons: fits a phone (the full name is in the answer)
NAME_MAX = 60  # a name or heard words in a message, shortened past this
CONTEXT_MAX = 70  # the bit of the line shown with a question
EXPIRED = "⌛ This question is closed, so the words stay as heard."
ONLY_DM = "Only the DM can answer this."
BUSY = "Already saving an answer to this. One moment."
NAME_GONE = "That name isn't in the campaign anymore, so the words stay as heard."
# The speaker stopped being recorded before the DM answered: their words aren't repeated.
GONE = "This question is closed: that person stopped being recorded."
UNDONE = "↩️ Undone. Those words stay as heard again. DMbot may ask about them next session."
UNDO_FAILED = "Couldn't undo: that was changed again since."
UNDO_ONLY_DM = "Only the campaign's DM can undo this."
TYPE_LABEL = "Type it…"
TYPED_MAX = 60  # a typed name, at most
FORM_TITLE = "Type the name"
FORM_FIELD = "The name, as it should be written"
FORM_HINT = "For example: Hrothgar"
TYPED_SECRET = (
    "That's a secret name, so DMbot won't write it in the transcript (everyone in the "
    "server can read it). Pick another answer, or ignore the question."
)
TYPED_TWO = (
    "Two names are written like that. Pick one of the buttons, or fix the names first: "
    "`/dmbot names`."
)

# Why the open question closed (an answer being saved needs to know).
ANSWERED, EXPIRED_WHY, STOPPED, ENDED, NOT_POSTED = (
    "answered",
    "expired",
    "stopped",
    "ended",
    "not posted",
)


@dataclass(frozen=True, slots=True)
class Asked:
    """A question posted (or about to be) to the DM screen."""

    id: str  # short, for the buttons
    speaker: int
    heard: str
    options: tuple[tuple[str, str], ...]  # (entity ID, name)
    asked_at: float  # monotonic seconds
    context: str = ""  # the bit of the line around the words
    # The line it was asked about, to fix it once answered (#503): when it started, all
    # of it as heard, the fixes made in it, and where the words are (as heard).
    started_ms: int = 0
    line: str = ""
    fixes: tuple[Fix, ...] = ()
    start: int = 0
    end: int = 0


class Begin(Enum):
    OK = "ok"  # this press saves the answer
    BUSY = "busy"  # another press is saving it now
    GONE = "gone"  # no such open question (answered, expired, or another session's)


@dataclass(slots=True)
class QuestionBook:
    """This session's questions: which words were asked about, and the open one. While
    an answer is being saved the question stays open ("answering"), so it can still be
    taken down if its speaker stops being recorded, and is reopened if saving fails.

    Not flooding the DM screen: one open at a time; a cooldown after any question
    closes; and only words heard a second time this session, or that may be a name in
    the scene (`matters`)."""

    ttl_s: float = QUESTION_TTL_S
    cooldown_s: float = COOLDOWN_S
    asked_keys: set[str] = field(default_factory=set)
    seen: dict[str, int] = field(default_factory=dict)  # words that raised a question
    open: Asked | None = None
    answering: bool = False
    closed_at: float | None = None
    closed_why: dict[str, str] = field(default_factory=dict)  # question ID → why

    def offer(
        self,
        speaker: int,
        questions: tuple[Question, ...],
        now: float,
        matters: Collection[str] = (),
        *,
        started_ms: int = 0,
        line: str = "",
        fixes: tuple[Fix, ...] = (),
    ) -> Asked | None:
        """The question to post now, if any. Every question counts towards "heard a
        second time" even when none is asked. Call `expire` first. `started_ms`, `line`
        and `fixes`: the line the questions are about, so the answer can fix it."""
        keys = []
        for question in questions:
            key = name_key(question.heard)
            self.seen[key] = self.seen.get(key, 0) + 1
            keys.append(key)
        if self.open is not None:
            return None
        if self.closed_at is not None and now - self.closed_at < self.cooldown_s:
            return None
        for question, key in zip(questions, keys, strict=True):
            if key in self.asked_keys:
                continue
            if self.seen[key] < 2 and not any(e in matters for e, _ in question.options):
                continue  # once, and nothing in the scene: not worth the DM's attention
            self.asked_keys.add(key)
            self.open = Asked(
                secrets.token_hex(4),
                speaker,
                question.heard,
                question.options,
                now,
                question.context,
                started_ms,
                line,
                fixes,
                question.start,
                question.end,
            )
            return self.open
        return None

    def expire(self, now: float) -> Asked | None:
        """The open question, closed now if nobody answered it in time (returned, so its
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
        """Saving failed: the question is open again for another try."""
        if self.open is not None and self.open.id == question_id:
            self.answering = False

    def is_open(self, question_id: str) -> bool:
        return self.open is not None and self.open.id == question_id

    def close(self, why: str, now: float) -> Asked | None:
        """Close the open question. A question never posted starts no cooldown."""
        asked, self.open, self.answering = self.open, None, False
        if asked is not None:
            self.closed_why[asked.id] = why
            if why != NOT_POSTED:
                self.closed_at = now
            else:
                self.asked_keys.discard(name_key(asked.heard))  # may be asked again
        return asked

    def why_closed(self, question_id: str) -> str | None:
        return self.closed_why.get(question_id)

    def drop_speaker(self, speaker: int, now: float) -> Asked | None:
        """They stopped being recorded: their open question goes, even mid-answer
        (returned, so its message can be taken down)."""
        if self.open is not None and self.open.speaker == speaker:
            return self.close(STOPPED, now)
        return None


def _short(text: str, limit: int = NAME_MAX) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def question_text(speaker: str, heard: str, context: str = "") -> str:
    """The question; `speaker`, `heard` and `context` must already be safe to show."""
    line = f' ("…{_short(context, CONTEXT_MAX)}…")' if context else ""
    return (
        f'❓ **DMbot heard {speaker} say "{_short(heard)}"**{line}. Did they mean…\n'
        "Not sure? Ignore this and it stays as heard."
    )


def option_label(name: str) -> str:
    return _short(name, LABEL_MAX)


def keep_label(heard: str) -> str:
    return f'Keep "{_short(heard, LABEL_MAX - 7)}"'


def fixed_text(heard: str, name: str, *, line_fixed: bool = False) -> str:
    """After the DM picked or typed a name (both already escaped). `line_fixed`: the line
    that was asked about was fixed too."""
    if line_fixed:
        return (
            f'✅ Got it: "{_short(heard)}" is now written **{_short(name)}**, in that line '
            "and from now on in this campaign. Older lines stay as heard."
        )
    return (
        f'✅ Got it: from now on, "{_short(heard)}" is written **{_short(name)}** in this '
        "campaign. Earlier lines stay as heard."
    )


def typed_problem(why: str) -> str:
    """A typed name that can't be saved (`why` from the names list's rules)."""
    return f"That can't be saved as a name: {why}. Try again."


def kept_text(heard: str) -> str:
    return f'✅ Got it: DMbot won\'t change "{_short(heard)}" in this campaign.'


def not_answered_text(heard: str) -> str:
    """An unanswered question, shrunk to one line (when it expired, or the session
    ended)."""
    return f'⌛ Not answered: "{_short(heard)}" stays as heard.'


def undone_text(heard: str | None) -> str:
    if not heard:
        return UNDONE
    return f'↩️ Undone. "{_short(heard)}" stays as heard again. DMbot may ask about it next session.'
