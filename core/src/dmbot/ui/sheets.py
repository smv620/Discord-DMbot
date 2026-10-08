"""Players' D&D Beyond sheets in Discord (#723, part A second half; dmbot.memory.sheets).

A player presses 📜 My character sheet (on their consent confirmation, or on a session's
reminder) and gets a private panel for their character: link a D&D Beyond sheet, tell
DMbot about the character instead, or unlink. The campaign's DMs see the sheet on the
character's name card, and can refresh every sheet from the names panel. The link
itself is shown only to the player and the campaign's DMs.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, cast

import discord

from dmbot.consent_dm import SHEET_LABEL
from dmbot.logs import log_context
from dmbot.memory import sheets
from dmbot.memory.sheet_store import CharacterSheet, PlayerCharacter, SheetRefused, SheetStore
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _Button,
    _failed,
    _Menu,
    _replace,
    _Select,
    _send,
    _tell,
)

if TYPE_CHECKING:
    from dmbot.bot import DMBot

log = logging.getLogger(__name__)

BUTTON_LABEL = SHEET_LABEL  # the same button, built where the consent messages are
LINK_LABEL = "Link my D&D Beyond sheet"
TELL_LABEL = "Tell DMbot about my character"
UNLINK_LABEL = "Unlink"
NO_CHARACTER = (
    "DMbot doesn't know your character in this server yet. Ask your DM to add it: "
    "`/dmbot names`, then **🧑 Add a player's character**."
)
NOT_AVAILABLE = "Character sheets aren't available on this DMbot right now."
PICK = "Which character?"
ONLY_YOU = "Only you and the campaign's DMs see the link."
WAIT = "You just did that. Try again in a few seconds."
READING = "Reading the sheet…"
UNLINKED = "Done. DMbot forgot **{name}**'s sheet."
TYPED_SAVED = (
    "✅ Saved. DMbot will listen for those names. Linking a D&D Beyond sheet later "
    "replaces what you typed."
)
LINKED_READ = (
    "✅ Linked. DMbot read **{name}**'s sheet{who}. It listens for the spell, feature and "
    "item names on it, and reads the sheet again when each session starts."
)
LINKED_UNREAD = (
    "Linked, but DMbot couldn't reach D&D Beyond just now. It tries again when the next "
    "session starts."
)
REFRESH_WAIT = "The sheets were read less than a minute ago. Try again in a moment."
NO_SHEETS = "No player has linked a sheet in this campaign yet."
LINK_COOLDOWN_S = 20  # per person: each Link reads D&D Beyond once
REFRESH_COOLDOWN_S = 60  # per campaign: Refresh sheets (#723)
_GUILD = r"(?P<guild>[0-9]{1,20})"
_CAMPAIGN = r"(?P<campaign>[0-9a-f]{32}|-)"

_last_link: dict[tuple[int, int], float] = {}
_last_refresh: dict[tuple[int, str], float] = {}


def _bot(interaction: discord.Interaction) -> DMBot:
    return cast("DMBot", interaction.client)


def _store(interaction: discord.Interaction) -> SheetStore | None:
    return _bot(interaction).sheets


def _md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _too_soon(last: dict[Any, float], key: Any, gap: float) -> bool:
    now = time.monotonic()
    if now - last.get(key, -gap) < gap:
        return True
    last[key] = now
    return False


# ---- words -------------------------------------------------------------------------


def panel_text(character: PlayerCharacter, sheet: CharacterSheet | None, *, link: bool) -> str:
    """The player's panel (or the DM's card line, with `link` for who may see the
    address)."""
    head = f"📜 **{_md(character.name)}** ({_md(character.campaign_name)})"
    return f"{head}: {sheet_status(sheet, link=link)}\n{ONLY_YOU}"


def sheet_status(sheet: CharacterSheet | None, *, link: bool) -> str:
    """ "no sheet yet" / "linked to D&D Beyond, read 2 days ago · Elf · Bard 3" / "typed in"."""
    if sheet is None:
        return (
            "no sheet yet. Link your D&D Beyond sheet so DMbot listens for your spell and "
            "feature names. No D&D Beyond? Tell DMbot about your character instead."
        )
    who = sheets.who(sheet.sheet) if sheet.sheet else ""
    who = f" · {_md(who)}" if who else ""
    if sheet.url is None:
        return f"typed in{who}."
    where = f"[D&D Beyond](<{sheet.url}>)" if link else "D&D Beyond"
    if sheet.sheet is None or sheet.fetched_at is None:
        return f"linked to {where}, not read yet (DMbot reads it when a session starts)."
    return f"linked to {where}, read <t:{sheet.fetched_at}:R>{who}."


def card_line(sheet: CharacterSheet | None, *, link: bool) -> str | None:
    """The line on the DM's name card for a player's character, or None (no sheet)."""
    if sheet is None:
        return None
    return f"**📜 Sheet:** {sheet_status(sheet, link=link)}"


# ---- reading a sheet now ------------------------------------------------------------


async def link_and_read(
    bot: DMBot, guild_id: int, campaign_id: str, entity_id: str, character: int
) -> str:
    """Link the sheet and read it once, now. The words to show whoever linked it."""
    store = bot.sheets
    assert store is not None
    await store.link(guild_id, campaign_id, entity_id, character)
    url = sheets.sheet_url(character)
    with log_context(guild_id=guild_id, campaign_id=campaign_id):
        try:
            snapshot = await sheets.fetch(character)
        except sheets.SheetError as exc:
            log.info("A new sheet link for entry %s couldn't be read yet", entity_id)
            return sheets.NOT_PUBLIC if exc.refused else LINKED_UNREAD
        saved = await store.save(
            guild_id, campaign_id, entity_id, snapshot, int(time.time()), url=url
        )
    bot.sheets_changed(guild_id, campaign_id)
    if not saved:  # unlinked or relinked while it was read
        return LINKED_UNREAD
    who = sheets.who(snapshot)
    return LINKED_READ.format(name=_md(snapshot["name"]), who=f" ({_md(who)})" if who else "")


# ---- the player's button and panel --------------------------------------------------


class MySheetButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:sheet:{_GUILD}:{_CAMPAIGN}",
):
    """📜 My character sheet: on the consent confirmation (any campaign of the server,
    `campaign` "-") and on a session's reminder (that campaign). Kept over a restart."""

    def __init__(self, guild_id: int, campaign_id: str | None = None) -> None:
        self.guild_id, self.campaign_id = guild_id, campaign_id
        super().__init__(
            discord.ui.Button(
                label=BUTTON_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:sheet:{guild_id}:{campaign_id or '-'}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: Any
    ) -> MySheetButton:
        campaign = match["campaign"]
        return cls(int(match["guild"]), None if campaign == "-" else campaign)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await self._run(interaction)
        except Exception as exc:
            await _failed(interaction, exc)

    async def _run(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)
        store = _store(interaction)
        if store is None:
            await _tell(interaction, NOT_AVAILABLE)
            return
        found = await store.characters_of(self.guild_id, interaction.user.id, self.campaign_id)
        if not found:
            await _tell(interaction, NO_CHARACTER)
            return
        if len(found) == 1:
            await show_panel(interaction, self.guild_id, found[0])
            return
        await _send(interaction, PICK, CharacterChoice(self.guild_id, found))


def my_sheet_view(guild_id: int, campaign_id: str | None = None) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(MySheetButton(guild_id, campaign_id))
    return view


class CharacterChoice(_Menu):
    def __init__(self, guild_id: int, found: list[PlayerCharacter]) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.found = {f"{c.campaign_id}:{c.entity_id}": c for c in found}
        self.pick = _Select(
            self._picked,
            placeholder=PICK,
            options=[
                discord.SelectOption(
                    label=logic.shorten(c.name, logic.OPTION_LABEL_MAX),
                    description=logic.shorten(c.campaign_name, logic.OPTION_LABEL_MAX),
                    value=key,
                )
                for key, c in list(self.found.items())[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_panel(interaction, self.guild_id, self.found[self.pick.values[0]], replace=True)


async def show_panel(
    interaction: discord.Interaction,
    guild_id: int,
    character: PlayerCharacter,
    *,
    replace: bool = False,
    note: str | None = None,
) -> None:
    store = _store(interaction)
    assert store is not None
    await _answer_first(interaction, in_place=replace)
    sheet = await store.sheet(guild_id, character.campaign_id, character.entity_id)
    text = panel_text(character, sheet, link=True)
    if note:
        text = f"{note}\n\n{text}"
    view = SheetPanel(guild_id, character, linked=sheet is not None)
    if replace:
        await _replace(interaction, text, view)
    else:
        await _send(interaction, text, view)


class SheetPanel(_Menu):
    def __init__(self, guild_id: int, character: PlayerCharacter, *, linked: bool) -> None:
        super().__init__()
        self.guild_id, self.character = guild_id, character
        self.add_item(_Button(self._link, label=LINK_LABEL, style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._typed, label=TELL_LABEL, style=discord.ButtonStyle.secondary))
        if linked:
            self.add_item(
                _Button(self._unlink, label=UNLINK_LABEL, style=discord.ButtonStyle.secondary)
            )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        await _failed(interaction, error)

    async def _link(self, interaction: discord.Interaction) -> None:
        self.origin = None  # the form's answer is a new message; this one stays
        await interaction.response.send_modal(LinkForm(self.guild_id, self.character))

    async def _typed(self, interaction: discord.Interaction) -> None:
        self.origin = None
        await interaction.response.send_modal(TypedForm(self.guild_id, self.character))

    async def _unlink(self, interaction: discord.Interaction) -> None:
        store = _store(interaction)
        assert store is not None
        self.stop()
        await _answer_first(interaction, in_place=True)
        await store.unlink(self.guild_id, self.character.campaign_id, self.character.entity_id)
        _bot(interaction).sheets_changed(self.guild_id, self.character.campaign_id)
        note = UNLINKED.format(name=_md(self.character.name))
        await show_panel(interaction, self.guild_id, self.character, replace=True, note=note)


class LinkForm(discord.ui.Modal, title="Link your D&D Beyond sheet"):
    link: discord.ui.TextInput[LinkForm] = discord.ui.TextInput(
        label="Your character's D&D Beyond link",
        placeholder="https://www.dndbeyond.com/characters/12345678",
        max_length=200,
    )

    def __init__(self, guild_id: int, character: PlayerCharacter) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.guild_id, self.character = guild_id, character

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        number = sheets.character_id(self.link.value)
        if number is None:
            await _tell(interaction, sheets.NOT_A_LINK)
            return
        if _too_soon(_last_link, (self.guild_id, interaction.user.id), LINK_COOLDOWN_S):
            await _tell(interaction, WAIT)
            return
        await _answer_first(interaction)
        c = self.character
        try:
            said = await link_and_read(
                _bot(interaction), self.guild_id, c.campaign_id, c.entity_id, number
            )
        except SheetRefused as exc:
            await _tell(interaction, str(exc))
            return
        await show_panel(interaction, self.guild_id, c, note=said)


class TypedForm(discord.ui.Modal, title="Tell DMbot about your character"):
    class_name: discord.ui.TextInput[TypedForm] = discord.ui.TextInput(
        label="Class", placeholder="For example: Wizard", max_length=sheets.NAME_MAX
    )
    level: discord.ui.TextInput[TypedForm] = discord.ui.TextInput(
        label="Level", placeholder="1 to 20", max_length=2
    )
    species: discord.ui.TextInput[TypedForm] = discord.ui.TextInput(
        label="Species (optional)", placeholder="For example: Elf", required=False,
        max_length=sheets.NAME_MAX,
    )  # fmt: skip
    names: discord.ui.TextInput[TypedForm] = discord.ui.TextInput(
        label="Spells and features DMbot should know",
        placeholder="Separate with commas, up to 20. For example: Misty Step, Shield",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1500,
    )

    def __init__(self, guild_id: int, character: PlayerCharacter) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.guild_id, self.character = guild_id, character

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        level = int(self.level.value) if self.level.value.strip().isdigit() else 0
        if not 1 <= level <= sheets.LEVEL_MAX:
            await _tell(interaction, "The level is a number from 1 to 20.")
            return
        c = self.character
        snapshot = sheets.typed(
            c.name,
            species=self.species.value,
            class_name=self.class_name.value,
            level=level,
            names=[n.strip() for n in self.names.value.replace(";", ",").split(",")],
        )
        if snapshot is None:  # can't happen with a name, but never save a bad one
            await _tell(interaction, "DMbot couldn't keep that. Check the class name.")
            return
        store = _store(interaction)
        assert store is not None
        await _answer_first(interaction)
        try:
            await store.save(self.guild_id, c.campaign_id, c.entity_id, snapshot, int(time.time()))
        except SheetRefused as exc:
            await _tell(interaction, str(exc))
            return
        _bot(interaction).sheets_changed(self.guild_id, c.campaign_id)
        await show_panel(interaction, self.guild_id, c, note=TYPED_SAVED)


# ---- the DM's side ------------------------------------------------------------------


async def refresh_all(interaction: discord.Interaction, campaign_id: str, guild_id: int) -> None:
    """Refresh sheets (the names panel): read every linked sheet again, at most once a
    minute per campaign."""
    from dmbot.memory.sheet_refresh import refresh

    store = _store(interaction)
    if store is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    if _too_soon(_last_refresh, (guild_id, campaign_id), REFRESH_COOLDOWN_S):
        await _tell(interaction, REFRESH_WAIT)
        return
    await _answer_first(interaction)
    found = await store.sheets(guild_id, campaign_id)
    linked = [s for s in found if s.url is not None]
    if not linked:
        await _tell(interaction, NO_SHEETS)
        return
    now = int(time.time())
    with log_context(guild_id=guild_id, campaign_id=campaign_id):
        after = await refresh(store, guild_id, campaign_id, now, found=found)
    _bot(interaction).sheets_changed(guild_id, campaign_id)
    read = sum(
        1 for s in after if s.url is not None and s.fetched_at is not None and s.fetched_at >= now
    )
    stale = len(linked) - read
    more = (
        f" {stale} couldn't be read (not public, or D&D Beyond didn't answer); the last "
        "copy of those stays."
        if stale
        else ""
    )
    await _tell(interaction, f"📜 Read {read} of {len(linked)} sheets.{more}")


async def dm_unlink(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, entity_id: str
) -> bool:
    """The DM's Unlink on a character's card. False if it had no sheet."""
    store = _store(interaction)
    if store is None:
        return False
    gone = await store.unlink(guild_id, campaign_id, entity_id)
    if gone:
        _bot(interaction).sheets_changed(guild_id, campaign_id)
    return gone
