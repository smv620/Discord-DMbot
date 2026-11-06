"""The DM's view of last time's recap in an imported session (#3298): "12 lines read as last
time's recap and not used as this session's events", with an Undo that reads them as play after
all. The story reader leaves a recap read aloud at the start of a session out of what it takes as
this session's events (a recap retells earlier sessions); this note says how many lines that was,
counts only, and the button hands them back. Only the campaign's DMs, checked on every press."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.ui.dmbot_commands import _answer_first, _bot, _tell

log = logging.getLogger(__name__)

NOT_YOURS = "Only this campaign's DM can do that."
GONE = "I can't find this session any more. Nothing was changed."
NOTHING = "Nothing to put back: those lines were already read as play."
FAILED = "Something went wrong, so nothing was changed. Try again in a moment."
UNDO = "↩️ Read them as play"
_TEMPLATE = r"dmbot:recaplines:undo:(?P<campaign>[0-9a-f]{32}):(?P<session>[0-9a-f]{32})"


def note(count: int) -> str:
    """For the DM screen, after an imported session was read: counts only, never a line."""
    lines = "1 line" if count == 1 else f"{count} lines"
    were = "was" if count == 1 else "were"
    return (
        f"🔁 {lines} read as last time's recap and not used as this session's events. "
        f"If {'it' if count == 1 else 'they'} {were} really play, press the button and "
        f"{'it' if count == 1 else 'they'} will be read as play."
    )


def done(count: int) -> str:
    lines = "1 line" if count == 1 else f"{count} lines"
    return (
        f"↩️ {lines} will be read as play in the next few minutes. A recap already written "
        "stays as it is: ask for it again to include them."
    )


class RecapLinesUndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE
):
    def __init__(self, campaign_id: str, session_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:recaplines:undo:{campaign_id}:{session_id}",
            )
        )
        self.campaign_id, self.session_id = campaign_id, session_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RecapLinesUndoButton:
        return cls(match["campaign"], match["session"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction)
        bot = _bot(interaction)
        guild = interaction.guild
        campaign = (
            await bot.campaigns.get(guild.id, self.campaign_id) if guild is not None else None
        )
        if guild is None or campaign is None:
            await _tell(interaction, GONE)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, NOT_YOURS)
            return
        try:
            count = await bot.undo_recap_lines(guild.id, campaign.id, self.session_id)
        except Exception as exc:
            log.warning("Recap lines: the Undo failed (%s)", type(exc).__name__)
            await _tell(interaction, FAILED)
            return
        await _tell(interaction, done(count) if count else NOTHING)


def undo_view(campaign_id: str, session_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(RecapLinesUndoButton(campaign_id, session_id))
    return view


__all__ = ["RecapLinesUndoButton", "done", "note", "undo_view"]
