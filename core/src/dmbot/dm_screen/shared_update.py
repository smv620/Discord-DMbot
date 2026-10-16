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

log = logging.getLogger(__name__)

HEADER = "📚 Shared rules updated at this start (DMbot took the new versions):"
ROWS_MAX = 4  # updates that get their own buttons; the rest are named, then handled in Settings
OVERFLOW = "and {n} more: open ⚙️ Settings → 📚 Shared rules."
TEXT_MAX = 1900  # a Discord message holds 2000


@dataclass
class Update:
    """One taken update: its line for the combined note, the note it would have alone, and its
    buttons (made on demand because a button needs its row and number)."""

    line: str
    single_text: str
    single_view: Callable[[], discord.ui.View]
    buttons: Callable[[int, str], list[discord.ui.Item[Any]]]
    extras: list[str] = field(default_factory=list)


@dataclass
class Collected:
    updates: list[Update] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)  # "isn't being shared any more" lines


def combined_text(collected: Collected) -> str:
    lines = [HEADER]
    for number, update in enumerate(collected.updates[:ROWS_MAX], 1):
        lines.append(f"{number}. {update.line}")
        lines.extend(f"   {extra}" for extra in update.extras)
    if len(collected.updates) > ROWS_MAX:
        lines.append(OVERFLOW.format(n=len(collected.updates) - ROWS_MAX))
    lines.extend(collected.gone)
    text = "\n".join(lines)
    return text if len(text) <= TEXT_MAX else text[: TEXT_MAX - 1].rsplit("\n", 1)[0] + "\n…"


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
    if posted is None:  # taken, but the DM has no note and no Undo button for it
        log.warning("Took %d shared-rules update(s) for campaign %s without a note",
                    len(updates), campaign_id)  # fmt: skip
