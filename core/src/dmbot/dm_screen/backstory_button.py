"""📜 Upload backstory: the way into the backstory import from the 👤 DMbot Main Menu (#2380).

It opens the same private menu as 📝 Names > From my story (paste it, upload a file, or paste a
link), so a DM never needs to find it in the names pages. Only drawn for the campaign's DMs, and the
press asks again: the menu checks the campaign's DMs on every open.
"""

from __future__ import annotations

import re
from typing import Any

import discord

_ID = r"(?P<campaign>[0-9a-f]{32})"
LABEL = "Upload backstory"
EMOJI = "📜"


class BackstoryButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:backstory:{_ID}",
):
    """📜 Upload backstory: opens the backstory menu, privately. For the campaign's DMs."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL,
                emoji=EMOJI,
                style=discord.ButtonStyle.secondary,
                row=row,
                custom_id=f"dmbot:backstory:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> BackstoryButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.name_background import open_background  # (ui imports this package: late)

        await open_background(interaction, self.campaign_id)
