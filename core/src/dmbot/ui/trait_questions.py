"""🧬 Lineage and class (#3926): a lore upload said something about a character the campaign has
already ("Torvin is a dwarf") that would change them, so DMbot asks instead of changing them
silently. ✅ Keep it writes it as the DM's own change (a new lineage replaces the old one; a class
adds to theirs), with Undo right after. ❌ Not this is remembered, so DMbot never asks it again.

Asked only in the DM's private chat, never on the DM Bot channel's notice (Supervisor's ruling:
the question quotes the DM's own lore and may spoil a backstory; the notice shows a count). Opened
from ❓ Questions for you or from the upload notice's button; only a DM of the campaign can open
it or answer, checked on every press.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.memory.models import SPECIES, MemoryRuleError, TooLateToUndo
from dmbot.memory.trait_offers import Offer, TraitOfferStore
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _send, _tell
from dmbot.ui.family_ties import _dm_campaign
from dmbot.ui.names import _md, _memory

log = logging.getLogger(__name__)

HEADING = "🧬 **Lineage and class**"
LABEL = "🧬 Lineage and class"
WHY = "DMbot never changes a character you have without asking you."
KEEP, NOT_THIS, UNDO = "✅ Keep it", "❌ Not this", "↩️ Undo"
HELP = f"_{KEEP}: DMbot adds it to the name card. {NOT_THIS}: DMbot won't ask about it again._"
KEPT = "Kept. ↩️ Undo takes it back."
NOT_KEPT = "OK. DMbot won't ask about that again."
UNDONE = "Taken back. The question is gone; the lore will ask again next time it's uploaded."
GONE_NOW = "Already answered. Showing what is left."
COULD_NOT = "DMbot couldn't add that one (the character may have changed). Showing what is left."
NOTHING_LEFT = "✅ Nothing left to answer. If a lore upload finds more, it will ask here."
NOT_AVAILABLE = (
    "Remembering names isn't switched on for this DMbot yet. Ask whoever runs DMbot to turn it on."
)
QUOTE_SHOWN = 200  # Discord: the quote stays well inside one message


def card_text(offer: Offer, waiting: int) -> str:
    """The question in plain words: what the lore says, where it said it, and what DMbot has now.
    Never posted in a shared channel."""
    count = f" ({waiting} waiting)" if waiting > 1 else ""
    who, what = _md(offer.entity_name), _md(offer.trait.name)
    source = f'Your lore "{_md(offer.upload)}"' if offer.upload else "Your lore"
    lines = [f"{HEADING}{count}", f"{source} says **{who}** is a **{what}**."]
    if offer.quote:
        quote = " ".join(offer.quote.split())
        if len(quote) > QUOTE_SHOWN:
            quote = quote[: QUOTE_SHOWN - 1].rstrip() + "…"
        lines.append(f'> "{_md(quote)}"')
    if offer.trait.kind == SPECIES:
        lines.append(
            f"DMbot has {who} as **{_md(offer.current)}**." if offer.current
            else f"DMbot has no lineage for {who} yet."
        )  # fmt: skip
    else:
        lines.append(f"DMbot doesn't have that class for {who} yet.")
    lines += [f"_{WHY}_", "", HELP]
    return "\n".join(lines)


class TraitQuestions(_Menu):
    """One question at a time: Keep it, Not this, and Undo right after a Keep."""

    def __init__(self, campaign_id: str, guild_id: int) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.guild_id = guild_id  # from the campaign: a DM's private chat has no server (#3210)
        self.current: Offer | None = None
        self.undo_batch: int | None = None  # the last Keep's change, while Undo is offered

    async def _load(self, interaction: discord.Interaction) -> str | None:
        """The next question's words (its buttons drawn), or None when none is left."""
        memory = _memory(interaction)
        if memory is None:
            return None
        waiting = await TraitOfferStore(memory._db).waiting(self.guild_id, self.campaign_id)
        if not waiting:
            self.current = None
            self.clear_items()
            if self.undo_batch is not None:
                self.add_item(_Button(self._undo, label=UNDO, style=discord.ButtonStyle.secondary))
            return None
        self.current = waiting[0]
        self.clear_items()
        self.add_item(_Button(self._keep, label=KEEP, style=discord.ButtonStyle.success))
        self.add_item(_Button(self._not_this, label=NOT_THIS, style=discord.ButtonStyle.danger))
        if self.undo_batch is not None:
            self.add_item(_Button(self._undo, label=UNDO, style=discord.ButtonStyle.secondary))
        return card_text(waiting[0], len(waiting))

    async def _keep(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "keep")

    async def _not_this(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "never")

    async def _undo(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "undo")

    async def _answer(self, interaction: discord.Interaction, what: str) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _dm_campaign(interaction, self.campaign_id)  # asked again each press
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        self.guild_id = campaign.guild_id  # (the campaign may have moved to another server)
        store = TraitOfferStore(memory._db)
        said = GONE_NOW
        offer = self.current
        try:
            if what == "undo" and self.undo_batch is not None:
                await memory.undo(self.guild_id, self.campaign_id, self.undo_batch)
                self.undo_batch, said = None, UNDONE
            elif what == "keep" and offer is not None:
                batch = await store.keep(memory, self.guild_id, self.campaign_id, offer)
                self.undo_batch, said = batch, KEPT
            elif what == "never" and offer is not None:
                if await store.never(self.guild_id, self.campaign_id, offer):
                    self.undo_batch, said = None, NOT_KEPT
        except TooLateToUndo:
            self.undo_batch, said = None, GONE_NOW
        except MemoryRuleError:
            self.undo_batch = None
            said = COULD_NOT if what == "keep" else GONE_NOW
        if what == "keep" and said == COULD_NOT and offer is not None:
            # (refused for good, the character is no longer one: not asked again)
            await store.never(self.guild_id, self.campaign_id, offer)
        shown = await self._load(interaction)
        if shown is None:
            if self.undo_batch is None:
                self.stop()
            await _replace(
                interaction, f"{said}\n\n{NOTHING_LEFT}", self if self.undo_batch else None
            )
            return
        await _replace(interaction, f"{said}\n\n{shown}", self)


class TraitQuestionsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:traits:ask:(?P<campaign>[0-9a-f]{32})",
):
    """🧬 Lineage and class, from the Questions message or an upload's notice; works after a
    restart (the campaign is in the button's ID). The card opens privately, for a DM only."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL,
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:traits:ask:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> TraitQuestionsButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_trait_questions(interaction, self.campaign_id)


async def start_trait_questions(interaction: discord.Interaction, campaign_id: str) -> None:
    # A new private message (the notice and the Questions message are never replaced).
    await interaction.response.defer(ephemeral=True, thinking=True)
    if _memory(interaction) is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None:
        return
    view = TraitQuestions(campaign.id, campaign.guild_id)
    shown = await view._load(interaction)
    if shown is None:
        await _tell(interaction, NOTHING_LEFT)
        return
    await _send(interaction, shown, view)


__all__ = ["TraitQuestions", "TraitQuestionsButton", "card_text", "start_trait_questions"]
