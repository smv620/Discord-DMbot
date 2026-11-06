"""One DM-screen note for every shared rule that changed at a session's start (#1538).

A campaign can follow a house-rules set and up to five homebrew packs, and all of them are checked
when a session starts. Posting a note for each would bury the DM screen at the worst moment, so the
checks (`set_update.collect`, `pack_update.collect`) hand their findings here and this posts them
as ONE message: a numbered line for each update and, under it, that update's own See changes / Pick
changes / Undo buttons (the same restart-proof buttons the single note uses). One update on its
own keeps exactly the note it always had. Four rows of buttons is what a message holds with room to
spare, so a fifth update or more is named in a line and handled from Settings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import discord

from dmbot.discord_limits import MESSAGE_MAX, fit_message

log = logging.getLogger(__name__)

HEADER = (
    "📚 Shared rules updated at this start. DMbot took the new versions. Buttons are by number:"
)
ROWS_MAX = 4  # updates that get their own buttons; the rest are named, then handled in Settings
OVERFLOW = "{n} without buttons here: open ⚙️ Manage DMbot → 📚 Shared rules."
TEXT_MAX = MESSAGE_MAX - 100  # margin kept from before: room for the words a sender adds around it


def numbered(label: str, tag: str) -> str:
    """A button's label: as it is alone, or short with its line's number in a combined note (a
    phone cuts a label from the right, so the number comes straight after one short word)."""
    if not tag:
        return label
    return f"{label.split()[0]} {tag}"


@dataclass
class Update:
    """One taken update: its line for the combined note, the note it would have alone, and its
    buttons (made on demand because a button needs its row and number)."""

    line: str
    single_text: str
    single_view: Callable[[], discord.ui.View]
    buttons: Callable[[int, str], list[discord.ui.Item[Any]]]
    extras: list[str] = field(default_factory=list)  # "{n}" becomes the line's number


@dataclass
class Collected:
    updates: list[Update] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)  # "isn't being shared any more" lines


def combined_text(collected: Collected) -> str:
    """Every update is named and every "no longer updated" line is kept (they are said only once,
    so they can't be lost); only the extra lines under an update give way when the text is long."""
    updates = collected.updates
    head = [HEADER]
    for number, update in enumerate(updates, 1):
        head.append(f"{number}. {update.line}")
    if len(updates) > ROWS_MAX:
        head.append(OVERFLOW.format(n=len(updates) - ROWS_MAX))
    tail = ["", *collected.gone] if collected.gone else []
    lines = [*head, *tail]
    size = len("\n".join(lines))
    # Extras go in behind their update's line while they fit, in order.
    for number, update in enumerate(updates[:ROWS_MAX], 1):
        extras = [f"   {e.replace('{n}', str(number))}" for e in update.extras]
        cost = sum(len(e) + 1 for e in extras)
        if size + cost > TEXT_MAX:
            continue
        at = lines.index(f"{number}. {update.line}") + 1
        lines[at:at] = extras
        size += cost
    return fit_message("\n".join(lines), limit=TEXT_MAX)


def combined_view(collected: Collected) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for number, update in enumerate(collected.updates[:ROWS_MAX], 1):
        for item in update.buttons(number - 1, str(number)):
            view.add_item(item)
    return view


async def post(bot: Any, campaign_id: str, screen_id: int, collected: Collected) -> None:
    """Post what was found: nothing, the single note as it always was, or the combined note."""
    updates = collected.updates
    if not updates:
        if collected.gone:
            await bot.post(screen_id, "\n".join(collected.gone))
        return
    if len(updates) == 1 and not collected.gone:
        text, view = updates[0].single_text, updates[0].single_view()
    else:
        text, view = combined_text(collected), combined_view(collected)
    posted = await bot.post_message(screen_id, text, view)
    if posted is None:  # one more try: the update is taken, and the DM needs its Undo
        posted = await bot.post_message(screen_id, text, view)
    if posted is None:
        log.warning(
            "Took %d shared-rules update(s) for campaign %s without a note",
            len(updates),
            campaign_id,
        )
