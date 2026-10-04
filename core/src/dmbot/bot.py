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
from dmbot.db import Database
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
from dmbot.logs import log_context, set_log_context
from dmbot.sessions import SavedSession, SessionStore
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


SAVE_FAILED = "I couldn't save the session, so I haven't started. Please try again in a moment."
STOP_FAILED = (
    "I couldn't save that DMbot stopped, so I'm still listening. "
    "Please try `/dmbot stop` again in a moment."
)
RESUME_NO_CAMPAIGN = (
    "🔄 I restarted, but the campaign that was playing isn't here any more, so I didn't "
    "rejoin. Use `/dmbot start` to begin again."
)


def resume_no_voice_message(campaign_name: str, voice_channel_id: int) -> str:
    return (
        f"🔄 I restarted, but I can't rejoin <#{voice_channel_id}> for **{campaign_name}** "
        "any more (the channel is gone or I'm not allowed in). Use `/dmbot start` to "
        "begin again."
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
    resumed: bool = False  # picked up again after a restart

    def is_dm(self, user_id: int) -> bool:
        return user_id == self.dm_user_id or user_id in self.dm_user_ids


class DMBotTree(app_commands.CommandTree["DMBot"]):
    """Tags every slash-command log line with the server it came from."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        set_log_context(guild_id=interaction.guild_id)
        return True


class DMBot(commands.AutoShardedBot):
    """One process serving the shards in `settings.shards` (one shard by default).

    Per-server state (sessions, locks, loops) only exists for servers on these shards,
    so nothing runs twice when several processes split the shards between them.
    """

    def __init__(
        self,
        settings: Settings,
        consent: ConsentStore,
        campaigns: CampaignStore,
        sessions: SessionStore,
        transcriber: Transcriber | None = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True  # who is in which voice channel; not privileged
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            shard_count=settings.shards.count,
            shard_ids=list(settings.shards.ids),
            tree_cls=DMBotTree,
        )
        self.settings = settings
        self.consent = consent
        self.campaigns = campaigns
        self.sessions = sessions
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
            shards=settings.shards,
            on_message=self._on_ears_message,
            on_audio=self._on_audio,
            on_link_change=self._on_ears_link_change,
        )
        self._background: list[asyncio.Task[None]] = []
        self._session_locks: dict[int, asyncio.Lock] = {}
        self._resume_done = False

    # ---- lifecycle ---------------------------------------------------------

    async def setup_hook(self) -> None:
        self.tree.add_command(dmbot_group)
        self.tree.add_command(consent_group)
        if self.settings.dev_guild_id:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        elif 0 in self.settings.shards.ids:
            # Commands are registered once for the whole bot, not per shard: only the
            # pod serving shard 0 does it, so many pods don't all sync at startup.
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

    def session_lock(self, guild_id: int) -> asyncio.Lock:
        """Held while a session starts or stops, or a campaign is replaced, per server."""
        return self._session_locks.setdefault(guild_id, asyncio.Lock())

    def start_blocker(self, guild_id: int) -> str | None:
        """Why `/dmbot start` can't begin right now, or None if it can."""
        if not self.ears.connected:
            return EARS_DOWN
        table = self.tables.get(guild_id)
        if table is not None:
            name = f"**{table.campaign_name}** " if table.campaign_name else ""
            return (
                f"DMbot is already running {name}in <#{table.voice_channel_id}>. "
                "Its DM can stop it with `/dmbot stop`."
            )
        return None

    async def start_campaign_session(
        self, interaction: discord.Interaction, campaign_id: str, voice_id: int
    ) -> tuple[bool, str]:
        """Start listening for a campaign. Returns (started, message for the DM).

        Runs under the server's session lock, so two people pressing Start at the same
        moment can't both start a session.
        """
        guild = interaction.guild
        if guild is None:
            return False, "Use this in a server."
        async with self.session_lock(guild.id):
            with log_context(guild_id=guild.id, campaign_id=campaign_id):
                return await self._start_locked(interaction, guild, campaign_id, voice_id)

    async def _start_locked(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        campaign_id: str,
        voice_id: int,
    ) -> tuple[bool, str]:
        problem = self.start_blocker(guild.id)
        if problem:
            return False, problem
        campaign = await self.campaigns.get(guild.id, campaign_id)
        if campaign is None:
            return False, "That campaign isn't here any more. Run `/dmbot start` again."
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False, "Use this in a server."
        if not ui_logic.can_run(campaign, user.id, user.guild_permissions.manage_guild):
            return False, ui_logic.NO_CAMPAIGN_ACCESS
        me = cast(discord.Member | None, guild.me)  # None while the guild is still loading
        if me is None:
            return False, STARTING_UP
        voice = guild.get_channel(voice_id)
        if not isinstance(voice, discord.VoiceChannel | discord.StageChannel):
            return False, "I can't find that voice channel. Pick another one."
        # The DM must be able to join the channel too, so nobody can point DMbot at a
        # private voice channel they aren't allowed in.
        user_perms = voice.permissions_for(user)
        if not (user_perms.view_channel and user_perms.connect):
            return False, (
                f"You can't join {voice.mention} yourself, so DMbot can't use it for your "
                "table. Pick a voice channel you can join."
            )
        voice_perms = voice.permissions_for(me)
        if not (voice_perms.view_channel and voice_perms.connect):
            return False, (
                f"I can't join {voice.mention}. Give me **View Channel** and **Connect** "
                "there (Edit Channel → Permissions), then press Start again."
            )

        screen_id = await ensure_dm_screen(self, interaction, campaign)
        if screen_id is None:
            return False, (
                "I couldn't find a channel for DM notes. Run `/dmbot start` from a "
                "private channel only you can see."
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
            dm_user_id=user.id,
            segmenter=Segmenter(guild.id),
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            dm_user_ids=campaign.dm_user_ids,
        )
        # Save before joining, so a restart can always pick the session up again.
        try:
            await self.sessions.save(
                SavedSession(
                    guild_id=guild.id,
                    campaign_id=campaign.id,
                    voice_channel_id=voice.id,
                    screen_channel_id=screen_id,
                    started_by=user.id,
                    started_at=int(time.time()),
                    notice_posted=False,
                )
            )
        except Exception:
            log.exception("Couldn't save the new session")
            return False, SAVE_FAILED
        await self.start_table(table)
        return True, (
            f"▶ Listening to **{campaign.name}** in {voice.mention}.\n"
            f"DM notes go to <#{screen_id}>"
            f"{ui_logic.screen_note(campaign.dm_screen_visibility)}. "
            "Only players who said yes are recorded."
        )

    async def stop_session(self, guild_id: int, user_id: int, is_server_manager: bool) -> str:
        async with self.session_lock(guild_id):
            table = self.tables.get(guild_id)
            if table is None:
                return "DMbot isn't listening right now."
            if not (table.is_dm(user_id) or is_server_manager):
                return (
                    "Only the DM can stop the session. To stop recording *you*, "
                    "use `/consent revoke`."
                )
            # Forget the saved session first: if that fails, keep listening rather than
            # stop now and come back by surprise after the next restart.
            try:
                await self.sessions.clear(guild_id)
            except Exception:
                log.exception("Couldn't clear the saved session")
                return STOP_FAILED
            await self.stop_table(guild_id)
        name = f" to **{table.campaign_name}**" if table.campaign_name else ""
        return f"Stopped listening{name}. See you next session! 👋"

    async def status_lines(self, guild_id: int) -> list[str]:
        """Plain-language status for the Status button. Raw counters go to the log."""
        table = self.tables.get(guild_id)
        lines: list[str] = []
        if not self.ears.connected:
            lines.append(
                "⚠️ DMbot's listening part isn't running. Whoever hosts DMbot needs to restart it."
            )
        if table is None:
            lines.append("🎙 Not listening. Use `/dmbot start` to begin.")
        else:
            campaign = f" to **{table.campaign_name}**" if table.campaign_name else ""
            joining = "" if table.listening else " (joining…)"
            lines.append(
                f"🎙 Listening{campaign} in <#{table.voice_channel_id}>{joining} · "
                f"DM notes go to <#{table.screen_channel_id}>"
            )
        users = await self.consent.consenting(guild_id)
        if users:
            names = ", ".join(sorted(self.name_of(guild_id, u) for u in users))
            lines.append(f"Recording only: {names} (people who said yes)")
        else:
            lines.append("Nobody has said yes to recording yet.")
        p = self.pipeline
        if table is not None:
            if p.backlog >= 16:
                lines.append("⚠️ Writing things down is falling behind.")
            else:
                behind = (
                    f" (about {p.last_latency_s:.0f} s behind)"
                    if p.last_latency_s is not None and p.last_latency_s >= 1
                    else ""
                )
                lines.append(f"Keeping up: yes{behind}")
        if p.dropped or self.ears.rejected_frames or p.total_failures:
            log.info(
                "Status for guild %s: dropped=%d failures=%d bad_frames=%d backlog=%d",
                guild_id,
                p.dropped,
                p.total_failures,
                self.ears.rejected_frames,
                p.backlog,
            )
            lines.append(
                "⚠️ Some speech was missed. If this keeps happening, use `/dmbot stop` "
                "then `/dmbot start`."
            )
        return lines

    # ---- resuming after a restart ------------------------------------------

    async def on_ready(self) -> None:
        # on_ready can fire again after Discord reconnects; resume only once.
        if self._resume_done:
            return
        self._resume_done = True
        try:
            await self.resume_sessions()
        except Exception:
            log.exception("Couldn't resume saved sessions")

    async def resume_sessions(self) -> int:
        """Rejoin the sessions that were running on this process's shards. Returns how
        many were resumed."""
        resumed = 0
        for guild_id in await self.sessions.guilds_to_resume(self.settings.shards):
            async with self.session_lock(guild_id):
                with log_context(guild_id=guild_id):
                    try:
                        if await self._resume_one(guild_id):
                            resumed += 1
                    except Exception:
                        log.exception("Couldn't resume this server's session")
        if resumed:
            log.info("Resumed %d session(s)", resumed)
        return resumed

    async def _resume_one(self, guild_id: int) -> bool:
        if guild_id in self.tables:
            return False  # already started again (e.g. by /dmbot start)
        saved = await self.sessions.get(guild_id)
        if saved is None:  # only the routing entry was left behind
            await self.sessions.clear(guild_id)
            return False
        guild = self.get_guild(guild_id)
        if guild is None:
            log.info("Not resuming: DMbot is no longer in this server")
            await self.sessions.clear(guild_id)
            return False
        campaign = await self.campaigns.get(guild_id, saved.campaign_id)
        if campaign is None:
            await self.sessions.clear(guild_id)
            await self.post(saved.screen_channel_id, RESUME_NO_CAMPAIGN)
            return False
        with log_context(campaign_id=campaign.id):
            me = cast(discord.Member | None, guild.me)
            voice = guild.get_channel(saved.voice_channel_id)
            can_join = (
                me is not None
                and isinstance(voice, discord.VoiceChannel | discord.StageChannel)
                and voice.permissions_for(me).view_channel
                and voice.permissions_for(me).connect
            )
            if not can_join:
                log.info("Not resuming: can't rejoin the voice channel")
                await self.sessions.clear(guild_id)
                await self.post(
                    saved.screen_channel_id,
                    resume_no_voice_message(campaign.name, saved.voice_channel_id),
                )
                return False
            table = Table(
                guild_id=guild_id,
                voice_channel_id=saved.voice_channel_id,
                screen_channel_id=saved.screen_channel_id,
                dm_user_id=saved.started_by,
                segmenter=Segmenter(guild_id),
                notice_posted=saved.notice_posted,
                campaign_id=campaign.id,
                campaign_name=campaign.name,
                dm_user_ids=campaign.dm_user_ids,
                resumed=True,
            )
            # If ears isn't connected yet, it gets the join when it connects.
            await self.start_table(table)
            log.info("Resuming session")
            return True

    async def _remember_notice(self, guild_id: int) -> None:
        try:
            await self.sessions.mark_notice_posted(guild_id)
        except Exception:
            # Worst case after a restart: players see the notice once more.
            log.exception("Couldn't save that the recording notice was posted")

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
        # Runs in the ears connection's task, which lives on: scope the IDs per table.
        for table in list(self.tables.values()):
            with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
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
        with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
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
            if table.resumed:
                table.resumed = False
                await self.post(
                    table.screen_channel_id,
                    f"🔄 I restarted and picked up{campaign} where we left off, in "
                    f"<#{table.voice_channel_id}>. A few seconds of what was said may be "
                    "missing.",
                )
            else:
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
                if table.notice_posted:
                    await self._remember_notice(table.guild_id)
                else:
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
            for table in list(self.tables.values()):
                with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
                    for utterance in table.segmenter.flush_idle(now_ms):
                        self.pipeline.enqueue(utterance)

    async def _summary_poster(self) -> None:
        while True:
            await asyncio.sleep(SUMMARY_INTERVAL_S)
            for table in list(self.tables.values()):
                with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
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
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await bot.consent.grant(gid, interaction.user.id)
    except Exception:
        log.exception("Couldn't save consent in guild %s", gid)
        await interaction.followup.send(
            "Sorry, DMbot couldn't save that just now, so it is **not** recording you. "
            "Please try `/consent give` again in a minute.",
            ephemeral=True,
        )
        return
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
    await interaction.followup.send(message, ephemeral=True)


@consent_group.command(
    name="revoke", description="Stop DMbot from recording your voice in this server"
)
async def consent_revoke(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    if interaction.guild is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    gid, uid = interaction.guild.id, interaction.user.id
    # Stop first, before anything that can be slow or fail.
    bot.consent.stop_now(gid, uid)
    table = bot.tables.get(gid)
    if table is not None:
        table.segmenter.drop(uid)
        with contextlib.suppress(Exception):
            await bot.push_allowlist(gid)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await bot.consent.revoke(gid, uid)
    except Exception:
        log.exception("Couldn't save a consent revoke in guild %s", gid)
        await interaction.followup.send(
            "DMbot has stopped recording you and discarded any unprocessed audio, but "
            "couldn't save that. Please run `/consent revoke` again in a minute so it "
            "sticks after a restart.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        "Done — DMbot has stopped recording you and discarded any unprocessed audio.",
        ephemeral=True,
    )


async def run(settings: Settings) -> None:
    db = await Database.open(settings.database_url)
    try:
        transcriber = build_transcriber(settings.transcription)
        await transcriber.warm_up()  # load the Whisper model now, not on the first word
        bot = DMBot(settings, ConsentStore(db), CampaignStore(db), SessionStore(db), transcriber)
        async with bot:
            await bot.start(settings.discord_token)
    finally:
        with contextlib.suppress(Exception):
            await db.close()
