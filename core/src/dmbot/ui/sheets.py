"""Players' D&D Beyond sheets in Discord (#723, part A second half; dmbot.memory.sheets).

A player presses 📜 My character sheet (on their consent confirmation, or on a session's
reminder) and gets a private panel for their character: link a D&D Beyond sheet, tell
DMbot about the character instead, or forget the sheet. The campaign's DMs see the sheet
on the character's name card, and can read every sheet again from the names panel. The
link itself is shown only to the player and the campaign's DMs.

The player's panel and forms act only while that person still plays the character and
is still in the server: a DM may give the character to someone else meanwhile.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import discord

from dmbot.consent_dm import SHEET_LABEL
from dmbot.logs import log_context
from dmbot.memory import sheet_checks, sheets
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
SHOW_LABEL = "Show my sheet"  # the player's view of it, a part at a time (#1126)
TELL_LABEL = "Type in my character"
DOC_LABEL = "Use a document link"  # any public web page or PDF link (#1125)
FILE_LABEL = "Upload my sheet file"  # a Word, PDF or text file, kept only as names and numbers
FORGET_LABEL = "Forget my sheet"
# The button says what it does, not what is set now (#1171): pressing "Turn off" turns off.
QUESTIONS_ON = "Turn sheet questions off"  # shown while DMbot may ask
QUESTIONS_OFF = "Turn sheet questions on"
QUESTIONS_NOW_ON = (
    "✅ Sheet questions are on. DMbot may privately ask if your sheet doesn't match what was said."
)
# The button says what it does (#1129): the spec's words, FightBot on / off.
FIGHTBOT_ON = "Turn FightBot off"  # shown while FightBot may remind this player
FIGHTBOT_OFF = "Turn FightBot on"
FIGHTBOT_NOW_ON = (
    "✅ FightBot is on. In a fight it may privately remind you of your own spells and features."
)
FIGHTBOT_NOW_OFF = "✅ FightBot is off for you."
QUESTIONS_NOW_OFF = "✅ Sheet questions are off."
NO_CHARACTER = (
    "DMbot doesn't know your character in this server yet. Ask your DM to add it: "
    "`/dmbot names`, then **🧑 Add a player's character**."
)
NO_CHARACTER_LINK = (
    "DMbot doesn't know your character yet. If you play on D&D Beyond, tap **Link my D&D "
    "Beyond sheet**. Otherwise ask your DM to add it."
)
NOTHING_ADDED = "\nNothing was added. Tap **Link my D&D Beyond sheet** to try again."
ADDED_FROM_SHEET = (
    "✅ Linked. DMbot added **{name}**{who} from your sheet. It will listen for your spells "
    "and items, and check your sheet again at the start of each session. Your DM can change "
    "this."
)
CHARACTER_NOT_ADDED = "DMbot read the sheet but couldn't add the character. Ask your DM to add it."
DM_ADDED = (
    "🧑 {name}'s player linked a D&D Beyond sheet; DMbot added {name}. Open the card to "
    "rename or remove it."
)
NOT_AVAILABLE = "Character sheets aren't available on DMbot right now. Try again later."
NOT_AVAILABLE_DM = (
    "The character is saved, but character sheets aren't available on DMbot right now, so "
    "the link wasn't kept. Ask the player to link it later with 📜 My character sheet."
)
NOT_A_MEMBER = "You're not in that server any more, so there's no character to change."
NOT_YOURS = "That isn't your character any more. Press 📜 My character sheet again."
PICK = "Which character?"
ONLY_YOU = "DMbot shows the link only to you and your DM. It's also in the campaign's backup file."
WAIT = "Wait 20 seconds, then press **Link my D&D Beyond sheet** again."
FORGOTTEN = (
    "Done. DMbot forgot **{name}**'s sheet: the link and what it read. You can link it "
    "again any time."
)
FORGOTTEN_TYPED = (
    "Done. DMbot forgot what you typed in for **{name}**. You can link a D&D Beyond sheet "
    "or type it in again any time."
)
TYPED_SAVED = (
    "✅ Saved. DMbot now listens for {names}. Linking a D&D Beyond sheet later replaces "
    "what you typed."
)
TYPED_REPLACED = " This replaced your D&D Beyond link."
TYPED_BAD_LEVEL = (
    "Nothing was saved: the level is a number from 1 to 20. Press **Type in my character** "
    "to try again."
)
TYPED_DROPPED = " ({n} left out: DMbot keeps up to 20 names of up to 60 letters.)"
LINKED_READ = (
    "✅ Linked. DMbot read **{name}**'s sheet{who}. DMbot now listens for its spell, "
    "feature and item names, and reads the sheet again when each session starts."
)
LINKED_UNREAD = (
    "Linked, but DMbot couldn't reach D&D Beyond just now. It tries again when the next "
    "session starts."
)
LINK_CHANGED = "The sheet was changed while DMbot read it. Press 📜 My character sheet again."
DM_BAD_LINK = (
    "The character is saved, but that D&D Beyond link didn't look right, so DMbot didn't "
    "link it. Ask the player to press 📜 My character sheet in DMbot's private message "
    "and paste their link."
)
DM_NOT_PUBLIC = (
    "The character is saved and linked, but its sheet isn't set to Public, so DMbot can't "
    "read it. Ask the player to set it to Public on D&D Beyond (Edit, then Settings, then "
    "Character Privacy). DMbot tries again when the next session starts."
)
DM_NOT_LINKED = (
    "The character is saved, but its sheet couldn't be linked just now. Ask the player to "
    "link it with 📜 My character sheet in DMbot's private message."
)
REFRESH_WAIT = "The sheets were read less than a minute ago. Try again in a moment."
NO_SHEETS = (
    "No player has linked a sheet in this campaign yet. Players link theirs with 📜 My "
    "character sheet in DMbot's private message."
)
LINK_COOLDOWN_S = 20  # per person: each Link reads D&D Beyond once
REFRESH_COOLDOWN_S = 60  # per campaign: Refresh sheets (#723)
_COOLDOWNS_KEPT = 1000  # entries before old ones are dropped
_GUILD = r"(?P<guild>[0-9]{1,20})"
_CAMPAIGN = r"(?P<campaign>[0-9a-f]{32}|-)"

_last_link: dict[tuple[int, int], float] = {}
# One character from a sheet at a time per player and campaign: two quick links make one.
_adding: dict[tuple[int, int, str], list[Any]] = {}  # key -> [lock, users of it]
_last_refresh: dict[tuple[int, str], float] = {}


def _bot(interaction: discord.Interaction) -> DMBot:
    return cast("DMBot", interaction.client)


def _store(interaction: discord.Interaction) -> SheetStore | None:
    return _bot(interaction).sheets


def _md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _too_soon(last: dict[Any, float], key: Any, gap: float) -> bool:
    """True if `key` went less than `gap` seconds ago; otherwise counts this one."""
    now = time.monotonic()
    if now - last.get(key, -gap) < gap:
        return True
    if len(last) >= _COOLDOWNS_KEPT:  # keep it small: only recent ones still matter
        for old in [k for k, t in last.items() if now - t >= gap]:
            del last[old]
    last[key] = now
    return False


# The website's character sheets page shares these cooldowns (#2817): each link reads D&D Beyond.
last_link, last_refresh, too_soon = _last_link, _last_refresh, _too_soon


# ---- words -------------------------------------------------------------------------


def panel_text(character: PlayerCharacter, sheet: CharacterSheet | None, *, link: bool) -> str:
    """The player's panel."""
    head = f"📜 **{_md(character.name)}** ({_md(character.campaign_name)})"
    return f"{head}: {sheet_status(sheet, link=link, mine=True)}\n{ONLY_YOU}"


def sheet_status(sheet: CharacterSheet | None, *, link: bool, mine: bool = False) -> str:
    """No sheet yet / linked to D&D Beyond, read 2 days ago · Elf · Bard 3 / told DMbot
    about this character. `mine`: the player's own panel."""
    if sheet is None:
        return (
            "no sheet yet. Link it so DMbot listens for your spell and feature names. No D&D "
            f"Beyond? Press **{TELL_LABEL}** instead."
        )
    who = sheets.who(sheet.sheet) if sheet.sheet else ""
    basic = sheet_checks.vitals(sheet.sheet) if sheet.sheet else ""  # the DM's card: basics only
    who = f" · {_md(who)}" if who else ""
    who += f" · {basic}" if basic else ""
    if sheet.url is None:
        return f"{'you' if mine else 'the player'} told DMbot about this character{who}."
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
    bot: DMBot,
    guild_id: int,
    campaign_id: str,
    entity_id: str,
    character: int,
    *,
    player: int | None = None,
    report: list[str] | None = None,
) -> str:
    """Link the sheet and read it once, now. The words to show whoever linked it.
    Raises SheetRefused if it's no longer `player`'s character (or a player's at all).
    `report`: if given, the import status card (#1124) is added to it once the sheet is read
    and kept; it is for the player alone, so only the player's own link asks for it."""
    store = bot.sheets
    assert store is not None
    await store.link(guild_id, campaign_id, entity_id, character, player=player)
    url = sheets.sheet_url(character)
    try:
        with log_context(guild_id=guild_id, campaign_id=campaign_id):
            try:
                snapshot = await sheets.fetch(character)
            except sheets.SheetError as exc:
                log.info("A new sheet link for entry %s couldn't be read yet", entity_id)
                if exc.refused:
                    return DM_NOT_PUBLIC if player is None else sheets.NOT_PUBLIC
                return LINKED_UNREAD
            now = int(time.time())
            saved = await store.save(guild_id, campaign_id, entity_id, snapshot, now, url=url)
    finally:
        bot.sheets_changed(guild_id, campaign_id)  # a new link drops the old names too
    if not saved:  # forgotten or linked again while it was read
        return LINK_CHANGED if player is not None else DM_NOT_LINKED
    if report is not None:
        report.append(await status_card(bot, guild_id, campaign_id, snapshot))
    who = sheets.who(snapshot)
    return LINKED_READ.format(name=_md(snapshot["name"]), who=f" ({_md(who)})" if who else "")


async def status_card(
    bot: DMBot, guild_id: int, campaign_id: str, snapshot: Mapping[str, Any]
) -> str:
    """The import status card for this campaign (#1124): names are checked against its own
    rulesets (a campaign with no fallback never sees an older-edition-only spell as ✅), and
    escaped for Discord."""
    campaign = await bot.campaigns.get(guild_id, campaign_id)
    target, fallback = (
        (campaign.target_ruleset, campaign.fallback_ruleset)
        if campaign is not None
        else sheet_checks.NO_CAMPAIGN_RULESETS
    )
    return sheet_checks.card(snapshot, target=target, fallback=fallback, escape=_md)


async def _send_card(interaction: discord.Interaction, report: list[str]) -> None:
    """The import status card, privately, after the panel (#1124): one message per import.
    The sheet is saved either way, so a card that can't be sent is only logged."""
    if not report:
        return
    try:
        await interaction.followup.send(report[0], ephemeral=True)
    except discord.HTTPException:
        log.info("The import status card couldn't be sent")


async def _still_member(interaction: discord.Interaction, guild_id: int) -> bool:
    """Is the person still in that server? (The button is in a private message, kept
    for good: someone who left keeps it.) Says so if not."""
    guild = interaction.client.get_guild(guild_id)
    member = guild.get_member(interaction.user.id) if guild is not None else None
    if member is None and guild is not None:
        try:
            member = await guild.fetch_member(interaction.user.id)
        except discord.NotFound:
            member = None
    if member is None:
        await _tell(interaction, NOT_A_MEMBER)
        return False
    return True


# ---- the player's button and panel --------------------------------------------------


class MySheetButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:sheet:{_GUILD}:{_CAMPAIGN}",
):
    """📜 My character sheet: on the consent confirmation (any campaign of the server,
    `campaign` "-") and on a session's reminder (that campaign). Kept over a restart.
    Only ever finds the presser's own characters."""

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
        if not await _still_member(interaction, self.guild_id):
            return
        found = await store.characters_of(self.guild_id, interaction.user.id, self.campaign_id)
        if not found:
            if self.campaign_id is not None:  # a session's reminder: this campaign, so offer
                await _send(  # to make the character from their sheet (#1119)
                    interaction,
                    NO_CHARACTER_LINK,
                    NoCharacterPanel(self.guild_id, self.campaign_id),
                )
                return
            await _tell(interaction, NO_CHARACTER)
            return
        if len(found) == 1:
            await show_panel(interaction, self.guild_id, found[0])
            return
        await _send(interaction, PICK, CharacterChoice(self.guild_id, found))


class NoCharacterPanel(_Menu):
    """For a player with no character yet: **Link my D&D Beyond sheet** makes it from the sheet."""

    def __init__(self, guild_id: int, campaign_id: str) -> None:
        super().__init__()
        self.guild_id, self.campaign_id = guild_id, campaign_id
        self.add_item(_Button(self._link, label=LINK_LABEL, style=discord.ButtonStyle.primary))

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        await _failed(interaction, error)

    async def _link(self, interaction: discord.Interaction) -> None:
        self.origin = None  # the form's answer is a new message; this one stays
        await interaction.response.send_modal(NewSheetForm(self.guild_id, self.campaign_id))


class NewSheetForm(discord.ui.Modal, title="Link your D&D Beyond sheet"):
    link: discord.ui.TextInput[NewSheetForm] = discord.ui.TextInput(
        label="Your character's D&D Beyond link",
        placeholder="https://www.dndbeyond.com/characters/12345678",
        max_length=200,
    )

    def __init__(self, guild_id: int, campaign_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.guild_id, self.campaign_id = guild_id, campaign_id

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
        await _answer_first(interaction)  # reading D&D Beyond takes a moment
        if not await _still_member(interaction, self.guild_id):
            return
        await add_from_sheet(interaction, self.guild_id, self.campaign_id, number)


async def add_from_sheet(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, number: int
) -> None:
    """A player with no character links their sheet: a sheet DMbot can read makes the
    character, from the sheet's name, played by that player (the same as the DM's "Add a
    player's character"); a sheet it can't read makes nothing. A player who already has a
    character in the campaign gets the sheet linked to it instead, never a second one."""
    bot = _bot(interaction)
    campaign = await bot.campaigns.get(guild_id, campaign_id)
    if bot.sheets is None or bot.memory is None or campaign is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    key = (guild_id, interaction.user.id, campaign_id)
    entry = _adding.setdefault(key, [asyncio.Lock(), 0])
    entry[1] += 1  # counted, because a lock handed to a waiter reads as free for a moment
    lock: asyncio.Lock = entry[0]
    made: list[Any] = []  # the character, as soon as it exists, so the DM hears of it either way
    try:
        async with lock:
            await _add_locked(interaction, bot, campaign, number, made)
    finally:
        entry[1] -= 1
        if entry[1] == 0:
            _adding.pop(key, None)  # idle: nothing keeps a lock per player for good
        if made:
            await _tell_the_dm(bot, campaign, made[0].id, made[0].name)


async def _add_locked(
    interaction: discord.Interaction, bot: DMBot, campaign: Any, number: int, made: list[Any]
) -> None:
    """`add_from_sheet`'s work, one at a time per player and campaign. A character it makes
    is put in `made` at once, for the line to the DM."""
    store, memory = bot.sheets, bot.memory
    assert store is not None and memory is not None
    guild_id, campaign_id, me = campaign.guild_id, campaign.id, interaction.user.id
    existing = await store.characters_of(guild_id, me, campaign_id)
    if existing:  # made a moment ago, or the DM added it: link that one
        c = existing[0]
        try:
            said = await link_and_read(bot, guild_id, c.campaign_id, c.entity_id, number, player=me)
        except SheetRefused:
            await _tell(interaction, NOT_YOURS)
            return
        await show_panel(interaction, guild_id, c, note=said)
        return
    try:
        with log_context(guild_id=guild_id, campaign_id=campaign_id):
            snapshot = await sheets.fetch(number)
    except sheets.SheetError as exc:
        log.info("A sheet to make a character from couldn't be read yet")
        unread = sheets.NOT_PUBLIC if exc.refused else LINKED_UNREAD
        await _tell(interaction, unread + NOTHING_ADDED)
        return
    from dmbot.memory.models import MemoryRuleError
    from dmbot.ui.names import PC, changed, save_name

    name = logic.shorten(str(snapshot.get("name") or "").strip(), sheets.NAME_MAX)
    if not name:  # a sheet with no name makes no character
        await _tell(interaction, CHARACTER_NOT_ADDED)
        return
    try:
        entity = await save_name(memory, campaign, name, PC, [], [], played_by=me)
    except MemoryRuleError as exc:
        log.info("A character from a sheet wasn't added: %s", exc)
        await _tell(interaction, CHARACTER_NOT_ADDED)
        return
    made.append(entity)
    changed(interaction, campaign)
    try:
        await store.link(guild_id, campaign_id, entity.id, number, player=me)
        await store.save(
            guild_id,
            campaign_id,
            entity.id,
            snapshot,
            int(time.time()),
            url=sheets.sheet_url(number),
        )
    finally:
        bot.sheets_changed(guild_id, campaign_id)
    who = sheets.who(snapshot)
    await _tell(
        interaction,
        ADDED_FROM_SHEET.format(name=_md(entity.name), who=f" ({_md(who)})" if who else ""),
    )


async def _tell_the_dm(bot: DMBot, campaign: Any, entity_id: str, name: str) -> None:
    """One line on the DM screen, with the name card's button: rename, merge or remove it."""
    if campaign.dm_screen_channel_id is None:
        return
    view = discord.ui.View(timeout=None)
    view.add_item(OpenCardButton(campaign.id, entity_id))
    await bot.post_message(campaign.dm_screen_channel_id, DM_ADDED.format(name=_md(name)), view)


class OpenCardButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:namecard:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32})",
):
    """Opens a character's name card (its rename, merge and remove buttons) privately, from
    the DM-screen line saying DMbot added it. The card's own rule decides who gets it: the
    campaign's DMs, and server managers (as for `/dmbot names`); a player is refused."""

    def __init__(self, campaign_id: str, entity_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Open character card",
                emoji="🧑",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:namecard:{campaign_id}:{entity_id}",
            )
        )
        self.campaign_id, self.entity_id = campaign_id, entity_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OpenCardButton:
        return cls(match["campaign"], match["entity"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.name_card import show_card

        await show_card(interaction, self.campaign_id, self.entity_id)


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
    found = await store.characters_of(guild_id, interaction.user.id, character.campaign_id)
    if not any(c.entity_id == character.entity_id for c in found):
        await _tell(interaction, NOT_YOURS)  # given to someone else, or removed
        return
    sheet = await store.sheet(
        guild_id, character.campaign_id, character.entity_id, player=interaction.user.id
    )
    text = panel_text(character, sheet, link=True)
    if note:
        text = f"{note}\n\n{text}"
    asks = sheet.asks if sheet is not None and sheet.url is not None else None
    fight: bool | None = None
    if sheet is not None:  # FightBot's switch is per player per campaign, kept apart from sheets
        mine = (await store.fight_players(guild_id, character.campaign_id)).get(interaction.user.id)
        fight = not (mine is not None and mine.never)
    view = SheetPanel(guild_id, character, has_sheet=sheet is not None, asks=asks, fight=fight)
    if replace:
        await _replace(interaction, text, view)
    else:
        await _send(interaction, text, view)


class SheetPanel(_Menu):
    def __init__(
        self,
        guild_id: int,
        character: PlayerCharacter,
        *,
        has_sheet: bool,
        asks: bool | None = None,
        fight: bool | None = None,
    ) -> None:
        super().__init__()
        self.guild_id, self.character = guild_id, character
        self.asks = asks
        self.fight = fight
        if has_sheet:  # the player's own view of it, one part at a time (#1126)
            self.add_item(_Button(self._show, label=SHOW_LABEL, style=discord.ButtonStyle.success))
        self.add_item(_Button(self._link, label=LINK_LABEL, style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._typed, label=TELL_LABEL, style=discord.ButtonStyle.secondary))
        # A document or a file the player already has (#1125); the forms live in sheet_import.
        self.add_item(_Button(self._file, label=FILE_LABEL, style=discord.ButtonStyle.secondary))
        self.add_item(_Button(self._doc, label=DOC_LABEL, style=discord.ButtonStyle.secondary))
        if asks is not None:  # a D&D Beyond sheet is re-read; its player may be asked (#1128)
            self.add_item(
                _Button(
                    self._questions,
                    label=QUESTIONS_ON if asks else QUESTIONS_OFF,
                    style=discord.ButtonStyle.secondary,
                )
            )
        if fight is not None:  # any kind of sheet: FightBot's reminders in combat (#1129)
            self.add_item(
                _Button(
                    self._fightbot,
                    label=FIGHTBOT_ON if fight else FIGHTBOT_OFF,
                    style=discord.ButtonStyle.secondary,
                )
            )
        if has_sheet:
            self.add_item(
                _Button(self._forget, label=FORGET_LABEL, style=discord.ButtonStyle.secondary)
            )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        await _failed(interaction, error)

    async def _show(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.sheet_view import show_my_sheet

        self.origin = None  # the view is a new message; this one stays
        await show_my_sheet(interaction, self.guild_id, self.character)

    async def _link(self, interaction: discord.Interaction) -> None:
        self.origin = None  # the form's answer is a new message; this one stays
        await interaction.response.send_modal(LinkForm(self.guild_id, self.character))

    async def _typed(self, interaction: discord.Interaction) -> None:
        self.origin = None
        await interaction.response.send_modal(TypedForm(self.guild_id, self.character))

    async def _doc(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.sheet_import import DocumentLinkForm

        self.origin = None
        await interaction.response.send_modal(DocumentLinkForm(self.guild_id, self.character))

    async def _file(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.sheet_import import DocumentFileForm

        self.origin = None
        await interaction.response.send_modal(DocumentFileForm(self.guild_id, self.character))

    async def _questions(self, interaction: discord.Interaction) -> None:
        """Turn DMbot's private questions about this sheet on or off (#1128)."""
        store = _store(interaction)
        assert store is not None
        self.stop()
        await _answer_first(interaction, in_place=True)
        if not await _still_member(interaction, self.guild_id):
            return
        c = self.character
        # The stored value, not what the panel showed when it was drawn: it may have changed.
        current = await store.sheet(
            self.guild_id, c.campaign_id, c.entity_id, player=interaction.user.id
        )
        turn_on = not (current.asks if current is not None else bool(self.asks))
        if await store.set_questions(
            self.guild_id, c.campaign_id, c.entity_id, interaction.user.id, turn_on
        ):
            note = QUESTIONS_NOW_ON if turn_on else QUESTIONS_NOW_OFF
        else:
            note = NOT_YOURS
        await show_panel(interaction, self.guild_id, c, replace=True, note=note)

    async def _fightbot(self, interaction: discord.Interaction) -> None:
        """Turn FightBot's private reminders in combat on or off for this player (#1129)."""
        store = _store(interaction)
        assert store is not None
        self.stop()
        await _answer_first(interaction, in_place=True)
        if not await _still_member(interaction, self.guild_id):
            return
        c = self.character
        # The stored value, not what the panel showed when it was drawn: it may have changed.
        mine = (await store.fight_players(self.guild_id, c.campaign_id)).get(interaction.user.id)
        turn_on = mine is not None and mine.never
        await store.set_fightbot(self.guild_id, c.campaign_id, interaction.user.id, turn_on)
        note = FIGHTBOT_NOW_ON if turn_on else FIGHTBOT_NOW_OFF
        await show_panel(interaction, self.guild_id, c, replace=True, note=note)

    async def _forget(self, interaction: discord.Interaction) -> None:
        store = _store(interaction)
        assert store is not None
        self.stop()
        await _answer_first(interaction, in_place=True)
        if not await _still_member(interaction, self.guild_id):
            return
        c = self.character
        mine = interaction.user.id
        before = await store.sheet(self.guild_id, c.campaign_id, c.entity_id, player=mine)
        if await store.unlink(self.guild_id, c.campaign_id, c.entity_id, player=mine):
            _bot(interaction).sheets_changed(self.guild_id, c.campaign_id)
        typed = before is not None and before.url is None
        note = (FORGOTTEN_TYPED if typed else FORGOTTEN).format(name=_md(c.name))
        await show_panel(interaction, self.guild_id, c, replace=True, note=note)


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
        if not await _still_member(interaction, self.guild_id):
            return
        c = self.character
        report: list[str] = []
        try:
            said = await link_and_read(
                _bot(interaction),
                self.guild_id,
                c.campaign_id,
                c.entity_id,
                number,
                player=interaction.user.id,
                report=report,
            )
        except SheetRefused:
            await _tell(interaction, NOT_YOURS)
            return
        await show_panel(interaction, self.guild_id, c, note=said)
        await _send_card(interaction, report)


class TypedForm(discord.ui.Modal, title="Type in your character"):
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
        label="Spell, feature and item names",
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
        raw = self.level.value.strip()
        level = int(raw) if raw.isdecimal() else 0
        if not 1 <= level <= sheets.LEVEL_MAX:
            await _tell(interaction, TYPED_BAD_LEVEL)
            return
        c = self.character
        typed = [n.strip() for n in self.names.value.replace(";", ",").split(",") if n.strip()]
        snapshot = sheets.typed(
            logic.shorten(c.name, sheets.NAME_MAX),  # a long character name still fits
            species=self.species.value,
            class_name=self.class_name.value,
            level=level,
            names=typed,
        )
        if snapshot is None:  # can't happen with a name, but never save a bad one
            await _tell(
                interaction,
                f"Nothing was saved. Check the class name, then press **{TELL_LABEL}** again.",
            )
            return
        store = _store(interaction)
        assert store is not None
        await _answer_first(interaction)
        if not await _still_member(interaction, self.guild_id):
            return
        before = await store.sheet(
            self.guild_id, c.campaign_id, c.entity_id, player=interaction.user.id
        )
        try:
            await store.save(
                self.guild_id,
                c.campaign_id,
                c.entity_id,
                snapshot,
                int(time.time()),
                player=interaction.user.id,
            )
        except SheetRefused:
            await _tell(interaction, NOT_YOURS)
            return
        _bot(interaction).sheets_changed(self.guild_id, c.campaign_id)
        kept = snapshot["features"]
        said = TYPED_SAVED.format(names="those names" if kept else "your character's details")
        if before is not None and before.url is not None:
            said += TYPED_REPLACED
        if len(kept) < len(typed):
            said += TYPED_DROPPED.format(n=len(typed) - len(kept))
        await show_panel(interaction, self.guild_id, c, note=said)
        await _send_card(
            interaction,
            [await status_card(_bot(interaction), self.guild_id, c.campaign_id, snapshot)],
        )


# ---- the DM's side ------------------------------------------------------------------


async def dm_link(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, entity_id: str, raw: str
) -> str:
    """The DM's link from "Add a player's character" (after the character is saved):
    the words to add to the answer. Never raises: the character is saved either way."""
    character = sheets.character_id(raw)
    if character is None:
        return DM_BAD_LINK
    if _bot(interaction).sheets is None:
        return NOT_AVAILABLE_DM
    try:
        return await link_and_read(_bot(interaction), guild_id, campaign_id, entity_id, character)
    except Exception as exc:  # never the text: it can quote a row
        log.error("Couldn't link a sheet from Add a player's character (%s)", type(exc).__name__)
        return DM_NOT_LINKED


async def refresh_all(interaction: discord.Interaction, campaign_id: str, guild_id: int) -> None:
    """Refresh sheets (the names panel): read every linked sheet again, at most once a
    minute per campaign."""
    from dmbot.memory.sheet_refresh import refresh

    store = _store(interaction)
    if store is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    await _answer_first(interaction)
    found = await store.sheets(guild_id, campaign_id)
    linked = [s for s in found if s.url is not None]
    if not linked:
        await _tell(interaction, NO_SHEETS)
        return
    if _too_soon(_last_refresh, (guild_id, campaign_id), REFRESH_COOLDOWN_S):
        await _tell(interaction, REFRESH_WAIT)
        return
    now = int(time.time())
    with log_context(guild_id=guild_id, campaign_id=campaign_id):
        after = await refresh(store, guild_id, campaign_id, now, found=found)
    _bot(interaction).sheets_changed(guild_id, campaign_id)
    unread = [
        s.name for s in after if s.url is not None and (s.fetched_at is None or s.fetched_at < now)
    ]
    read = len(linked) - len(unread)
    more = (
        f" Couldn't read: {', '.join(f'**{_md(n)}**' for n in unread)} (set to private, or "
        "D&D Beyond didn't answer). DMbot keeps what it read before. Ask those players to "
        "set their sheet to Public on D&D Beyond."
        if unread
        else ""
    )
    total = f"{len(linked)} sheet" + ("" if len(linked) == 1 else "s")
    await _tell(interaction, f"📜 Read {read} of {total}.{more}")


async def dm_unlink(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, entity_id: str
) -> bool:
    """The DM's Forget sheet on a character's card. False if it had none."""
    store = _store(interaction)
    if store is None:
        return False
    gone = await store.unlink(guild_id, campaign_id, entity_id)
    if gone:
        _bot(interaction).sheets_changed(guild_id, campaign_id)
    return gone
