"""Taking the buttons off DM-screen lines once the DM has decided (#1509).

A ready recap, a recap that is still waiting, and a player's suggested fix can each be on the DM
screen twice (the line posted when it was ready and a re-post at the next session start). Deciding
on one copy used to leave the other with buttons that only answer "already decided". The bot keeps
the messages it posted, in memory only (nothing is stored; after a restart a copy is found again
only when someone presses it), and edits the other copies when a decision is made.

Only the bot's own messages are ever kept, so only the bot's own messages are ever edited.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Hashable
from typing import Any

import discord

from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

KEYS_KEPT = 300  # items remembered; the oldest go first, so the memory stays small
COPIES = 4  # messages kept per item


def with_outcome(content: str, outcome: str) -> str:
    """The message's words with one short outcome word on a new line, within Discord's limit."""
    tail = f"\n{outcome}"
    return fit_message(content, limit=MESSAGE_MAX - len(tail)) + tail


class DecisionLines:
    def __init__(self) -> None:
        self._copies: OrderedDict[Hashable, list[Any]] = OrderedDict()

    def remember(self, key: Hashable, message: Any) -> None:
        """Keep a message the bot posted for this item (None, or one already kept: ignored)."""
        if message is None:
            return
        copies = self._copies.setdefault(key, [])
        if all(m.id != message.id for m in copies):
            copies.append(message)
            del copies[:-COPIES]
        self._copies.move_to_end(key)
        while len(self._copies) > KEYS_KEPT:
            self._copies.popitem(last=False)

    def remember_pressed(self, key: Hashable, interaction: discord.Interaction) -> None:
        """Keep the message a button was pressed on, if it is the bot's own."""
        message, me = interaction.message, interaction.client.user
        if message is not None and me is not None and message.author.id == me.id:
            self.remember(key, message)

    async def close(self, key: Hashable, outcome: str, *, pressed_id: int | None = None) -> None:
        """Take the buttons off every other kept copy and add the outcome word. A failed edit is
        logged and goes no further: the decision is already made."""
        for message in self._copies.pop(key, []):
            if message.id == pressed_id:
                continue
            try:
                await message.edit(
                    content=with_outcome(message.content, outcome),
                    view=None,
                    allowed_mentions=NO_PINGS,
                )
            except (
                Exception
            ):  # a deleted message or a missing permission must not undo the decision
                log.warning("Couldn't take the buttons off a decided line")
