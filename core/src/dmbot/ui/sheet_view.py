"""Show my sheet (#1126; dmbot.memory.sheet_view): the player's private view of their own
character sheet, one button per section, and the edit forms for a sheet they typed in or gave
as a document.

- The basic line is on top; pressing a section opens it (one at a time, so the message stays
  short).
- A typed or document sheet has **Edit this part** on the open section: a form with that
  section's fields, checked like an import, with a card that says what changed.
- A D&D Beyond sheet has no Edit anywhere: D&D Beyond is the record, so there is an **Open on
  D&D Beyond** link button to the player's own sheet instead.
- Only the person who plays the character, while they still play it and are still in the
  server (the #723 rules): every press looks that up again, and the save is refused for anyone
  else.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import discord

from dmbot.memory import sheet_view
from dmbot.memory.sheet_store import CharacterSheet, PlayerCharacter, SheetRefused
from dmbot.ui import sheets as ui
from dmbot.ui.dmbot_commands import (
    NO_PINGS,
    VIEW_TIMEOUT_S,
    _answer_first,
    _Button,
    _failed,
    _Menu,
    _replace,
    _send,
    _tell,
)

log = logging.getLogger(__name__)

SHOW_LABEL = "Show my sheet"
EDIT_LABEL = "Edit this part"
DDB_LABEL = "Open on D&D Beyond"
NO_SHEET = "DMbot has no sheet for this character yet. Link or type it in first."
SAVED = "✏️ Saved. Changed: {what}."
SAME = "Nothing changed."
DDB_NOTE = "This sheet comes from D&D Beyond, so change it there. DMbot reads it again later."


def view_text(character: PlayerCharacter, sheet: CharacterSheet, open_key: str | None) -> str:
    snapshot = sheet.sheet or {}
    lines = [
        f"📜 **{ui._md(character.name)}**: {ui._md(sheet_view.basic_line(snapshot))}",
    ]
    section = sheet_view.BY_KEY.get(open_key or "")
    if section is not None:
        lines += [f"**{section.label}**", ui._md(section.show(snapshot))]
    else:
        lines.append("Press a button to see that part of your sheet.")
    if sheet.url is not None:
        lines.append(DDB_NOTE)
    lines.append(ui.ONLY_YOU)
    return "\n".join(lines)


class SheetView(_Menu):
    def __init__(
        self,
        guild_id: int,
        character: PlayerCharacter,
        sheet: CharacterSheet,
        open_key: str | None = None,
    ) -> None:
        super().__init__()
        self.guild_id, self.character, self.open_key = guild_id, character, open_key
        for i, section in enumerate(sheet_view.SECTIONS):
            self.add_item(
                _Button(
                    self._opener(section.key),
                    label=section.label,
                    style=discord.ButtonStyle.primary
                    if section.key == open_key
                    else discord.ButtonStyle.secondary,
                    row=i // 5,
                )
            )
        if sheet.url is not None:  # D&D Beyond is the record: a link, never an Edit
            self.add_item(
                discord.ui.Button(
                    label=DDB_LABEL, style=discord.ButtonStyle.link, url=sheet.url, row=2
                )
            )
        elif open_key in sheet_view.BY_KEY:
            self.add_item(
                _Button(self._edit, label=EDIT_LABEL, style=discord.ButtonStyle.success, row=2)
            )

    def _opener(self, key: str) -> Any:
        async def press(interaction: discord.Interaction) -> None:
            await self.open(interaction, key)

        return press

    async def open(self, interaction: discord.Interaction, key: str) -> None:
        self.stop()
        sheet = await mine(interaction, self.guild_id, self.character)
        if sheet is None:
            return
        await _replace(
            interaction,
            view_text(self.character, sheet, key),
            SheetView(self.guild_id, self.character, sheet, key),
        )

    async def _edit(self, interaction: discord.Interaction) -> None:
        sheet = await mine(interaction, self.guild_id, self.character)
        if sheet is None or sheet.url is not None or self.open_key not in sheet_view.BY_KEY:
            return
        section = sheet_view.BY_KEY[self.open_key]
        self.origin = None
        await interaction.response.send_modal(
            SectionForm(self.guild_id, self.character, section, sheet.sheet or {})
        )


async def mine(
    interaction: discord.Interaction, guild_id: int, character: PlayerCharacter
) -> CharacterSheet | None:
    """The sheet, if this person still plays the character and has one; else they are told
    why. Looked up again on every press: the character may have been given to someone else."""
    store = ui._store(interaction)
    assert store is not None
    found = await store.characters_of(guild_id, interaction.user.id, character.campaign_id)
    if not any(c.entity_id == character.entity_id for c in found):
        await _tell(interaction, ui.NOT_YOURS)
        return None
    sheet = await store.sheet(
        guild_id, character.campaign_id, character.entity_id, player=interaction.user.id
    )
    if sheet is None or sheet.sheet is None:
        await _tell(interaction, NO_SHEET)
        return None
    return sheet


async def show_my_sheet(
    interaction: discord.Interaction, guild_id: int, character: PlayerCharacter
) -> None:
    await _answer_first(interaction)
    sheet = await mine(interaction, guild_id, character)
    if sheet is None:
        return
    await _send(
        interaction, view_text(character, sheet, None), SheetView(guild_id, character, sheet)
    )


class SectionForm(discord.ui.Modal):
    def __init__(
        self,
        guild_id: int,
        character: PlayerCharacter,
        section: sheet_view.Section,
        snapshot: dict[str, Any],
    ) -> None:
        super().__init__(title=f"Edit: {section.label}"[:45], timeout=VIEW_TIMEOUT_S)
        self.guild_id, self.character, self.section = guild_id, character, section
        now = section.fill(snapshot)
        self._inputs: dict[str, discord.ui.TextInput[SectionForm]] = {}
        for field in section.fields:
            box: discord.ui.TextInput[SectionForm] = discord.ui.TextInput(
                label=field.label[:45],
                placeholder=field.placeholder[:100] or None,
                default=now.get(field.key, "")[: sheet_view.FIELD_MAX] or None,
                style=discord.TextStyle.paragraph if field.long else discord.TextStyle.short,
                required=False,
                max_length=sheet_view.FIELD_MAX,
            )
            self._inputs[field.key] = box
            self.add_item(box)

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {key: box.value for key, box in self._inputs.items()}
        await _answer_first(interaction)
        if not await ui._still_member(interaction, self.guild_id):
            return
        sheet = await mine(interaction, self.guild_id, self.character)
        if sheet is None:
            return
        if sheet.url is not None:  # became a D&D Beyond sheet meanwhile
            await _tell(interaction, DDB_NOTE)
            return
        old = sheet.sheet or {}
        new, problem = sheet_view.edit(old, self.section, values)
        if new is None:
            await _tell(interaction, problem or "That didn't save.")
            return
        changed = sheet_view.changed_sections(old, new)
        if not changed:
            await _tell(interaction, SAME)
            return
        store = ui._store(interaction)
        assert store is not None
        c = self.character
        try:
            await store.save(
                self.guild_id,
                c.campaign_id,
                c.entity_id,
                new,
                int(time.time()),
                player=interaction.user.id,
            )
        except SheetRefused:
            await _tell(interaction, ui.NOT_YOURS)
            return
        ui._bot(interaction).sheets_changed(self.guild_id, c.campaign_id)
        log.info("A sheet part was edited: guild=%s campaign=%s", self.guild_id, c.campaign_id)
        card = await ui.status_card(ui._bot(interaction), self.guild_id, c.campaign_id, new)
        said = SAVED.format(what=", ".join(changed))
        await interaction.followup.send(
            f"{said}\n{card}", ephemeral=True, allowed_mentions=NO_PINGS
        )
