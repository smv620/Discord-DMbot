"""Consent by private message (docs/PLAN.md, "Consent"; #33, #34).

When a session starts, and whenever someone joins the table's voice channel, DMbot
privately messages each person once per session:
- not yet consented in this server: what DMbot does, with [✅ I consent] [No thanks];
- already consented (consent carries over per server): a reminder of when, with
  [🛑 Stop recording me].

Buttons carry the server's ID, so they keep working after a restart and each message is
about exactly one server. "No thanks" and "Stop recording me" both remove any consent,
so an old message can never leave someone recorded after they said no. `/consent give`
shows the same request, so everyone sees the same terms before agreeing.

Register the buttons with `bot.add_dynamic_items(ConsentButton, DeclineButton,
StopButton)` in `setup_hook`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, Literal, Protocol

import discord

from dmbot.logs import log_context

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_GUILD = r"(?P<guild>[0-9]{1,20})"

CONSENT_LABEL = "I consent"
DECLINE_LABEL = "No thanks"
STOP_LABEL = "Stop recording me"

NOT_HERE = (
    "DMbot can't change this from here. If DMbot is still in that server, use "
    "`/consent give` or `/consent revoke` there."
)
NOT_A_MEMBER = "You're not in that server any more, so DMbot won't record you there."
GRANT_FAILED = (
    "Sorry, DMbot couldn't save that just now, so it is **not** recording you. "
    "Please press the button again in a minute."
)
REVOKE_NOT_SAVED = (
    "DMbot has stopped recording you. It couldn't save this yet, so it might record you "
    f"again after a restart. Please press **{STOP_LABEL}** again in a minute."
)
CLOUD_NOTE = (
    "Note: this server uses another company to turn speech into text, so your voice "
    "clips and Discord name are sent to them."
)
# What happens to what someone said before they stopped (docs/PLAN.md, Retention).
ALREADY_RECORDED = (
    "What was already recorded stays in the transcript, which anyone in this server can still read."
)


def _plain(name: str) -> str:
    """A name from Discord, shown as-is (no bold, links or fake formatting)."""
    return discord.utils.escape_markdown(discord.utils.escape_mentions(name))


def _date(timestamp: int) -> str:
    """Discord shows this in each reader's own time zone."""
    return f"<t:{timestamp}:D>"


def request_text(server: str, *, voice: str | None, dm: str | None, cloud: bool) -> str:
    lines = [
        f"🎙️ **Can DMbot record you for your D&D game on {_plain(server)}?** Please choose below."
    ]
    if voice and dm:
        lines.append(f"**{_plain(dm)}** turned on DMbot in the **{_plain(voice)}** voice channel.")
    lines += [
        "DMbot listens and gives the DM private notes. It never talks in the game and never "
        "decides anything. Your DM does.",
        f"• **{CONSENT_LABEL}:** DMbot records what you say and turns it into text. "
        "Anyone in this server can read and download that text, even if you stop later.",
        f"• **{DECLINE_LABEL}:** DMbot ignores your voice. You can still play as normal.",
        "DMbot is just for your game. Please don't use it or its text for anything else.",
        "A yes is remembered for this server. If you say no, DMbot asks again next session.",
    ]
    if cloud:
        lines.append(CLOUD_NOTE)
    return "\n".join(lines)


def confirmed_text(server: str, granted_at: int) -> str:
    return (
        f"✅ You said yes on {_date(granted_at)}. DMbot now records you in "
        f"**{_plain(server)}**, this session and later ones. You'll get a short reminder "
        "each time you play. Press 🛑 below to stop any time."
    )


def reminder_text(server: str, voice: str | None, granted_at: int) -> str:
    where = f"**{_plain(voice)}** on " if voice else ""
    return (
        f"🎙️ DMbot is recording you in {where}**{_plain(server)}**. You said yes on "
        f"{_date(granted_at)}. Press 🛑 below to stop any time."
    )


def declined_text(server: str) -> str:
    return (
        f"OK, DMbot won't record you in **{_plain(server)}**. You can still play as normal. "
        f"DMbot will ask again next session. Changed your mind? Press **{CONSENT_LABEL}** "
        "below."
    )


def stopped_text(server: str) -> str:
    return (
        f"🛑 Stopped. DMbot won't record you anymore in **{_plain(server)}**. "
        f"{ALREADY_RECORDED} "
        f"Changed your mind? Press **{CONSENT_LABEL}** below."
    )


def unreachable_text(dms_off: list[str], failed: list[str]) -> str | None:
    """For the DM screen: people DMbot couldn't ask privately, or None."""
    lines = []
    if dms_off:
        lines.append(
            f"📭 **Not recording: {', '.join(map(_plain, dms_off))}.** DMbot couldn't "
            "message them privately. They can type `/consent give` in this server, or turn "
            "on DMs from server members and rejoin the voice channel."
        )
    if failed:
        lines.append(
            f"📭 **Not recording yet: {', '.join(map(_plain, failed))}.** Discord didn't let "
            "DMbot message them just now. They can type `/consent give` in this server, or "
            "rejoin the voice channel to get the message again."
        )
    return "\n".join(lines) or None


Sent = Literal["sent", "dms_off", "failed"]


class ConsentActions(Protocol):
    """What the buttons need from the bot (DMBot)."""

    def get_guild(self, guild_id: int, /) -> discord.Guild | None: ...

    async def give_consent(self, guild_id: int, user_id: int) -> int: ...

    def stop_recording(self, guild_id: int, user_id: int) -> None: ...

    async def withdraw_consent(self, guild_id: int, user_id: int) -> None: ...


def _actions(interaction: discord.Interaction) -> ConsentActions:
    return interaction.client  # type: ignore[return-value]  # DMBot implements it


def _served_here(interaction: discord.Interaction, guild_id: int) -> discord.Guild | None:
    """The server, if this process serves it.

    Consent changes must happen in the process that records that server, whose cache and
    voice service they update. Buttons in private messages reach the process serving
    shard 0; for any other server (or one DMbot left) they point to the slash commands,
    which Discord routes to the right process.
    """
    return _actions(interaction).get_guild(guild_id)


async def _is_member(interaction: discord.Interaction, guild: discord.Guild) -> bool | None:
    """Whether the presser is in the server; None if Discord couldn't say."""
    if isinstance(interaction.user, discord.Member) and interaction.user.guild.id == guild.id:
        return True
    try:
        await guild.fetch_member(interaction.user.id)  # no members intent: ask Discord
    except discord.NotFound:
        return False
    except discord.HTTPException:
        return None
    return True


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
            guild = _served_here(interaction, self.guild_id)
            if guild is None:
                await interaction.response.send_message(NOT_HERE, ephemeral=True)
                return
            await interaction.response.defer()
            member = await _is_member(interaction, guild)
            if member is not True:
                await interaction.followup.send(
                    NOT_A_MEMBER if member is False else GRANT_FAILED, ephemeral=True
                )
                return
            try:
                granted_at = await _actions(interaction).give_consent(
                    self.guild_id, interaction.user.id
                )
            except Exception:
                log.exception("Couldn't save consent from a consent button")
                await interaction.followup.send(GRANT_FAILED, ephemeral=True)
                return
            await interaction.edit_original_response(
                content=confirmed_text(guild.name, granted_at), view=stop_view(self.guild_id)
            )


async def _stop(
    interaction: discord.Interaction, guild_id: int, done_text: Callable[[str], str]
) -> None:
    """Shared by No thanks and Stop: never leave someone recorded after either."""
    with log_context(guild_id=guild_id):
        guild = _served_here(interaction, guild_id)
        if guild is None:
            await interaction.response.send_message(NOT_HERE, ephemeral=True)
            return
        actions = _actions(interaction)
        actions.stop_recording(guild_id, interaction.user.id)  # before any await
        await interaction.response.defer()
        try:
            await actions.withdraw_consent(guild_id, interaction.user.id)
        except Exception:
            log.exception("Couldn't save a consent revoke from a consent button")
            await interaction.followup.send(REVOKE_NOT_SAVED, ephemeral=True)
            return
        await interaction.edit_original_response(
            content=done_text(guild.name), view=consent_view(guild_id)
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
        # An old request can be answered after a yes given elsewhere: "no" must stop it.
        await _stop(interaction, self.guild_id, declined_text)


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
        await _stop(interaction, self.guild_id, stopped_text)


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


async def send_prompt(member: discord.Member, text: str, view: discord.ui.View) -> Sent:
    """Privately message one person. Never raises: a failure is reported, not thrown."""
    try:
        await member.send(text, view=view, allowed_mentions=NO_PINGS)
    except discord.Forbidden as exc:
        # "Cannot send messages to this user" (50007): DMs from server members are off,
        # or they blocked DMbot. IDs only in logs.
        log.info("Couldn't message user %s privately: %s", member.id, exc)
        return "dms_off"
    except Exception as exc:
        # Rate limits, "opening DMs too fast" (40003), Discord outages: not their settings.
        log.warning("Couldn't message user %s privately: %s", member.id, exc)
        return "failed"
    return "sent"
