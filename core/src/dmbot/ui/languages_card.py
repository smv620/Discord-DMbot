"""The languages a character knows, on the DM's name card (#4229 slice 1b): one line that says
where they come from, and a small screen to add, check or take away the ones the story gives a
character with no sheet. A character with a sheet keeps its languages on the sheet (the owner's
ruling on #4229), so its card only shows them. For the campaign's DMs only, asked again on every
press. Nothing here goes to players."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord

from dmbot.campaigns import Campaign
from dmbot.memory.models import DM, STORY_LANGUAGE_MAX, Known, MemoryRuleError
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _Button,
    _failed,
    _Menu,
    _replace,
    _Select,
    _tell,
)
from dmbot.ui.names import GONE, _campaign_for, _md, _memory, changed, sees_secrets

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)

BUTTON = "🗣️ Languages"
ADD_BUTTON = "➕ Add a language"
CHECKED_BUTTON = "✅ These are right"
BACK_BUTTON = "↩ Back"
ONLY_DM = "Only the DM can change a character's languages."
HOLDER = "Take one away"
HAS_SHEET = (
    "This character has a sheet, so its languages are on the sheet. Change them there and "
    "DMbot reads them from it."
)
LINE_MAX = 300  # the card's line, so a long list never pushes the rest of the card out


def languages_line(known: Sequence[Known]) -> str:
    """The card's line, or "" when DMbot knows none: "🗣️ Languages: Common, Dwarvish (from the
    sheet)", or the story's, each one DMbot read and the DM hasn't checked marked so."""
    if not known:
        return ""
    if all(k.source == "sheet" for k in known):
        words = ", ".join(_md(k.name) for k in known)
        line = f"\n🗣️ **Languages:** {words} (from the sheet)"
    else:
        words = ", ".join(
            _md(k.name) + (" (DMbot read)" if k.source == "read" else "") for k in known
        )
        line = f"\n🗣️ **Languages:** {words}"
    return line if len(line) <= LINE_MAX else line[: LINE_MAX - 1] + "…"


def screen_text(name: str, known: Sequence[Known]) -> str:
    said = ", ".join(_md(k.name) + (" (DMbot read)" if k.source == "read" else "") for k in known)
    now = f" Now: {said}." if said else " None yet."
    unchecked = (
        f" Tap {CHECKED_BUTTON} if the ones DMbot read are right."
        if any(k.source == "read" for k in known)
        else ""
    )
    return (
        f"🗣️ **What languages does {_md(name)} know?**{now}{unchecked}\n"
        "DMbot uses them to tell you who at the table understands something. Only you see them."
    )


async def show_languages(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, note: str = ""
) -> None:
    """Replace the card with the languages screen."""
    await _answer_first(interaction, in_place=True)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DM)
        return
    entity = await memory.entity(campaign.guild_id, campaign.id, entity_id)
    if entity is None:
        await _replace(interaction, GONE, None)
        return
    known = (await memory.languages_known(campaign.guild_id, campaign.id, [entity_id])).get(
        entity_id, []
    )
    if any(k.source == "sheet" for k in known):
        await _replace(
            interaction, HAS_SHEET, LanguagesMenu(campaign_id, entity_id, [], [], can_add=False)
        )
        return
    rows = await memory.story_languages(campaign.guild_id, campaign.id, entity_id)
    text = screen_text(entity.name, known)
    await _replace(
        interaction,
        f"{note}\n\n{text}" if note else text,
        LanguagesMenu(
            campaign_id,
            entity_id,
            [(r.id, r.name) for r in rows],
            [r.id for r in rows if r.given_by == "read"],
        ),
    )


class LanguagesMenu(_Menu):
    """Take one away (a menu of the story's), add one (a form), or check the ones DMbot read. A
    character with a sheet gets only ↩ Back (`rows` empty, nothing to add here)."""

    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        rows: list[tuple[str, str]],
        unchecked: list[str],
        *,
        can_add: bool = True,
    ) -> None:
        super().__init__()
        self.campaign_id, self.entity_id = campaign_id, entity_id
        self.unchecked = unchecked
        if rows:
            self.pick = _Select(
                self._remove,
                placeholder=HOLDER,
                options=[
                    discord.SelectOption(label=name[:100], value=row_id)
                    for row_id, name in rows[:25]
                ],
            )
            self.add_item(self.pick)
        if can_add:
            self.add_item(_Button(self._add, label=ADD_BUTTON, row=1))
        if unchecked:
            self.add_item(
                _Button(
                    self._checked,
                    label=CHECKED_BUTTON,
                    style=discord.ButtonStyle.success,
                    row=1,
                )
            )
        self.add_item(_Button(self._back, label=BACK_BUTTON, row=1))

    async def _campaign(
        self, interaction: discord.Interaction
    ) -> tuple[Campaign, MemoryStore] | None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return None
        if not sees_secrets(campaign, interaction.user.id):  # asked again on every press
            await _tell(interaction, ONLY_DM)
            return None
        return campaign, memory

    async def _remove(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await self._campaign(interaction)
        if found is None:
            return
        campaign, memory = found
        try:
            await memory.remove_story_language(
                campaign.guild_id,
                campaign.id,
                self.pick.values[0],
                source=DM,
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't take it away. {exc} Tap {BACK_BUTTON} to see it.")
            return
        changed(interaction, campaign)
        self.stop()
        await show_languages(interaction, self.campaign_id, self.entity_id, "🗣️ Taken away.")

    async def _add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AddLanguageForm(self.campaign_id, self.entity_id))

    async def _checked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await self._campaign(interaction)
        if found is None:
            return
        campaign, memory = found
        rows = await memory.story_languages(
            campaign.guild_id,
            campaign.id,
            self.entity_id,
        )
        try:
            for row in rows:
                if row.id in self.unchecked and row.given_by == "read":
                    # the DM's word on one DMbot read checks it (one change for each)
                    await memory.add_story_language(
                        campaign.guild_id,
                        campaign.id,
                        self.entity_id,
                        row.name,
                        source=DM,
                    )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't save it. {exc} Tap {BACK_BUTTON} to see it.")
            return
        changed(interaction, campaign)
        self.stop()
        await show_languages(interaction, self.campaign_id, self.entity_id, "🗣️ Checked.")

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class AddLanguageForm(discord.ui.Modal, title="Add a language"):
    """One language the story says this character knows, in the DM's own words: one of the SRD's
    ("Elvish") or the campaign's own tongue."""

    name: discord.ui.TextInput[AddLanguageForm] = discord.ui.TextInput(
        label="Language",
        placeholder="Example: Elvish, or the old tongue of Netheril",
        max_length=STORY_LANGUAGE_MAX,
    )

    def __init__(self, campaign_id: str, entity_id: str) -> None:
        super().__init__()
        self.campaign_id, self.entity_id = campaign_id, entity_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):  # after the wait, as on a press
            await _tell(interaction, ONLY_DM)
            return
        try:
            written = await memory.add_story_language(
                campaign.guild_id, campaign.id, self.entity_id, self.name.value, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't add it. {exc} Try again.")
            return
        changed(interaction, campaign)
        note = (
            f"🗣️ Added: **{_md(written.value.name)}**."
            if written.value is not None
            else "🗣️ Not added: this character has a sheet."
        )
        await show_languages(interaction, self.campaign_id, self.entity_id, note)
