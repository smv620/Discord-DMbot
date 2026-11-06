"""What the DM screen says about the biggest clashes PlotBot found (#2660; docs/STORY_MEMORY.md
9a): up to three a session, each as one plain line, with nothing to press. The minor ones wait for
the after-session report. DMbot only points: nothing changes a belief, the DM decides.

Private to the DM like all DM-screen content: the line is DMbot's own words about the story and can
name an entry, so the caller posts it as a message that is pulled back if the screen opens up. Pure
text; no Discord, no database."""

from __future__ import annotations

from collections.abc import Sequence

from dmbot.memory.contradiction_grades import Stored
from dmbot.recaps.post import PART_MAX, parts_of

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


def waiting_text(count: int, *, private: bool) -> str:
    """For the DM screen when the DMs' private messages are closed. A private screen says how many
    wait; one that players may peek at says only that something waits, no number (a number about
    the story is a spoiler to a player who peeks)."""
    if not private:
        return "DMbot has something for you. Open your private messages from DMbot."
    what = "clash" if count == 1 else "clashes"
    return (
        f"⚠️ {count} {what} in your story may not add up. Open your private messages from DMbot "
        "to hear them."
    )


def told_parts(items: Sequence[Stored]) -> list[str]:
    """The messages for these clashes (at most `SAY_MAX` of them), split at line ends so that no
    reason is cut short (a long one goes on in the next message). Empty for none."""
    shown = list(items)[:SAY_MAX]
    if not shown:
        return []
    head = "⚠️ **This may not add up.**" if len(shown) == 1 else "⚠️ **These may not add up.**"
    body = "\n".join(line(i) for i in shown)
    text = f"{head}\n{body}\nNothing was changed. You decide what is true."
    return parts_of("\n".join(w for ln in text.split("\n") for w in _wrapped(ln)))


def _wrapped(text: str) -> list[str]:
    """A line longer than a message goes on in the next line, broken at a space, so the message
    split never lands inside a word."""
    out: list[str] = []
    while len(text) > PART_MAX:
        cut = text.rfind(" ", 0, PART_MAX)
        if cut <= 0:
            cut = PART_MAX  # one word longer than a message: nothing better
        out.append(text[:cut])
        text = text[cut:].lstrip(" ")
    return [*out, text]
