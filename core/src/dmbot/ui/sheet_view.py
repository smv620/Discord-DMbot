"""Show my sheet (#1126; dmbot.memory.sheet_view): the player's private view of their own
character sheet, one button per section, and the edit forms for a sheet they typed in or gave
as a document.

- The basic line is on top; pressing a section opens it (one at a time, so the message stays
  short).
- A typed or document sheet has **Edit <part>** on the open section: a form with that
  section's fields, checked like an import, with a card that says what changed.
- A D&D Beyond sheet has no Edit anywhere: D&D Beyond is the record, so there is an **Open on
  D&D Beyond** link button to the player's own sheet instead.
- Only the person who plays the character, while they still play it and are still in the
  server (the #723 rules): every press looks that up again, and the save is refused for anyone
  else.
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import replace
from typing import Any

import discord

from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.memory import sheet_view
from dmbot.memory.sheet_store import CharacterSheet, PlayerCharacter, SheetRefused
from dmbot.mentions import NO_PINGS
from dmbot.ui import sheets as ui
from dmbot.ui.dmbot_commands import (
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

EDIT_LABEL = "Edit"  # the button says which part: "Edit Skills"
DDB_LABEL = "Open on D&D Beyond"
NO_SHEET = (
    "DMbot has no sheet for this character yet. Press **Link my D&D Beyond sheet** or **Type in "
    "my character** first."
)
SAVED = "✏️ Saved. Changed: {what}."
SAME = "Nothing changed."
CHANGED_SINCE = "Your sheet changed since you opened this. Press the part again to open it fresh."
ONLY_YOU = "Only you see this."  # a sheet-view line: the link line in ui.sheets is about links
MORE = "… and more"
DDB_NOTE = (
    "This sheet comes from D&D Beyond, so change it there. DMbot reads it again when a "
    "session starts."
)


def view_text(character: PlayerCharacter, sheet: CharacterSheet, open_key: str | None) -> str:
    """The message: always within Discord's limit, ending an overlong section with "and more"
    (a full list of names would otherwise be refused and the player told it broke)."""
    snapshot = sheet.sheet or {}
    head = f"📜 **{ui._md(character.name)}**: {ui._md(sheet_view.basic_line(snapshot))}"
    tail = [DDB_NOTE] if sheet.url is not None else []
    tail.append(ONLY_YOU)
    section = sheet_view.BY_KEY.get(open_key or "")
    if section is None:
        middle = [
            "Tap a part to see it."
            if sheet.url is not None
            else "Tap a part to see it. Tap Edit to change it."
        ]
        return fit_message("\n".join([head, *middle, *tail]))
    heading = f"**{section.label}**"
    room = MESSAGE_MAX - len("\n".join([head, heading, "", *tail])) - 1
    body = ui._md(section.show(snapshot))
    if len(body) > room:
        body = body[: max(room - len(MORE), 0)].rstrip("\\ ,\n") + MORE
    return fit_message("\n".join([head, heading, body, *tail]))


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
        self.snapshot: dict[str, Any] = (
            sheet.sheet or {}
        )  # fills the edit form (checked again on save)
        for i, section in enumerate(sheet_view.SECTIONS):
            self.add_item(
                _Button(
                    self._opener(section.key),
                    label=section.button,
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
                _Button(
                    self._edit,
                    label=f"{EDIT_LABEL} {sheet_view.BY_KEY[open_key].label}",
                    style=discord.ButtonStyle.success,
                    row=2,
                )
            )

    def _opener(self, key: str) -> Any:
        async def press(interaction: discord.Interaction) -> None:
            await self.open(interaction, key)

        return press

    async def open(self, interaction: discord.Interaction, key: str) -> None:
        await _answer_first(interaction, in_place=True)
        sheet = await mine(interaction, self.guild_id, self.character)
        if sheet is None:
            return  # the buttons stay: nothing here was replaced
        self.stop()
        await _replace(
            interaction,
            view_text(self.character, sheet, key),
            SheetView(self.guild_id, self.character, sheet, key),
        )

    async def redraw(self, sheet: CharacterSheet) -> None:
        """Show the saved values in the message the view is on, so it never shows the old ones
        beside a "Saved" (best effort: the message may be gone or too old to edit)."""
        origin = self.origin
        if origin is None:
            return
        with contextlib.suppress(discord.HTTPException):
            await origin.edit_original_response(
                content=view_text(self.character, sheet, self.open_key), view=self
            )

    async def _edit(self, interaction: discord.Interaction) -> None:
        # No lookup before the form (Discord gives 3 seconds): `on_submit` looks again.
        if self.open_key not in sheet_view.BY_KEY:
            return
        section = sheet_view.BY_KEY[self.open_key]
        await interaction.response.send_modal(
            SectionForm(self.guild_id, self.character, section, self.snapshot, view=self)
        )


async def mine(
    interaction: discord.Interaction, guild_id: int, character: PlayerCharacter
) -> CharacterSheet | None:
    """The sheet, if this person still plays the character and has one; else they are told
    why. Looked up again on every press: the character may have been given to someone else."""
    if not await ui._still_member(interaction, guild_id):
        return None
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
        view: SheetView | None = None,
    ) -> None:
        super().__init__(title=f"Edit: {section.label}"[:45], timeout=VIEW_TIMEOUT_S)
        self.guild_id, self.character, self.section = guild_id, character, section
        self._opened = sheet_view.owned(snapshot, section)  # what the boxes were filled from
        self._view = view
        now = section.fill(snapshot)
        # Names a box can't hold are kept here and added back on submit (never cut).
        self._kept: dict[str, list[str]] = {}
        self._inputs: dict[str, discord.ui.TextInput[SectionForm]] = {}
        for field in section.fields:
            shown, left_out = sheet_view.fit(now.get(field.key, ""))
            if left_out:
                self._kept[field.key] = left_out
            hidden = len(left_out)
            box: discord.ui.TextInput[SectionForm] = discord.ui.TextInput(
                label=field.label[:45],
                placeholder=(
                    f"{hidden} more names not shown here are kept"
                    if hidden
                    else field.placeholder[:100] or None
                ),
                default=shown or None,
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
        sheet = await mine(interaction, self.guild_id, self.character)
        if sheet is None:
            return
        if sheet.url is not None:  # became a D&D Beyond sheet meanwhile
            await _tell(interaction, DDB_NOTE)
            return
        old = sheet.sheet or {}
        if sheet_view.owned(old, self.section) != self._opened:
            await _tell(interaction, CHANGED_SINCE)  # a form filled long ago must not revert a save
            return
        new, problem = sheet_view.edit(old, self.section, values, kept=self._kept)
        if new is None:
            await _tell(
                interaction,
                ui._md(problem or "That didn't save. Check what you typed and try again."),
            )
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
        if self._view is not None:
            self._view.snapshot = new  # the next Edit on this message starts from the save
            await self._view.redraw(replace(sheet, sheet=new))
        ui._bot(interaction).sheets_changed(self.guild_id, c.campaign_id)
        log.info("A sheet part was edited: guild=%s campaign=%s", self.guild_id, c.campaign_id)
        card = await ui.status_card(ui._bot(interaction), self.guild_id, c.campaign_id, new)
        said = SAVED.format(what=", ".join(changed))
        await interaction.followup.send(
            f"{said}\n{card}", ephemeral=True, allowed_mentions=NO_PINGS
        )
