"""Set a character's player by typing their Discord ID (#3004), confirmed by name.

A DM can only pick people already in the server from a menu, so a player who has not joined yet
could not be set up. Here the DM types the ID, DMbot looks it up with Discord's user lookup (not a
member lookup, so no server membership is needed) and shows the name Discord shows; nothing links
until the DM says "✅ That's them". Only the ID is kept (`played_by`), never the name, and neither
is written to the log: the log gets the kind of answer only.

This only links an ID to a character, so DMbot knows which character is a player's and who plays
it. It records nobody: consent is a person's own choice, unchanged (CLAUDE.md, Consent).

`look_up` is the one lookup. The website's queued lookup (#3004, WebDev's half) will call it too.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import discord

from dmbot.mentions import NO_PINGS
from dmbot.ui.dmbot_commands import _Button, _failed, _Menu, _tell

log = logging.getLogger(__name__)

TYPE_ID_LABEL = "⌨️ Type a Discord ID"
SNOWFLAKE_MAX = 2**63 - 1

FOUND, NOT_AN_ID, UNKNOWN, FAILED, BOT = "found", "not_an_id", "unknown", "failed", "bot"

NOT_AN_ID_TEXT = (
    "That isn't a Discord ID. It is a long number, like 123456789012345678: turn on Developer "
    "Mode in Discord's settings, then right-click the person and choose Copy User ID."
)
UNKNOWN_TEXT = "Discord doesn't know that ID. Check it and try again."
FAILED_TEXT = "Couldn't check that ID with Discord. Try again in a minute."
BOT_TEXT = "That's a bot account. A bot can't play a character."
LINK_ONLY = "This only links them to the character. It does not record anyone."


@dataclass(frozen=True, slots=True)
class Lookup:
    """What Discord says about an ID. `name` is for showing only and is never stored."""

    kind: str
    user_id: int = 0
    name: str = ""
    in_server: bool = True


def parse_id(text: str) -> int | None:
    """The ID in what was typed: digits, or a pasted mention like <@123…>. None if it is not one."""
    found = re.fullmatch(r"\s*(?:<@!?(\d{15,20})>|(\d{15,20}))\s*", text, re.ASCII)
    if found is None:
        return None
    value = int(found.group(1) or found.group(2))
    return value if 0 < value <= SNOWFLAKE_MAX else None


async def look_up(client: Any, guild_id: int, text: str) -> Lookup:
    """Who that ID is, by the user lookup (no server membership needed), and whether they are in
    this server. A failed lookup is never a link: the caller shows a message and stops."""
    user_id = parse_id(text)
    if user_id is None:
        return Lookup(NOT_AN_ID)
    try:
        user = await client.fetch_user(user_id)
    except discord.NotFound:
        return Lookup(UNKNOWN)
    except discord.HTTPException:
        return Lookup(FAILED)
    if user.bot:
        return Lookup(BOT)
    name = str(user.global_name or user.name)
    return Lookup(FOUND, user_id, name, await _in_server(client, guild_id, user_id))


async def _in_server(client: Any, guild_id: int, user_id: int) -> bool:
    """Is this person in the server? Unsure counts as yes: only a clear "not a member" from
    Discord says otherwise."""
    guild = client.get_guild(guild_id)
    if guild is None:
        return True
    if guild.get_member(user_id) is not None:
        return True
    try:
        await guild.fetch_member(user_id)
    except discord.NotFound:
        return False
    except discord.HTTPException:
        return True
    return True


def confirm_text(found: Lookup) -> str:
    name = discord.utils.escape_markdown(found.name)
    away = f"{name} isn't in this server yet. Link them anyway?\n" if not found.in_server else ""
    return f"**Please confirm: {name}**\n{away}{LINK_ONLY}"


Linked = Callable[[discord.Interaction, int, str], Awaitable[None]]
Allowed = Callable[[discord.Interaction], Awaitable[bool]]
Back = Callable[[discord.Interaction], Awaitable[None]]


class ConfirmPlayer(_Menu):
    """ "Please confirm: <name>" with ✅ That's them and Cancel; nothing links before the first."""

    def __init__(self, user_id: int, name: str, linked: Linked, back: Back | None = None) -> None:
        super().__init__()
        self._user_id, self._name, self._linked, self._back = user_id, name, linked, back
        self.add_item(_Button(self._yes, label="✅ That's them", style=discord.ButtonStyle.success))
        self.add_item(_Button(self._cancel, label="Cancel", style=discord.ButtonStyle.secondary))

    async def _yes(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self._linked(interaction, self._user_id, self._name)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        if self._back is not None:  # back to the "Who plays it?" step it came from
            await self._back(interaction)
            return
        await interaction.response.edit_message(content="Cancelled. Nobody was linked.", view=None)


class PlayerIdForm(discord.ui.Modal, title="Type a Discord ID"):
    """One box. `allowed` is the caller's own DM check, run before anything is looked up."""

    box: discord.ui.TextInput[PlayerIdForm] = discord.ui.TextInput(
        label="The player's Discord ID",
        placeholder="For example: 123456789012345678",
        max_length=40,
    )

    def __init__(
        self, guild_id: int, allowed: Allowed, linked: Linked, back: Back | None = None
    ) -> None:
        super().__init__()
        self._guild_id, self._allowed, self._linked, self._back = guild_id, allowed, linked, back

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self._allowed(interaction):
            return
        # The button was on the "Who plays it?" message, so this answer updates that message: the
        # confirmation takes its place, and there is no second copy of the step left to press.
        await interaction.response.defer()  # the lookup is one call to Discord
        found = await look_up(interaction.client, self._guild_id, self.box.value)
        log.info("A player ID was looked up: %s", found.kind)  # the kind only, never an ID or name
        messages = {
            NOT_AN_ID: NOT_AN_ID_TEXT,
            UNKNOWN: UNKNOWN_TEXT,
            FAILED: FAILED_TEXT,
            BOT: BOT_TEXT,
        }
        if found.kind != FOUND:
            await _tell(interaction, messages[found.kind])
            return
        view = ConfirmPlayer(found.user_id, found.name, self._linked, self._back)
        await interaction.edit_original_response(
            content=confirm_text(found), view=view, allowed_mentions=NO_PINGS
        )
        view.origin = interaction


def type_id_button(
    guild_id: int, allowed: Allowed, linked: Linked, back: Back | None = None
) -> _Button:
    """The ⌨️ button for a "Who plays it?" step: opens the form. `back`: what Cancel returns to."""

    async def press(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(PlayerIdForm(guild_id, allowed, linked, back))

    return _Button(press, label=TYPE_ID_LABEL, style=discord.ButtonStyle.secondary)
