"""A note whose words the DM Bot channel can't hold (#3874): a pointer there, the words private.

The DM Bot channel is read by the campaign's people, so a note that carries house-rule text, a
sheet's name or who handed a campaign to whom goes to the DMs in a private message. The channel
gets one counts-only line saying something is waiting, only when the campaign has that channel
and only when the words really went to a DM's private chat: when no DM can be reached the words
go on the DM screen (as `private_note` does) and the pointer is left out, since it would send
people to a private message that does not exist."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

import discord

from dmbot.dm_questions import NO_PRIVATE_MESSAGE


def _copy(view: discord.ui.View) -> discord.ui.View:
    """A new view holding the same buttons: sending a view to a private chat adds link buttons to
    it, and the screen fallback must not carry those."""
    made = discord.ui.View(timeout=view.timeout)
    for item in view.children:
        made.add_item(item)
    return made


async def send(
    bot: Any,
    campaign: Any,
    words: str,
    make_view: Callable[[], discord.ui.View],
    pointer: str | None,
    *,
    note: bool = False,
) -> bool:
    """True when the words went somewhere a DM will see them. `pointer` must hold counts and kinds
    only: it is shown to everyone who can read the DM Bot channel (None: no pointer). `note`: the
    words are a statement, not a question, so the private message says so."""
    if await bot.ask_privately(
        campaign.guild_id, campaign.id, words, _copy(make_view()), note=note
    ):
        if pointer and campaign.dm_bot_channel_id:
            with contextlib.suppress(Exception):  # (a courtesy: the words have gone out)
                await bot.post(campaign.dm_bot_channel_id, pointer)
        return True
    if campaign.dm_screen_channel_id is None:
        return False
    posted = await bot.post_message(
        campaign.dm_screen_channel_id, words + NO_PRIVATE_MESSAGE, make_view()
    )
    return posted is not None
