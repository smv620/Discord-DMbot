"""⏸️ Pause the table: the DM's off the record (#2466, docs/PLAN.md, "Consent").

A button on the DM screen's "Listening" message, for breaks such as food or real-life talk. It
takes everyone off the record at once, for 5 minutes, 15 minutes or until the DM presses ▶️ Resume
now, and the table sees one plain line in the DMbot chat saying so. The DM sidebar still answers
the DM's typed questions. Only this campaign's DMs may press it, checked on every press; a
player who wants a break pauses their own recording from the 🗂️ Menu instead.

Register with `bot.add_dynamic_items(PauseTableButton, PauseTableForButton, ResumeTableButton)`.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.dm_screen import messages
from dmbot.mentions import NO_PINGS
from dmbot.off_the_record import CHOICES, paused_text

_ID = r"(?P<campaign>[0-9a-f]{32})"
LENGTHS = {5: "5 minutes", 15: "15 minutes", None: "Until I press Resume"}
ASK = "⏸️ How long should the table be off the record? Nobody is recorded while it is."
ONLY_DMS = "Only a DM of this campaign can pause the table."
BACK = "▶️ The table is back on the record."
ALREADY = "The table is already on the record."


def _table(interaction: discord.Interaction, campaign_id: str) -> Any | None:
    """The running table of this campaign, if the presser is one of its DMs (asked on every
    press); None otherwise."""
    bot: Any = interaction.client
    guild = interaction.guild
    if guild is None or not hasattr(bot, "tables"):
        return None
    table = bot.tables.get(guild.id)
    if table is None or table.campaign_id != campaign_id:
        return None
    return table if table.is_dm(interaction.user.id) else None


class PauseTableButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:tablepause:{_ID}",
):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=messages.PAUSE_TABLE_LABEL,
                emoji="⏸️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:tablepause:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PauseTableButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        if _table(interaction, self.campaign_id) is None:
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        view = discord.ui.View(timeout=None)
        for minutes in CHOICES:
            view.add_item(PauseTableForButton(self.campaign_id, minutes))
        await interaction.response.send_message(
            ASK, view=view, ephemeral=True, allowed_mentions=NO_PINGS
        )


class PauseTableForButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:tablepausefor:{_ID}:(?P<minutes>[0-9]{{1,3}})",
):
    def __init__(self, campaign_id: str, minutes: int | None) -> None:
        super().__init__(
            discord.ui.Button(
                label=LENGTHS[minutes],
                style=discord.ButtonStyle.primary
                if minutes == 5
                else discord.ButtonStyle.secondary,
                custom_id=f"dmbot:tablepausefor:{campaign_id}:{minutes or 0}",
            )
        )
        self.campaign_id = campaign_id
        self.minutes = minutes

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PauseTableForButton:
        return cls(match["campaign"], int(match["minutes"]) or None)

    async def callback(self, interaction: discord.Interaction) -> Any:
        table = _table(interaction, self.campaign_id)
        if table is None:
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        await interaction.response.defer()
        bot: Any = interaction.client
        done = await bot.off_the_record.pause_table(table.guild_id, self.minutes)
        if not done.ok:
            await interaction.edit_original_response(content=done.why, view=None)
            return
        view = discord.ui.View(timeout=None)
        view.add_item(ResumeTableButton(self.campaign_id))
        await interaction.edit_original_response(
            content=paused_text(done.until).replace("recording you", "recording the table"),
            view=view,
        )


class ResumeTableButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:tableresume:{_ID}",
):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Resume now",
                emoji="▶️",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:tableresume:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ResumeTableButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        table = _table(interaction, self.campaign_id)
        if table is None:
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        await interaction.response.defer()
        bot: Any = interaction.client
        was = await bot.off_the_record.resume_table(table.guild_id)
        await interaction.edit_original_response(content=BACK if was else ALREADY, view=None)
