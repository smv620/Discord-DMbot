"""The DM's choice between two house rules that disagree, met while answering a question (#2284).

Two buttons, one for each rule ("House rule 3 applies"). The press records that rule as the pair's
winner (`dmbot.rules.wins`) and asks the question again, which now follows the winner. Only the
campaign's DMs can press. DMbot never picks: until a DM does, neither rule is used.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Collection
from typing import Any

import discord

from dmbot.rules.wins import RuleRef
from dmbot.sidebar.rule_clash import ONLY_DM, Side

log = logging.getLogger(__name__)

TIMEOUT_S = 15 * 60  # the question is a live table's; later, the DM asks again
NOTED = "Noted: {label} applies from now on. Asking again…"
TIMED_OUT = "No choice made, so neither rule was used. Ask again to choose."
RETRY_FAILED = "Choice saved, but I couldn't answer. Ask the question again."
NOT_SAVED = "Couldn't save your choice, so neither rule was used. Ask again to choose."
Record = Callable[[RuleRef, RuleRef, int], Awaitable[bool]]
Retry = Callable[[], Awaitable[None]]


class _Pick(discord.ui.Button[Any]):
    def __init__(self, view: ClashView, mine: Side, other: Side) -> None:
        super().__init__(label=f"{mine.label} applies", style=discord.ButtonStyle.primary)
        self._owner, self._mine, self._other = view, mine, other

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._owner.chosen(interaction, self._mine, self._other)


class ClashView(discord.ui.View):
    def __init__(
        self, sides: tuple[Side, Side], *, dm_ids: Collection[int], record: Record, retry: Retry
    ) -> None:
        super().__init__(timeout=TIMEOUT_S)
        self._dm_ids, self._record, self._retry = frozenset(dm_ids), record, retry
        self._answered = False
        first, second = sides
        self.add_item(_Pick(self, first, second))
        self.add_item(_Pick(self, second, first))

    async def on_timeout(self) -> None:
        """Buttons that do nothing any more are taken away, so none looks live and fails."""
        message = self.message
        if message is not None and not self._answered:
            with contextlib.suppress(discord.HTTPException):
                await message.edit(content=TIMED_OUT, view=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self._dm_ids:  # on every press
            return True
        await interaction.response.send_message(ONLY_DM, ephemeral=True)
        return False

    async def chosen(self, interaction: discord.Interaction, winner: Side, loser: Side) -> None:
        if self._answered:  # a second press on a question already answered
            await interaction.response.defer()
            return
        self._answered = True
        self.stop()
        try:
            saved = await self._record(winner.ref, loser.ref, interaction.user.id)
        except Exception:
            log.exception("Couldn't store which house rule applies")
            saved = False
        if not saved:
            await interaction.response.edit_message(content=NOT_SAVED, view=None)
            return
        await interaction.response.edit_message(content=NOTED.format(label=winner.label), view=None)
        try:
            await self._retry()
        except Exception:
            log.exception("The sidebar couldn't answer again after a rule choice")
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(content=RETRY_FAILED, view=None)
