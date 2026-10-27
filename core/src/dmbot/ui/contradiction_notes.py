"""What the DM screen says about the biggest clashes PlotBot found (#2660; docs/STORY_MEMORY.md
9a): up to three a session, each as one plain line, with nothing to press. The minor ones wait for
the after-session report. DMbot only points: nothing changes a belief, the DM decides.

Private to the DM like all DM-screen content: the line is DMbot's own words about the story and can
name an entry, so the caller posts it as a message that is pulled back if the screen opens up. Pure
text; no Discord, no database."""

from __future__ import annotations

from collections.abc import Sequence

from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.memory.contradiction_grades import Stored

SAY_MAX = 3  # a session says no more than this (the rest wait, and show in the report)
NO_REASON = "Two things in your story clash for the same point of view."


def line(item: Stored) -> str:
    """One clash as one line, with what is unsure about it."""
    said = " ".join(item.reason.split()) or NO_REASON
    notes = []
    if item.times_unknown:
        notes.append("The game times aren't known.")
    if item.involves_guess:
        notes.append("One of them is a guess.")
    return f"• {said}" + (f" ({' '.join(notes)})" if notes else "")


def told_text(items: Sequence[Stored]) -> str:
    """The DM-screen message for these clashes (at most `SAY_MAX` of them). Empty for none."""
    shown = list(items)[:SAY_MAX]
    if not shown:
        return ""
    head = "⚠️ **This may not add up.**" if len(shown) == 1 else "⚠️ **These may not add up.**"
    body = "\n".join(line(i) for i in shown)
    return fit_message(
        f"{head}\n{body}\nNothing was changed. You decide what is true.", limit=MESSAGE_MAX
    )
