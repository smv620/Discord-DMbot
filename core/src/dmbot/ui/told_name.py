"""Telling one character a secret other name is its entry (#3089, #3542), on the name card's "Who
knows this?" page: "Tavin now knows the guard is Ken", and Take back. It only records what the DM
says; the store holds every rule (a confirmed secret name, a character who holds it, the DM) and
the DM is checked again on every press. Private to the campaign's DMs, never shown to players.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import discord

from dmbot.campaigns import Campaign
from dmbot.memory.models import DM, Alias, MemoryRuleError
from dmbot.memory.store import MemoryStore
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _tell
from dmbot.ui.names import _md, _memory, changed, sees_secrets

ONLY_DMS = "Only the campaign's DMs can do that."
NOT_ON_PAGE = "That character isn't one of the player characters on this page."
# One page holds at most this many told lines, and this many characters of them; the rest are
# counted, so the message never outgrows Discord's 2,000.
MAX_LINES = 8
LINES_BUDGET = 900
PRIVATE_LINE = "Only the characters listed here were told. Nobody else sees this."
GONE = "That name was removed or changed. Open the name card again."
# The store's own messages, said for this feature (the store shares some with other pages).
PLAIN = {
    "Only the DM can say who knows this.": ONLY_DMS,
    "That is gone or was never here.": GONE,
    "Only a secret name can be told to one character.": (
        "This name isn't secret, so everyone already knows it."
    ),
    "That character does not know that name.": (
        "That character doesn't know this name yet. Add them under "
        '"👥 Only some characters" first.'
    ),
    "That character was not told this.": (
        "That character wasn't told this, so there is nothing to take back. Open the page again."
    ),
}


def plain(exc: Exception) -> str:
    """A store refusal in this feature's words; any other message is shown as it is."""
    return PLAIN.get(str(exc), str(exc))


TELL_PLACEHOLDER = "Tell one character who this is…"
TAKE_BACK_PLACEHOLDER = "Take back what a character was told…"
MAX_OPTIONS = 25  # one Discord menu holds this many


def now_knows(character: str, alias: str, entry: str) -> str:
    """The choice and its confirmation: "Tavin now knows the guard is Ken" (plain text)."""
    return f"{character} now knows {alias} is {entry}"


def was_told(character: str, alias: str, entry: str) -> str:
    """The line once told: "Tavin was told that the guard is Ken" (plain text)."""
    return f"{character} was told that {alias} is {entry}"


def take_back_question(character: str, alias: str, entry: str) -> str:
    """The one confirmation: what changes for that character (plain text, escaped by the caller)."""
    return f"Take back? {character} will see {alias} and {entry} as two different names again."


def told_lines(told: Sequence[str], names: dict[str, str], alias: str, entry: str) -> str:
    """The page's lines for who was told, escaped for Discord; "" when no one was."""
    shown = [c for c in told if c in names]
    lines: list[str] = []
    used = 0
    for c in shown[:MAX_LINES]:
        line = f"🗣️ {_md(was_told(names[c], alias, entry))}"
        if lines and used + len(line) > LINES_BUDGET:  # a page never outgrows Discord's limit
            break
        lines.append(line)
        used += len(line) + 1
    if len(shown) > len(lines):
        lines.append(f"…and {len(shown) - len(lines)} more were told.")
    return "\n".join([*lines, PRIVATE_LINE]) if lines else ""


def tell_options(
    holders: Sequence[str], names: dict[str, str], alias: str, entry: str
) -> list[discord.SelectOption]:
    """One choice for each character who holds the name and was not told yet."""
    return [
        discord.SelectOption(
            label=logic.shorten(now_knows(names[c], alias, entry), logic.OPTION_LABEL_MAX), value=c
        )
        for c in holders[:MAX_OPTIONS]
        if c in names
    ]


def take_back_options(
    told: Sequence[str], names: dict[str, str], alias: str, entry: str
) -> list[discord.SelectOption]:
    """One choice for each character who was told, to take it back."""
    return [
        discord.SelectOption(
            label=logic.shorten(was_told(names[c], alias, entry), logic.OPTION_LABEL_MAX), value=c
        )
        for c in told[:MAX_OPTIONS]
        if c in names
    ]


class ConfirmTakeBack(_Menu):
    """The one confirmation before a telling is taken back. `done` runs the take back and shows
    the page again; Keep it shows the page as it was."""

    def __init__(self, campaign_id: str, entity_id: str, alias_id: str, character_id: str) -> None:
        super().__init__()
        self.campaign_id, self.entity_id = campaign_id, entity_id
        self.alias_id, self.character_id = alias_id, character_id
        self.add_item(
            _Button(self._yes, label="Take back", style=discord.ButtonStyle.danger, row=0)
        )
        self.add_item(
            _Button(self._no, label="Keep it", style=discord.ButtonStyle.secondary, row=0)
        )

    async def _yes(self, interaction: discord.Interaction) -> None:
        self.stop()
        await take_back(
            interaction, self.campaign_id, self.entity_id, self.alias_id, self.character_id
        )

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        from dmbot.ui.name_card import show_who_page

        await show_who_page(interaction, self.campaign_id, self.entity_id, self.alias_id)


@dataclass(frozen=True, slots=True)
class Page:
    """What a press works on, once it is checked: the campaign and its memory, the other name, the
    main name of the entry it belongs to, and the player characters by id."""

    campaign: Campaign
    memory: MemoryStore
    alias: Alias
    entry: str
    names: dict[str, str]


async def _checked(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, alias_id: str
) -> Page | None:
    """The page's things, if all are still there and this person is one of the campaign's DMs
    (asked on every press, so a stale page or a removed DM writes nothing)."""
    from dmbot.ui.name_card import _one_name
    from dmbot.ui.who_knows import player_characters

    found = await _one_name(interaction, campaign_id, entity_id, alias_id)
    memory = _memory(interaction)
    if found is None or memory is None:
        return None
    campaign, alias, entry = found
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DMS)
        return None
    return Page(campaign, memory, alias, entry, dict(await player_characters(memory, campaign)))


async def tell(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    alias_id: str,
    character_id: str,
) -> None:
    """Tell this character the name is the entry; then the page again, with what was done."""
    from dmbot.ui.name_card import show_who_page

    await _answer_first(interaction, in_place=True)
    page = await _checked(interaction, campaign_id, entity_id, alias_id)
    if page is None:
        return
    if character_id not in page.names:  # only the page's own player characters are offered
        await _tell(interaction, NOT_ON_PAGE)
        return
    try:
        written = await page.memory.tell_character(
            page.campaign.guild_id, page.campaign.id, alias_id, character_id, source=DM
        )
    except MemoryRuleError as exc:
        await _tell(interaction, plain(exc))
        return
    who = page.names[character_id]
    if not written.value:  # a double press or a stale page: nothing was written
        note = f"{_md(who)} was already told."
        await show_who_page(interaction, campaign_id, entity_id, alias_id, note=note)
        return
    changed(interaction, page.campaign)
    note = (
        f"✅ {_md(now_knows(who, page.alias.text, page.entry))}. Only {_md(who)} was told; "
        "everyone else still sees two names."
    )
    await show_who_page(interaction, campaign_id, entity_id, alias_id, note=note)


async def ask_take_back(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    alias_id: str,
    character_id: str,
) -> None:
    """The one confirmation, in place of the page."""
    await _answer_first(interaction, in_place=True)
    page = await _checked(interaction, campaign_id, entity_id, alias_id)
    if page is None:
        return
    told = await page.memory.told_characters(page.campaign.guild_id, page.campaign.id, alias_id)
    who = page.names.get(character_id)
    if who is None or character_id not in told:
        await _tell(interaction, PLAIN["That character was not told this."])
        return
    await _replace(
        interaction,
        _md(take_back_question(who, page.alias.text, page.entry)),
        ConfirmTakeBack(campaign_id, entity_id, alias_id, character_id),
    )


async def take_back(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    alias_id: str,
    character_id: str,
) -> None:
    from dmbot.ui.name_card import show_who_page

    await _answer_first(interaction, in_place=True)
    page = await _checked(interaction, campaign_id, entity_id, alias_id)
    if page is None:
        return
    try:
        await page.memory.take_back_told(
            page.campaign.guild_id, page.campaign.id, alias_id, character_id, source=DM
        )
    except MemoryRuleError as exc:
        # (the confirmation is finished: show the page as it is now, with why nothing changed)
        await show_who_page(interaction, campaign_id, entity_id, alias_id, note=plain(exc))
        return
    changed(interaction, page.campaign)
    who = page.names.get(character_id, "That character")
    note = (
        f"↩️ Taken back. {_md(who)} will see {_md(page.alias.text)} and {_md(page.entry)} as "
        "two different names again."
    )
    await show_who_page(interaction, campaign_id, entity_id, alias_id, note=note)
