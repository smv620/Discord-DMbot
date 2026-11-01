"""One private message for all of the questions DMbot has for a DM after a session (#3210).

The steps that find a question (new names, things said at the table, worked-out family links)
each hand over an `Ask`; the last step sends them as one message with one button per question, so
a DM is pinged once, not three times. A single question goes out as its own plain message, as
before. Counts and kinds only: nothing said at the table is in here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import discord

NAMES, CLAIMS, FAMILY = "names", "claims", "family"
# Always listed in this order, so the message reads the same each time.
ORDER = (NAMES, CLAIMS, FAMILY)


@dataclass(frozen=True, slots=True)
class Ask:
    """One question for the DM: what to say alone, what to say as a line in a list, its button,
    and the words for the DM screen if no DM can be reached."""

    kind: str
    alone: str  # the whole message when this is the only question
    line: str  # one short line when it is one of several ("3 new names to check")
    # A maker, not a button: one button belongs to one view, and the same question may need a
    # second view (the DM screen) if the private message could not be sent.
    button: Callable[[], discord.ui.Item[discord.ui.View]]
    # The button for the DM screen fallback: it can differ from the private chat's (the names
    # question has a screen button that finds the server from the press, the private one has it in
    # its id).
    screen_button: Callable[[], discord.ui.Item[discord.ui.View]]
    screen: str  # the DM screen's words, if the private message could not be sent


def ordered(asks: list[Ask]) -> list[Ask]:
    """The questions in the fixed order; a kind not in the list goes last."""
    return sorted(asks, key=lambda a: ORDER.index(a.kind) if a.kind in ORDER else len(ORDER))


def combine(asks: list[Ask]) -> tuple[str, discord.ui.View] | None:
    """The one message and its buttons, or None with nothing to ask. One question keeps its own
    words; several become a list under a count, each with its button."""
    if not asks:
        return None
    asks = ordered(asks)
    view = discord.ui.View(timeout=None)
    for ask in asks:
        view.add_item(ask.button())
    if len(asks) == 1:
        return asks[0].alone, view
    lines = "\n".join(f"• {ask.line}" for ask in asks)
    # (no ❓ here: the private message already opens with DMbot's own "has a question" line)
    head = f"**{len(asks)} questions** about your last session:"
    text = f"{head}\n{lines}\nPress a button to answer."
    return text, view


def view_of(ask: Ask) -> discord.ui.View:
    """A view with just this question's screen button, for the DM screen's fallback."""
    view = discord.ui.View(timeout=None)
    view.add_item(ask.screen_button())
    return view
