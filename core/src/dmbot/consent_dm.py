"""Consent by private message (docs/PLAN.md, "Consent"; #33, #34).

When a session starts, and whenever someone joins the table's voice channel, DMbot
privately messages each person once per session:
- not yet consented in this server: what DMbot does, with [✅ I consent] [No thanks];
- already consented (consent carries over per server): a reminder of when, with
  [⚙️ Menu].

The ⚙️ Menu (#807) offers 📜 My character sheet, Stop recording me (or I consent, for
someone who stopped) and Close. Stop recording me first shows one warning, with [Yes,
stop recording me] [Keep recording]; a 🛑 button on a message from before the menu shows
that warning too. One warning, one tap: never a second ask, a wait or a reason box.

Buttons carry the server's ID, so they keep working after a restart and each message is
about exactly one server. "No thanks" and "Yes, stop recording me" both remove any
consent, so an old message can never leave someone recorded after they said no. `/consent
give` shows the same request, so everyone sees the same terms before agreeing.

Register the buttons with `bot.add_dynamic_items(*CONSENT_BUTTONS)` in `setup_hook`.
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
_CAMPAIGN = r"(?P<campaign>[0-9a-f]{32}|-)"

CONSENT_LABEL = "I consent"
DECLINE_LABEL = "No thanks"
STOP_LABEL = "Stop recording me"
MENU_LABEL = "Menu"  # with the ⚙️ emoji
STOP_YES_LABEL = "Yes, stop recording me"
KEEP_LABEL = "Keep recording"
CLOSE_LABEL = "Close"

NOT_HERE = (
    "DMbot can't change this from here. If DMbot is still in that server, use "
    "`/consent give` or `/consent revoke` there."
)
NOT_A_MEMBER = "You're not in that server any more, so DMbot won't record you there."
GRANT_FAILED = (
    "Sorry, DMbot couldn't save that just now, so it is **not** recording you. "
    "Please press the button again in a minute."
)


def revoke_not_saved(button: str) -> str:
    return (
        "DMbot has stopped recording you. It couldn't save this yet, so it might record you "
        f"again after a restart. Please press **{button}** again in a minute."
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
# Next to the 📜 button (#723). It explains an optional button and adds nothing anyone
# agrees to, so it doesn't change the terms version (decided on #781).
SHEET_NOTE = (
    f"Optional: if you play on D&D Beyond, press ⚙️ {MENU_LABEL}, then {SHEET_LABEL}, so "
    "DMbot spells your spell names better. It doesn't change recording."
)
MENU_HINT = f"Press ⚙️ {MENU_LABEL} below to stop or change things."


def confirmed_text(server: str, granted_at: int) -> str:
    return (
        f"✅ You said yes on {_date(granted_at)}. DMbot now records you in "
        f"**{_plain(server)}**, this session and later ones. You'll get a short reminder "
        f"each time you play. {MENU_HINT} {SHEET_NOTE}"
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
        f"{MENU_HINT} {SHEET_NOTE}"
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


def menu_text(server: str) -> str:
    return f"What do you want to do in **{_plain(server)}**?"


def warning_text(server: str) -> str:
    """The one warning before stopping (#807, wording from the owner's issue). Plain facts:
    no guilt and no pressure, and stopping stays one tap away."""
    return (
        f"Stop recording you in **{_plain(server)}**? DMbot won't write down anything you say "
        "from now on. The campaign's record will have gaps wherever you speak, so its "
        "summaries can miss things and plot holes can appear. You can start again any time."
    )


def kept_text(server: str) -> str:
    return f"OK, DMbot keeps recording you in **{_plain(server)}**."


def not_recorded_text(server: str) -> str:
    """Keep recording or /consent revoke from someone DMbot isn't recording."""
    return (
        f"DMbot isn't recording you in **{_plain(server)}**, so there's nothing to stop. To "
        f"start again, press ⚙️ {MENU_LABEL}, then {CONSENT_LABEL}."
    )


NOTHING_CHANGED = "Nothing changed."
MENU_CLOSED = "Closed. Nothing changed."


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

    @property
    def sheets(self) -> object | None: ...  # only whether it's None: sheets are off

    async def recorded(self, guild_id: int, user_id: int) -> bool: ...

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
                view=menu_view(self.guild_id),
            )


async def _stop(interaction: discord.Interaction, guild_id: int, button: str) -> None:
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
            await interaction.followup.send(revoke_not_saved(button), ephemeral=True)
            return
        done = stopped_text if had_consented else declined_text
        await interaction.edit_original_response(  # a reminder's warning card goes too
            content=done(guild.name), embeds=[], view=consent_view(guild_id)
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
        await _stop(interaction, self.guild_id, DECLINE_LABEL)


class StopButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:stop:{_GUILD}(?::{_CAMPAIGN})?",
):
    """Stop recording me, in the ⚙️ Menu: shows the one warning, and stops nothing. The id
    is the old red 🛑 button's (without a campaign), so a 🛑 on a message from before the
    menu warns too."""

    def __init__(self, guild_id: int, campaign_id: str | None = None) -> None:
        tail = f":{campaign_id}" if campaign_id else ""
        super().__init__(
            discord.ui.Button(
                label=STOP_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:consent:stop:{guild_id}{tail}",
            )
        )
        self.guild_id = guild_id
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StopButton:
        return cls(int(match["guild"]), _campaign(match))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            guild = _served_here(interaction, self.guild_id)
            if guild is None:
                await interaction.response.send_message(NOT_HERE, ephemeral=True)
                return
            text = warning_text(guild.name)
            view = warning_view(self.guild_id, self.campaign_id)
            if _lasting(interaction):
                # Under the message's own text, so Keep recording can give it back as it
                # was, even after a restart.
                embed = discord.Embed(description=text)
                await interaction.response.edit_message(embed=embed, view=view)
            else:  # an only-you menu: it becomes the warning
                await interaction.response.edit_message(content=text, view=view)


def _campaign(match: re.Match[str]) -> str | None:
    campaign = match["campaign"]
    return None if campaign in (None, "-") else campaign


def _lasting(interaction: discord.Interaction) -> bool:
    """Whether the button is on a private message that stays (the session reminder, the
    "you said yes" message), not an only-you one that Discord drops on reload. A lasting
    message changes in place (#807): it must never still say "recording you" after a
    stop."""
    message = interaction.message
    return message is not None and not message.flags.ephemeral


class StopYesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:stopyes:{_GUILD}",
):
    """Yes, stop recording me: today's stop, at once and everywhere (#69). A Yes on a
    stale warning still stops."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=STOP_YES_LABEL,
                style=discord.ButtonStyle.danger,
                custom_id=f"dmbot:consent:stopyes:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StopYesButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _stop(interaction, self.guild_id, STOP_YES_LABEL)


class KeepButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:keep:{_GUILD}:{_CAMPAIGN}",
):
    """Keep recording: changes nothing. On a lasting message, its text and ⚙️ Menu come
    back as they were."""

    def __init__(self, guild_id: int, campaign_id: str | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=KEEP_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:consent:keep:{guild_id}:{campaign_id or '-'}",
            )
        )
        self.guild_id = guild_id
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> KeepButton:
        return cls(int(match["guild"]), _campaign(match))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            guild = _served_here(interaction, self.guild_id)
            recorded = guild is not None and await _actions(interaction).recorded(
                self.guild_id, interaction.user.id
            )
            if _lasting(interaction):
                menu = menu_view(self.guild_id, self.campaign_id)
                if recorded or guild is None:  # the reminder is still true
                    await interaction.response.edit_message(embeds=[], view=menu)
                else:  # a stale warning: they stopped some other way, so say so
                    await interaction.response.edit_message(
                        content=not_recorded_text(guild.name), embeds=[], view=menu
                    )
                return
            if guild is None:
                text = NOTHING_CHANGED
            elif recorded:
                text = kept_text(guild.name)
            else:
                text = not_recorded_text(guild.name)
            await interaction.response.edit_message(content=text, view=None)


class MenuButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:menu:{_GUILD}:{_CAMPAIGN}",
):
    """⚙️ Menu, on the yes confirmation, the session reminder and the /consent give answer.
    `campaign_id`: the session's campaign, for 📜 (or "-": any of the server's). On a
    lasting message the menu takes its buttons' place; otherwise it comes as an only-you
    message."""

    def __init__(self, guild_id: int, campaign_id: str | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=MENU_LABEL,
                emoji="⚙️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:consent:menu:{guild_id}:{campaign_id or '-'}",
            )
        )
        self.guild_id = guild_id
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> MenuButton:
        return cls(int(match["guild"]), _campaign(match))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            guild = _served_here(interaction, self.guild_id)
            if guild is None:
                await interaction.response.send_message(NOT_HERE, ephemeral=True)
                return
            actions = _actions(interaction)
            view = options_view(
                self.guild_id,
                self.campaign_id,
                recording=await actions.recorded(self.guild_id, interaction.user.id),
                sheets=actions.sheets is not None,
            )
            if _lasting(interaction):
                await interaction.response.edit_message(view=view)
                return
            await interaction.response.send_message(
                menu_text(guild.name), view=view, ephemeral=True, allowed_mentions=NO_PINGS
            )


class CloseButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:close:{_GUILD}:{_CAMPAIGN}",
):
    """Close: a lasting message gets its ⚙️ Menu back; an only-you menu goes."""

    def __init__(self, guild_id: int, campaign_id: str | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=CLOSE_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:consent:close:{guild_id}:{campaign_id or '-'}",
            )
        )
        self.guild_id = guild_id
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> CloseButton:
        return cls(int(match["guild"]), _campaign(match))

    async def callback(self, interaction: discord.Interaction) -> Any:
        if _lasting(interaction):
            await interaction.response.edit_message(view=menu_view(self.guild_id, self.campaign_id))
            return
        await interaction.response.defer()
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:  # couldn't delete it: collapse it instead
            await interaction.edit_original_response(content=MENU_CLOSED, view=None)


class StartButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:start:{_GUILD}",
):
    """✅ I consent, in the menu of someone DMbot isn't recording: shows the full current
    request in its place, so a yes is only ever saved to wording they've just read."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=CONSENT_LABEL,
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"dmbot:consent:start:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StartButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            guild = _served_here(interaction, self.guild_id)
            if guild is None:
                await interaction.response.send_message(NOT_HERE, ephemeral=True)
                return
            actions = _actions(interaction)
            engine = actions.outside_engine
            text = request_text(
                guild.name, voice=None, dm=None, cloud=engine is not None, company=actions.company
            )
            await interaction.response.edit_message(
                content=text, view=request_view(self.guild_id, outside=engine)
            )


CONSENT_BUTTONS = (
    ConsentButton,
    StartButton,
    DeclineButton,
    StopButton,
    StopYesButton,
    KeepButton,
    MenuButton,
    CloseButton,
)


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


def menu_view(guild_id: int, campaign_id: str | None = None) -> discord.ui.View:
    """Just ⚙️ Menu: what the messages that once had 🛑 carry (#807)."""
    view = discord.ui.View(timeout=None)
    view.add_item(MenuButton(guild_id, campaign_id))
    return view


def options_view(
    guild_id: int, campaign_id: str | None, *, recording: bool, sheets: bool
) -> discord.ui.View:
    """The menu's buttons, only those that apply: 📜 (when sheets are on), Stop recording
    me (or I consent, for someone who stopped), and Close."""
    view = discord.ui.View(timeout=None)
    if sheets:
        view.add_item(sheet_button(guild_id, campaign_id))
    if recording:
        view.add_item(StopButton(guild_id, campaign_id or "-"))
    else:
        view.add_item(StartButton(guild_id))
    view.add_item(CloseButton(guild_id, campaign_id))
    return view


def warning_view(guild_id: int, campaign_id: str | None = None) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(StopYesButton(guild_id))
    view.add_item(KeepButton(guild_id, campaign_id))
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
