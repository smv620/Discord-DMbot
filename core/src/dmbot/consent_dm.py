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
from typing import Any, Literal, Protocol

import discord

from dmbot.consent import CONSENT_COMMAND, PRIVATE_MESSAGE, TERMS_VERSION, ConsentMethod
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


def cloud_note(company: str | None = None) -> str:
    """For the consent request when another company writes things down."""
    # The generic sentence is pinned to the terms version (test_consent_terms.py); a
    # named company is covered by consent.outside_to instead.
    who = company or "another company"
    return (
        f"Note: this server uses {who} to turn speech into text, so your voice clips and "
        "Discord name are sent to them."
    )


CLOUD_NOTE = cloud_note()
# Shown when someone presses I consent on a message whose wording is out of date, or
# that named another company (or none) than the one in use now.
STALE_INTRO = (
    "🔄 **DMbot's consent message has changed since this one,** so please read the "
    "current one and choose."
)
REASK_INTRO = (
    "🔄 **DMbot now uses an outside company to turn speech into text,** so it needs to ask "
    "you again. Please read this and choose."
)
# What happens to what someone said before they stopped: the whole server can read it
# (docs/PLAN.md, Retention and "who can see what"; transcripts #124, #125).
ALREADY_RECORDED = (
    "What DMbot already wrote down stays, and anyone in this server can still read it."
)


def _plain(name: str) -> str:
    """A name from Discord, shown as-is (no bold, links or fake formatting)."""
    return discord.utils.escape_markdown(discord.utils.escape_mentions(name))


def _date(timestamp: int) -> str:
    """Discord shows this in each reader's own time zone."""
    return f"<t:{timestamp}:D>"


# Says what changed in the current consent.TERMS_VERSION; rewrite it when that goes up.
# It explains rather than adds terms, so it isn't part of the pinned wording.
RENEWED = (
    "**What's new:** DMbot's helper now sends the text of what you say, with who said it, "
    "to an AI company (Anthropic) to give your DM notes. It isn't used to train their AI. "
    "You agreed before this change, so DMbot is asking you again. It won't record you "
    "until you say yes."
)
# The AI that reads the text, said once for every helper (#52; TERMS_VERSION 3).
AI_NOTE = (
    "DMbot's helper reads that text, with who said it, to give your DM notes. For that, the "
    "text goes to an AI company (Anthropic). It isn't used to train their AI."
)
AI_SHORT = (
    "An AI company (Anthropic) reads the text, with who said it, to give your DM notes. "
    "It isn't used to train their AI."
)


def renewed_text(names: list[str]) -> str | None:
    """For the DM screen: people who said yes before and are being asked again, so the
    DM knows why they aren't recorded yet."""
    if not names:
        return None
    return (
        f"🔁 **Asked again: {', '.join(map(_plain, names))}.** DMbot's consent message "
        "changed, so they need to say yes again. DMbot isn't recording them until they do."
    )


def request_text(
    server: str,
    *,
    voice: str | None,
    dm: str | None,
    cloud: bool,
    renewed: bool = False,
    company: str | None = None,
) -> str:
    """The consent request. Changing what it says people agree to means bumping
    consent.TERMS_VERSION, so everyone who agreed before is asked again (#35)."""
    lines = [
        f"🎙️ **Can DMbot record you for your D&D game on {_plain(server)}?** Please choose below."
    ]
    if renewed:
        lines.append(RENEWED)
    if voice and dm:
        lines.append(f"**{_plain(dm)}** turned on DMbot in the **{_plain(voice)}** voice channel.")
    lines += [
        "DMbot listens and gives the DM private notes. It never talks in the game and never "
        "decides anything. Your DM does.",
        f"• **{CONSENT_LABEL}:** DMbot records what you say and turns it into text. "
        "Anyone in this server can read and download that text. It stays there even if you "
        f"stop later. {AI_NOTE}",
        f"• **{DECLINE_LABEL}:** DMbot ignores your voice. You can still play as normal.",
        "DMbot is just for your game. Please don't use it or its text for anything else.",
        "A yes is remembered for this server. If you say no, DMbot asks again next session.",
    ]
    if cloud:
        lines.append(cloud_note(company))
    return "\n".join(lines)


SHEET_LABEL = "📜 My character sheet"


def confirmed_text(server: str, granted_at: int) -> str:
    return (
        f"✅ You said yes on {_date(granted_at)}. DMbot now records you in "
        f"**{_plain(server)}**, this session and later ones. You'll get a short reminder "
        "each time you play. Press 🛑 below to stop any time. Play on D&D Beyond? Press "
        f"{SHEET_LABEL} so the transcript spells your spells right."
    )


def outside_note(company: str | None = None) -> str:
    """Short form for the reminder sent each session."""
    return f"Your voice and Discord name go to {company or 'another company'} to be written down."


OUTSIDE_NOTE = outside_note()


def reminder_text(
    server: str,
    voice: str | None,
    granted_at: int,
    *,
    cloud: bool = False,
    company: str | None = None,
) -> str:
    where = f"**{_plain(voice)}** on " if voice else ""
    outside = f" {outside_note(company)}" if cloud else ""
    return (
        f"🎙️ DMbot is recording you in {where}**{_plain(server)}** (you said yes on "
        f"{_date(granted_at)}). Anyone in this server can read the text.{outside} {AI_SHORT} "
        "Press 🛑 below to stop any time."
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

    @property
    def outside_engine(self) -> str | None: ...

    @property
    def company(self) -> str | None: ...

    async def give_consent(
        self, guild_id: int, user_id: int, method: ConsentMethod, *, outside_to: str | None
    ) -> int: ...

    def stop_recording(self, guild_id: int, user_id: int) -> None: ...

    async def withdraw_consent(self, guild_id: int, user_id: int) -> bool: ...


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
    template=(
        rf"dmbot:consent:yes:{_GUILD}(?::v(?P<version>[0-9]{{1,4}}))?"
        r"(?::out:(?P<out>[a-z-]{1,20}))?"
    ),
):
    """The button records what the message it sits on said: the consent wording's version
    (#35) and the outside engine it named, if any (#170). A yes is only saved from a
    message that matches what DMbot does now; otherwise the current question is shown.
    Buttons from before versions were recorded count as version 1."""

    def __init__(
        self, guild_id: int, *, outside: str | None = None, version: int = TERMS_VERSION
    ) -> None:
        marker = f":v{version}" + (f":out:{outside}" if outside else "")
        super().__init__(
            discord.ui.Button(
                label=CONSENT_LABEL,
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"dmbot:consent:yes:{guild_id}{marker}",
            )
        )
        self.guild_id = guild_id
        self.outside = outside
        self.version = version

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ConsentButton:
        version = int(match["version"]) if match["version"] else 1
        return cls(int(match["guild"]), outside=match["out"] or None, version=version)

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
            actions = _actions(interaction)
            engine = actions.outside_engine
            if self.version != TERMS_VERSION or (engine is not None and self.outside != engine):
                # This message's wording is out of date, or it didn't name the company that
                # writes things down now: show the current question instead of saving a
                # yes to terms they never saw.
                intro = STALE_INTRO if self.version != TERMS_VERSION else REASK_INTRO
                text = request_text(
                    guild.name,
                    voice=None,
                    dm=None,
                    cloud=engine is not None,
                    company=actions.company,
                )
                await interaction.edit_original_response(
                    content=f"{intro}\n\n{text}",
                    view=request_view(self.guild_id, outside=engine),
                )
                return
            try:
                # In a private message, or in the reply to /consent give in the server.
                method = PRIVATE_MESSAGE if interaction.guild_id is None else CONSENT_COMMAND
                granted_at = await actions.give_consent(
                    self.guild_id, interaction.user.id, method, outside_to=self.outside
                )
            except Exception:
                log.exception("Couldn't save consent from a consent button")
                await interaction.followup.send(GRANT_FAILED, ephemeral=True)
                return
            await interaction.edit_original_response(
                content=confirmed_text(guild.name, granted_at),
                view=stop_view(self.guild_id, sheet=True),
            )


async def _stop(interaction: discord.Interaction, guild_id: int) -> None:
    """Shared by No thanks and Stop: never leave someone recorded after either.

    Someone whose yes was removed is told what happens to what DMbot already wrote down;
    a plain "no" isn't (nothing of theirs was ever recorded).
    """
    with log_context(guild_id=guild_id):
        guild = _served_here(interaction, guild_id)
        if guild is None:
            await interaction.response.send_message(NOT_HERE, ephemeral=True)
            return
        actions = _actions(interaction)
        actions.stop_recording(guild_id, interaction.user.id)  # before any await
        await interaction.response.defer()
        try:
            had_consented = await actions.withdraw_consent(guild_id, interaction.user.id)
        except Exception:
            log.exception("Couldn't save a consent revoke from a consent button")
            await interaction.followup.send(REVOKE_NOT_SAVED, ephemeral=True)
            return
        done = stopped_text if had_consented else declined_text
        await interaction.edit_original_response(
            content=done(guild.name), view=consent_view(guild_id)
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
        await _stop(interaction, self.guild_id)


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
        await _stop(interaction, self.guild_id)


def request_view(guild_id: int, *, outside: str | None = None) -> discord.ui.View:
    """`outside`: the outside engine the request shown with it names, if any."""
    view = discord.ui.View(timeout=None)
    view.add_item(ConsentButton(guild_id, outside=outside))
    view.add_item(DeclineButton(guild_id))
    return view


def consent_view(guild_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ConsentButton(guild_id))
    return view


def sheet_button(guild_id: int, campaign_id: str | None) -> discord.ui.Button[Any]:
    """The player's 📜 My character sheet (handled by dmbot.ui.sheets.MySheetButton, by
    its id): link a D&D Beyond sheet for their character (#723)."""
    return discord.ui.Button(
        label=SHEET_LABEL,
        style=discord.ButtonStyle.secondary,
        custom_id=f"dmbot:sheet:{int(guild_id)}:{campaign_id or '-'}",
    )


def stop_view(
    guild_id: int, *, sheet: bool = False, campaign_id: str | None = None
) -> discord.ui.View:
    """🛑 Stop, and with `sheet` the player's sheet button (for `campaign_id`, or any of
    the server's campaigns)."""
    view = discord.ui.View(timeout=None)
    view.add_item(StopButton(guild_id))
    if sheet:
        view.add_item(sheet_button(guild_id, campaign_id))
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
