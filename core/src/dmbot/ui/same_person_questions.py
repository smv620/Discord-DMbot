"""📜 "Is the guard at the gate the same as Ken?" (#3037): an unnamed person the story described and
a named one that may be them, with the evidence in fixed words. DMbot only asks; a yes joins them
(`join_same_person`: nothing new is shown to any player), a no is remembered so the pair is never
asked again. Shown in 📜 Check the connections after the worked-out family links, one at a time, in
the DM's private message."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

import discord

from dmbot.memory.models import MemoryRuleError
from dmbot.memory.same_person import Offer
from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _Button, _Menu, _tell
from dmbot.ui.name_background import ONLY_DMS, campaign_for, markdown_safe, memory_of

log = logging.getLogger(__name__)

KEEPS = "Joining them keeps what each player knew. Nothing new is shown to players."


@dataclass(frozen=True, slots=True)
class Asked:
    """One offer with the words the card shows."""

    offer: Offer
    unnamed: str
    named: str


class SamePersonQuestions(_Menu):
    """One possible match at a time: ✅ Yes, the same / ❌ No / ⏭️ Later."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.current: Asked | None = None
        self.yes = 0
        self.no = 0
        self.total = 0
        self.busy = asyncio.Lock()  # a second press waits for the first, so none is counted twice
        grey = discord.ButtonStyle.secondary
        self.add_item(
            _Button(self._yes, label="✅ Yes, the same", style=discord.ButtonStyle.success)
        )
        self.add_item(_Button(self._no, label="❌ No, someone else", style=grey))
        self.add_item(_Button(self._later, label="⏭️ Later", style=grey))

    async def _waiting(self, interaction: discord.Interaction) -> list[Asked]:
        memory = memory_of(interaction)
        campaign = await campaign_for(interaction, self.campaign_id)
        if memory is None or campaign is None:
            return []
        offers = await memory.same_person_offers(campaign.guild_id, campaign.id)
        if not offers:
            return []
        labels = {e.id: label for e, label in await memory.unnamed(campaign.guild_id, campaign.id)}
        found = {
            e.id: e.name
            for e in await memory.entities(campaign.guild_id, campaign.id)
            if e.id in {o.named_id for o in offers}
        }
        return [
            Asked(o, labels[o.unnamed_id], found[o.named_id])
            for o in offers
            if o.unnamed_id in labels and o.named_id in found
        ]

    def _set(self, waiting: list[Asked]) -> None:
        self.total = max(self.total, self.yes + self.no + len(waiting))
        self.current = waiting[0] if waiting else None

    async def load(self, interaction: discord.Interaction) -> bool:
        campaign = await campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:
            return False
        self._set(await self._waiting(interaction))
        return self.current is not None

    def text(self) -> str:
        c = self.current
        if c is None:
            return f"🔎 Done. {self.yes} joined, {self.no} kept apart."
        place = f" ({self.yes + self.no + 1} of {self.total})" if self.total > 1 else ""
        why = ", ".join(c.offer.reasons)
        return (
            f"🔎 **Is {markdown_safe(c.unnamed)} the same as {markdown_safe(c.named)}?**{place}\n"
            f"DMbot is only guessing: {why}.\n"
            f"{KEEPS}"
        )

    async def _show(self, interaction: discord.Interaction, waiting: list[Asked]) -> None:
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
            now = await self._waiting(interaction)
            pair = (shown.offer.unnamed_id, shown.offer.named_id)
            if pair not in {(a.offer.unnamed_id, a.offer.named_id) for a in now}:
                await self._show(interaction, now)  # answered or gone meanwhile
                return
            try:
                if yes:
                    await memory.join_same_person(campaign.guild_id, campaign.id, *pair)
                else:
                    await memory.not_the_same_person(
                        campaign.guild_id, campaign.id, *pair, user_id=interaction.user.id
                    )
            except MemoryRuleError as exc:
                # (the rule's own words say what is in the way, for example someone described by
                # this person)
                log.info("A possible match could not be answered: %s", exc)
                await _tell(interaction, f"{exc} It stays waiting.")
                return
            except Exception:
                log.exception("A possible match could not be answered")
                await _tell(interaction, "DMbot couldn't save that one. It stays waiting.")
                return
            self.yes, self.no = self.yes + int(yes), self.no + int(not yes)
            log.info("A possible match was %s", "joined" if yes else "kept apart")  # counts only
            await self._show(interaction, await self._waiting(interaction))

    async def _yes(self, interaction: discord.Interaction) -> None:
        await self._answer(True, interaction)

    async def _no(self, interaction: discord.Interaction) -> None:
        await self._answer(False, interaction)

    async def _later(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        campaign = await campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:  # every press
            await _tell(interaction, ONLY_DMS)
            return
        left = len(await self._waiting(interaction))
        self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=(
                    f"⏭️ Saved for later. {left} still waiting: press 📜 Check the "
                    "connections when you are ready."
                ),
                view=None,
                allowed_mentions=NO_PINGS,
            )


async def offer_same_person(interaction: discord.Interaction, campaign_id: str) -> bool:
    """The possible matches, one at a time, in a private message. Returns whether there was
    anything to ask."""
    try:
        view = SamePersonQuestions(campaign_id)
        if not await view.load(interaction):
            return False
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                view.text(), view=view, ephemeral=True, allowed_mentions=NO_PINGS
            )
        return True
    except Exception:
        log.exception("Couldn't offer the possible matches")
        return False
