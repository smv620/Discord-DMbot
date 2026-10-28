"""The names list for people the story never named (#2790, slice 4b): the "Not named yet" group in
📚 Browse and **Give them a name**. Nothing here guesses: the DM types the name, and if it is one
DMbot already knows the DM is asked whether they are the same person (never joined on its own).
Only the campaign's DMs see the group (`Entity.unseen_by_players`)."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.memory import unnamed
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import DM, LIVE, MemoryRuleError, name_key
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _Button,
    _failed,
    _Menu,
    _replace,
    _tell,
)
from dmbot.ui.names import GONE, _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

NOT_NAMED = "not_named"  # the pseudo-kind of the group in the kind menu
NAME_LABEL = "Their name"
NAME_HELP = "What the story calls them, or what you know them as"
ALREADY_DONE = "That person has a name now, or is gone. Open 📚 Browse names to see the list."
FORM_TITLE = "Give them a name"


def unnamed_ids(names: CampaignLookup) -> list[str]:
    """The live people with no name, A to Z by the label shown for them."""
    ids = [e.id for e in names.entities.values() if e.unnamed and e.status in LIVE]
    return sorted(ids, key=lambda i: (name_key(shown(names, i)), i))


def shown(names: CampaignLookup, entity_id: str) -> str:
    """The label for display, built from the parts with the anchor's name as it is now."""
    entity = names.entities[entity_id]
    if not entity.unnamed or not entity.unnamed_what:
        return entity.name
    anchor = names.entities.get(entity.unnamed_anchor or "")
    label = unnamed.label(entity.unnamed_what, anchor.name if anchor else None)
    # one hanging off another unnamed person ("the king's sister"): that one's label, built the same
    if anchor is not None and anchor.unnamed and anchor.unnamed_what:
        label = unnamed.label(entity.unnamed_what, shown(names, anchor.id))
    return label


class GiveNameForm(discord.ui.Modal, title=FORM_TITLE):
    name: discord.ui.TextInput[GiveNameForm] = discord.ui.TextInput(
        label=NAME_LABEL, placeholder=NAME_HELP, min_length=1, max_length=100
    )

    def __init__(self, campaign_id: str, entity_id: str, label: str) -> None:
        super().__init__(title=logic.shorten(f"Name: {label}", 45), timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id
        self.entity_id = entity_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await give_name(
            interaction, self.campaign_id, self.entity_id, " ".join(self.name.value.split())
        )


async def give_name(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, name: str
) -> None:
    """Name a person the story never named: a new name becomes theirs; a name DMbot already knows
    asks first whether they are the same person."""
    await _answer_first(interaction, in_place=True)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id) or not name:
        await _tell(interaction, "Only a DM of this campaign can name them." if name else GONE)
        return
    entity = await memory.entity(campaign.guild_id, campaign.id, entity_id)
    if entity is None or not entity.unnamed or entity.status not in LIVE:
        await _tell(interaction, ALREADY_DONE)
        return
    cache = getattr(interaction.client, "lookup", None)
    others: dict[str, str] = {}
    if cache is not None:
        found = await cache.get(campaign.guild_id, campaign.id)
        for entry in found.exact(name):
            other = found.entities.get(entry.entity_id)
            if (
                other is not None
                and other.id != entity_id
                and not other.unnamed
                and not entry.person
            ):
                others[other.id] = other.name
    if len(others) > 1:
        await _tell(
            interaction,
            f"More than one entry is called **{_md(name)}**. Open the right one with 🔍 Find a "
            "name and use 🔗 Same as… on it.",
        )
        return
    label = entity.name
    if others:
        ((other_id, other_name),) = others.items()
        ask = JoinConfirm(campaign.id, entity_id, other_id, label, other_name)
        await _replace(
            interaction,
            f"**{_md(name)}** is already known as **{_md(other_name)}**. Is **{_md(label)}** "
            "the same person?",
            ask,
        )
        return
    try:
        written = await memory.rename_entity(
            campaign.guild_id, campaign.id, entity_id, name, source=DM
        )
    except MemoryRuleError as exc:
        await _tell(interaction, f"Couldn't name them. {exc}")
        return
    except Exception:
        log.exception("Naming an unnamed person failed")
        await _tell(interaction, "Something went wrong and nothing was changed.")
        return
    changed(interaction, campaign)
    await _replace(interaction, f"✏️ **{_md(label)}** is now **{_md(written.value.name)}**.", None)
    if written.batch is not None:
        # Its own message, so Undo outlives this one (and a restart).
        view = discord.ui.View(timeout=None)
        view.add_item(UndoNamingButton(campaign.id, entity_id, written.batch))
        await interaction.followup.send(
            "Wrong? Press **Undo** to make them not named yet again.",
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class JoinConfirm(_Menu):
    """They are, or they are not, the same person as a name DMbot knows."""

    def __init__(
        self, campaign_id: str, entity_id: str, other_id: str, label: str, other_name: str
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.other_id = other_id
        self.label = label
        self.other_name = other_name
        self.add_item(
            _Button(self._yes, label="Yes, the same person", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._no, label="No, different", style=discord.ButtonStyle.secondary))

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _replace(interaction, "OK, nothing was changed.", None)

    async def _yes(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import UndoButton, show_card

        self.stop()
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        await _replace(
            interaction, f"🔗 Joining **{_md(self.label)}** into **{_md(self.other_name)}**…", None
        )
        try:
            written = await memory.merge(
                campaign.guild_id,
                campaign.id,
                self.other_id,
                self.entity_id,
                source=DM,
                dm_said_same=True,
            )
        except MemoryRuleError as exc:
            await _replace(interaction, f"Couldn't join them. {exc} Nothing was changed.", None)
            return
        except Exception:
            log.exception("Joining an unnamed person failed")
            await _replace(interaction, "Something went wrong and nothing was changed.", None)
            return
        changed(interaction, campaign)
        note = f"🔗 Done: **{_md(self.label)}** is **{_md(self.other_name)}**."
        await show_card(interaction, campaign.id, self.other_id, replace=True, note=note)
        if written.batch is not None:
            view = discord.ui.View(timeout=None)
            view.add_item(UndoButton(campaign.id, self.entity_id, written.batch))
            await interaction.followup.send(
                f"Wrong? Press **Undo** to split **{_md(self.label)}** from "
                f"**{_md(self.other_name)}** again.",
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )


class UndoNamingButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:unname:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Undo giving a person a name: back to "not named yet". Keeps working after a restart."""

    PREFIX = "dmbot:unname"

    def __init__(self, campaign_id: str, entity_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{self.PREFIX}:{campaign_id}:{entity_id}:{batch}",
            )
        )
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.batch = batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoNamingButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        entity = await memory.entity(campaign.guild_id, campaign.id, self.entity_id)
        if entity is None or entity.unnamed:
            await _tell(interaction, "Nothing to undo: that was already undone or changed.")
            return
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch)
        except MemoryRuleError as exc:
            await _replace(interaction, f"Couldn't undo. {exc}", None)
            return
        changed(interaction, campaign)
        await _replace(interaction, "↩️ They are not named yet again.", None)
