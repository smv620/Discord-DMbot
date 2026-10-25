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
import time
from collections.abc import Sequence
from typing import Any

import discord

from dmbot.memory.models import DM, KEEP, MemoryRuleError
from dmbot.mentions import NO_PINGS
from dmbot.transcript.name_pass import Fixed
from dmbot.transcript.name_pass_log import NamePassLog
from dmbot.ui.dmbot_commands import _bot, _tell

log = logging.getLogger(__name__)

NOT_YOURS = "Only this campaign's DM can undo that."
GONE = "I can't find this campaign any more. Nothing was changed."
NOTHING = "Nothing to put back: the lines were changed since, or this was undone already."
FAILED = "Something went wrong, so nothing was changed. Try again in a moment."
BACK = "↩️ Put the spellings back in {lines} {lines_word}."

MAX_UNDOS = 20  # a live session's report; an import's report spills into more messages
BUTTONS_PER_MESSAGE = 25  # Discord's limit
_TEMPLATE = r"dmbot:namepass:undo:(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,12})"


class UndoNamePassButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE
):
    def __init__(self, campaign_id: str, batch: int, name: str = "") -> None:
        super().__init__(
            discord.ui.Button(
                label=f"Undo {name}"[:80] if name else "Undo",
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
        if lines:  # the website's recap page shows it as put back (#2684)
            try:
                await NamePassLog.of(store).mark_undone(
                    guild.id, campaign.id, self.batch, now=int(time.time())
                )
            except Exception:
                log.exception("Name pass undo: couldn't note it for the website")
        if not lines:
            await _tell(interaction, NOTHING)
            return
        text = BACK.format(lines=lines, lines_word="line" if lines == 1 else "lines")
        await _tell(interaction, text)
        message = interaction.message
        if message is not None:
            await _drop_button(message, self.batch)


def undo_view(campaign_id: str, fixed: Sequence[Fixed]) -> discord.ui.View | None:
    """One Undo for each name that was fixed (at most `MAX_UNDOS`: a message holds 25 buttons)."""
    shown = list(fixed)[:MAX_UNDOS]
    if not shown:
        return None
    view = discord.ui.View(timeout=None)
    for f in shown:
        view.add_item(UndoNamePassButton(campaign_id, f.batch, f.name))
    return view


def undo_views_import(
    campaign_id: str, reports: Sequence[Sequence[Fixed]]
) -> list[discord.ui.View]:
    """The Undo buttons of one import's single report (#2649): one for each name fixed in a
    session, a number after the name when the name was fixed in more than one (the report's
    order, not the session's own number), the biggest first. A message holds 25 buttons, so a
    bigger import gets one more view for each further 25: every fix keeps its own Undo, since
    Undo is what keeps the AI's fixes under the DM's control."""
    seen: dict[str, int] = {}
    for fixed in reports:
        for f in fixed:
            seen[f.name] = seen.get(f.name, 0) + 1
    items = [
        (f.times, f"{f.name} #{n}" if seen[f.name] > 1 else f.name, f.batch)
        for n, fixed in enumerate(reports, 1)
        for f in fixed
    ]
    items.sort(key=lambda t: (-t[0], t[1]))
    views: list[discord.ui.View] = []
    for start in range(0, len(items), BUTTONS_PER_MESSAGE):
        view = discord.ui.View(timeout=None)
        for _, label, batch in items[start : start + BUTTONS_PER_MESSAGE]:
            view.add_item(UndoNamePassButton(campaign_id, batch, label))
        views.append(view)
    return views


async def _drop_button(message: Any, batch: int) -> None:
    """Take the pressed Undo off the report (the others stay); a message that cannot be edited
    keeps it, and pressing again is harmless."""
    try:
        view = discord.ui.View.from_message(message, timeout=None)
        keep = [
            c for c in view.children if getattr(c, "custom_id", "").split(":")[-1] != str(batch)
        ]
        view.clear_items()
        for item in keep:
            view.add_item(item)
        await message.edit(view=view if view.children else None, allowed_mentions=NO_PINGS)
    except Exception:
        log.info("Name pass undo: couldn't update the report")
