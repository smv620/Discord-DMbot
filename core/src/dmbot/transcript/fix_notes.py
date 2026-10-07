"""Name fixes the DM can undo (docs/PLAN.md, "Transcript Cleaner"; #296). Pure: no
Discord, no database.

A fix from a name DMbot only suggested is made, but never silently: the DM screen keeps
one "✏️ Name fixes this scene" message, edited in place, with one line and one Undo
each. Nothing about these guesses ever goes in the transcript channel. Undo puts the
heard words back in that line (stored, waiting, or posted in the last ~30 s) and saves a
"keep as heard" rule, so the same words aren't fixed again.

The notes live with the running session, in memory; a speaker who stops being recorded
has their notes taken down; at the session's end the Undo buttons go.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

from dmbot.transcript.cleaner import Fix

SHOWN = 10  # lines in the message (the newest); older ones are no longer undoable here
NAME_MAX = 60
HEADER = "✏️ **Name fixes this scene**: names DMbot only suggested. Wrong? Press Undo."
ONLY_DM = "Only the DM can undo this."
EXPIRED = "That fix can't be undone here any more."
DONE = "↩️ Undone: those words stay as heard, in this line and from now on."
STOPPED = "That person stopped being recorded, so this is closed."


@dataclass(slots=True)
class Note:
    id: str  # short, for the button
    speaker: int
    started_ms: int  # the line, with `speaker`
    heard: str  # the whole line as heard
    fixes: tuple[Fix, ...]  # every fix made in that line
    index: int  # which of them this note is about
    undone: bool = False

    @property
    def fix(self) -> Fix:
        return self.fixes[self.index]


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= NAME_MAX else text[: NAME_MAX - 1].rstrip() + "…"


@dataclass(slots=True)
class FixNotes:
    """This session's fixes with Undo, newest last."""

    notes: list[Note] = field(default_factory=list)

    def add(self, speaker: int, started_ms: int, heard: str, fixes: tuple[Fix, ...]) -> list[Note]:
        """A note for each unsure fix in this line (none for sure ones)."""
        added = [
            Note(secrets.token_hex(4), speaker, started_ms, heard, fixes, i)
            for i, fix in enumerate(fixes)
            if not fix.sure
        ]
        self.notes.extend(added)
        return added

    def shown(self) -> list[Note]:
        return self.notes[-SHOWN:]

    def find(self, note_id: str) -> Note | None:
        return next((n for n in self.shown() if n.id == note_id), None)

    def undo(self, note_id: str) -> Note | None:
        """Mark it undone (once); None if it's gone or already undone."""
        note = self.find(note_id)
        if note is None or note.undone:
            return None
        note.undone = True
        return note

    def line_text(self, note: Note) -> str:
        """That line's words now: its fixes applied, except the ones undone."""
        undone = {
            n.index
            for n in self.notes
            if n.undone and (n.speaker, n.started_ms) == (note.speaker, note.started_ms)
        }
        out, at = [], 0
        for i, fix in enumerate(note.fixes):
            if i in undone:
                continue
            out += [note.heard[at : fix.start], fix.written]
            at = fix.end
        out.append(note.heard[at:])
        return "".join(out)

    def drop_speaker(self, speaker: int) -> bool:
        """They stopped being recorded: their notes go. True if any did."""
        before = len(self.notes)
        self.notes = [n for n in self.notes if n.speaker != speaker]
        return len(self.notes) != before


def message_text(
    notes: list[Note], names: dict[int, str], escape: Callable[[str], str] = str
) -> str:
    """The DM screen's message. `names`: speaker → display name (already safe to show);
    `escape` makes heard words and names safe (Discord markdown)."""
    lines = [HEADER]
    for number, note in enumerate(notes, start=1):
        who = names.get(note.speaker, "Someone")
        heard, written = escape(_short(note.fix.heard)), escape(_short(note.fix.written))
        change = f"**{heard}** → **{written}**"
        lines.append(
            f"{number}. ~~{change}~~ ({who}): ↩️ kept as heard"
            if note.undone
            else f"{number}. {change} ({who})"
        )
    return "\n".join(lines)
