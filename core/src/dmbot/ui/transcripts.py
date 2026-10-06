"""`/transcript` and the download button sent privately when a session ends (#41, #125).

Anyone in the server can download any of its campaigns' transcripts (decided
2026-10-05): only people who agreed were recorded, and they were told the whole server
can read it. Replies are private. Downloads are plain text files built on request from
the stored lines, so a later fix (the Transcript Cleaner, Phase 2b) shows up in the next
download. Building one takes database reads and name lookups, so the reply is deferred
first (Discord wants an answer within 3 seconds).
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import discord

from dmbot.campaigns import Campaign
from dmbot.transcript import export
from dmbot.transcript.models import TranscriptSession
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    NO_PINGS,
    NOT_IN_SERVER,
    _bot,
    _Button,
    _Menu,
    _replace,
    _Select,
    _send,
    _tell,
)

if TYPE_CHECKING:
    from dmbot.bot import DMBot

log = logging.getLogger(__name__)

FILE_LIMIT = 10 * 1024 * 1024  # what a bot may upload anywhere
MAX_NAME_LOOKUPS = 25
NAME_LOOKUPS_AT_ONCE = 5
NOT_AVAILABLE = (
    "Saved transcripts aren't switched on for this DMbot. Ask whoever runs DMbot to turn them on."
)
NONE_YET = "No saved transcripts here yet. DMbot saves one every time it listens to a session."
GONE = "That transcript isn't there anymore. Run `/transcript` to see what's saved."
TOO_BIG = "This transcript is too big to send as one file. Ask whoever runs DMbot for help."
FAILED = "Something went wrong getting that transcript. Try again in a moment."


def ended_text(campaign_name: str) -> str:
    """The private message to the DM and everyone recorded, when DMbot stops."""
    return (
        f"The session for **{discord.utils.escape_markdown(campaign_name)}** has ended. "
        "Press the button to download what was said, as a text file. It has exactly what "
        "DMbot wrote down, with no fixes.\n"
        "Anyone in the server can also get it with `/transcript`."
    )


def still_recording_text(is_dm: bool, so_far: str) -> str:
    if is_dm:
        return (
            f"DMbot is still recording, so this file has only the first {so_far}. You'll "
            "get the full transcript in a private message when you stop with `/dmbot stop`."
        )
    return (
        f"DMbot is still recording, so this file has only the first {so_far}. You'll get "
        "the full transcript in a private message when the DM ends the session."
    )


async def display_names(guild: discord.Guild | None, user_ids: tuple[int, ...]) -> dict[int, str]:
    """Display names now, for a download. DMbot doesn't store names; people it can't
    find show as 'Someone'. Lookups run a few at a time."""
    if guild is None:
        return {}
    limit = asyncio.Semaphore(NAME_LOOKUPS_AT_ONCE)

    async def one(user_id: int) -> tuple[int, str | None]:
        member = guild.get_member(user_id)
        if member is None:
            async with limit:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    return user_id, None
        return user_id, member.display_name

    found = await asyncio.gather(*(one(u) for u in user_ids[:MAX_NAME_LOOKUPS]))
    return {user_id: name for user_id, name in found if name is not None}


def _running(bot: DMBot, session: TranscriptSession) -> bool:
    table = bot.tables.get(session.guild_id)
    return table is not None and table.transcript_session_id == session.id


async def make_file(
    bot: DMBot, guild: discord.Guild | None, guild_id: int, session_id: str
) -> discord.File | str:
    """The download, or what to tell the person instead."""
    store = bot.transcripts
    if store is None:
        return NOT_AVAILABLE
    session = await store.session(guild_id, session_id)
    campaign = await bot.campaigns.get(guild_id, session.campaign_id) if session else None
    if session is None or campaign is None:
        return GONE
    running = _running(bot, session)
    if running:  # save what's waiting, so the file is as full as it can be
        table = bot.tables.get(guild_id)
        if table is not None:
            await bot.save_transcript(table)
    lines = await store.lines(guild_id, session.id)
    names = await display_names(guild, tuple(sorted({line.user_id for line in lines})))
    text = export.render(campaign.name, session, lines, names, running=running)
    data = text.encode("utf-8")
    if len(data) > FILE_LIMIT:
        return TOO_BIG
    return discord.File(io.BytesIO(data), filename=export.file_name(campaign.name, session))


async def send_file(interaction: discord.Interaction, guild_id: int, session_id: str) -> None:
    """Answer at once ("DMbot is thinking…"), then send the file privately."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    bot = _bot(interaction)
    try:
        result = await make_file(bot, bot.get_guild(guild_id), guild_id, session_id)
    except Exception:
        log.exception("Couldn't build a transcript download")
        await _tell(interaction, FAILED)
        return
    if isinstance(result, str):
        await _tell(interaction, result)
        return
    await interaction.followup.send(
        "📄 Here's the transcript. Open it in any text app.",
        file=result,
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )


# ---- /transcript -----------------------------------------------------------------------


@discord.app_commands.command(
    name="transcript", description="Download what was said in a session (as a text file)"
)
@discord.app_commands.guild_only()
async def transcript_command(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    bot = _bot(interaction)
    if bot.transcripts is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    campaigns = await bot.campaigns.list_campaigns(guild.id)
    if not campaigns:
        await _tell(interaction, NONE_YET)
    elif len(campaigns) == 1:
        await show_sessions(interaction, campaigns[0])
    else:
        await _send(interaction, "**Which campaign?**", CampaignPicker(campaigns))


class CampaignPicker(_Menu):
    def __init__(self, campaigns: list[Campaign]) -> None:
        super().__init__()
        self.by_id = {c.id: c for c in campaigns}
        self.pick = _Select(
            self._picked,
            placeholder="Which campaign?",
            options=[
                discord.SelectOption(
                    label=logic.shorten(c.name, logic.OPTION_LABEL_MAX), value=c.id
                )
                for c in campaigns[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_sessions(interaction, self.by_id[self.pick.values[0]], replace=True)


async def show_sessions(
    interaction: discord.Interaction, campaign: Campaign, *, replace: bool = False
) -> None:
    bot = _bot(interaction)
    assert bot.transcripts is not None
    sessions = await bot.transcripts.sessions(campaign.guild_id, campaign.id)
    name = discord.utils.escape_markdown(campaign.name)
    if not sessions:
        text = f"No saved transcripts for **{name}** yet. DMbot saves one every session."
        if replace:
            await _replace(interaction, text, None)
        else:
            await _tell(interaction, text)
        return
    view = SessionPicker(bot, campaign.guild_id, sessions)
    text = (
        f"**Which session of {name}?** Newest first. Session {sessions[0].number} started "
        f"<t:{sessions[0].started_at}:f>."
    )
    if replace:
        await _replace(interaction, text, view)
    else:
        await _send(interaction, text, view)


def _session_option(session: TranscriptSession, running: bool) -> discord.SelectOption:
    people = len(session.speakers)
    return discord.SelectOption(
        label=logic.shorten(export.session_label(session, running=running), logic.OPTION_LABEL_MAX),
        value=session.id,
        description=f"{people} {'person' if people == 1 else 'people'} recorded · "
        f"{session.lines} {'line' if session.lines == 1 else 'lines'}",
    )


class SessionPicker(_Menu):
    def __init__(self, bot: DMBot, guild_id: int, sessions: list[TranscriptSession]) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.sessions = {s.id: s for s in sessions}
        self.pick = _Select(
            self._picked,
            placeholder="Pick a session…",
            options=[
                _session_option(s, _running(bot, s)) for s in sessions[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        session = self.sessions[self.pick.values[0]]
        bot = _bot(interaction)
        if _running(bot, session):
            table = bot.tables.get(self.guild_id)
            is_dm = table is not None and table.is_dm(interaction.user.id)
            so_far = export.duration(int(time.time()) - session.started_at)
            await _replace(
                interaction,
                still_recording_text(is_dm, so_far),
                StillRecording(self.guild_id, session.id),
            )
            return
        self.stop()
        await send_file(interaction, self.guild_id, session.id)


class StillRecording(_Menu):
    def __init__(self, guild_id: int, session_id: str) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.session_id = session_id
        self.add_item(
            _Button(self._anyway, label="Download anyway", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._cancel, label="Cancel", style=discord.ButtonStyle.secondary))

    async def _anyway(self, interaction: discord.Interaction) -> None:
        self.stop()
        await send_file(interaction, self.guild_id, self.session_id)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _replace(interaction, "OK, nothing downloaded.", None)


# ---- the button in the private message when a session ends -----------------------------


class DownloadButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:transcript:(?P<guild>[0-9]{1,20}):(?P<session>[0-9a-f]{32})",
):
    """In a private message: downloads that session's transcript. Works after a restart
    (the server and session are in the button's ID). Only for people still in that
    server, since transcripts belong to the server; the session is looked up inside that
    server only, so a made-up ID finds nothing."""

    def __init__(self, guild_id: int, session_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="🎙 Download transcript",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:transcript:{guild_id}:{session_id}",
            )
        )
        self.guild_id = guild_id
        self.session_id = session_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> DownloadButton:
        return cls(int(match["guild"]), match["session"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        guild = _bot(interaction).get_guild(self.guild_id)
        if guild is None:
            await _tell(interaction, "DMbot isn't in that server anymore.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if guild.get_member(interaction.user.id) is None:
            try:
                await guild.fetch_member(interaction.user.id)
            except discord.NotFound:
                await _tell(interaction, "Only people in that server can download it.")
                return
            except discord.HTTPException:
                await _tell(interaction, "Discord didn't answer. Try again in a moment.")
                return
        await send_file(interaction, self.guild_id, self.session_id)


def download_view(guild_id: int, session_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(DownloadButton(guild_id, session_id))
    return view
