"""The table's guesses at a story thread (#4655, quests slice 7b; design `docs/design/quests.md` §7
and §10): what the characters said they think is behind a mystery, as the DM placed it from a
session's reading, shown on the thread's 🧵 card as "💭 Guess: Cerric is sure …".

The rules of §7 hold here, in the words and in what is worked out:
- DMbot never says a guess is right, wrong, warm or cold, never sets one beside the DM's answer
  with a verdict, and never marks a mystery solved: a line is the guess, who holds it and how sure
  they said they were, nothing else.
- Who sees which guess: the DM sees all; a player only those held by one of their own characters
  or by the whole party (which every player holds), never another character's and never a count
  (Supervisor's condition on #4655). The read (`MemoryStore.thread_guesses`) does the choosing.

Pure: the read is the store's. Words and counts only, never a line of the transcript."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

SURELY_AT, UNLIKELY_AT = 0.75, 0.25  # `readings.SURELY` and `readings.UNLIKELY`, as kept


@dataclass(frozen=True, slots=True)
class Guess:
    """One guess as a card shows it."""

    belief_id: str
    holder: str  # the character's name; "" when the whole party holds it
    words: str  # the statement: "Mira Vell poisoned the Old Well"
    confidence: float
    accusation: bool
    seq: int  # the belief's order, for "newest first"


def guess_line(g: Guess) -> str:
    """One guess, the holder first so the sureness is plainly theirs and never DMbot's (the
    ux-critic on #4655): "Cerric is sure Mira Vell poisoned the Old Well", "The whole party thinks
    …", "Oswin doubts …"."""
    who = g.holder or "The whole party"
    if g.confidence >= SURELY_AT:
        verb = "is sure"
    elif g.confidence <= UNLIKELY_AT:
        verb = "doubts"
    else:
        verb = "thinks"
    return f"{who} {verb} {g.words}"


def card_lines(guesses: Sequence[Guess], *, most: int | None = None) -> list[str]:
    """The 📋 screen's lines: one guess on one line, several under one heading, the newest first;
    `most` (None: all) with "…and N more. Tap 📋 to see them." after them. Nothing with no guess."""
    if not guesses:
        return []
    newest = sorted(guesses, key=lambda g: -g.seq)
    if len(newest) == 1:
        return [f"💭 **Guess:** {guess_line(newest[0])}"]
    shown = newest if most is None else newest[:most]
    lines = ["💭 **Guesses:**", *(f"• {guess_line(g)}" for g in shown)]
    if len(newest) > len(shown):
        lines.append(f"…and {len(newest) - len(shown)} more. Tap 📋 to see them.")
    return lines


def summary_lines(guesses: Sequence[Guess]) -> list[str]:
    """The name card's one line (it has little room, and its own trim must never leave a heading
    with nothing under it): the guess itself when there is one, else how many and where they are."""
    if len(guesses) <= 1:
        return card_lines(guesses)
    return [f"💭 **Guesses:** {len(guesses)}. Tap 📋 to see them."]
