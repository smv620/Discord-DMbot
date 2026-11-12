"""One private message for all of the questions DMbot has for a DM after a session (#3210).

The steps that find a question (new names, things said at the table, worked-out family links)
each hand over an `Ask`; the last step sends them as one message with one button per question, so
a DM is pinged once, not three times. A single question goes out as its own plain message, as
before. Counts and kinds only: nothing said at the table is in here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import discord

# Added to a question that had to stay on the DM screen (#3210), so a DM whose private messages are
# closed learns why it is there and how to get it privately. Here, not in bot.py, so the screens can
# use it without importing the bot.
NO_PRIVATE_MESSAGE = (
    "\n_DMbot couldn't message you privately. Allow messages from this server in your Discord "
    "privacy settings._"
)

NAMES, PEOPLE, CLAIMS, FAMILY = "names", "people", "claims", "family"
SCRIPT = "script"  # a script of the session can be made (#3244)
KINDS = "kinds"  # some words can become a kind of connection (#3033)
DEEDS = "deeds"  # deeds the party did, waiting for the DM to record (#3175)
REVEALS = "reveals"  # what the characters learned, waiting for the DM to keep (#2996)
CONNECTIONS = "connections"  # backstory connections and suggested names (after an upload, #3314)
# Always listed in this order, so the message reads the same each time.
ORDER = (NAMES, PEOPLE, CLAIMS, CONNECTIONS, FAMILY, KINDS, DEEDS, REVEALS, SCRIPT)


@dataclass(frozen=True, slots=True)
class Ask:
    """One question for the DM: what to say alone, what to say as a line in a list, its button,
    and the words for the fallback channel if no DM can be reached (counts only: the DM Bot
    channel is read by the campaign's people)."""

    kind: str
    alone: str  # the whole message when this is the only question
    line: str  # one short line when it is one of several ("3 new names to check")
    # A maker, not a button: one button belongs to one view, and the same question may need a
    # second view (the DM screen) if the private message could not be sent.
    button: Callable[[], discord.ui.Item[Any]]
    # The button for the DM screen fallback: it can differ from the private chat's (the names
    # question has a screen button that finds the server from the press, the private one has it in
    # its id).
    screen_button: Callable[[], discord.ui.Item[Any]]
    screen: str  # counts-only words for the fallback channel, if the private message fails
    # How many checks wait behind this question (0 when not counted), for "Check everything (N)".
    count: int = 0
    # A link to a summary message elsewhere, not a button that opens cards (the upload's summary,
    # which holds Add all): "Check everything" keeps it.
    link: bool = False


def ordered(asks: list[Ask]) -> list[Ask]:
    """The questions in the fixed order; a kind not in the list goes last."""
    return sorted(asks, key=lambda a: ORDER.index(a.kind) if a.kind in ORDER else len(ORDER))


# The kinds "✅ Check everything" walks through (#4192), in ORDER. The other kinds keep their own
# buttons until they join the walk.
WALKED = (NAMES, PEOPLE, CLAIMS, CONNECTIONS)
WALK_PRESS = "Press **✅ Check everything** to go through them."


def combine(
    asks: list[Ask],
    about: str = "your last session",
    walk: Callable[[int], discord.ui.Item[Any]] | None = None,
) -> tuple[str, discord.ui.View] | None:
    """The one message and its buttons, or None with nothing to ask. One question keeps its own
    words; several become a list under a count. With `walk` (a maker taking the total waiting, 0
    when not counted) and two or more of the walked kinds, those kinds' buttons become one
    "✅ Check everything"; a link to an upload's summary stays, and so does every other kind's
    own button."""
    if not asks:
        return None
    asks = ordered(asks)
    walked = [a for a in asks if a.kind in WALKED]  # (two or more kinds, not two questions)
    use_walk = walk is not None and len({a.kind for a in walked}) >= 2
    view = discord.ui.View(timeout=None)
    if use_walk and walk is not None:
        counted = [a.count for a in walked]
        view.add_item(walk(sum(counted) if all(counted) else 0))
    for ask in asks:
        if use_walk and ask.kind in WALKED and not ask.link:
            continue
        view.add_item(ask.button())
    if len(asks) == 1:
        return asks[0].alone, view
    lines = "\n".join(f"• {ask.line}" for ask in asks)
    # (no ❓ here: the private message already opens with DMbot's own "has a question" line)
    head = f"**{len(asks)} questions** about {about}:"
    press = WALK_PRESS if use_walk else "Press a button to answer."
    text = f"{head}\n{lines}\n{press}"
    return text, view


def view_of(ask: Ask) -> discord.ui.View:
    """A view with just this question's screen button, for the DM screen's fallback."""
    view = discord.ui.View(timeout=None)
    view.add_item(ask.screen_button())
    return view
