"""📜 "Is Norro another name of Cerric?" (#3034): the other names an AI read of the DM's lore
suggested for characters the DM already confirmed. A suggested name is not used anywhere until the
DM says yes here (matching, fixes and connection ends all wait), and a no is remembered, so the
same text never asks again. The sentence of the text around the name is shown while DMbot still
holds it in memory (never stored); a secret name says so, and stays secret when confirmed."""

from __future__ import annotations

import asyncio
import contextlib
import logging

import discord

from dmbot.memory import lore_context
from dmbot.memory.models import CONFIRMED, DM, REJECTED, Alias, MemoryRuleError
from dmbot.mentions import NO_PINGS
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _bot, _Button, _Menu, _tell
from dmbot.ui.name_background import (
    ONLY_DMS,
    QUOTE_SHOWN,
    campaign_for,
    markdown_safe,
    memory_of,
)

log = logging.getLogger(__name__)


class NameQuestions(_Menu):
    """One suggested other name at a time: ✅ Yes, it is another name / ❌ No / ⏭️ Not now."""

    def __init__(self, campaign_id: str, held: lore_context.Held | None = None) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.held = held  # the sentences of the text, in memory only (#2561)
        self.guild_id = 0
        self.current: tuple[Alias, str] | None = None
        self.yes = 0
        self.no = 0
        self.left = 0
        self.total = 0
        self.busy = asyncio.Lock()  # a second press waits for the first, so none is counted twice
        grey = discord.ButtonStyle.secondary
        self.add_item(
            _Button(self._yes, label="✅ Yes, the same person", style=discord.ButtonStyle.success)
        )
        self.add_item(_Button(self._no, label="❌ No, someone else", style=grey))
        self.add_item(_Button(self._not_now, label="⏭️ Not now", style=grey))

    async def _waiting(self, interaction: discord.Interaction) -> list[tuple[Alias, str]]:
        memory = memory_of(interaction)
        campaign = await campaign_for(interaction, self.campaign_id)
        if memory is None or campaign is None:
            return []
        return await memory.suggested_names(campaign.guild_id, campaign.id)

    def _set(self, waiting: list[tuple[Alias, str]]) -> None:
        self.left = len(waiting)
        self.total = max(self.total, self.yes + self.no + self.left)
        self.current = waiting[0] if waiting else None

    async def load(self, interaction: discord.Interaction) -> bool:
        campaign = await campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:
            return False
        self.guild_id = campaign.guild_id
        self._set(await self._waiting(interaction))
        return self.current is not None

    def text(self, interaction: discord.Interaction | None = None) -> str:
        if self.current is None:
            return f"🔎 Done. {self.yes} added as other names, {self.no} turned down."
        alias, entry = self.current
        place = f" ({self.yes + self.no + 1} of {self.total})" if self.total > 1 else ""
        said = ""
        sentence = self.held.get(self.guild_id, alias.id) if self.held is not None else ""
        if sentence:
            said = f"From your text: “{markdown_safe(logic.shorten(sentence, QUOTE_SHOWN))}”\n"
        hidden = " It is a secret name: players will not see it." if alias.secret else ""
        return (
            f"🔎 **Is {markdown_safe(alias.text)} another name of "
            f"{markdown_safe(entry)}?**{place}\n"
            f"{said}Nothing uses the name until you say yes.{hidden}"
        )

    async def _show(
        self, interaction: discord.Interaction, waiting: list[tuple[Alias, str]]
    ) -> None:
        self._set(waiting)
        if self.current is None:
            self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=self.text(),
                view=self if self.current else None,
                allowed_mentions=NO_PINGS,
            )

    async def _answer(self, yes: bool, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        shown = self.current  # the card this press was made on, taken before any wait
        async with self.busy:
            campaign = await campaign_for(interaction, self.campaign_id)
            memory = memory_of(interaction)
            if campaign is None or memory is None:
                return
            if interaction.user.id not in campaign.dm_user_ids:  # on every press
                await _tell(interaction, ONLY_DMS)
                return
            if shown is None:
                return
            now = await self._waiting(interaction)
            if shown[0].id not in {a.id for a, _ in now}:  # answered or gone meanwhile
                await self._show(interaction, now)
                return
            try:
                await memory.update_alias(
                    campaign.guild_id, campaign.id, shown[0].id,
                    source=DM, status=CONFIRMED if yes else REJECTED,
                )  # fmt: skip
            except MemoryRuleError as exc:
                log.info("A suggested name could not be answered: %s", exc)
                await _tell(interaction, "DMbot couldn't save that one. It stays waiting.")
                return
            if self.held is not None:
                self.held.forget(campaign.guild_id, shown[0].id)
            self.yes, self.no = self.yes + int(yes), self.no + int(not yes)
            await self._show(interaction, await self._waiting(interaction))

    async def _yes(self, interaction: discord.Interaction) -> None:
        await self._answer(True, interaction)

    async def _no(self, interaction: discord.Interaction) -> None:
        await self._answer(False, interaction)

    async def _not_now(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        campaign = await campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:  # every press
            await _tell(interaction, ONLY_DMS)
            return
        self.left = len(await self._waiting(interaction))
        self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=(
                    f"⏭️ Saved for later. {self.left} still waiting: press 📜 Check the "
                    "connections when you are ready."
                ),
                view=None,
                allowed_mentions=NO_PINGS,
            )


async def offer_names(interaction: discord.Interaction, campaign_id: str) -> bool:
    """The suggested other names, one at a time in a private message, before the connections.
    Returns whether there was anything to ask."""
    try:
        view = NameQuestions(campaign_id, getattr(_bot(interaction), "lore_context", None))
        if not await view.load(interaction):
            return False
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                view.text(), view=view, ephemeral=True, allowed_mentions=NO_PINGS
            )
        return True
    except Exception:
        log.exception("Couldn't offer the suggested names from a background")
        return False
