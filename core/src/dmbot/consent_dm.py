"""Consent by private message (docs/PLAN.md, "Consent"; #33, #34).

When a session starts, and whenever someone joins the table's voice channel, DMbot
privately messages each person once per session:
- not yet consented in this server: what DMbot does, with the consent button ("I'm 16 or
  older, record me") and [No thanks];
- already consented (consent carries over per server): a reminder of when, with
  [🗂️ Menu].

The 🗂️ Menu (#807) offers 📜 My character sheet, Stop recording me (or the consent button, for
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
from dmbot.consent_words import AGE_LINE, CONSENT_LABEL, UNDER_LINE
from dmbot.logs import log_context
from dmbot.mentions import NO_PINGS
from dmbot.off_the_record import CHOICES, NOT_TOLD, Paused, paused_text, resumed_text

log = logging.getLogger(__name__)

_GUILD = r"(?P<guild>[0-9]{1,20})"
_CAMPAIGN = r"(?P<campaign>[0-9a-f]{32}|-)"

DECLINE_LABEL = "No thanks"
STOP_LABEL = "Stop recording me"
MENU_LABEL = "Menu"  # with the 🗂️ emoji (⚙️ is Settings)
PAUSE_LABEL = "Pause recording me"  # with ⏸️: off the record for a while (#2466)
RESUME_LABEL = "Resume now"  # with ▶️
STOP_YES_LABEL = "Yes, stop recording me"
DELETE_LABEL = "🗑 Delete my transcripts"  # in the 🗂️ Menu (#1532); the issue says "past"
DELETE_YES_LABEL = "Yes, delete my lines"
DELETE_NO_LABEL = "Cancel"
KEEP_LABEL = "Keep recording"
PAUSE_LENGTHS = {5: "5 minutes", 15: "15 minutes", None: "Until I resume"}
PAUSE_ASK = "⏸️ How long should DMbot stop recording you? Only you. Others are still recorded."
ALREADY_BACK = "You're already on the record."
RESUMED_AFTER_BREAK = "▶️ You're back on when the table's break ends."
CLOSE_LABEL = "Close"
# Test recordings (#1019): only in a server listed in DMBOT_TEST_RECORDING_GUILDS.
TEST_VOICE_LABEL = "Save my voice for tests"
STOP_SAVING_LABEL = "Stop saving my voice"

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
# Shown when someone presses the consent button on a message whose wording is out of date, or
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
# How to remove it (#1532): said with the stop message, whose buttons carry the 🗂️ Menu.
HOW_TO_DELETE = f"Want it gone? Press 🗂️ {MENU_LABEL}, then **{DELETE_LABEL}**."


def _plain(name: str) -> str:
    """A name from Discord, shown as-is (no bold, links or fake formatting)."""
    return discord.utils.escape_markdown(discord.utils.escape_mentions(name))


def _date(timestamp: int) -> str:
    """Discord shows this in each reader's own time zone."""
    return f"<t:{timestamp}:D>"


# Says what changed in the current consent.TERMS_VERSION; rewrite it when that goes up.
# It explains rather than adds terms, so it isn't part of the pinned wording.
RENEWED = (
    "**What's new:** anyone at the table can now ask DMbot a quick rules question. DMbot also "
    "reads what you type in its chat channel while it is listening. That is why it is "
    "asking you again. Until you press "
    f"**{CONSENT_LABEL}**, it records nothing from you. Don't want that? Press "
    f"**{DECLINE_LABEL}** and keep playing."
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
    test_voice: bool = False,
) -> str:
    """The consent request. `test_voice`: a test server, which asks the second question
    (#1019); it is added after the terms, which are the same everywhere. Changing what it
    says people agree to means bumping consent.TERMS_VERSION, so everyone who agreed before
    is asked again (#35)."""
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
        AGE_LINE,
        UNDER_LINE,
        f"• Press **{CONSENT_LABEL}** and DMbot records what you say while it is listening. "
        "It also reads what you type in the voice channel's chat, in DMbot's chat channel and "
        "in one more channel if your DM picks one. It turns it all into text. "
        "Anyone in this server can read and download that text. It stays there even if you "
        f"stop later. {AI_NOTE}",
        "• If you ask DMbot a question in its chat channel, your name and question are posted "
        "there for everyone to read.",
        f"• Press **{DECLINE_LABEL}** and DMbot ignores your voice and your typing. "
        "You can still play as normal.",
        "DMbot is just for your game. Please don't use it or its text for anything else.",
        "A yes is remembered for this server. If you say no, DMbot asks again next session.",
    ]
    if cloud:
        lines.append(cloud_note(company))
    if test_voice:
        lines.append(test_voice_note())
    return "\n".join(lines)


def test_voice_note() -> str:
    """Added to the request in a test server (#1019): a second, separate choice, kept
    apart from the terms above it (which are the same everywhere)."""
    return (
        "\n🧪 **A second choice, only because this is a test server. You can skip it.**\n"
        f"If you press **{TEST_VOICE_LABEL}**, DMbot also keeps a recording of your voice, on "
        "DMbot's own computer, so its makers can check that it still works. Only the people "
        "who run DMbot can hear it. It is never shared or posted, and DMbot deletes it after "
        "7 days unless it is kept as a lasting test.\n"
        "If you don't press it, you are still recorded and written down (if you said yes "
        "above), but DMbot keeps no recording of your voice.\n"
        f"To stop and delete it any time: 🗂️ {MENU_LABEL}, then **{STOP_SAVING_LABEL}**."
    )


def test_voice_saved_text() -> str:
    return (
        "🧪 Done. DMbot is now also keeping your voice, in this server only. Stop any time: "
        f"🗂️ {MENU_LABEL}, then **{STOP_SAVING_LABEL}**. That deletes it."
    )


def test_voice_first_text() -> str:
    return (
        f"First say yes to being recorded (the **{CONSENT_LABEL}** button). After that, open "
        f"🗂️ {MENU_LABEL} and press **{TEST_VOICE_LABEL}**."
    )


def test_voice_stopped_text() -> str:
    return "🧪 DMbot has stopped saving your voice, and deleted what it had kept."


def test_voice_not_here_text() -> str:
    return "This is not a test server, so DMbot saves nobody's voice here."


SHEET_LABEL = "📜 My character sheet"
# Next to the 📜 button (#723). It explains an optional button and adds nothing anyone
# agrees to, so it doesn't change the terms version (decided on #781).
SHEET_NOTE = (
    f"Optional: if you play on D&D Beyond, press 🗂️ {MENU_LABEL}, then {SHEET_LABEL}, so "
    "DMbot spells your spell names better. It doesn't change recording."
)
MENU_HINT = f"To stop being recorded, press 🗂️ {MENU_LABEL} below."
# What a reminder from before the menu said; given the menu's words when it's restored.
OLD_STOP_HINT = "Press 🛑 below to stop any time."
OLD_SHEET_NOTE = (
    f"Optional: if you play on D&D Beyond, press {SHEET_LABEL} so DMbot spells your spell "
    "names better. It doesn't change recording."
)


def _sheet_note(sheets: bool) -> str:
    """Only when character sheets are on: otherwise the menu has no 📜 to point at."""
    return f" {SHEET_NOTE}" if sheets else ""


def confirmed_text(
    server: str, granted_at: int, *, sheets: bool = True, test_voice: bool = False
) -> str:
    text = (
        f"✅ You said yes on {_date(granted_at)}. DMbot now records you in "
        f"**{_plain(server)}**, this session and later ones. You'll get a short reminder "
        f"each time you play. {MENU_HINT}{_sheet_note(sheets)}"
    )
    if test_voice:  # a test server (#1019)
        text += (
            f"\n🧪 Test server: you can also choose to save your voice. Press 🗂️ {MENU_LABEL}, "
            f"then **{TEST_VOICE_LABEL}**."
        )
    return text


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
    sheets: bool = True,
) -> str:
    where = f"**{_plain(voice)}** on " if voice else ""
    outside = f" {outside_note(company)}" if cloud else ""
    return (
        f"🎙️ DMbot is recording you in {where}**{_plain(server)}** (you said yes on "
        f"{_date(granted_at)}). Anyone in this server can read the text.{outside} {AI_SHORT} "
        f"{MENU_HINT}{_sheet_note(sheets)}"
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
        f"{ALREADY_RECORDED} {HOW_TO_DELETE} "
        f"Changed your mind? Press **{CONSENT_LABEL}** below."
    )


def delete_warning_text(server: str, lines: int, sessions: int) -> str:
    """The one warning before deleting (#1532): plain facts, what it covers and that it can't be
    undone. Others' lines stay, and so does anything already downloaded."""
    return (
        f"This deletes everything you said that DMbot wrote down in **{_plain(server)}**: "
        f"{lines} line{'' if lines == 1 else 's'} in {sessions} session"
        f"{'' if sessions == 1 else 's'}. Other people's lines and the DM's story notes stay. "
        "Copies people already downloaded stay too. This can't be undone."
    )


def delete_done_text(server: str) -> str:
    return f"🗑 Done. What you said is deleted from **{_plain(server)}**'s transcripts."


def delete_nothing_text(server: str) -> str:
    return (
        f"DMbot has nothing written down from you in **{_plain(server)}**, "
        "so there is nothing to delete."
    )


DELETE_FAILED = "Sorry, that didn't work and nothing was deleted. Please try again in a minute."
DELETE_KEPT = "OK, nothing was deleted."


def menu_text(server: str, *, saving: bool = False) -> str:
    text = f"What do you want to do in **{_plain(server)}**?"
    if saving:  # a test server, and they said yes to saving (#1019)
        text += f"\n🧪 Your voice is being saved for tests. Stop: **{STOP_SAVING_LABEL}**."
    return text


def warning_text(server: str, *, saving: bool = False) -> str:
    """The one warning before stopping (#807, wording from the owner's issue). Plain facts:
    no guilt and no pressure, and stopping stays one tap away. `saving`: they also said yes
    to saving their voice for tests (#1019), which stopping ends and deletes."""
    text = (
        f"Stop recording you in **{_plain(server)}**? DMbot won't write down anything you say "
        "from now on. The campaign's record will have gaps wherever you speak, so its "
        "summaries can miss things and plot holes can appear. You can start again any time."
    )
    if saving:
        text += " Your voice saved for tests will be deleted too."
    return text


def kept_text(server: str) -> str:
    return (
        f"OK, DMbot keeps recording you in **{_plain(server)}**. To stop later, press "
        f"🗂️ {MENU_LABEL} in DMbot's private message, or type `/consent revoke`."
    )


def not_recorded_menu_text(server: str) -> str:
    """A stale Keep recording on a lasting message, which keeps its 🗂️ Menu."""
    return (
        f"DMbot isn't recording you in **{_plain(server)}**. To start again, press "
        f"🗂️ {MENU_LABEL} below, then **{CONSENT_LABEL}**."
    )


def no_longer_recorded_text(server: str) -> str:
    """A stale Keep recording on an only-you menu, which has no buttons left."""
    return (
        f"DMbot isn't recording you in **{_plain(server)}** anymore. To start again, type "
        "`/consent give`."
    )


def nothing_to_stop_text(server: str) -> str:
    """/consent revoke from someone DMbot isn't recording."""
    return (
        f"DMbot isn't recording you in **{_plain(server)}**, so there's nothing to stop. To "
        "start, type `/consent give`."
    )


def with_warning(server: str, text: str) -> str:
    """A lasting message with the warning first, in its text (an embed can be hidden)."""
    return f"{warning_text(server)}\n\n{text}"


# The warning as it starts a lasting message, whatever the server was called then (it may
# have been renamed since Stop was pressed).
_HEAD, _TAIL = warning_text("\0").split(_plain("\0"))
_WARNING_FIRST = re.compile(f"{re.escape(_HEAD)}.+?{re.escape(_TAIL)}\n\n")


def without_warning(text: str) -> str:
    """The message's own text back, exactly; an old reminder's 🛑 hint and sheet note
    become the menu's. Text without the warning (edited, or from elsewhere) stays as it
    is."""
    match = _WARNING_FIRST.match(text)
    if match:
        text = text[match.end() :]
    return text.replace(OLD_STOP_HINT, MENU_HINT).replace(OLD_SHEET_NOTE, SHEET_NOTE)


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

    async def recorded(self, guild_id: int, user_id: int) -> bool: ...  # unsure: True

    def stop_recording(self, guild_id: int, user_id: int) -> None: ...

    async def withdraw_consent(self, guild_id: int, user_id: int) -> bool: ...

    # Delete my past transcripts (#1532): (lines, sessions) in this server
    async def count_my_lines(self, guild_id: int, user_id: int) -> tuple[int, int]: ...

    async def delete_my_lines(self, guild_id: int, user_id: int) -> tuple[int, int]: ...

    # Test recordings (#1019)
    def test_voice_listed(self, guild_id: int) -> bool: ...  # a test server

    def test_voice_saving(self, guild_id: int, user_id: int) -> bool: ...

    async def grant_test_voice(self, guild_id: int, user_id: int) -> None: ...

    async def stop_saving_voice(self, guild_id: int, user_id: int) -> None: ...

    # Off the record (#2466): a pause for a while, which ends by itself and changes no consent
    async def pause_recording(self, guild_id: int, user_id: int, minutes: int | None) -> Paused: ...

    async def resume_recording(self, guild_id: int, user_id: int) -> bool: ...

    def is_paused(self, guild_id: int, user_id: int) -> bool: ...

    def table_is_paused(self, guild_id: int) -> bool: ...


def _test_voice_state(actions: ConsentActions, guild_id: int, user_id: int) -> str | None:
    """None: not a test server. "saving": their voice is being kept. "ask": it isn't."""
    if not actions.test_voice_listed(guild_id):
        return None
    return "saving" if actions.test_voice_saving(guild_id, user_id) else "ask"


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
                    test_voice=actions.test_voice_listed(self.guild_id),
                )
                await interaction.edit_original_response(
                    content=f"{intro}\n\n{text}",
                    view=keep_invite(
                        interaction,
                        request_view(
                            self.guild_id,
                            outside=engine,
                            test_voice=actions.test_voice_listed(self.guild_id),
                        ),
                        self.guild_id,
                    ),
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
                content=confirmed_text(
                    guild.name,
                    granted_at,
                    sheets=actions.sheets is not None,
                    test_voice=actions.test_voice_listed(self.guild_id),
                ),
                view=keep_invite(interaction, menu_view(self.guild_id), self.guild_id),
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
        await interaction.edit_original_response(
            content=done(guild.name),
            view=keep_invite(
                interaction,
                stopped_view(guild_id) if had_consented else consent_view(guild_id),
                guild_id,
            ),
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
    """Stop recording me, in the 🗂️ Menu: shows the one warning, and stops nothing. The id
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
            text = warning_text(
                guild.name,
                saving=_actions(interaction).test_voice_saving(self.guild_id, interaction.user.id),
            )
            view = keep_invite(
                interaction, warning_view(self.guild_id, self.campaign_id), self.guild_id
            )
            message = interaction.message
            if _lasting(interaction) and message is not None:
                # First in the message's own text (an embed can be hidden), so Keep
                # recording can give the text back exactly, even after a restart.
                content = with_warning(guild.name, without_warning(message.content))
                await interaction.response.edit_message(content=content, view=view)
            else:  # an only-you menu: it becomes the warning
                await interaction.response.edit_message(content=text, view=view)


class DeleteButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:del:{_GUILD}",
):
    """🗑 Delete my past transcripts, in the 🗂️ Menu (#1532): shows the one warning with how many
    lines and sessions it covers, and deletes nothing. It is the person's own lines in this one
    server, whoever presses: the id used is the presser's."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=DELETE_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:consent:del:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> DeleteButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await ask_delete(interaction, self.guild_id)


async def ask_delete(interaction: discord.Interaction, guild_id: int) -> None:
    """The warning (or "nothing written down") as an only-you message, for the menu button and
    the `/dmbot delete-my-lines` command alike. Membership is not checked: removing your own
    words is a right, and the presser's own id is the only one ever used."""
    with log_context(guild_id=guild_id):
        guild = _served_here(interaction, guild_id)
        if guild is None:
            await interaction.response.send_message(NOT_HERE, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            lines, sessions = await _actions(interaction).count_my_lines(
                guild_id, interaction.user.id
            )
        except Exception:
            log.exception("Couldn't count a person's lines for the delete warning")
            await interaction.followup.send(DELETE_FAILED, ephemeral=True)
            return
        if lines == 0:
            await interaction.followup.send(
                delete_nothing_text(guild.name), ephemeral=True, allowed_mentions=NO_PINGS
            )
            return
        await interaction.followup.send(
            delete_warning_text(guild.name, lines, sessions),
            view=delete_view(guild_id),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class DeleteYesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:delyes:{_GUILD}",
):
    """Delete my lines: deletes at once, in one step. A Yes on an old warning still deletes the
    presser's own lines (and says how many went), never anyone else's."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=DELETE_YES_LABEL,
                style=discord.ButtonStyle.danger,
                custom_id=f"dmbot:consent:delyes:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> DeleteYesButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            guild = _served_here(interaction, self.guild_id)
            if guild is None:
                await interaction.response.send_message(NOT_HERE, ephemeral=True)
                return
            await interaction.response.defer()
            try:
                lines, _ = await _actions(interaction).delete_my_lines(
                    self.guild_id, interaction.user.id
                )
            except Exception:
                log.exception("Couldn't delete a person's past transcripts")
                await interaction.edit_original_response(content=DELETE_FAILED, view=None)
                return
            text = delete_done_text(guild.name) if lines else delete_nothing_text(guild.name)
            await interaction.edit_original_response(
                content=text, view=None, allowed_mentions=NO_PINGS
            )


class DeleteNoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:delno:{_GUILD}",
):
    """Cancel: changes nothing."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=DELETE_NO_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:consent:delno:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> DeleteNoButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.edit_message(content=DELETE_KEPT, view=None)


def delete_view(guild_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(DeleteYesButton(guild_id))
    view.add_item(DeleteNoButton(guild_id))
    return view


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
    """Keep recording: changes nothing. On a lasting message, its text and 🗂️ Menu come
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
            message = interaction.message
            if _lasting(interaction) and message is not None:
                menu = keep_invite(
                    interaction, menu_view(self.guild_id, self.campaign_id), self.guild_id
                )
                # The message's own text, as it was. For a server not served here it may
                # still say "recording you": nothing is captured there, so that's safe.
                if guild is None or recorded:
                    content = without_warning(message.content)
                    await interaction.response.edit_message(content=content, view=menu)
                else:  # a stale warning: they stopped some other way, so say so
                    await interaction.response.edit_message(
                        content=not_recorded_menu_text(guild.name), view=menu
                    )
                return
            if guild is None:
                text = NOTHING_CHANGED
            elif recorded:
                text = kept_text(guild.name)
            else:
                text = no_longer_recorded_text(guild.name)
            await interaction.response.edit_message(content=text, view=None)


class MenuButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:menu:{_GUILD}:{_CAMPAIGN}",
):
    """🗂️ Menu, on the yes confirmation, the session reminder and the /consent give answer.
    `campaign_id`: the session's campaign, for 📜 (or "-": any of the server's). On a
    lasting message the menu takes its buttons' place; otherwise it comes as an only-you
    message."""

    def __init__(self, guild_id: int, campaign_id: str | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=MENU_LABEL,
                emoji="🗂️",
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
            view = keep_invite(
                interaction,
                options_view(
                    self.guild_id,
                    self.campaign_id,
                    recording=await actions.recorded(self.guild_id, interaction.user.id),
                    sheets=actions.sheets is not None,
                    test_voice=_test_voice_state(actions, self.guild_id, interaction.user.id),
                    paused=actions.is_paused(self.guild_id, interaction.user.id),
                ),
                self.guild_id,
            )
            if _lasting(interaction):
                await interaction.response.edit_message(view=view)
                return
            await interaction.response.send_message(
                menu_text(
                    guild.name,
                    saving=actions.test_voice_saving(self.guild_id, interaction.user.id),
                ),
                view=view,
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )


class OffRecordButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:pause:{_GUILD}:{_CAMPAIGN}",
):
    """⏸️ Pause recording me, in the 🗂️ Menu and on the session's reminder (#2466): asks how long,
    in one tap with no warning (a pause ends by itself), and pauses nothing until a length is
    chosen. Only your own recording."""

    def __init__(self, guild_id: int, campaign_id: str | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=PAUSE_LABEL,
                emoji="⏸️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:consent:pause:{guild_id}:{campaign_id or '-'}",
            )
        )
        self.guild_id = guild_id
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OffRecordButton:
        return cls(int(match["guild"]), _campaign(match))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            if _served_here(interaction, self.guild_id) is None:
                await interaction.response.send_message(NOT_HERE, ephemeral=True)
                return
            view = pause_choices_view(self.guild_id)
            if _lasting(interaction):  # a message others may scroll past: the choice is only yours
                await interaction.response.send_message(
                    PAUSE_ASK, view=view, ephemeral=True, allowed_mentions=NO_PINGS
                )
                return
            await interaction.response.edit_message(content=PAUSE_ASK, view=view)


class OffRecordForButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:pausefor:{_GUILD}:(?P<minutes>[0-9]{{1,3}})",
):
    """One of the lengths: 5 minutes, 15 minutes, or until you press Resume (0)."""

    def __init__(self, guild_id: int, minutes: int | None) -> None:
        super().__init__(
            discord.ui.Button(
                label=PAUSE_LENGTHS.get(minutes, "Until I resume"),
                style=discord.ButtonStyle.primary
                if minutes == 5
                else discord.ButtonStyle.secondary,
                custom_id=f"dmbot:consent:pausefor:{guild_id}:{minutes or 0}",
            )
        )  # fmt: skip
        self.guild_id = guild_id
        self.minutes = minutes

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OffRecordForButton:
        return cls(int(match["guild"]), int(match["minutes"]) or None)

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            if _served_here(interaction, self.guild_id) is None:
                await interaction.response.send_message(NOT_HERE, ephemeral=True)
                return
            await interaction.response.defer()  # the pause is a quick call, but answer first
            if self.minutes not in CHOICES:
                await interaction.edit_original_response(content="Nothing was paused.", view=None)
                return
            done = await _actions(interaction).pause_recording(
                self.guild_id, interaction.user.id, self.minutes
            )
            if not done.ok:
                await interaction.edit_original_response(content=done.why, view=None)
                return
            await interaction.edit_original_response(
                content=paused_text(done.until) + ("" if done.told else NOT_TOLD),
                view=resume_view(self.guild_id),
            )


class ResumeButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:resume:{_GUILD}",
):
    """▶️ Resume now: back on the record. Only your own."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=RESUME_LABEL,
                emoji="▶️",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:consent:resume:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ResumeButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            if _served_here(interaction, self.guild_id) is None:
                await interaction.response.send_message(NOT_HERE, ephemeral=True)
                return
            await interaction.response.defer()
            actions = _actions(interaction)
            was = await actions.resume_recording(self.guild_id, interaction.user.id)
            if was and actions.table_is_paused(self.guild_id):  # the DM's break is still on
                text = RESUMED_AFTER_BREAK
            else:
                text = resumed_text() if was else ALREADY_BACK
            await interaction.edit_original_response(content=text, view=None)


def pause_choices_view(guild_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for minutes in CHOICES:
        view.add_item(OffRecordForButton(guild_id, minutes))
    return view


def resume_view(guild_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ResumeButton(guild_id))
    return view


class CloseButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:consent:close:{_GUILD}:{_CAMPAIGN}",
):
    """Close: a lasting message gets its 🗂️ Menu back; an only-you menu goes."""

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
        message = interaction.message
        if _lasting(interaction) and message is not None:
            await interaction.response.edit_message(
                content=without_warning(message.content),
                view=keep_invite(
                    interaction, menu_view(self.guild_id, self.campaign_id), self.guild_id
                ),
            )
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
    """✅ The consent button, in the menu of someone DMbot isn't recording: shows the full current
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
            listed = actions.test_voice_listed(self.guild_id)
            text = request_text(
                guild.name,
                voice=None,
                dm=None,
                cloud=engine is not None,
                company=actions.company,
                test_voice=listed,
            )
            await interaction.response.edit_message(
                content=text,
                view=keep_invite(
                    interaction,
                    request_view(self.guild_id, outside=engine, test_voice=listed),
                    self.guild_id,
                ),
            )


class SaveVoiceButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:testvoice:yes:{_GUILD}",
):
    """🧪 Save my voice for tests (#1019), in a test server: a second yes, kept apart from
    the recording consent. Only for someone who is already recorded; it changes nothing
    about their recording."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=TEST_VOICE_LABEL,
                emoji="🧪",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:testvoice:yes:{guild_id}",
                row=1,  # apart from the two answers to the question above it
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SaveVoiceButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            guild = _served_here(interaction, self.guild_id)
            if guild is None:
                await interaction.response.send_message(NOT_HERE, ephemeral=True)
                return
            actions = _actions(interaction)
            if not actions.test_voice_listed(self.guild_id):  # not a test server (any more)
                await interaction.response.send_message(test_voice_not_here_text(), ephemeral=True)
                return
            member = await _is_member(interaction, guild)
            if member is not True:
                await interaction.response.send_message(
                    NOT_A_MEMBER if member is False else GRANT_FAILED, ephemeral=True
                )
                return
            if not await actions.recorded(self.guild_id, interaction.user.id):
                await interaction.response.send_message(test_voice_first_text(), ephemeral=True)
                return
            try:
                await actions.grant_test_voice(self.guild_id, interaction.user.id)
            except Exception:
                log.exception("Couldn't save the test-voice yes")
                await interaction.response.send_message(GRANT_FAILED, ephemeral=True)
                return
            await interaction.response.send_message(test_voice_saved_text(), ephemeral=True)


class StopSavingButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:testvoice:stop:{_GUILD}",
):
    """Stop saving my voice (#1019), in 🗂️ Menu: nothing more is saved, and what was saved
    is deleted from every test session at once."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=STOP_SAVING_LABEL,
                emoji="🧪",
                style=discord.ButtonStyle.danger,
                custom_id=f"dmbot:testvoice:stop:{guild_id}",
                row=1,  # on its own row, so the menu's own five stay together on the first
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StopSavingButton:
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        with log_context(guild_id=self.guild_id):
            guild = _served_here(interaction, self.guild_id)
            if guild is None:
                await interaction.response.send_message(NOT_HERE, ephemeral=True)
                return
            actions = _actions(interaction)
            # At once, before anything that waits: nothing more is saved from this moment.
            await actions.stop_saving_voice(self.guild_id, interaction.user.id)
            await interaction.response.send_message(test_voice_stopped_text(), ephemeral=True)


CONSENT_BUTTONS = (
    SaveVoiceButton,
    StopSavingButton,
    ConsentButton,
    StartButton,
    DeclineButton,
    StopButton,
    StopYesButton,
    DeleteButton,
    DeleteYesButton,
    DeleteNoButton,
    KeepButton,
    MenuButton,
    OffRecordButton,
    OffRecordForButton,
    ResumeButton,
    CloseButton,
)


JOIN_LABEL = "Join on D&D Beyond"


def invite_button(url: str) -> discord.ui.Button[Any]:
    """A link button to the DM's D&D Beyond campaign invite (#1119): a plain link, nothing
    DMbot handles. `url` is a checked invite link (dmbot.campaigns.invite)."""
    return discord.ui.Button(label=JOIN_LABEL, style=discord.ButtonStyle.link, url=url)


def keep_invite(
    interaction: discord.Interaction, view: discord.ui.View, guild_id: int
) -> discord.ui.View:
    """The message being edited may carry the DM's Join on D&D Beyond button (#1119): a new
    view built for it must keep that link, or the button vanishes the moment the player
    answers. While a session is running the campaign's *current* link decides (a DM who
    removed a leaked link must not see it come back from an old message); with none running,
    the link is read off the message itself. Adds it to `view` unless the view has one."""
    if any(getattr(item, "url", None) for item in view.children):
        return view
    tables = getattr(interaction.client, "tables", None)
    table = tables.get(guild_id) if isinstance(tables, dict) else None
    if table is not None and table.campaign_id is not None:
        if table.dndbeyond_invite:
            view.add_item(invite_button(table.dndbeyond_invite))
        return view
    message = getattr(interaction, "message", None)
    for row in getattr(message, "components", None) or []:
        for part in getattr(row, "children", None) or []:
            url = getattr(part, "url", None)
            if isinstance(url, str) and getattr(part, "label", None) == JOIN_LABEL:
                view.add_item(invite_button(url))
                return view
    return view


def request_view(
    guild_id: int,
    *,
    outside: str | None = None,
    test_voice: bool = False,
    invite: str | None = None,
) -> discord.ui.View:
    """`outside`: the outside engine the request shown with it names, if any.
    `test_voice`: a test server, which adds the second question's button (#1019).
    `invite`: the campaign's D&D Beyond invite link, shown as a Join button (#1119)."""
    view = discord.ui.View(timeout=None)
    view.add_item(ConsentButton(guild_id, outside=outside))
    view.add_item(DeclineButton(guild_id))
    if test_voice:
        view.add_item(SaveVoiceButton(guild_id))
    if invite:
        view.add_item(invite_button(invite))
    return view


def consent_view(guild_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ConsentButton(guild_id))
    return view


def stopped_view(guild_id: int, campaign_id: str | None = None) -> discord.ui.View:
    """What a stop message carries: the consent button to start again, and the 🗂️ Menu that
    holds 🗑 Delete my past transcripts (#1532), which the message points at."""
    view = discord.ui.View(timeout=None)
    view.add_item(ConsentButton(guild_id))
    view.add_item(MenuButton(guild_id, campaign_id))
    return view


def sheet_button(guild_id: int, campaign_id: str | None) -> discord.ui.Button[Any]:
    """The player's 📜 My character sheet (handled by dmbot.ui.sheets.MySheetButton, by
    its id): link a D&D Beyond sheet for their character (#723)."""
    return discord.ui.Button(
        label=SHEET_LABEL,
        style=discord.ButtonStyle.secondary,
        custom_id=f"dmbot:sheet:{int(guild_id)}:{campaign_id or '-'}",
    )


def menu_view(
    guild_id: int, campaign_id: str | None = None, *, invite: str | None = None
) -> discord.ui.View:
    """Just 🗂️ Menu: what the messages that once had 🛑 carry (#807). The session's reminder
    adds the campaign's D&D Beyond Join button when the DM set one (#1119)."""
    view = discord.ui.View(timeout=None)
    view.add_item(MenuButton(guild_id, campaign_id))
    view.add_item(OffRecordButton(guild_id, campaign_id))  # one tap from the reminder (#2466)
    if invite:
        view.add_item(invite_button(invite))
    return view


def options_view(
    guild_id: int,
    campaign_id: str | None,
    *,
    recording: bool,
    sheets: bool,
    test_voice: str | None = None,
    paused: bool = False,
) -> discord.ui.View:
    """The menu's buttons, only those that apply: 📜 (when sheets are on), Stop recording
    me (or the consent button, for someone who stopped), and Close. `test_voice`: in a test
    server (#1019), "saving" adds Stop saving my voice, "ask" adds Save my voice for tests."""
    view = discord.ui.View(timeout=None)
    if sheets:
        view.add_item(sheet_button(guild_id, campaign_id))
    if test_voice == "saving":
        view.add_item(StopSavingButton(guild_id))
    elif test_voice == "ask" and recording:
        view.add_item(SaveVoiceButton(guild_id))
    if recording:
        # Off the record for a while (#2466), or back on it if they already are. In a test server's
        # menu too: real people are recorded there. The test-voice button has its own row.
        view.add_item(ResumeButton(guild_id) if paused else OffRecordButton(guild_id, campaign_id))
        view.add_item(StopButton(guild_id, campaign_id or "-"))
    else:
        view.add_item(StartButton(guild_id))
    view.add_item(DeleteButton(guild_id))
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
