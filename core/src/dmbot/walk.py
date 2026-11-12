"""The "Check everything" walk-through's plain parts (#4192): which checks it goes through, in what
order, and the words of its progress line. No Discord and no database here, so the rules are
unit-testable on their own.

The walk keeps nothing in the database. What it is "up to" is read again from what still waits each
time a part ends or the DM presses Carry on, so a restart loses nothing and a count that changed
meanwhile is simply read again. Counts and part names only, never a name or a word of the text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from dmbot.dm_questions import CLAIMS, CONNECTIONS, NAMES, ORDER, PEOPLE

# The parts the walk goes through, in `dm_questions.ORDER`: a connection is only asked once both its
# names are answered, and a claim reads better once its names are settled (the owner's ruling,
# #4192). Slice 1 has these four; the rest of ORDER joins in the second slice.
PARTS = tuple(kind for kind in ORDER if kind in (NAMES, PEOPLE, CLAIMS, CONNECTIONS))

LABELS = {
    NAMES: "Names",
    PEOPLE: "Who is who",
    CLAIMS: "What was said",
    CONNECTIONS: "Connections",
}


def label(kind: str) -> str:
    return LABELS.get(kind, kind)


@dataclass(frozen=True, slots=True)
class Walk:
    """One DM's walk through one campaign's waiting checks. `parts` are the kinds that had
    something waiting when the walk began (or was carried on); `index` is the one being shown."""

    campaign_id: str
    guild_id: int
    user_id: int
    parts: tuple[str, ...]
    index: int = 0
    # The message the DM pressed (channel id, message id), so Stop for now can change its button
    # to Carry on. None when the press came from somewhere that can't be edited.
    origin: tuple[int, int] | None = None

    @property
    def kind(self) -> str:
        return self.parts[self.index]

    @property
    def last(self) -> bool:
        return self.index >= len(self.parts) - 1

    def next(self) -> Walk:
        return replace(self, index=self.index + 1)

    def progress(self, done: int, total: int) -> str:
        """ "Names, part 1 of 3 · 4 of 11": which part, and how far in it."""
        # (a walk of one part has no "part 1 of 1" to show)
        part = (
            f"{label(self.kind)}, part {self.index + 1} of {len(self.parts)}"
            if len(self.parts) > 1
            else label(self.kind)
        )
        return f"{part} · {done} of {total}" if total > 0 else part


def parts_for(counts: dict[str, int]) -> tuple[str, ...]:
    """The kinds with something waiting, in the walk's fixed order."""
    return tuple(kind for kind in PARTS if counts.get(kind, 0) > 0)


def between(done_kind: str, next_kind: str, waiting: int) -> str:
    """The one line between two parts: "Names done. Next: what was said (11)."."""
    return f"{label(done_kind)} done. Next: {label(next_kind).lower()} ({waiting})."


STOPPED = "Stopped. Your answers are saved. Press **✅ Carry on** on the message above to go on."
# (the message to press was not kept, so the way back is the button on the first message)
STOPPED_NO_ORIGIN = "Stopped. Your answers are saved. Press **✅ Check everything** again to go on."
ALL_DONE = "✅ Everything is checked. DMbot will ask again after the next session."


def left_for_later(left: int) -> str:
    """The end of a walk when some answers were left for later (Later, Skip the rest)."""
    what = "check" if left == 1 else "checks"
    return (
        f"Done for now. {left} {what} left for later. "
        "Press **✅ Check everything** on the message when you want them."
    )


NOTHING_TO_CHECK = "Nothing to check right now. DMbot will ask again when there is something new."
STOP_LABEL = "Stop for now"


def button_label(left: int, *, started: bool) -> str:
    """The message's button: "✅ Check everything (7)" before a walk, "✅ Carry on (4 left)" after
    Stop for now."""
    return f"✅ Carry on ({left} left)" if started else f"✅ Check everything ({left})"
