"""`/dmbot import` (#2154): the private reply with the link to the website's import page, the same
as the 📥 Import sessions button on ⚙️ Settings (`dm_screen.import_button`). It takes no options and
no file: importing happens only on the website."""

from __future__ import annotations

import discord

from dmbot.dm_screen import import_button
from dmbot.ui.dmbot_commands import NOT_IN_SERVER, _bot, _tell, dmbot_group


@dmbot_group.command(name="import", description="Bring in older sessions, on the website")
async def dmbot_import(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    # The DM of any campaign in this server: the page lets the DM pick which one.
    campaigns = await _bot(interaction).campaigns.list_campaigns(guild.id)
    is_dm = any(interaction.user.id in c.dm_user_ids for c in campaigns)
    await import_button.reply(interaction, is_dm=is_dm)
