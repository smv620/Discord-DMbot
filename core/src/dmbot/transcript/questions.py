""" "Did they mean…?" for the DM (docs/PLAN.md, "Transcript Cleaner"; #296). Pure: no
Discord, no database.

When a word sounds like two or three confirmed names, the Transcript Cleaner leaves it
as heard and DMbot asks the DM which one it was, in the DM screen only:
"❓ **Mia said "Beleros"**: did they mean… [Belleros] [Bellaros] [Keep as heard]".
The answer is saved for the campaign (a fixed spelling, or "keep as heard"), so the
same words are handled silently from then on.

Not flooding the DM screen: at most one question is open at a time, and each word is
asked about at most once per session. Questions live with the running session, in
memory; when it ends they expire and the line stays as heard.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from dmbot.memory.models import name_key
from dmbot.transcript.cleaner import Question

KEEP_LABEL = "Keep as heard"
NAME_MAX = 60  # a name or heard words in the message, shortened past this
EXPIRED = "That question has expired, so the words stay as heard."
ONLY_DM = "Only the DM can answer this."


@dataclass(frozen=True, slots=True)
class Asked:
    """A question posted (or about to be) to the DM screen."""

    id: str  # short, for the buttons
    speaker: int
    heard: str
    options: tuple[tuple[str, str], ...]  # (entity ID, name)


@dataclass(slots=True)
class QuestionBook:
    """This session's questions: which words were asked about, and the open one."""

    asked_keys: set[str] = field(default_factory=set)
    open: Asked | None = None

    def offer(self, speaker: int, questions: tuple[Question, ...]) -> Asked | None:
        """The question to post now, if any: none while one is open, and never the same
        words twice in a session."""
        if self.open is not None:
            return None
        for question in questions:
            key = name_key(question.heard)
            if key in self.asked_keys:
                continue
            self.asked_keys.add(key)
            self.open = Asked(secrets.token_hex(4), speaker, question.heard, question.options)
            return self.open
        return None

    def take(self, question_id: str) -> Asked | None:
        """The open question with this ID, closed now (answered, or given up)."""
        if self.open is None or self.open.id != question_id:
            return None
        asked, self.open = self.open, None
        return asked

    def drop_speaker(self, speaker: int) -> Asked | None:
        """They stopped being recorded: their open question goes (returned, so its
        message can be taken down)."""
        if self.open is not None and self.open.speaker == speaker:
            asked, self.open = self.open, None
            return asked
        return None


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= NAME_MAX else text[: NAME_MAX - 1].rstrip() + "…"


def question_text(speaker: str, heard: str) -> str:
    """The question; `speaker` and `heard` must already be safe to show (escaped)."""
    return f'❓ **{speaker} said "{_short(heard)}"**: did they mean…'


def option_label(name: str) -> str:
    return _short(name)[:80]  # Discord's limit for a button label


def fixed_text(heard: str, name: str) -> str:
    """After the DM picked a name (both already escaped)."""
    return f'✅ Got it: from now on, "{_short(heard)}" is written as **{_short(name)}**.'


def kept_text(heard: str) -> str:
    return f'✅ Got it: "{_short(heard)}" stays as heard from now on.'


# The speaker stopped being recorded before the DM answered: their words aren't repeated.
GONE = "This question is closed: that person stopped being recorded."
