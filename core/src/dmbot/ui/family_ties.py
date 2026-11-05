"""👪 Family ties (#3463): a fact kept because a name in it could be two different entries ("Mira"
the ferrywoman or "Mira" the smuggler's daughter). DMbot only asks: the DM picks which one the name
means, says "None of these" (the fact is forgotten), or takes a pick back. Pressing a name records
the choice and nothing more; the fact joins the story only when the DM answers it in 🗣️ Check
what was said. The words match the website's "Which one?" card (`web-waiting.ts`), and the calls
are the ones the website queues (`claim-pick`, `claim-unpick`, `claim-reject`). Private to the
campaign's DMs; no transcript line is ever shown, only the claim's own statement."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.memory import tags
from dmbot.memory.claims import Claim, ClaimStore, WhichOne
from dmbot.memory.models import MemoryRuleError
from dmbot.ui.claims_review import statement
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _bot,
    _Button,
    _Menu,
    _replace,
    _Select,
    _send,
    _tell,
)
from dmbot.ui.names import _campaign_for, _md, _memory

log = logging.getLogger(__name__)

HEADING = "👪 **Which one?**"
NOTE = (
    "DMbot found a name that could mean two different entries. You choose which one. "
    "DMbot never chooses for you."
)
WHY = "DMbot isn't sure which one this name means, so it is asking you."
PICK_HELP = (
    "Picking only saves your choice. To add it to your story, answer it in 🗣️ Check what was said."
)
NEITHER = "None of these"
NEITHER_HELP = f"{NEITHER}: DMbot forgets this fact for good."
UNDO = "↩️ Undo my pick"
NO_NOTE = "No note"
PICKED = (
    "Choice saved. Now answer it in 🗣️ Check what was said to add it to your story. "
    "↩️ Undo my pick takes it back."
)
NEITHER_DONE = "Forgotten. DMbot won't ask about this fact again."
UNDONE = "Pick taken back. That fact is back on this card."
GONE_NOW = "Already answered. Showing what is left."
NOTHING_LEFT = (
    "✅ Nothing left to pick. If DMbot finds more after the next session, it will ask here."
)
ONLY_DMS = "Only a DM of this campaign can do that."
NOT_AVAILABLE = (
    "Remembering names isn't switched on for this DMbot yet. Ask whoever runs DMbot to turn it on."
)
SELECT_MAX = 25  # Discord: a menu holds 25 choices
LABEL_MAX = 100


def question(words: str) -> str:
    return f"Which {_md(words)} does this mean?"


def card_text(claim: Claim, choices: list[WhichOne], waiting: int, said: str = "") -> str:
    """The question card: what was said first, then why DMbot asks; the names to pick for are the
    menus below (their placeholders are the question), the help lines are last. The six parts of
    a question (PLAN, "Questions for you"): the question, the choices, why, where it was said (the
    claim's own statement is all that is kept here), what each answer does, the buttons."""
    count = f" ({waiting} waiting)" if waiting > 1 else ""
    lines = [f"{HEADING}{count}", statement(claim), f"_{WHY}_", ""]
    lines += [f"_{NOTE}_", f"_{PICK_HELP}_", f"_{NEITHER_HELP}_"]
    body = "\n".join(lines)
    return f"{said}\n\n{body}" if said else body


class FamilyTies(_Menu):
    """One kept fact at a time: a menu for each name to pick for, 'None of these', and Undo."""

    def __init__(self, campaign_id: str, guild_id: int) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.guild_id = guild_id  # from the campaign: a DM's private chat has no server (#3210)
        self.current: str | None = None  # the claim shown (kept in view across its two sides)
        # The claim whose pick can still be taken back: the last one picked (the website can undo
        # any kept claim; here Undo is offered right after the pick).
        self.undo_of: str | None = None

    async def _load(self, interaction: discord.Interaction) -> tuple[str, list[WhichOne]] | None:
        """The text and menus for the next kept fact, or None when none is left."""
        memory = _memory(interaction)
        if memory is None:
            return None
        claims = ClaimStore(memory)
        agreed = await _bot(interaction).consent.consenting(self.guild_id)
        kept = await claims.unplaced(self.guild_id, self.campaign_id, opted_in=agreed)
        # the one in hand first, then the oldest
        kept.sort(key=lambda c: c.id != self.current)
        for claim in kept:
            # (a menu needs two choices: a name with fewer is no question, and an empty menu would
            # make Discord refuse the whole card)
            choices = [
                c
                for c in await claims.which_one(self.guild_id, self.campaign_id, claim.id)
                if len(c.entries) >= 2
            ]
            if not choices:
                continue
            self.current = claim.id
            await self._draw(interaction, claim, choices)
            return card_text(claim, choices, len(kept)), choices
        self.current = None
        self.clear_items()
        return None

    async def _draw(
        self, interaction: discord.Interaction, claim: Claim, choices: list[WhichOne]
    ) -> None:
        """A menu for each name, then None of these (and Undo while a pick can be taken back)."""
        self.clear_items()
        memory = _memory(interaction)
        assert memory is not None
        ids = [o.id for c in choices for o in c.entries]
        ties = await memory.tag_ties(self.guild_id, self.campaign_id, ids)
        for row, choice in enumerate(choices):
            options = []
            for option in choice.entries[:SELECT_MAX]:
                entity = await memory.entity(self.guild_id, self.campaign_id, option.id)
                shown = (
                    tags.tag_for(entity, ties.get(option.id, ()), dm=True)
                    if entity is not None
                    else option.name
                )
                options.append(
                    discord.SelectOption(
                        label=shown[:LABEL_MAX] or option.name[:LABEL_MAX],
                        value=option.id,
                        description=(option.note or NO_NOTE)[:LABEL_MAX],
                    )
                )
            self.add_item(
                _Select(
                    self._pick(claim.id, choice.side),
                    placeholder=question(choice.words)[:LABEL_MAX],
                    options=options,
                    row=row,
                )
            )
        self.add_item(
            _Button(
                self._neither(claim.id),
                label=NEITHER,
                style=discord.ButtonStyle.danger,  # the one answer that cannot be taken back
                row=len(choices),
            )
        )
        if self.undo_of is not None:
            self.add_item(
                _Button(
                    self._undo(self.undo_of),
                    label=UNDO,
                    style=discord.ButtonStyle.secondary,
                    row=len(choices),
                )
            )

    def _pick(self, claim_id: str, side: str) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            data: Any = interaction.data or {}
            chosen = data.get("values") or [""]
            await self._decide(interaction, claim_id, "pick", side=side, entry=str(chosen[0]))

        return handler

    def _neither(self, claim_id: str) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            await self._decide(interaction, claim_id, "neither")

        return handler

    def _undo(self, claim_id: str) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            await self._decide(interaction, claim_id, "undo")

        return handler

    async def _decide(
        self,
        interaction: discord.Interaction,
        claim_id: str,
        what: str,
        *,
        side: str = "",
        entry: str = "",
    ) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _dm_campaign(interaction, self.campaign_id)  # asked again each press
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        self.guild_id = campaign.guild_id  # (the campaign may have moved to another server)
        claims = ClaimStore(memory)
        said = GONE_NOW
        try:
            if what == "pick":
                await claims.pick_one(self.guild_id, self.campaign_id, claim_id, side, entry)
                self.undo_of, said = claim_id, PICKED
            elif what == "neither":
                await claims.reject(self.guild_id, self.campaign_id, claim_id)
                self.undo_of, said = None, NEITHER_DONE
            elif await claims.unpick(self.guild_id, self.campaign_id, claim_id):
                self.undo_of, said, self.current = None, UNDONE, claim_id
            else:
                self.undo_of = None
        except MemoryRuleError:
            self.undo_of = None  # the question is not waiting any more: said stays GONE_NOW
        if what != "pick" or said != PICKED:
            self.current = None if what != "undo" else self.current
        shown = await self._load(interaction)
        if shown is None:
            self.stop()
            await _replace(interaction, f"{said}\n\n{NOTHING_LEFT}", None)
            return
        text, _choices = shown
        await _replace(interaction, f"{said}\n\n{text}", self)


class FamilyTiesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:family:ties:(?P<campaign>[0-9a-f]{32})",
):
    """👪 Family ties, from the Questions message; works after a restart (the campaign is in the
    button's ID). The DM check is `_campaign_for`'s."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="👪 Family ties",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:family:ties:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> FamilyTiesButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_family_ties(interaction, self.campaign_id)


async def _dm_campaign(interaction: discord.Interaction, campaign_id: str) -> Any:
    """The campaign, only for one of its DMs: a server manager who is not a DM may run the names
    screens but is not shown a story fact, and cannot pick or forget it (the Questions message's
    own rule)."""
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return None
    if interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, ONLY_DMS)
        return None
    return campaign


async def start_family_ties(interaction: discord.Interaction, campaign_id: str) -> None:
    # A new private message (the Questions message is never replaced), answered first.
    await interaction.response.defer(ephemeral=True, thinking=True)
    if _memory(interaction) is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None:
        return
    view = FamilyTies(campaign.id, campaign.guild_id)
    shown = await view._load(interaction)
    if shown is None:
        await _tell(interaction, NOTHING_LEFT)
        return
    await _send(interaction, shown[0], view)


__all__ = ["FamilyTies", "FamilyTiesButton", "card_text", "question", "start_family_ties"]
