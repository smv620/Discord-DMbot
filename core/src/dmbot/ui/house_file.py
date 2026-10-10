"""Sending a campaign's house-rules file (#969): `house-rules-<campaign>.txt`, privately.

After every change a DM makes in Discord (Add, Edit, Remove, an Override, a rule said or
typed to DMbot) the DM gets the updated file, to put wherever the table keeps its rules.
The 📥 **Download** button on the `/dmbot houserules` list gives anyone in the server the
same file: they may already read the list. The file holds this campaign's house rules and
no one else's. The format is `dmbot.rules.house_file`.

Sending the file is never worth failing the change for: a problem is logged and skipped.
"""

from __future__ import annotations

import io
import logging

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import house_file
from dmbot.ui.dmbot_commands import NO_PINGS, _bot

log = logging.getLogger(__name__)

AFTER_CHANGE = "Put this in your house-rules file so everyone can see it."
DOWNLOAD_LABEL = "📥 Download rules"
DOWNLOAD_NOTE = (
    "Your table's house rules, as a text file. Only you can see this message. "
    "Save it anywhere you like."
)
COULDNT = "Couldn't make the file just now. Try again in a moment."


async def build(interaction: discord.Interaction, campaign: Campaign) -> discord.File | None:
    """The file for this campaign's current rules, or None if they can't be read."""
    store = _bot(interaction).house_rules
    if store is None:
        return None
    try:
        rules = await store.list(campaign.guild_id, campaign.id)
    except Exception:
        log.exception("Couldn't read the house rules for the file")
        return None
    data = house_file.write(campaign.name, rules).encode()
    return discord.File(io.BytesIO(data), filename=house_file.filename(campaign.name))


async def send_after_change(
    interaction: discord.Interaction, campaign: Campaign, change: str
) -> None:
    """The updated file, privately, after a change. `change` says what happened ("Added
    house rule 12."). Never raises."""
    try:
        file = await build(interaction, campaign)
        if file is None:
            return
        await interaction.followup.send(
            f"{change} {AFTER_CHANGE}", file=file, ephemeral=True, allowed_mentions=NO_PINGS
        )
    except Exception:
        log.warning("Couldn't send the house-rules file", exc_info=True)


async def send_download(interaction: discord.Interaction, campaign: Campaign) -> None:
    """The 📥 Download button: the file as it is now, privately, for anyone."""
    file = await build(interaction, campaign)
    if file is None:
        await interaction.followup.send(COULDNT, ephemeral=True)
        return
    try:
        await interaction.followup.send(
            DOWNLOAD_NOTE, file=file, ephemeral=True, allowed_mentions=NO_PINGS
        )
    except discord.HTTPException:
        log.warning("Couldn't send the house-rules file", exc_info=True)
        await interaction.followup.send(COULDNT, ephemeral=True)
