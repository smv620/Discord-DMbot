"""Consent by private message (docs/PLAN.md, "Consent"; #33, #34).

When a session starts, and whenever someone joins the table's voice channel, DMbot
privately messages each person once per session:
- not yet consented in this server: what DMbot does, with [✅ I consent] [No thanks];
- already consented (consent carries over per server): a reminder of when, with
  [🛑 Stop recording me].

Buttons carry the server's ID, so they keep working after a restart and each message is
about exactly one server. Register them with
`bot.add_dynamic_items(ConsentButton, DeclineButton, StopButton)` in `setup_hook`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

import discord

from dmbot.logs import log_context

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_GUILD = r"(?P<guild>[0-9]{1,20})"

CONSENT_LABEL = "I consent"
DECLINE_LABEL = "No thanks"
STOP_LABEL = "Stop recording me"

SERVER_GONE = "DMbot isn't in that server any more, so there's nothing to change."
GRANT_FAILED = (
    "Sorry, DMbot couldn't save that just now, so it is **not** recording you. "
    "Please press the button again in a minute."
)
REVOKE_NOT_SAVED = (
    "DMbot has stopped recording you, but couldn't save that. Please press "
    f"**{STOP_LABEL}** again in a minute so it sticks."
)
CLOUD_NOTE = (
    "\nNote: this server uses an outside speech-to-text service, so your voice clips and "
    "display name are sent to that service to be turned into text."
)


def _when(timestamp: int) -> str:
    """Discord shows this in each reader's own time zone."""
    return f"<t:{timestamp}:f>"


def request_text(server: str, voice: str, *, cloud: bool) -> str:
    return (
        f"🎙️ **DMbot is listening in {voice} on {server}**\n"
        "DMbot helps the Dungeon Master run your D&D game. It's for fun only. "
        "Any other use isn't allowed.\n"
        "• If you agree, DMbot **records what you say and turns it into text** during "
        "this server's game sessions.\n"
        "• Everyone who agrees can read and download that text.\n"
        "• Until you agree, DMbot ignores your voice.\n"
        "Your answer is remembered for this server, so you only need to answer once."
        + (CLOUD_NOTE if cloud else "")
    )


def _how_to_stop() -> str:
    return (
        f"To stop at any time, press **{STOP_LABEL}** below, or use `/consent revoke` in "
        "the server. It works right away."
    )


def confirmed_text(server: str, granted_at: int) -> str:
    return (
        f"✅ You agreed on {_when(granted_at)}. DMbot now records you in **{server}**, "
        f"in this session and later ones.\n{_how_to_stop()}"
    )


def reminder_text(server: str, voice: str, granted_at: int) -> str:
    return (
        f"🎙️ **DMbot is listening in {voice} on {server}.** You agreed to be recorded "
        f"on {_when(granted_at)}.\n{_how_to_stop()}"
    )


def declined_text(server: str) -> str:
    return (
        f"OK, DMbot will ignore your voice in **{server}**. "
        f"Changed your mind? Press **{CONSENT_LABEL}** below."
    )


def stopped_text(server: str) -> str:
    return (
        f"🛑 Stopped. DMbot no longer records you in **{server}**. "
        f"Changed your mind? Press **{CONSENT_LABEL}** below."
    )


def unreachable_text(names: list[str]) -> str:
    """For the DM screen: people DMbot couldn't message privately."""
    who = ", ".join(names)
    return (
        f"📭 I couldn't send a private message to {who}, so they haven't been asked about "
        "recording and aren't recorded. They can use `/consent give` in this server, or "
        "turn on direct messages from server members."
    )


class ConsentActions(Protocol):
    """What the buttons need from the bot (DMBot)."""

    def get_guild(self, guild_id: int, /) -> discord.Guild | None: ...

    async def give_consent(self, guild_id: int, user_id: int) -> int: ...

    def stop_recording(self, guild_id: int, user_id: int) -> None: ...

    async def withdraw_consent(self, guild_id: int, user_id: int) -> None: ...


def _actions(interaction: discord.Interaction) -> ConsentActions:
    return interaction.client  # type: ignore[return-value]  # DMBot implements it


def _server(interaction: discord.Interaction, guild_id: int) -> discord.Guild | None:
    return _actions(interaction).get_guild(guild_id)


class ConsentButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:yes:{_GUILD}",
):
    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=CONSENT_LABEL,
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"dmbot:consent:yes:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ConsentButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            guild = _server(interaction, self.guild_id)
            if guild is None:
                await interaction.response.send_message(SERVER_GONE)
                return
            await interaction.response.defer()
            try:
                granted_at = await _actions(interaction).give_consent(
                    self.guild_id, interaction.user.id
                )
            except Exception:
                log.exception("Couldn't save consent from a private message")
                await interaction.followup.send(GRANT_FAILED)
                return
            await interaction.edit_original_response(
                content=confirmed_text(guild.name, granted_at), view=stop_view(self.guild_id)
            )


class DeclineButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:no:{_GUILD}",
):
    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=DECLINE_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:consent:no:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> DeclineButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        guild = _server(interaction, self.guild_id)
        if guild is None:
            await interaction.response.send_message(SERVER_GONE)
            return
        # Nothing to save: without consent DMbot already ignores them.
        await interaction.response.edit_message(
            content=declined_text(guild.name), view=consent_view(self.guild_id)
        )


class StopButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:stop:{_GUILD}",
):
    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=STOP_LABEL,
                emoji="🛑",
                style=discord.ButtonStyle.danger,
                custom_id=f"dmbot:consent:stop:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StopButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            # Stopping works even if DMbot has left the server: it only removes a record.
            actions = _actions(interaction)
            actions.stop_recording(self.guild_id, interaction.user.id)  # before any await
            guild = _server(interaction, self.guild_id)
            server = guild.name if guild is not None else "that server"
            await interaction.response.defer()
            try:
                await actions.withdraw_consent(self.guild_id, interaction.user.id)
            except Exception:
                log.exception("Couldn't save a consent revoke from a private message")
                await interaction.followup.send(REVOKE_NOT_SAVED)
                return
            await interaction.edit_original_response(
                content=stopped_text(server), view=consent_view(self.guild_id)
            )


def request_view(guild_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ConsentButton(guild_id))
    view.add_item(DeclineButton(guild_id))
    return view


def consent_view(guild_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ConsentButton(guild_id))
    return view


def stop_view(guild_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(StopButton(guild_id))
    return view


async def send_prompt(
    member: discord.Member, voice: str, granted_at: int | None, *, cloud: bool
) -> bool:
    """Privately ask (or remind) one person. False if Discord wouldn't deliver it."""
    guild = member.guild
    if granted_at is None:
        text = request_text(guild.name, voice, cloud=cloud)
        view = request_view(guild.id)
    else:
        text = reminder_text(guild.name, voice, granted_at)
        view = stop_view(guild.id)
    try:
        await member.send(text, view=view, allowed_mentions=NO_PINGS)
    except discord.HTTPException as exc:
        # Usually "Cannot send messages to this user" (50007): DMs from server members
        # are off, or they blocked DMbot. IDs only in logs.
        log.info("Couldn't message user %s privately: %s", member.id, exc)
        return False
    return True
