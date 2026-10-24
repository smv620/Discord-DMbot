"""Undo for PlotBot's name pass (#2628): one button under the note on the DM screen.

"Fixed 12 spellings of Kethrandil (heard as Keirandal)." has **↩️ Undo**. Pressing it puts every
spelling the pass wrote in that session back (only where the line is as the pass left it), and
saves a "keep as heard" rule for each old spelling, so the cleaner does not fix it the same way
again (as an Undo of a live fix does). Only the campaign's DMs, checked on every press. Nothing
here decides anything: the pass wrote what PlotBot was sure of, and the DM can take it back.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.memory.models import DM, KEEP, MemoryRuleError
from dmbot.mentions import NO_PINGS
from dmbot.ui.decision_lines import with_outcome
from dmbot.ui.dmbot_commands import _bot, _tell

log = logging.getLogger(__name__)

NOT_YOURS = "Only this campaign's DM can undo that."
GONE = "I can't find this campaign any more. Nothing was changed."
NOTHING = "Nothing to put back: the lines were changed since, or this was undone already."
FAILED = "Something went wrong, so nothing was changed. Try again in a moment."
BACK = "↩️ Put the spellings back in {lines} {lines_word}."

_TEMPLATE = r"dmbot:namepass:undo:(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,12})"


class UndoNamePassButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE
):
    def __init__(self, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:namepass:undo:{campaign_id}:{batch}",
            )
        )
        self.campaign_id, self.batch = campaign_id, batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoNamePassButton:
        return cls(match["campaign"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
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
        store = bot.transcripts
        if store is None:
            await _tell(interaction, FAILED)
            return
        try:
            olds = await store.respelled_olds(guild.id, campaign.id, self.batch)
            lines, _sessions = await store.undo_respell(guild.id, campaign.id, self.batch)
        except Exception:
            log.exception("Undo of the name pass failed")
            await _tell(interaction, FAILED)
            return
        if bot.memory is not None:
            for heard in olds:
                try:
                    await bot.memory.add_correction(
                        guild.id, campaign.id, heard, action=KEEP, source=DM
                    )
                except MemoryRuleError:
                    log.info("Name pass undo: a keep-as-heard rule was refused")
                except Exception:
                    log.exception("Name pass undo: couldn't save the keep-as-heard rule")
        if not lines:
            await _tell(interaction, NOTHING)
            return
        text = BACK.format(lines=lines, lines_word="line" if lines == 1 else "lines")
        message = interaction.message
        if message is not None:
            await interaction.response.edit_message(
                content=with_outcome(message.content, "Undone."),
                view=None,
                allowed_mentions=NO_PINGS,
            )
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
        else:
            await _tell(interaction, text)


def undo_view(campaign_id: str, batch: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(UndoNamePassButton(campaign_id, batch))
    return view
