"""Discord bot: sessions for each campaign, and the capture pipeline.

A session ("table") is one campaign being played in one voice channel. `/dmbot start`
(dmbot.ui.dmbot_commands) picks the campaign and channel; this module joins the voice
channel through ears, enforces consent, segments speech per speaker, and posts periodic
capture summaries to the campaign's DM screen.
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
from dmbot.campaigns import CampaignStore
from dmbot.capture_log import CaptureLog
from dmbot.channel_access import (
    SAME_CHANNEL,
    STARTING_UP,
    join_blocked_message,
    notice_failed_message,
    post_problems,
)
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.dm_screen import ensure_dm_screen
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
from dmbot.ui import logic as ui_logic
from dmbot.ui.dmbot_commands import dmbot_group

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
    campaign_id: str | None = None
    campaign_name: str = ""
    dm_user_ids: frozenset[int] = frozenset()

    def is_dm(self, user_id: int) -> bool:
        return user_id == self.dm_user_id or user_id in self.dm_user_ids


class DMBot(commands.Bot):
    def __init__(
        self,
        settings: Settings,
        consent: ConsentStore,
        transcriber: Transcriber | None = None,
        campaigns: CampaignStore | None = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True  # who is in which voice channel; not privileged
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.consent = consent
        # Tests may omit the store; run() always passes the real one.
        self.campaigns = campaigns if campaigns is not None else CampaignStore(":memory:")
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
        self.tree.add_command(dmbot_group)
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

    # ---- sessions (used by /dmbot start · stop · help) ---------------------

    def active_campaign_id(self, guild_id: int) -> str | None:
        table = self.tables.get(guild_id)
        return table.campaign_id if table else None

    def start_blocker(self, guild_id: int) -> str | None:
        """Why `/dmbot start` can't begin right now, or None if it can."""
        if not self.ears.connected:
            return EARS_DOWN
        table = self.tables.get(guild_id)
        if table is not None:
            name = f"**{table.campaign_name}** " if table.campaign_name else ""
            return (
                f"DMbot is already listening to {name}in <#{table.voice_channel_id}>. "
                "Use `/dmbot stop` first."
            )
        return None

    async def start_campaign_session(
        self, interaction: discord.Interaction, campaign_id: str, voice_id: int
    ) -> tuple[bool, str]:
        """Start listening for a campaign. Returns (started, message for the DM)."""
        guild = interaction.guild
        if guild is None:
            return False, "Use this in a server."
        problem = self.start_blocker(guild.id)
        if problem:
            return False, problem
        campaign = await self.campaigns.get(guild.id, campaign_id)
        if campaign is None:
            return False, "That campaign isn't here any more. Run `/dmbot start` again."
        manager = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_guild
        )
        if not ui_logic.can_run(campaign, interaction.user.id, manager):
            return False, ui_logic.NO_CAMPAIGN_ACCESS
        me = cast(discord.Member | None, guild.me)  # None while the guild is still loading
        if me is None:
            return False, STARTING_UP
        voice = guild.get_channel(voice_id)
        if not isinstance(voice, discord.VoiceChannel | discord.StageChannel):
            return False, "I can't find that voice channel. Pick another one."
        voice_perms = voice.permissions_for(me)
        if not (voice_perms.view_channel and voice_perms.connect):
            return False, (
                f"I can't join {voice.mention}. Give me **View Channel** and **Connect** "
                "there (Edit Channel → Permissions), then press Start again."
            )

        screen_id = await ensure_dm_screen(self, interaction, campaign)
        if screen_id is None:
            return False, (
                "I couldn't find a channel for DM updates. Run `/dmbot start` from a "
                "private channel."
            )
        if screen_id == voice.id:
            return False, SAME_CHANNEL
        screen = self.get_channel(screen_id)
        screen_perms: discord.Permissions | None
        if screen is None:
            screen_perms = None
        elif screen_id == interaction.channel_id:
            # Discord's own resolved view of the invoking channel (threads too).
            screen_perms = interaction.app_permissions
        elif isinstance(screen, discord.abc.GuildChannel | discord.Thread):
            screen_perms = screen.permissions_for(me)
        else:
            screen_perms = None
        problems = post_problems(
            screen_id=screen_id,
            screen_perms=screen_perms,
            screen_in_thread=isinstance(screen, discord.Thread),
            voice_id=voice.id,
            voice_perms=voice_perms,
        )
        if problems:
            return False, join_blocked_message(problems)

        await self.campaigns.set_dm_screen(guild.id, campaign.id, screen_id)
        await self.campaigns.set_last_voice_channel(guild.id, campaign.id, voice.id)
        campaign = await self.campaigns.mark_played(guild.id, campaign.id)
        table = Table(
            guild_id=guild.id,
            voice_channel_id=voice.id,
            screen_channel_id=screen_id,
            dm_user_id=interaction.user.id,
            segmenter=Segmenter(guild.id),
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            dm_user_ids=campaign.dm_user_ids,
        )
        await self.start_table(table)
        return True, (
            f"▶ Starting **{campaign.name}** in {voice.mention}.\n"
            f"DM updates will appear in <#{screen_id}>. Keep that channel private."
        )

    async def stop_session(self, guild_id: int, user_id: int, is_server_manager: bool) -> str:
        table = self.tables.get(guild_id)
        if table is None:
            return "DMbot isn't listening right now."
        if not (table.is_dm(user_id) or is_server_manager):
            return "Only the DM (or a server manager) can stop the session."
        await self.stop_table(guild_id)
        name = f" to **{table.campaign_name}**" if table.campaign_name else ""
        return f"Stopped listening{name}. See you next session! 👋"

    async def status_lines(self, guild_id: int) -> list[str]:
        table = self.tables.get(guild_id)
        users = await self.consent.consenting(guild_id)
        names = ", ".join(sorted(self.name_of(guild_id, u) for u in users)) or "nobody yet"
        voice_ok = "connected ✅" if self.ears.connected else "not connected ⚠️"
        lines = [f"Voice service: {voice_ok}"]
        if table is None:
            lines.append("Listening: no. Use `/dmbot start` to begin.")
        else:
            state = "listening" if table.listening else "connecting…"
            campaign = f" to **{table.campaign_name}**" if table.campaign_name else ""
            lines.append(f"Listening{campaign} in <#{table.voice_channel_id}> ({state})")
            lines.append(f"DM updates go to <#{table.screen_channel_id}>")
        lines.append(f"Agreed to be recorded: {names}")
        p = self.pipeline
        lag = f", last delay {p.last_latency_s:.1f} s" if p.last_latency_s is not None else ""
        lines.append(f"Transcription: {p.backlog} waiting{lag}")
        if p.dropped or self.ears.rejected_frames or p.total_failures:
            lines.append(
                f"Problems: {p.dropped} dropped clip(s), {p.total_failures} failed "
                f"transcription(s), {self.ears.rejected_frames} bad frame(s)"
            )
        return lines

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
            campaign = f" for **{table.campaign_name}**" if table.campaign_name else ""
            await self.post(
                table.screen_channel_id,
                f"✅ Listening in <#{table.voice_channel_id}>{campaign}. "
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
    db_path = settings.data_dir / "dmbot.sqlite"
    campaigns = CampaignStore(db_path)
    consent = ConsentStore(db_path)
    transcriber = build_transcriber(settings.transcription)
    try:
        await transcriber.warm_up()  # load the Whisper model now, not on the first word
        bot = DMBot(settings, consent, transcriber, campaigns)
        async with bot:
            await bot.start(settings.discord_token)
    finally:
        with contextlib.suppress(Exception):
            consent.close()
        with contextlib.suppress(Exception):
            campaigns.close()
