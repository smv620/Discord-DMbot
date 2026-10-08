"""Name fixes the DM can undo (docs/PLAN.md, "Transcript Cleaner"; #296). Pure: no
Discord, no database.

A fix from a name DMbot only suggested is made, but never silently: the DM screen keeps
one "✏️ Name fixes to check" message, edited in place, with one line and one Undo each.
Nothing about these guesses ever goes in the transcript channel. Undo puts the heard
words back in that line (stored, waiting, or posted in the last ~30 s) and saves a
"keep as heard" rule, so the same words aren't fixed again; "Allow again" takes the rule
back.

The same notes keep the answers to "Did they mean…?" that fixed their line (#503), so
a line's words now are worked out in one place, whichever was changed or undone first.

Each fix keeps its number for the whole session, so a number never changes meaning while
the DM aims at it. The notes live with the running session, in memory; a speaker who
stops being recorded has their notes taken down; at the session's end the Undo buttons
go.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from dmbot.memory.models import name_key
from dmbot.transcript.cleaner import Fix

SHOWN = 10  # lines in the message (the newest); older ones are no longer undoable here
ANSWERS_KEPT = 20  # answers whose line can still be put back by their Undo
NAME_MAX = 60
MESSAGE_MAX = 2000  # Discord's limit for one message
HEADER = (
    "✏️ **Name fixes to check**: DMbot changed these words in the transcript but isn't "
    "sure. Wrong? Press its Undo to put back what was heard."
)
NOTHING = "_Nothing to check right now._"
ONLY_DM = "Only this campaign's DM can undo this."
EXPIRED = (
    "That fix is closed (the session ended, DMbot restarted, or it's too old). The line "
    "stays as written."
)
ALREADY = "That one is already undone."
STOPPED = "That person stopped being recorded, so this is closed."
ALLOW_AGAIN = "Allow again"
ALLOWED = "OK: DMbot may fix those words again from now on. The line stays as heard."


@dataclass(slots=True)
class Note:
    id: str  # short, for the button
    number: int  # its number for the whole session
    speaker: int
    started_ms: int  # the line, with `speaker`
    heard: str  # the whole line as heard
    fixes: tuple[Fix, ...]  # every fix made in that line
    index: int  # which of them this note is about
    undone: bool = False

    @property
    def fix(self) -> Fix:
        return self.fixes[self.index]

    @property
    def line(self) -> tuple[int, int]:
        return self.speaker, self.started_ms


@dataclass(frozen=True, slots=True)
class Answer:
    """An answer to "Did they mean…?" that fixed the line it was about (#503)."""

    batch: int | None  # the saved change its Undo takes back (None: nothing to undo)
    speaker: int
    started_ms: int
    heard: str  # the whole line as heard
    fixes: tuple[Fix, ...]  # the line's other fixes, as they were when answered
    start: int  # where the words are, as heard
    end: int
    written: str

    @property
    def line(self) -> tuple[int, int]:
        return self.speaker, self.started_ms


def rewrite(heard: str, edits: list[tuple[int, int, str]]) -> str:
    """The line with these (start, end, written) changes, which never overlap."""
    out, at = [], 0
    for start, end, written in sorted(edits):
        out += [heard[at:start], written]
        at = end
    out.append(heard[at:])
    return "".join(out)


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= NAME_MAX else text[: NAME_MAX - 1].rstrip() + "…"


def done_text(heard: str, written: str, *, line_kept: bool = False) -> str:
    """After an Undo (both already escaped): what it put back and what it learned.
    `line_kept`: the line itself couldn't be put back (saving failed, or it's too old)."""
    text = (
        f'↩️ Undone. "{heard}" stays as heard: DMbot won\'t change it to **{written}** '
        "again in this campaign."
    )
    if line_kept:
        text += f" That line couldn't be put back, so it still says **{written}**."
    return text


@dataclass(slots=True)
class FixNotes:
    """This session's fixes with Undo, oldest first."""

    notes: list[Note] = field(default_factory=list)
    count: int = 0  # numbers handed out this session
    answers: list[Answer] = field(default_factory=list)  # oldest first

    def add(self, speaker: int, started_ms: int, heard: str, fixes: tuple[Fix, ...]) -> list[Note]:
        """A note for each unsure fix in this line (none for sure ones)."""
        added = []
        for i, fix in enumerate(fixes):
            if fix.sure:
                continue
            self.count += 1
            added.append(
                Note(secrets.token_hex(4), self.count, speaker, started_ms, heard, fixes, i)
            )
        self.notes.extend(added)
        self._trim()
        return added

    def _trim(self) -> None:
        """Keep the shown notes, and the others on their lines (a line's text needs to
        know which of its fixes were undone); the rest can't be undone any more."""
        lines = {n.line for n in self.shown()}
        self.notes = [n for n in self.notes if n.line in lines]

    def shown(self) -> list[Note]:
        return self.notes[-SHOWN:]

    def find(self, note_id: str) -> Note | None:
        # Among all shown notes, even one a very long message left out: harmless, since
        # every redraw replaces the buttons, so a left-out note has none to press.
        return next((n for n in self.shown() if n.id == note_id), None)

    def undo(self, note_id: str) -> list[Note]:
        """Mark it undone, with the same words wherever else that line has them (one
        press, one rule): the notes now undone, none if it's gone or already undone."""
        note = self.find(note_id)
        if note is None or note.undone:
            return []
        key = name_key(note.fix.heard)
        same = [
            n
            for n in self.notes
            if n.line == note.line and not n.undone and name_key(n.fix.heard) == key
        ]
        for n in same:
            n.undone = True
        # The answers on that line no longer carry it, even once its notes are let go.
        gone = {n.fix for n in same}
        self.answers = [
            replace(a, fixes=tuple(f for f in a.fixes if f not in gone))
            if a.line == note.line
            else a
            for a in self.answers
        ]
        return same

    def line_text(self, note: Note) -> str:
        """That line's words now: its fixes applied, except the ones undone, and the
        answers that fixed it."""
        return self.words_now(note.speaker, note.started_ms, note.heard, note.fixes)

    def still_fixed(self, speaker: int, started_ms: int, fixes: tuple[Fix, ...]) -> tuple[Fix, ...]:
        """A line's fixes, without the ones undone."""
        undone = {n.fix for n in self.notes if n.undone and n.line == (speaker, started_ms)}
        return tuple(f for f in fixes if f not in undone)

    def words_now(self, speaker: int, started_ms: int, heard: str, fixes: tuple[Fix, ...]) -> str:
        """A line's words now: `fixes` without the ones undone, and the answers that
        fixed it."""
        line = (speaker, started_ms)
        edits = [(f.start, f.end, f.written) for f in self.still_fixed(speaker, started_ms, fixes)]
        edits += [(a.start, a.end, a.written) for a in self.answers if a.line == line]
        return rewrite(heard, edits)

    def answered(self, answer: Answer) -> None:
        """Keep the newest answers, and any on a line that still has notes (its text
        needs them)."""
        answers = [*self.answers, answer]
        lines = {n.line for n in self.notes}
        newest = answers[-ANSWERS_KEPT:]
        self.answers = [a for a in answers[:-ANSWERS_KEPT] if a.line in lines] + newest

    def take_back(self, batch: int) -> Answer | None:
        """Its Undo was pressed: the answer no longer fixes its line (returned, so the
        line can be put back), or None if it's too old or another session's."""
        found = next((a for a in self.answers if a.batch is not None and a.batch == batch), None)
        if found is not None:
            self.answers.remove(found)
        return found

    def drop_speaker(self, speaker: int) -> bool:
        """They stopped being recorded: their notes and answers go. True if any notes
        did."""
        before = len(self.notes)
        self.notes = [n for n in self.notes if n.speaker != speaker]
        self.answers = [a for a in self.answers if a.speaker != speaker]
        return len(self.notes) != before


def message_text(
    notes: list[Note], names: dict[int, str], escape: Callable[[str], str] = str
) -> tuple[str, list[Note]]:
    """The DM screen's message, and the notes it shows. `names`: speaker → display name
    (already safe to show); `escape` makes heard words and names safe (Discord
    markdown). Too long for Discord: the oldest whole lines go (never half a line, so
    no bold or strikethrough breaks), and with them their Undo buttons."""
    if not notes:
        return f"{HEADER}\n{NOTHING}", []
    lines = []
    for note in notes:
        who = names.get(note.speaker, "Someone")
        heard, written = escape(_short(note.fix.heard)), escape(_short(note.fix.written))
        if note.undone:
            lines.append(f"{note.number}. ~~{heard} → {written}~~ ({who}): ↩️ undone")
        else:
            lines.append(f"{note.number}. **{heard}** → **{written}** ({who})")
    kept = len(lines)
    while kept > 1 and len(HEADER) + sum(len(x) + 1 for x in lines[-kept:]) > MESSAGE_MAX:
        kept -= 1
    shown = notes[-kept:]
    # The cut below is only a safety net: one line is at most ~350 characters (names
    # are cut to NAME_MAX first), so whole lines always fit.
    return "\n".join([HEADER, *lines[-kept:]])[:MESSAGE_MAX], shown
