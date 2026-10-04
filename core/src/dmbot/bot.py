"""Discord bot: slash commands, table sessions, and the capture pipeline.

Phase 0 scope: join the table voice channel through ears, enforce consent, segment
speech per speaker, and post periodic capture summaries to the DM's private channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from functools import partial
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.capture_log import CaptureLog
from dmbot.channel_access import (
    PostProblem,
    join_blocked_message,
    missing_post_permissions,
    notice_failed_message,
)
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.ears.protocol import (
    AudioFrame,
    EarsMessage,
    Health,
    Hello,
    Speaking,
    Status,
    allowlist_command,
    join_command,
    leave_command,
)
from dmbot.ears.server import EarsServer
from dmbot.transcription.base import PlaceholderTranscriber, Transcriber
from dmbot.transcription.factory import build_transcriber
from dmbot.transcription.pipeline import TranscriptionPipeline

log = logging.getLogger(__name__)

SUMMARY_INTERVAL_S = 15
IDLE_SWEEP_INTERVAL_S = 1
NO_PINGS = discord.AllowedMentions.none()

EARS_DOWN = (
    "The voice service (ears) isn't connected. Start it from the `ears` folder "
    "with `npm run dev`, then try again."
)


@dataclass(slots=True)
class Table:
    guild_id: int
    voice_channel_id: int
    screen_channel_id: int
    dm_user_id: int
    segmenter: Segmenter
    capture_log: CaptureLog = field(default_factory=CaptureLog)
    listening: bool = False
    notice_posted: bool = False


class DMBot(commands.Bot):
    def __init__(
        self,
        settings: Settings,
        consent: ConsentStore,
        transcriber: Transcriber | None = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True  # who is in which voice channel; not privileged
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.consent = consent
        self.tables: dict[int, Table] = {}
        self.pipeline = TranscriptionPipeline(
            transcriber or PlaceholderTranscriber(),
            consent,
            is_active=lambda guild_id: guild_id in self.tables,
            hints=self._name_hints,
            deliver=self._deliver_transcript,
            alert=self._alert_dm,
        )
        self.ears = EarsServer(
            host=settings.ears_host,
            port=settings.ears_port,
            secret=settings.ears_secret,
            on_message=self._on_ears_message,
            on_audio=self._on_audio,
            on_link_change=self._on_ears_link_change,
        )
        self._background: list[asyncio.Task[None]] = []

    # ---- lifecycle ---------------------------------------------------------

    async def setup_hook(self) -> None:
        self.tree.add_command(table_group)
        self.tree.add_command(consent_group)
        if self.settings.dev_guild_id:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        await self.ears.start()
        self._background = [
            asyncio.create_task(self.pipeline.run(), name="transcribe"),
            asyncio.create_task(self._idle_sweeper(), name="idle-sweep"),
            asyncio.create_task(self._summary_poster(), name="summaries"),
        ]

    async def close(self) -> None:
        for task in self._background:
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        await self.pipeline.transcriber.close()
        for guild_id in list(self.tables):
            await self.ears.send(leave_command(guild_id))
        await self.ears.stop()
        await super().close()

    # ---- table control -----------------------------------------------------

    async def push_allowlist(self, guild_id: int) -> None:
        users = await self.consent.consenting(guild_id)
        await self.ears.send(allowlist_command(guild_id, users))

    async def start_table(self, table: Table) -> bool:
        self.tables[table.guild_id] = table
        await self.push_allowlist(table.guild_id)
        return await self.ears.send(join_command(table.guild_id, table.voice_channel_id))

    async def stop_table(self, guild_id: int) -> Table | None:
        table = self.tables.pop(guild_id, None)
        if table is None:
            return None
        await self.ears.send(leave_command(guild_id))
        return table

    def name_of(self, guild_id: int, user_id: int) -> str:
        guild = self.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        return member.display_name if member else f"<@{user_id}>"

    async def post(self, channel_id: int, text: str) -> bool:
        """Send a message; returns False (and logs) if it could not be posted."""
        channel = self.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("Could not post to channel %s: channel not found", channel_id)
            return False
        try:
            await channel.send(text, allowed_mentions=NO_PINGS)
        except discord.HTTPException as exc:
            log.warning("Could not post to channel %s: %s", channel_id, exc)
            return False
        return True

    # ---- ears events -------------------------------------------------------

    async def _on_ears_link_change(self, connected: bool) -> None:
        for table in self.tables.values():
            if connected:
                # ears (re)started: restore its state for every active table.
                await self.push_allowlist(table.guild_id)
                await self.ears.send(join_command(table.guild_id, table.voice_channel_id))
            else:
                table.listening = False
                await self.post(
                    table.screen_channel_id,
                    "⚠️ Lost contact with the voice service. I'll rejoin automatically "
                    "when it's back.",
                )

    async def _on_ears_message(self, message: EarsMessage) -> None:
        guild_id = None if isinstance(message, Hello) else message.guild_id
        table = self.tables.get(guild_id) if guild_id is not None else None
        if table is None:
            return
        if isinstance(message, Status):
            await self._on_status(table, message)
        elif isinstance(message, Speaking) and message.event == "end":
            utterance = table.segmenter.end(message.user_id)
            if utterance is not None:
                self.pipeline.enqueue(utterance)
        elif isinstance(message, Health):
            table.capture_log.add_health(
                message.user_id, message.frames_received, message.frames_expected
            )

    async def _on_status(self, table: Table, status: Status) -> None:
        if status.state == "joined":
            table.listening = True
            count = len(await self.consent.consenting(table.guild_id))
            await self.post(
                table.screen_channel_id,
                f"✅ Listening in <#{table.voice_channel_id}>. "
                f"{count} player(s) have opted in to recording.",
            )
            if not table.notice_posted:
                # Players learn they're being recorded from this notice, so a failure
                # must reach the DM; it is retried on the next join.
                table.notice_posted = await self.post(
                    table.voice_channel_id,
                    "🔴 **DMbot is listening in this channel** to help the DM.\n"
                    "Only people who opt in with `/consent give` are recorded. "
                    "Change your mind any time with `/consent revoke`.",
                )
                if not table.notice_posted:
                    await self.post(
                        table.screen_channel_id, notice_failed_message(table.voice_channel_id)
                    )
        elif status.state in ("left", "error"):
            table.listening = False
            detail = status.detail or "The voice connection ended."
            await self.post(table.screen_channel_id, f"⚠️ {detail}")

    def _on_audio(self, frame: AudioFrame) -> None:
        table = self.tables.get(frame.guild_id)
        # Defence in depth: ears enforces consent too, but core re-checks every frame.
        if table is None or not self.consent.has_consent(frame.guild_id, frame.user_id):
            return
        utterance = table.segmenter.add(frame)
        if utterance is not None:
            self.pipeline.enqueue(utterance)

    # ---- background loops --------------------------------------------------

    def _deliver_transcript(self, utterance: Utterance, text: str | None) -> None:
        table = self.tables.get(utterance.guild_id)
        if table is not None:
            table.capture_log.add_utterance(utterance, text)

    async def _alert_dm(self, guild_id: int, message: str) -> None:
        table = self.tables.get(guild_id)
        if table is not None:
            await self.post(table.screen_channel_id, message)

    async def _name_hints(self, guild_id: int) -> list[str]:
        """Names Whisper should expect. Phase 1: players' display names.

        Later phases add character, NPC, and place names.
        """
        users = await self.consent.consenting(guild_id)
        return [self.name_of(guild_id, uid) for uid in users]

    async def _idle_sweeper(self) -> None:
        while True:
            await asyncio.sleep(IDLE_SWEEP_INTERVAL_S)
            now_ms = int(time.time() * 1000)
            for table in self.tables.values():
                for utterance in table.segmenter.flush_idle(now_ms):
                    self.pipeline.enqueue(utterance)

    async def _summary_poster(self) -> None:
        while True:
            await asyncio.sleep(SUMMARY_INTERVAL_S)
            for table in list(self.tables.values()):
                text = table.capture_log.render(partial(self.name_of, table.guild_id))
                if text:
                    await self.post(table.screen_channel_id, text)


# ---- slash commands ----------------------------------------------------------


def _bot(interaction: discord.Interaction) -> DMBot:
    return cast(DMBot, interaction.client)


def _post_problems(
    guild: discord.Guild,
    screen_channel_id: int,
    voice: discord.VoiceChannel | discord.StageChannel,
) -> list[PostProblem]:
    """Channels the bot must post in for `/table join`, with any missing permissions."""
    me = guild.me
    screen = guild.get_channel_or_thread(screen_channel_id)
    checks: list[tuple[int, list[str], str]] = [
        (
            screen_channel_id,
            (
                missing_post_permissions(
                    screen.permissions_for(me), in_thread=isinstance(screen, discord.Thread)
                )
                if screen is not None
                else ["View Channel", "Send Messages"]
            ),
            "your DM updates go here.",
        ),
        (
            voice.id,
            missing_post_permissions(voice.permissions_for(me)),
            "players need to see the recording notice in its chat.",
        ),
    ]
    return [PostProblem(cid, tuple(missing), why) for cid, missing, why in checks if missing]


table_group = app_commands.Group(
    name="table", description="Start or stop listening to your D&D table", guild_only=True
)


@table_group.command(
    name="join", description="Listen to the voice channel you're in (you become the DM)"
)
async def table_join(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    member = interaction.user
    guild = interaction.guild
    if guild is None or not isinstance(member, discord.Member):
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    if member.voice is None or member.voice.channel is None:
        await interaction.response.send_message(
            "Join the table's voice channel first, then run `/table join`.", ephemeral=True
        )
        return
    if not bot.ears.connected:
        await interaction.response.send_message(EARS_DOWN, ephemeral=True)
        return
    if interaction.channel_id is None:
        await interaction.response.send_message("Run this from a text channel.", ephemeral=True)
        return

    voice = member.voice.channel
    problems = _post_problems(guild, interaction.channel_id, voice)
    if problems:
        await interaction.response.send_message(
            join_blocked_message(problems), ephemeral=True, allowed_mentions=NO_PINGS
        )
        return

    table = Table(
        guild_id=guild.id,
        voice_channel_id=voice.id,
        screen_channel_id=interaction.channel_id,
        dm_user_id=member.id,
        segmenter=Segmenter(guild.id),
    )
    await bot.stop_table(guild.id)
    await bot.start_table(table)
    await interaction.response.send_message(
        f"Joining {voice.mention}. Updates for the DM will appear in this channel, "
        "so keep it private to you.",
        ephemeral=True,
    )


@table_group.command(name="leave", description="Stop listening")
async def table_leave(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    table = bot.tables.get(interaction.guild.id)
    if table is None:
        await interaction.response.send_message("I'm not listening right now.", ephemeral=True)
        return
    is_admin = interaction.user.guild_permissions.manage_guild
    if interaction.user.id != table.dm_user_id and not is_admin:
        await interaction.response.send_message(
            "Only the DM (or a server manager) can stop the session.", ephemeral=True
        )
        return
    await bot.stop_table(interaction.guild.id)
    await interaction.response.send_message("Stopped listening. 👋", ephemeral=True)


@table_group.command(name="status", description="Show what DMbot is doing right now")
async def table_status(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    if interaction.guild is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    gid = interaction.guild.id
    table = bot.tables.get(gid)
    users = await bot.consent.consenting(gid)
    names = ", ".join(sorted(bot.name_of(gid, u) for u in users)) or "nobody yet"
    lines = [
        f"Voice service: {'connected ✅' if bot.ears.connected else 'not connected ⚠️'}",
        (
            f"Table: <#{table.voice_channel_id}> "
            f"({'listening' if table.listening else 'connecting…'}), DM <@{table.dm_user_id}>"
            if table
            else "Table: not listening"
        ),
        f"Opted in to recording: {names}",
    ]
    p = bot.pipeline
    lag = f", last delay {p.last_latency_s:.1f} s" if p.last_latency_s is not None else ""
    lines.append(f"Transcription: {p.backlog} waiting{lag}")
    if p.dropped or bot.ears.rejected_frames or p.total_failures:
        lines.append(
            f"Problems: {p.dropped} dropped clip(s), {p.total_failures} failed "
            f"transcription(s), {bot.ears.rejected_frames} bad frame(s)"
        )
    await interaction.response.send_message(
        "\n".join(lines), ephemeral=True, allowed_mentions=NO_PINGS
    )


consent_group = app_commands.Group(
    name="consent", description="Choose whether DMbot may record your voice", guild_only=True
)


@consent_group.command(
    name="give", description="Let DMbot record and transcribe your voice in this server"
)
async def consent_give(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    if interaction.guild is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    gid = interaction.guild.id
    await bot.consent.grant(gid, interaction.user.id)
    if gid in bot.tables:
        await bot.push_allowlist(gid)
    message = (
        "Thanks — DMbot will now transcribe your voice in this server's table channel. "
        "Use `/consent revoke` to stop at any time."
    )
    if bot.settings.transcription.engine == "cloud":
        message += (
            "\nNote: this server uses an outside speech-to-text service, so your voice "
            "clips and display name are sent to that service to be transcribed."
        )
    await interaction.response.send_message(message, ephemeral=True)


@consent_group.command(
    name="revoke", description="Stop DMbot from recording your voice in this server"
)
async def consent_revoke(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    if interaction.guild is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    gid = interaction.guild.id
    await bot.consent.revoke(gid, interaction.user.id)
    table = bot.tables.get(gid)
    if table is not None:
        table.segmenter.drop(interaction.user.id)
        await bot.push_allowlist(gid)
    await interaction.response.send_message(
        "Done — DMbot has stopped recording you and discarded any unprocessed audio.",
        ephemeral=True,
    )


async def run(settings: Settings) -> None:
    consent = ConsentStore(settings.data_dir / "dmbot.sqlite")
    transcriber = build_transcriber(settings.transcription)
    try:
        await transcriber.warm_up()  # load the Whisper model now, not on the first word
        bot = DMBot(settings, consent, transcriber)
        async with bot:
            await bot.start(settings.discord_token)
    finally:
        with contextlib.suppress(Exception):
            consent.close()
