"""A note whose words the DM Bot channel can't hold (#3874): a pointer there, the words private.

The DM Bot channel is read by the campaign's people, so a note that carries house-rule text, a
sheet's name or who handed a campaign to whom goes to the DMs in a private message. The channel
gets one counts-only line saying something is waiting, only when the campaign has that channel
and only after the words reached a DM (or the screen, the fallback `private_note` uses when no DM
can be reached)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

import discord

from dmbot.dm_screen import private_note


async def send(
    bot: Any,
    campaign: Any,
    words: str,
    make_view: Callable[[], discord.ui.View],
    pointer: str,
) -> bool:
    """True when the words went somewhere a DM will see them. `pointer` must hold counts and kinds
    only: it is shown to everyone who can read the DM Bot channel."""
    told = bool(await private_note.send(bot, campaign, words, make_view))
    if told and campaign.dm_bot_channel_id:
        with contextlib.suppress(Exception):  # (a courtesy: the words have gone out)
            await bot.post(campaign.dm_bot_channel_id, pointer)
    return told
