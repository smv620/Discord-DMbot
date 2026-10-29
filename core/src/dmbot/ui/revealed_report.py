"""The after-session line "Revealed at the table" (#2404): the names the table said that the
players had not been shown yet, now shown to them, with an Undo. It names no secret: a 🤫 spelling
is never revealed this way, and the names here were just said at the table.

The Undo button carries the campaign and the change's number, so it works after a restart. Only a
DM of the campaign can press it, checked on every press; it hides the names again, as one change.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.memory.revealed_aloud import MAX_LISTED
from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _bot, _tell

NOT_YOURS = "Only this campaign's DM can undo this."
GONE = "I can't find this campaign any more, so nothing was changed."
UNDONE = "↩️ Undone. Those names are hidden from your players again."
TOO_LATE = "Not undone: it was changed since, or it is too late. Use each name's card instead."


def report_text(names: list[str], *, where: str, waiting: int = 0) -> str:
    """ "👁️ Revealed at the table: A, B (Session 4)." and, when connections now join names the
    players know, one more line saying where to show them."""
    shown = [discord.utils.escape_markdown(n) for n in names[:MAX_LISTED]]
    more = len(names) - len(shown)
    listed = ", ".join(f"**{n}**" for n in shown) + (f" and {more} more" if more > 0 else "")
    text = f"👁️ Revealed at the table: {listed} ({where}). Your players can see them now."
    if waiting:
        what = "connection joins" if waiting == 1 else "connections join"
        text += (
            f"\n{waiting} hidden {what} names your players know now. "
            "Open a name's card → its connections → 👁️ Show to players."
        )
    return text


class UndoRevealedButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:unrevealed:(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    def __init__(self, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="↩️ Undo",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:unrevealed:{campaign_id}:{batch}",
            )
        )
        self.campaign_id = campaign_id
        self.batch = batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoRevealedButton:
        return cls(match["campaign"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)
        guild = interaction.guild
        campaign = (
            await bot.campaigns.get(guild.id, self.campaign_id) if guild is not None else None
        )
        if campaign is None:
            await _tell(interaction, GONE)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # checked on every press
            await _tell(interaction, NOT_YOURS)
            return
        if bot.memory is None:
            await _tell(interaction, GONE)
            return
        try:
            await bot.memory.undo(campaign.guild_id, campaign.id, self.batch)
        except (TooLateToUndo, MemoryRuleError):
            await _tell(interaction, TOO_LATE)
            return
        if bot.lookup is not None:
            bot.lookup.drop([campaign.id])
        await interaction.response.edit_message(
            content=UNDONE, view=None, allowed_mentions=NO_PINGS
        )


def revealed_view(campaign_id: str, batch: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(UndoRevealedButton(campaign_id, batch))
    return view
