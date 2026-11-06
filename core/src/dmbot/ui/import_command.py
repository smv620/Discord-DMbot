"""`/dmbot import` (#2154): the private reply with the link to the website's import page, the same
as the 📥 Import sessions button on ⚙️ Manage DMbot (`dm_screen.import_button`). It takes no
options and no file: importing happens only on the website."""

from __future__ import annotations

import logging

import discord

from dmbot.dm_screen import import_button
from dmbot.ui.dmbot_commands import NOT_IN_SERVER, _bot, _tell, dmbot_group

log = logging.getLogger(__name__)


@dmbot_group.command(name="import", description="Bring in older sessions, on the website")
async def dmbot_import(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    try:
        campaigns = await _bot(interaction).campaigns.list_campaigns(guild.id)
    except Exception:
        log.exception("Couldn't load the campaigns for /dmbot import")
        await interaction.response.send_message(import_button.FAILED, ephemeral=True)
        return
    # The campaigns here that this person is a DM of: one link for each.
    mine = [c for c in campaigns if interaction.user.id in c.dm_user_ids]
    await import_button.reply(interaction, mine)
