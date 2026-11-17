"""The server a campaign's card belongs to, for a button pressed in a private chat (#4630).

A private message has no server (`interaction.guild` is None), so a card that reads
`interaction.guild` does nothing there. The campaign knows its server: this asks the database
(`CampaignStore.guild_of`), the way the names card does (`ui/names.py` `_campaign_for`). It
answers only where the server is; who may do what is still checked on every press.
"""

from __future__ import annotations

from typing import Any

import discord


async def guild_id_for(interaction: discord.Interaction, campaign_id: str) -> int | None:
    """The server of this campaign: the one the press came from, else the campaign's own. None
    when the campaign is gone."""
    if interaction.guild is not None:
        return interaction.guild.id
    client: Any = interaction.client
    found = await client.campaigns.guild_of(campaign_id)
    return None if found is None else int(found)
