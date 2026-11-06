"""📇 Names: the way into the campaign's names from the DM Bot channel and ⚙️ Settings (#1986).

The names menu is `/dmbot names`; this button opens the same private home card, so a DM never needs
the command and never meets a second menu. It is only drawn for the campaign's DMs, and the press
asks again (the home card checks the campaign's DMs and server managers on every open).
"""

from __future__ import annotations

import re
from typing import Any

import discord

_ID = r"(?P<campaign>[0-9a-f]{32})"
LABEL = "Names"
EMOJI = "📇"


class NamesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:names_home:{_ID}",
):
    """📇 Names: opens the names home card, privately. For the campaign's DMs."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL,
                emoji=EMOJI,
                style=discord.ButtonStyle.secondary,
                row=row,
                custom_id=f"dmbot:names_home:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> NamesButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.names import show_home  # (ui imports the dm_screen package: import it late)

        await show_home(interaction, self.campaign_id)
