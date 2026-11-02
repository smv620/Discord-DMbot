"""Send a question about a suggestion to a campaign's DMs (#3210): their private chat first, the DM
screen only if no DM can be reached, with the line saying why. Shared by the set and pack
suggestion notes, which both ask the publisher's DMs whether to add something."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import discord

from dmbot.dm_questions import NO_PRIVATE_MESSAGE


async def send(
    bot: Any, campaign: Any, text: str, make_view: Callable[[], discord.ui.View]
) -> bool:
    """True when the note went somewhere a DM will see it. A maker, not a view: the private chat
    and the screen each need their own view."""
    if await bot.ask_privately(campaign.guild_id, campaign.id, text, make_view()):
        return True
    if campaign.dm_screen_channel_id is None:
        return False
    posted = await bot.post_message(
        campaign.dm_screen_channel_id, text + NO_PRIVATE_MESSAGE, make_view()
    )
    return posted is not None
