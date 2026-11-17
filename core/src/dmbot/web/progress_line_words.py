"""The words of one "Progress to check" line for the website (#4616). Discord's 🧵 card words
them in `ui/thread_progress_card.line_text`; the website's API may not import the Discord
screens (an import contract), so the same words are here and a test holds the two equal for every
kind, so they cannot drift. Plain text: the page escapes what a DM typed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from dmbot.discord_limits import shorten
from dmbot.memory.threads import PROMISE, words_for

LINE_MAX = 90
KIND_WORDS = {
    "objective": "Step", "complication": "In the way", "reward": "Reward",
    "reward_given": "Reward given", "help": "Help", "time_limit": "Time limit",
}  # fmt: skip
STATE_SAID = {
    "to_do": "to do", "done": "done", "failed": "failed", "not_needed": "not needed now",
    "in_the_way": "still in the way", "overcome": "overcome",
    "no_longer_matters": "no longer matters", "owed": "still owed", "settled": "all given",
    "wont_be_given": "won't be given", "turned_down": "the party turned it down",
    "given": "given", "to_keep": "theirs to keep", "lent": "lent", "returned": "returned",
    "not_returned": "not returned", "running": "still running", "met": "met in time",
    "lifted": "lifted",
}  # fmt: skip


def _and(names: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def line_text(row: Mapping[str, Any]) -> str:
    """One line from the door's row, the change first, in the 🧵 card's own words: "New · Step:
    find the map", "Done: find Garret", "This thread: happening now", "Clue found: the torn letter
    (by Ash)"."""
    kind = str(row["kind"])
    words = shorten(str(row.get("words") or ""), LINE_MAX)
    state = str(row.get("state") or "")
    if kind == "part":
        part_kind = str(row.get("partKind") or "")
        text = f"New · {KIND_WORDS.get(part_kind, part_kind)}: {words}"
        if state == "lent":
            text += " (lent)"
        if row.get("due"):
            text += f" (by: {shorten(str(row['due']), LINE_MAX)})"
        return text
    if kind == "part_state":
        part = shorten(str(row.get("partWords") or "a step"), LINE_MAX)
        return f"{STATE_SAID.get(state, state).capitalize()}: {part}"
    if kind == "clue":
        known = bool(row.get("clueKnown"))
        clue = str(row.get("clue") or "") if known else words
        who = [str(n) for n in row.get("finders") or []]
        name = shorten(clue or "a clue", LINE_MAX)
        text = f"Clue found: {name}"
        if not who:  # (every finder gone: a clue the campaign has must not read as a new one)
            return f"Clue found again: {name}" if known else text
        if known:
            return f"{text} (now known by {_and(who)})"
        return f"{text} (by {_and(who)})"
    which = "promise" if str(row.get("threadType") or "") == PROMISE else "thread"
    return f"This {which}: {words_for(str(row.get('threadType') or '')).get(state, state).lower()}"
