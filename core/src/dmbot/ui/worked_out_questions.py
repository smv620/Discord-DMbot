"""📜 "Is Aldric a parent of Mira?" (#2960): a family link DMbot worked out from two links the DM
confirmed, never stated by anyone. It is a suggestion until the DM says yes; a no is final; players
never see it. Shown in 📜 Check the connections after the suggested other names, one at a time, in
the DM's private message, with the two links it follows from."""

from __future__ import annotations

import asyncio
import contextlib
import logging

import discord

from dmbot.memory import worked_out
from dmbot.memory.models import CONFIRMED, DM, REJECTED
from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _Button, _Menu, _tell
from dmbot.ui.name_background import ONLY_DMS, campaign_for, markdown_safe, memory_of

log = logging.getLogger(__name__)


class WorkedOutQuestions(_Menu):
    """One worked-out family link at a time: ✅ Yes / ❌ No / ⏭️ Not now."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.current: worked_out.Suggestion | None = None
        self.yes = 0
        self.no = 0
        self.left = 0
        self.total = 0
        self.busy = asyncio.Lock()  # a second press waits for the first, so none is counted twice
        grey = discord.ButtonStyle.secondary
        self.add_item(
            _Button(self._yes, label="✅ Yes, remember it", style=discord.ButtonStyle.success)
        )
        self.add_item(_Button(self._no, label="❌ No, that's not true", style=grey))
        self.add_item(_Button(self._not_now, label="⏭️ Not now", style=grey))

    async def _waiting(self, interaction: discord.Interaction) -> list[worked_out.Suggestion]:
        memory = memory_of(interaction)
        campaign = await campaign_for(interaction, self.campaign_id)
        if memory is None or campaign is None:
            return []
        return await memory.worked_out_waiting(campaign.guild_id, campaign.id)

    def _set(self, waiting: list[worked_out.Suggestion]) -> None:
        self.left = len(waiting)
        self.total = max(self.total, self.yes + self.no + self.left)
        self.current = waiting[0] if waiting else None

    async def load(self, interaction: discord.Interaction) -> bool:
        campaign = await campaign_for(interaction, self.campaign_id)
        memory = memory_of(interaction)
        if campaign is None or memory is None or interaction.user.id not in campaign.dm_user_ids:
            return False
        try:  # (the suggestions follow the links as they are now)
            await memory.rework_family(campaign.guild_id, campaign.id)
        except Exception:
            log.exception("Worked-out family links could not be refreshed")
        self._set(await self._waiting(interaction))
        return self.current is not None

    def text(self) -> str:
        c = self.current
        if c is None:
            return f"🔎 Done. {self.yes} remembered, {self.no} turned down."
        place = f" ({self.yes + self.no + 1} of {self.total})" if self.total > 1 else ""
        first, second = (markdown_safe(w) for w in c.from_words)
        hidden = " It is secret: players will not see it." if c.secret else ""
        return (
            f"🔎 **Is {markdown_safe(c.subject)} a {c.role} of "
            f"{markdown_safe(c.object)}?**{place}\n"
            f"Nobody said this: DMbot worked it out from “{first}” and “{second}”. "
            f"Nothing is remembered until you say yes.{hidden}"
        )

    async def _show(
        self, interaction: discord.Interaction, waiting: list[worked_out.Suggestion]
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
        shown = self.current  # the card this press was made on, taken before any wait
        await interaction.response.defer()
        async with self.busy:
            campaign = await campaign_for(interaction, self.campaign_id)
            memory = memory_of(interaction)
            if campaign is None or memory is None:
                await _tell(interaction, "DMbot couldn't find that campaign. Try again.")
                return
            if interaction.user.id not in campaign.dm_user_ids:  # on every press
                await _tell(interaction, ONLY_DMS)
                return
            if shown is None:
                return
            try:  # (a source may have become secret since the card was drawn)
                await memory.rework_family(campaign.guild_id, campaign.id)
            except Exception:
                log.exception("Worked-out family links could not be refreshed")
            now = await self._waiting(interaction)
            if shown.relation_id not in {s.relation_id for s in now}:  # answered or gone meanwhile
                await self._show(interaction, now)
                return
            try:
                await memory.update_relation(
                    campaign.guild_id, campaign.id, shown.relation_id,
                    source=DM, status=CONFIRMED if yes else REJECTED,
                )  # fmt: skip
                if yes:  # (what follows from it, now that it stands)
                    await memory.rework_family(campaign.guild_id, campaign.id)
            except Exception:  # (a rule refusal or a database error: the card stays as it was)
                log.exception("A worked-out link could not be answered")
                await _tell(interaction, "DMbot couldn't save that one. It stays waiting.")
                return
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


async def offer_worked_out(interaction: discord.Interaction, campaign_id: str) -> bool:
    """The worked-out family links, one at a time, in a private message. Returns whether there was
    anything to ask."""
    try:
        view = WorkedOutQuestions(campaign_id)
        if not await view.load(interaction):
            return False
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                view.text(), view=view, ephemeral=True, allowed_mentions=NO_PINGS
            )
        return True
    except Exception:
        log.exception("Couldn't offer the worked-out family links")
        return False
