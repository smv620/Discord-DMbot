"""🐾 Travels with…, on a character's name card (#3505, #3176): the DM says that a familiar, a
hired guide or a rescued prisoner travels with one player character or with the party. The
Connect menu is full at Discord's 25 choices, so this is its own button. It only records what the
DM says: the store holds the rule (a companion is a character who is not a player character; the
other end is a player character or the party), and the DM is checked again on every step.
Private to the campaign's DMs; nothing here reads speech or shows a transcript line."""

from __future__ import annotations

import logging
from typing import Any

import discord

from dmbot.memory.link_words import connection_words
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, DM, PLAYER_CHARACTER, MemoryRuleError
from dmbot.memory.store import TRAVELS_WITH
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _Button,
    _Menu,
    _replace,
    _Select,
    _tell,
)
from dmbot.ui.names import GONE, _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

BUTTON = "🐾 Travels with…"  # on the name card
ONLY_DM = "Only the DM can say who travels with whom."
NOT_A_COMPANION = (
    "🐾 Travels with… is for characters who are not player characters, like a familiar or a "
    "hired guide. Pick a different name, or use 🧭 Connect to…."
)
NOBODY = "No player characters yet. Add one under Names, then try again."
PLACEHOLDER = "Pick a player or the party…"
PARTY_LABEL = "The party"
BACK = "◀ Back"
MENU_MAX = 25  # Discord: a menu holds 25 choices
LABEL_MAX = 100
END_HELP = "To change or remove this: 🧭 Connect to…, then ✏️ Edit a connection…"


async def _checked(interaction: discord.Interaction, campaign_id: str) -> tuple[Any, Any] | None:
    """The campaign and its memory, if this person is one of its DMs (asked on every step)."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return None
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return None
    return campaign, memory


def choices(lookup: CampaignLookup, own_id: str) -> list[tuple[str, str]]:
    """(id, name) of who it may travel with: the campaign's confirmed player characters by name,
    then the party if the campaign has one. The party is the group the sessions made (found by its
    source, the way the store's rule finds it); it is never made here."""
    found = [
        (e.id, e.name)
        for e in lookup.entities.values()
        if e.role == PLAYER_CHARACTER
        and e.status == CONFIRMED
        and not e.merged_into
        and e.id != own_id
    ]
    found.sort(key=lambda item: item[1].casefold())
    party = [
        (e.id, PARTY_LABEL)  # (whatever the DM renamed it to, it is "The party" here)
        for e in lookup.entities.values()
        if e.type == "faction" and e.party and e.status == CONFIRMED
        and not e.merged_into
    ]  # fmt: skip
    return [*found[: MENU_MAX - len(party[:1])], *party[:1]]


def saved_note(companion: str, other: str, party: bool) -> str:
    who = "the party" if party else _md(other)
    return f"🐾 Saved: **{_md(companion)}** {connection_words(TRAVELS_WITH)} **{who}**. {END_HELP}"


class TravelsWith(_Menu):
    """One menu of who the character may travel with, and Back."""

    def __init__(
        self, campaign_id: str, entity_id: str, name: str, options: list[tuple[str, str]]
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.name = name
        self.shown = dict(options)
        self.add_item(
            _Select(
                self._picked,
                placeholder=PLACEHOLDER,
                options=[
                    discord.SelectOption(label=label[:LABEL_MAX], value=key)
                    for key, label in options
                ],
                row=0,
            )
        )
        self.add_item(_Button(self._back, label=BACK, style=discord.ButtonStyle.secondary, row=1))

    async def _picked(self, interaction: discord.Interaction) -> None:
        data: Any = interaction.data or {}
        chosen = str((data.get("values") or [""])[0])
        await save(interaction, self.campaign_id, self.entity_id, chosen)

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


async def start(interaction: discord.Interaction, campaign_id: str, entity_id: str) -> bool:
    """The pick: a menu of who this character may travel with, in place of the card. True when
    the menu replaced the card; False when a refusal was told instead (the card stays as it was,
    so the caller must not stop its buttons)."""
    await _answer_first(interaction, in_place=True)  # the lookup can be slow (3-second limit)
    checked = await _checked(interaction, campaign_id)
    if checked is None:
        return False
    campaign, memory = checked
    entity = await memory.entity(campaign.guild_id, campaign.id, entity_id)
    if entity is None or entity.status != CONFIRMED:
        await _tell(interaction, GONE)
        return False
    if entity.type != "character" or entity.role == PLAYER_CHARACTER:
        await _tell(interaction, NOT_A_COMPANION)
        return False
    lookup = CampaignLookup.build(await memory.lookup_data(campaign.guild_id, campaign.id))
    options = choices(lookup, entity_id)
    if not options:
        await _tell(interaction, NOBODY)
        return False
    await _replace(
        interaction,
        f"**Who does {_md(entity.name)} travel with?** Pick a player character"
        + (" or the party." if any(label == PARTY_LABEL for _, label in options) else ".")
        + " DMbot only records what you say.",
        TravelsWith(campaign.id, entity_id, entity.name, options),
    )
    return True


async def save(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, other_id: str
) -> None:
    """Record it (the DM's word, so confirmed) and show the card again with what was saved."""
    from dmbot.ui.name_card import show_card

    await _answer_first(interaction, in_place=True)
    checked = await _checked(interaction, campaign_id)
    if checked is None:
        return
    campaign, memory = checked
    lookup = CampaignLookup.build(await memory.lookup_data(campaign.guild_id, campaign.id))
    allowed = dict(choices(lookup, entity_id))
    if other_id not in allowed:  # gone, merged or changed since the menu was drawn
        await _tell(interaction, GONE)
        return
    own = lookup.entities.get(entity_id)
    if own is None:
        await _tell(interaction, GONE)
        return
    other = lookup.entities[other_id]
    try:
        await memory.add_relation(
            campaign.guild_id,
            campaign.id,
            entity_id,
            TRAVELS_WITH,
            other_id,
            source=DM,
            confidence=1.0,
            status=CONFIRMED,
            by_hand=True,  # the DM's own word, so the newest thing the table knows (#3363)
        )
    except MemoryRuleError as exc:
        await _tell(interaction, f"Couldn't save that. {exc}")
        return
    changed(interaction, campaign)
    note = saved_note(own.name, other.name, other.party)
    await show_card(interaction, campaign.id, entity_id, replace=True, note=note)
