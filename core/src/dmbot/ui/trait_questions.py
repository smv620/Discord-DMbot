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
from dmbot.memory.trait_offers import NotWaiting, Offer, TraitOfferStore
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _send, _tell
from dmbot.ui.family_ties import _dm_campaign
from dmbot.ui.names import _md, _memory

log = logging.getLogger(__name__)

HEADING = "🧬 **Lineage and class**"
LABEL = "🧬 Lineage and class"
WHY = "DMbot never changes a character without asking you."
# (not "Keep it": on DMbot's other cards that is the answer that changes nothing; ux-critic)
YES, NO, UNDO = "✅ Yes, change it", "❌ No, leave it", "↩️ Undo"
GONE_NOW = "Already answered. Showing what is left."
NOTHING_LEFT = (
    "✅ Nothing left to answer. If a lore upload finds more, it shows in ❓ Questions for you."
)
NOT_AVAILABLE = (
    "Remembering names isn't switched on for this DMbot yet. Ask whoever runs DMbot to turn it on."
)
QUOTE_SHOWN = 200  # Discord: the quote stays well inside one message


def _a(word: str) -> str:
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def _shown(offer: Offer) -> tuple[str, str, str]:
    """The character, the trait and what DMbot has now, safe for Discord."""
    who, what = _md(offer.entity_name), _md(offer.trait.name.lower())
    if offer.trait.kind == SPECIES:
        now = _md(offer.current.lower())
    else:
        now = ", ".join(_md(c.lower()) for c in offer.classes)
    return who, what, now


def card_text(offer: Offer, waiting: int) -> str:
    """The question first, in one plain line, then where the lore said it, why DMbot asks and
    what each button does. Never posted in a shared channel."""
    count = f" ({waiting} waiting)" if waiting > 1 else ""
    who, what, now = _shown(offer)
    if offer.trait.kind == SPECIES:
        has = (
            f"DMbot has {who} as **{_a(now)}**." if now else f"DMbot has no lineage for {who} yet."
        )
        lines = [f"{HEADING}{count}", f"Make **{who}** {_a(f'**{what}**')}? {has}"]
        if now:
            yes = f"{who} becomes {_a(what)} instead of {_a(now)}."
            no = f"{who} stays {now}"
        else:
            yes, no = f"{who} becomes {_a(what)}.", "nothing changes"
    else:
        has = f"DMbot has {who} as **{now}**." if now else f"DMbot has no class for {who} yet."
        lines = [f"{HEADING}{count}", f"Add **{what}** to **{who}**'s classes? {has}"]
        yes = f"{what} is added to {who}'s classes" + ("; the ones they have stay." if now else ".")
        no = "nothing changes"
    source = f'Your lore "{_md(offer.upload)}" says so' if offer.upload else "Your lore says so"
    if offer.quote:
        quote = " ".join(offer.quote.split())
        if len(quote) > QUOTE_SHOWN:
            quote = quote[: QUOTE_SHOWN - 1].rstrip() + "…"
        lines += [f"{source}:", f'> "{_md(quote)}"']
    else:
        lines.append(f"{source}.")
    lines += [
        f"_{WHY}_",
        "",
        f"_{YES}: {yes} {NO}: {no}, and DMbot won't ask this again._",
    ]
    return "\n".join(lines)


def kept_text(offer: Offer) -> str:
    who, what, _now = _shown(offer)
    if offer.trait.kind == SPECIES:
        return f"Done: **{who}** is now {_a(f'**{what}**')}. ↩️ Undo takes it back."
    return f"Done: **{what}** added to **{who}**. ↩️ Undo takes it back."


def left_text(offer: Offer) -> str:
    who, what, _now = _shown(offer)
    return (
        f"Left as it was. DMbot won't ask about **{who}** and **{what}** again. "
        f"To change it later, open {who}'s name card."
    )


def undone_text(offer: Offer) -> str:
    who, _what, now = _shown(offer)
    back = (
        f"**{who}** is {_a(f'**{now}**')} again." if offer.trait.kind == SPECIES and now
        else "it is taken back."
    )  # fmt: skip
    return f"Undone: {back} The question is back, so you can answer it again."


def could_not_text(offer: Offer) -> str:
    who, _what, _now = _shown(offer)
    return (
        f"DMbot couldn't change **{who}**: they may have been joined with another name or "
        "removed. DMbot won't ask this one again. Showing what is left."
    )


class TraitQuestions(_Menu):
    """One question at a time: Keep it, Not this, and Undo right after a Keep."""

    def __init__(self, campaign_id: str, guild_id: int) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.guild_id = guild_id  # from the campaign: a DM's private chat has no server (#3210)
        self.current: Offer | None = None
        self.undo_batch: int | None = None  # the last Yes's change, while Undo is offered
        self.kept: Offer | None = None  # the question that Yes answered, to bring back on Undo

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
        self.add_item(_Button(self._keep, label=YES, style=discord.ButtonStyle.success))
        self.add_item(_Button(self._not_this, label=NO, style=discord.ButtonStyle.danger))
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
            if what == "undo" and self.undo_batch is not None and self.kept is not None:
                await memory.undo(self.guild_id, self.campaign_id, self.undo_batch)
                said = undone_text(self.kept)
                # the question comes back, so a Yes pressed by mistake can still be a No
                await store.offer(self.guild_id, self.campaign_id, [self.kept])
                self.undo_batch = self.kept = None
            elif what == "keep" and offer is not None:
                self.undo_batch = await store.keep(memory, self.guild_id, self.campaign_id, offer)
                self.kept, said = offer, kept_text(offer)
            elif what == "never" and offer is not None:
                self.undo_batch = self.kept = None
                if await store.never(self.guild_id, self.campaign_id, offer):
                    said = left_text(offer)
        except (TooLateToUndo, NotWaiting):
            self.undo_batch = self.kept = None  # (said stays GONE_NOW)
        except MemoryRuleError:
            self.undo_batch = self.kept = None
            if what == "keep" and offer is not None:
                # refused for good (the character is no longer one): the store remembers it
                said = could_not_text(offer)
        shown = await self._load(interaction)
        if shown is None:
            if self.undo_batch is None:
                self.stop()
            await _replace(
                interaction,
                f"{said}\n\n{NOTHING_LEFT}",
                self if self.undo_batch is not None else None,
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
