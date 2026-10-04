"""The DM screen hook used by `/dmbot start`.

#30 (PyCharm session) replaces `ensure_dm_screen` with the real feature: creating a
private `#dm-screen-<campaign>` channel, applying the campaign's DM-screen visibility,
and pinning the help card. Until then, this default keeps today's behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from dmbot.campaigns import Campaign

if TYPE_CHECKING:
    from dmbot.bot import DMBot


async def ensure_dm_screen(
    bot: DMBot, interaction: discord.Interaction, campaign: Campaign
) -> int | None:
    """Return the channel where this campaign's DM updates go, or None if there's none.

    Default: the campaign's saved DM screen if DMbot can still see it, otherwise the
    channel `/dmbot start` was run in. Permission checks happen afterwards, in the caller.
    """
    saved = campaign.dm_screen_channel_id
    if saved is not None and bot.get_channel(saved) is not None:
        return saved
    return interaction.channel_id
