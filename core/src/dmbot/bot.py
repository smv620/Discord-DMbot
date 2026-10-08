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
import signal
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, cast

import discord
from discord import app_commands
from discord.ext import commands

from dmbot import install
from dmbot.ai import AnthropicClient
from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.campaigns import Campaign, CampaignStore
from dmbot.capture_log import CaptureLog, SessionTotals
from dmbot.channel_access import (
    SAME_CHANNEL,
    STARTING_UP,
    join_blocked_message,
    missing_post_permissions,
    notice_failed_message,
    post_problems,
)
from dmbot.config import Settings
from dmbot.consent import ConsentMethod, ConsentStore
from dmbot.consent_dm import (
    ALREADY_RECORDED,
    REASK_INTRO,
    ConsentButton,
    DeclineButton,
    StopButton,
    confirmed_text,
    reminder_text,
    renewed_text,
    request_text,
    request_view,
    send_prompt,
    stop_view,
    unreachable_text,
)
from dmbot.db import Database
from dmbot.dm_screen import (
    DMScreenError,
    HideButton,
    PeekButton,
    StopListeningButton,
    VisibilityButton,
    ensure_dm_screen,
    peek_view,
    stop_listening_view,
)
from dmbot.dm_screen import messages as screen_messages
from dmbot.dm_screen.name_questions import (
    FixUndoButton,
    NameAnswerUndoButton,
    NameQuestionButton,
    fix_notes_view,
    question_view,
)
from dmbot.dm_screen.transcript_channel import (
    TranscriptChannelError,
    is_transcript_name,
    setup_transcript_channel,
)
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
from dmbot.memory.backup import MemorySection
from dmbot.memory.lookup import CampaignLookup, LookupCache
from dmbot.memory.models import DM, FIX, KEEP, Heard, MemoryRuleError, name_key
from dmbot.memory.scan import MAX_SUGGESTIONS, find_new_names, group_alike
from dmbot.memory.scene import PLAYER_CHARACTER, HintParts, SceneTracker, mentions, scene_hints
from dmbot.memory.scene import prepare as prepare_hints
from dmbot.memory.store import MemoryStore
from dmbot.sessions import SavedSession, SessionStore
from dmbot.transcript import fix_notes
from dmbot.transcript import questions as name_questions
from dmbot.transcript import stream as transcript_lines
from dmbot.transcript.cleaner import (
    Cleaned,
    Vocabulary,
    clean,
    own_name,
    safe_answer,
    with_ending,
)
from dmbot.transcript.models import Line, TranscriptBuffer
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.stream import TranscriptStream
from dmbot.transcription.base import PlaceholderTranscriber, Transcriber
from dmbot.transcription.factory import build_transcriber
from dmbot.transcription.pipeline import TranscriptionPipeline
from dmbot.ui import logic as ui_logic
from dmbot.ui.dmbot_commands import dmbot_group
from dmbot.ui.name_card import UndoButton
from dmbot.ui.name_lists import UndoListButton
from dmbot.ui.names import ReviewButton, after_session_text, review_view
from dmbot.ui.optional_rules import dmbot_optional_rules  # noqa: F401 (registers it)
from dmbot.ui.transcripts import DownloadButton, download_view, ended_text, transcript_command

log = logging.getLogger(__name__)

SUMMARY_INTERVAL_S = 15
# Lines for the transcript channel are grouped and posted this often (#124): well inside
# Discord's 5 messages per 5 seconds per channel, and still feels live.
TRANSCRIPT_FLUSH_S = 2.0
TRANSCRIPT_SAVE_S = 5.0  # stored transcript lines are saved in batches this often
HEARD_MAX = 20_000  # lines kept for the after-session name scan
HINTS_FAIL_LOG_S = 60.0
EDIT_TIMEOUT_S = 5.0  # a transcript message edit, at most (it holds the post lock)
HINT_PEOPLE_S = 5.0  # who's in the voice channel, for name hints: looked at this often
HintPeople = tuple[tuple[str, ...], tuple[str, ...]]  # (at the table, agreed but not there)
TRANSCRIPT_POST_TIMEOUT_S = 10.0  # one stuck post can't hold the others up for long
TRANSCRIPT_PARALLEL = 10  # campaigns posting at once (one rate-limited channel can't stall all)
STOP_DRAIN_TIMEOUT_S = 120.0  # at stop, wait this long for the last words to be written
FINAL_FLUSH_TIMEOUT_S = 15.0  # at stop or shutdown, give up on posting after this
IDLE_SWEEP_INTERVAL_S = 1
NO_PINGS = discord.AllowedMentions.none()

EARS_DOWN = (
    "The voice service (ears) isn't connected. Start it from the `ears` folder "
    "with `npm run dev`, then try again."
)


SAVE_FAILED = "I couldn't start just now. Try `/dmbot start` again in a moment."
STOP_FAILED = (
    "⚠️ I'm **still listening**. I couldn't stop just now. Try `/dmbot stop` again in a moment."
)


def resume_no_voice_message(campaign_name: str, voice_channel_id: int) -> str:
    return (
        f"🔄 I restarted, but I can't get back into <#{voice_channel_id}> for "
        f"**{campaign_name}**, so I'm not listening. Check the channel is still there and "
        "that I'm allowed to join it, then use `/dmbot start`."
    )


def resume_too_old_message(campaign_name: str) -> str:
    return (
        f"🔄 I restarted, but the **{campaign_name}** session started too long ago to pick "
        "up, so I'm not listening. Use `/dmbot start` when you play next."
    )


def resume_gave_up_message(campaign_name: str) -> str:
    return (
        f"⚠️ I kept restarting during **{campaign_name}**, so I've stopped listening to be "
        "safe. Use `/dmbot start` to try again. If it keeps happening, tell whoever runs "
        "DMbot."
    )


def resumed_message(campaign_name: str, voice_channel_id: int) -> str:
    name = f" for **{campaign_name}**" if campaign_name else ""
    return (
        f"🔄 I restarted and I'm listening again in <#{voice_channel_id}>{name}. "
        "I may have missed a few seconds of talk."
    )


# A session older than this isn't resumed (nobody plays that long without a break).
MAX_RESUME_AGE_S = 16 * 3600
# Resumes closer together than this count as one streak...
RESUME_STREAK_WINDOW_S = 30 * 60
# ...and a streak this long means DMbot keeps crashing: stop instead of looping.
MAX_RESUMES_IN_A_ROW = 5
# Quiet repeat restarts: only post "listening again" if the last resume wasn't this recent.
RESUME_QUIET_S = 10 * 60
# Space out rejoining voice channels, so many sessions don't all hit Discord at once.
RESUME_SPACING_S = 0.5
# Retry the startup resume when the database or Discord isn't ready yet.
RESUME_RETRY_DELAYS_S = (5, 15, 30, 60, 120, 300)


def silence_voice_warnings() -> None:
    """Turn off discord.py's 'PyNaCl/davey is not installed, voice will NOT be
    supported' warnings (#38).

    core never joins voice; ears does. The warnings are harmless here but read like a
    voice failure. Don't install PyNaCl or davey in core to hide them: core doesn't use
    them. `warn_dave` only exists in newer discord.py versions.
    """
    for flag in ("warn_nacl", "warn_dave"):
        if hasattr(discord.VoiceClient, flag):
            setattr(discord.VoiceClient, flag, False)


@dataclass(slots=True)
class Table:
    guild_id: int
    voice_channel_id: int
    screen_channel_id: int
    dm_user_id: int
    segmenter: Segmenter
    capture_log: CaptureLog = field(default_factory=CaptureLog)
    # Names said lately, for hints that follow the scene (#126, #127), and the campaign's
    # names as last loaded (matching a line needs them without waiting).
    scene: SceneTracker = field(default_factory=SceneTracker)
    name_lookup: CampaignLookup | None = None
    hint_parts: HintParts | None = None
    # For the Transcript Cleaner (#127): what this session's lines say about words, and
    # the display names of people who agreed (never "fixed" into a name).
    vocabulary: Vocabulary = field(default_factory=Vocabulary)
    # "Did they mean…?" for the DM (#296): one open at a time, each word once a session.
    questions: name_questions.QuestionBook = field(default_factory=name_questions.QuestionBook)
    question_message: discord.Message | None = None
    # Fixes from names DMbot only suggested, each with Undo, in one DM-screen message.
    fix_notes: fix_notes.FixNotes = field(default_factory=fix_notes.FixNotes)
    fix_message: discord.Message | None = None
    fix_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    fix_queued: bool = False  # a redraw is waiting: more changes just ride along
    fix_ended: bool = False  # the session is over: the list stays, the Undo buttons go
    people: tuple[str, ...] = ()
    # The whole session's numbers, for the summary when it ends (#109).
    totals: SessionTotals = field(default_factory=SessionTotals)
    listening: bool = False
    notice_posted: bool = False
    peek_offered: bool = False  # players have been shown the Peek button this session
    campaign_id: str | None = None
    campaign_name: str = ""
    dm_user_ids: frozenset[int] = frozenset()
    resumed: bool = False  # picked up again after a restart
    announce_resume: bool = True  # post "listening again" when voice is back
    # Asked privately about recording (or reminded) this session: at most once each.
    asked: set[int] = field(default_factory=set)
    # The live transcript channel (#124); None if it couldn't be set up this session.
    transcript_channel_id: int | None = None
    transcript: TranscriptStream = field(default_factory=TranscriptStream)
    transcript_lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # keeps order
    # What was heard this session (speaker, text as heard), for the after-session scan
    # that suggests new names to the DM (#126). Kept in memory only, capped.
    heard: list[tuple[int, str]] = field(default_factory=list)
    # How many lines of each speaker named each known name, kept at the end of the
    # session so hints can rank names by how often and when they come up. Bounded by
    # names times speakers.
    heard_counts: Counter[tuple[str, int]] = field(default_factory=Counter)
    # The stored transcript (#41, #125): this session's row, and lines not saved yet.
    started_at: int = 0  # Unix seconds; the same after a restart
    transcript_session_id: str | None = None  # set at the first save
    unsaved: TranscriptBuffer = field(default_factory=TranscriptBuffer)
    transcript_warned: bool = False  # told the DM saving isn't working
    listening_message: discord.Message | None = None  # carries the Stop button (#108)
    dropped_logged: bool = False
    save_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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
        memory: MemoryStore | None = None,
        transcripts: TranscriptStore | None = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True  # who is in which voice channel; not privileged
        silence_voice_warnings()  # before super().__init__, which logs them
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            shard_count=settings.shards.count,
            shard_ids=list(settings.shards.ids),
            tree_cls=DMBotTree,
        )
        self.settings = settings
        # With an outside speech-to-text company, only yeses given knowing that count
        # (#170). Set here, from the same settings, so the two can never disagree.
        consent.outside = settings.transcription.outside_engine
        self.consent = consent
        self.campaigns = campaigns
        self.sessions = sessions
        # Campaign memory (#126): names the DM gives, suggestions after each session, and
        # an in-memory copy of each campaign's names, kept up to date by notifications.
        self.memory = memory
        self.lookup = LookupCache(memory) if memory is not None else None
        # AI text calls (a document into a names list); None when no key is set.
        self.ai = AnthropicClient(settings.ai_key, settings.ai_model) if settings.ai_key else None
        self._hints_failed_at = -HINTS_FAIL_LOG_S
        # Per server: (when, who agreed, (names at the table, names not there)).
        self._hint_people_cache: dict[
            int, tuple[float, frozenset[int], int | None, HintPeople]
        ] = {}
        self._clean_failed_at = -HINTS_FAIL_LOG_S
        # Stored session transcripts anyone in the server can download (#41, #125).
        self.transcripts = transcripts
        self.tables: dict[int, Table] = {}
        self.pipeline = TranscriptionPipeline(
            transcriber or PlaceholderTranscriber(),
            consent,
            # Stopped sessions stay active while their last words are written (#109).
            is_active=lambda utterance: self._table_for(utterance) is not None,
            hints=self._name_hints,
            deliver=self._deliver_transcript,
            alert=self._alert_dm,
            outside=settings.transcription.sends_audio_out,
            workers=settings.transcription.workers,
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
        self._asking: set[asyncio.Task[None]] = set()  # private-message rounds in flight
        self._finishing: set[asyncio.Task[None]] = set()  # stopped sessions winding down
        # Stopped sessions still writing down their last words, by server.
        self._ending: dict[int, list[Table]] = {}
        self._session_locks: dict[int, asyncio.Lock] = {}
        self._resume_started = False
        self._closing = False
        # Servers whose saved session is waiting for Discord to make the server available.
        self._resume_when_available: set[int] = set()

    # ---- lifecycle ---------------------------------------------------------

    async def setup_hook(self) -> None:
        # The application ID comes from Discord at login; messages use it for the
        # one-click install link, and the log shows it to whoever runs DMbot.
        install.configure(self.application_id)
        log.info(
            "Install link (add DMbot to a server, or fix its permissions): %s",
            install.install_link(),
        )
        self.tree.add_command(dmbot_group)
        self.tree.add_command(consent_group)
        self.tree.add_command(transcript_command)
        # DM-screen buttons keep working after a restart.
        self.add_dynamic_items(PeekButton, HideButton, VisibilityButton, StopListeningButton)
        # Consent buttons in private messages, likewise.
        self.add_dynamic_items(ConsentButton, DeclineButton, StopButton)
        # "Check new names" on the DM screen after a session.
        self.add_dynamic_items(ReviewButton)
        # Undo after forgetting a name (its card), after a restart too.
        self.add_dynamic_items(UndoButton, UndoListButton)
        # "Download transcript" in the private message when a session ends.
        self.add_dynamic_items(DownloadButton)
        self.add_dynamic_items(NameQuestionButton, NameAnswerUndoButton, FixUndoButton)
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
            *(
                [asyncio.create_task(self.lookup.follow(self.memory.listen), name="names")]
                if self.lookup is not None and self.memory is not None
                else []
            ),
            asyncio.create_task(self._transcript_poster(), name="transcripts"),
            asyncio.create_task(self._transcript_saver(), name="transcript-saves"),
        ]

    async def close(self) -> None:
        if self._closing:  # SIGTERM and the normal exit can both call this
            return
        self._closing = True
        # Stopped sessions stop waiting for their last words and finish now (saving
        # first), alongside everything below.
        self.pipeline.stop_waiting()
        # Post what's waiting for each transcript channel (the sessions resume after the
        # restart, so no "ended" divider), but never hold up shutdown for long.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                asyncio.gather(*(self.flush_transcript(t) for t in list(self.tables.values()))),
                FINAL_FLUSH_TIMEOUT_S / 3,
            )
        # Save what's waiting for the stored transcripts likewise (not ended: they
        # carry on after the restart), and finish sessions that were just stopped.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                asyncio.gather(
                    *(self.save_transcript(t) for t in list(self.tables.values())),
                    *self._finishing,
                ),
                FINAL_FLUSH_TIMEOUT_S,
            )
        for task in [*self._background, *self._asking]:
            task.cancel()
        await asyncio.gather(*self._background, *self._asking, return_exceptions=True)
        await self.pipeline.transcriber.close()
        if self.ai is not None:
            await self.ai.close()
        for guild_id in list(self.tables):
            await self.ears.send(leave_command(guild_id))
        await self.ears.stop()
        await super().close()

    # ---- table control -----------------------------------------------------

    async def push_allowlist(self, guild_id: int) -> None:
        users = await self.consent.consenting(guild_id)
        await self.ears.send(allowlist_command(guild_id, users))

    # ---- consent (slash commands and private-message buttons) ---------------

    @property
    def sends_audio_out(self) -> bool:
        """Another company writes things down (TRANSCRIBER=deepgram or cloud)."""
        return self.settings.transcription.sends_audio_out

    @property
    def outside_engine(self) -> str | None:
        return self.settings.transcription.outside_engine

    @property
    def company(self) -> str | None:
        return self.settings.transcription.company

    async def give_consent(
        self, guild_id: int, user_id: int, method: ConsentMethod, *, outside_to: str | None
    ) -> int:
        """Save consent and start capturing at once. Returns when it was given.

        `outside_to`: the outside engine the request they agreed to named, if any.
        Raises if it couldn't be saved; then nothing changed.
        """
        already = self.consent.has_consent(guild_id, user_id)
        recorded = await self.consent.grant(guild_id, user_id, method=method, outside_to=outside_to)
        log.info("Consent given: user %s", user_id)
        # Only a yes that really counts (it names this server's speech-to-text, and no
        # stop arrived meanwhile) is shown as recording.
        if user_id in recorded and not already:
            self._tell_dm_about_consent(guild_id, user_id, agreed=True)
        # Always tell ears (if connected), even with no session here: it may still be
        # in voice from before a restart.
        with contextlib.suppress(Exception):
            await self.push_allowlist(guild_id)
        try:
            when = await self.consent.granted_at(guild_id, user_id)
        except Exception:
            when = None  # saved; only the date for the reply is missing
        return when or int(time.time())

    def stop_recording(self, guild_id: int, user_id: int) -> None:
        """Stop capturing this player now, without waiting for anything.

        Callers run this before their first await; `withdraw_consent` repeats it.
        """
        self.consent.stop_now(guild_id, user_id)
        for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
            if table is None:
                continue
            table.segmenter.drop(user_id)
            table.transcript.drop_speaker(user_id)  # words not posted yet are discarded
            table.heard = [h for h in table.heard if h[0] != user_id]  # and never scanned
            table.unsaved.drop_speaker(user_id)  # and never saved
            table.scene.forget_speaker(user_id)  # and no longer shape the hints
            table.vocabulary.forget_speaker(user_id)  # or the name fixes
            if table.fix_notes.drop_speaker(user_id):  # their fixes leave the DM screen
                self._redraw_fix_notes(table)
            if table.questions.drop_speaker(user_id, time.monotonic()) is not None:
                self._track(self._close_question(table), "name-question")
            for key in [k for k in table.heard_counts if k[1] == user_id]:
                del table.heard_counts[key]

    async def withdraw_consent(self, guild_id: int, user_id: int) -> bool:
        """Stop capturing at once, then save; True if they had consented. Raises if saving
        failed; the player stays stopped in this process either way."""
        was_recorded = self.consent.has_consent(guild_id, user_id)
        self.stop_recording(guild_id, user_id)
        if was_recorded:  # stopped here at once, even if saving fails below
            self._tell_dm_about_consent(guild_id, user_id, agreed=False)
        log.info("Consent withdrawn: user %s", user_id)
        with contextlib.suppress(Exception):
            await self.push_allowlist(guild_id)
        try:
            return await self.consent.revoke(guild_id, user_id)
        finally:
            # Again, in case a grant that was saving meanwhile sent ears an older list.
            with contextlib.suppress(Exception):
                await self.push_allowlist(guild_id)

    def _track(self, work: Awaitable[Any], name: str) -> None:
        """Run `work` in the background; close() cancels it. Failures are logged."""
        if self._closing:
            return

        async def guarded() -> None:
            try:
                await work
            except Exception:
                log.exception("Background %s failed", name)

        task = asyncio.create_task(guarded(), name=name)
        self._asking.add(task)
        task.add_done_callback(self._asking.discard)

    def start_asking(
        self, table: Table, members: list[discord.Member], *, only_renewals: bool = False
    ) -> None:
        """Ask in the background: Discord calls must never hold up the voice link."""
        if self._closing:
            return  # close() may already be gathering; a new task would never be cancelled
        task = asyncio.create_task(
            self.ask_for_consent(table, members, only_renewals=only_renewals),
            name="ask-consent",
        )
        self._asking.add(task)
        task.add_done_callback(self._asking.discard)

    async def ask_for_consent(
        self, table: Table, members: list[discord.Member], *, only_renewals: bool = False
    ) -> None:
        """Privately ask each person about recording, or remind them they agreed.

        Once per person per session; never bots. Anyone DMbot couldn't reach is named in
        the DM screen and asked again if they rejoin. Anyone who said yes only under older
        wording is asked again, and the DM screen says why they aren't recorded yet.
        `only_renewals` (after a restart, when everyone else was already asked) asks just
        those people.
        """
        gid = table.guild_id
        people = [m for m in members if not m.bot and m.id not in table.asked]
        if not people:
            return
        table.asked.update(m.id for m in people)  # before any await: nobody asked twice
        with log_context(guild_id=gid, campaign_id=table.campaign_id):
            try:
                status = await self.consent.status(gid, [m.id for m in people])
            except Exception:
                log.exception("Couldn't look up consent; they'll be asked when they rejoin")
                table.asked.difference_update(m.id for m in people)
                return
            times, renew = status.granted, status.ask_again
            if only_renewals:
                table.asked.difference_update(m.id for m in people if m.id not in renew)
                people = [m for m in people if m.id in renew]
                if not people:
                    return
            voice = self.get_channel(table.voice_channel_id)
            voice_name = voice.name if isinstance(voice, discord.abc.GuildChannel) else None
            dm_name = self.name_of(gid, table.dm_user_id)
            cloud = self.sends_audio_out
            company = self.company
            dms_off: list[str] = []
            failed: list[str] = []
            for member in people:
                if self.tables.get(gid) is not table:
                    return  # the session ended: say nothing more
                # Re-checked now, after the awaits above: a revoke may have landed.
                granted = times.get(member.id)
                server = member.guild.name
                if granted is not None and self.consent.has_consent(gid, member.id):
                    text = reminder_text(server, voice_name, granted, cloud=cloud, company=company)
                    view = stop_view(gid)
                else:
                    text = request_text(
                        server,
                        voice=voice_name,
                        dm=dm_name,
                        cloud=cloud,
                        renewed=member.id in status.outdated,  # the "What's new" note
                        company=company,
                    )
                    if cloud and member.id in status.other_company:
                        text = f"{REASK_INTRO}\n\n{text}"  # why they're asked again
                    view = request_view(gid, outside=self.outside_engine)
                result = await send_prompt(member, text, view)
                if result != "sent":
                    table.asked.discard(member.id)  # try again if they rejoin
                    (dms_off if result == "dms_off" else failed).append(member.display_name)
            renewed = [m.display_name for m in people if m.id in renew]
            notes = [n for n in (renewed_text(renewed), unreachable_text(dms_off, failed)) if n]
            if notes and self.tables.get(gid) is table:
                await self.post(table.screen_channel_id, "\n".join(notes))

    async def _post_listening(self, table: Table, text: str) -> None:
        """The DM screen's "listening" message, with a Stop listening button (#108). The
        button comes off when the session ends, so old messages can't be pressed."""
        view = stop_listening_view(table.campaign_id) if table.campaign_id else None
        message = await self.post_message(table.screen_channel_id, text, view)
        if message is None:
            return
        await self._remove_stop_button(table)  # only the newest one keeps it
        table.listening_message = message
        if self.tables.get(table.guild_id) is not table:  # stopped while posting
            await self._remove_stop_button(table)

    @staticmethod
    async def _remove_stop_button(table: Table) -> None:
        message, table.listening_message = table.listening_message, None
        if message is not None:
            with contextlib.suppress(discord.HTTPException):
                await message.edit(view=None)

    def _people_in_voice(self, table: Table) -> list[int]:
        """People (not bots) in the session's voice channel right now."""
        voice = self.get_channel(table.voice_channel_id)
        if not isinstance(voice, discord.VoiceChannel | discord.StageChannel):
            return []
        return [m.id for m in voice.members if not m.bot]

    def _tell_dm_about_consent(self, guild_id: int, user_id: int, *, agreed: bool) -> None:
        """During a session, the DM screen shows each yes and each stop as it happens
        (#107), so it stays a true picture of who is recorded."""
        table = self.tables.get(guild_id)
        # Only people at the table: someone elsewhere in the server isn't recorded now.
        if table is None or self._closing or user_id not in self._people_in_voice(table):
            return
        name = self.name_of(guild_id, user_id)
        text = (
            screen_messages.agreed_message(name)
            if agreed
            else screen_messages.stopped_message(name)
        )
        self._track(self.post(table.screen_channel_id, text), "consent-note")

    def _ask_everyone_in_voice(self, table: Table, *, only_renewals: bool = False) -> None:
        voice = self.get_channel(table.voice_channel_id)
        if isinstance(voice, discord.VoiceChannel | discord.StageChannel):
            self.start_asking(table, list(voice.members), only_renewals=only_renewals)

    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        table = self.tables.get(member.guild.id)
        if table is None or after.channel is None or after.channel.id != table.voice_channel_id:
            return
        if before.channel is not None and before.channel.id == after.channel.id:
            return  # mute, deafen and the like: not a join
        self.start_asking(table, [member])  # tracked, so close() cancels it
        if not member.bot and table.listening:
            # Keep the DM screen's picture of who is recorded true (#107).
            recorded = self.consent.has_consent(member.guild.id, member.id)
            text = (
                screen_messages.joined_recorded_message(member.display_name)
                if recorded
                else screen_messages.joined_not_recorded_message(member.display_name)
            )
            self._track(self.post(table.screen_channel_id, text), "join-note")

    async def start_table(self, table: Table) -> bool:
        """Register the session, send ears the consent list, then the join.

        If the consent list can't be loaded, nothing is left half-started.
        """
        self.tables[table.guild_id] = table
        self.pipeline.session_started(table.guild_id)  # told of an outage afresh (#470)
        try:
            await self.push_allowlist(table.guild_id)
        except BaseException:
            if self.tables.get(table.guild_id) is table:
                del self.tables[table.guild_id]
            raise
        sent = await self.ears.send(join_command(table.guild_id, table.voice_channel_id))
        with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
            log.info(
                "Session started%s: voice channel %s, DM screen %s",
                " again after a restart" if table.resumed else "",
                table.voice_channel_id,
                table.screen_channel_id,
            )
        return sent

    async def stop_table(self, guild_id: int, reason: str) -> Table | None:
        """End a running session. `reason` goes in the log (IDs only, no names)."""
        table = self.tables.pop(guild_id, None)
        if table is None:
            return None
        with log_context(guild_id=guild_id, campaign_id=table.campaign_id):
            log.info("Session ended: %s", reason)
        await self.ears.send(leave_command(guild_id))
        # Speech still being heard or written down is finished, not dropped (#109): the
        # session stays "ending" until the pipeline has caught up.
        self._ending.setdefault(guild_id, []).append(table)
        # An unanswered "Did they mean…?" ends too (an answer being saved still finishes).
        if not table.questions.answering:
            ended = table.questions.close(name_questions.ENDED, time.monotonic())
            if ended is not None:
                self._track(self._close_question(table, self._not_answered(table, ended)), "q")
        table.fix_ended = True  # the fixes stay listed; their Undo buttons go
        self._redraw_fix_notes(table)
        for utterance in table.segmenter.flush_all():
            self.pipeline.enqueue(utterance)
        # In the background: /dmbot stop must answer within Discord's 3 seconds. Kept
        # apart from other background work, so a shutdown right after a stop still
        # saves the end of the transcript.
        task = asyncio.create_task(self.wind_down(table, int(time.time())), name="wind-down")
        self._finishing.add(task)
        task.add_done_callback(self._finishing.discard)
        return table

    async def wind_down(self, table: Table, ended_at: int) -> None:
        """After a stop: wait for the last words, then save and end the stored transcript
        (private download messages), end the transcript channel, post the summary, and
        suggest new names. Each step runs even if an earlier one fails. On shutdown the
        wait is cut short, and saving comes first."""
        gid = table.guild_id
        session = table.segmenter.session
        with log_context(guild_id=gid, campaign_id=table.campaign_id):
            try:
                caught_up = await self.pipeline.drain(session, STOP_DRAIN_TIMEOUT_S)
                if not caught_up:
                    log.warning("Stopped before the last speech was written down")
                await self._after_session(table, ended_at, caught_up)
            finally:
                ending = self._ending.get(gid, [])
                if table in ending:
                    ending.remove(table)
                if not ending:
                    self._ending.pop(gid, None)
                self.pipeline.missed_in.pop(session, None)
                self.pipeline.failed_in.pop(session, None)

    async def _after_session(self, table: Table, ended_at: int, caught_up: bool) -> None:
        sent = 0

        async def stored() -> None:
            nonlocal sent
            sent = await self.finish_transcript(table, ended_at)

        async def channel() -> None:
            if table.transcript_channel_id is not None:
                table.transcript.add_divider(
                    transcript_lines.ended(table.campaign_name), at_ms=_now_ms() + 1
                )
                await asyncio.wait_for(self.flush_transcript(table), FINAL_FLUSH_TIMEOUT_S)

        steps: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
            ("stored transcript", stored),
            ("stop button", lambda: self._remove_stop_button(table)),
            ("capture check", lambda: self.post_summary(table)),
            ("final transcript", channel),
            ("summary", lambda: self.post_session_summary(table, ended_at, caught_up, sent)),
            ("names heard", lambda: self.keep_heard_names(table)),
            ("name scan", lambda: self.suggest_names(table)),
            ("stale flags", lambda: self.close_stale_flags(table)),
            ("old undo history", lambda: self.prune_old_changes(table)),
        ]
        for name, step in steps:
            try:
                await step()
            except Exception:
                log.exception("After the session: %s failed", name)

    async def post_session_summary(
        self, table: Table, ended_at: int, caught_up: bool, downloads_sent: int
    ) -> None:
        """One plain summary in the DM screen (#109): how long, who was recorded and how
        much, and anything that went wrong. Never anything that was said."""
        gid = table.guild_id
        spoke = [
            screen_messages.Spoke(self.name_of(gid, user_id), total.seconds, total.percent)
            for user_id, total in table.totals.speakers.items()
            if total.seconds > 0
        ]
        session = table.segmenter.session
        problems = screen_messages.summary_problems(
            self.pipeline.missed_in[session], self.pipeline.failed_in[session], caught_up
        )
        text = screen_messages.session_summary(
            table.campaign_name,
            table.started_at or ended_at,
            ended_at,
            spoke,
            problems,
            downloads_sent=downloads_sent,
        )
        await self.post(table.screen_channel_id, text)

    # ---- sessions (used by /dmbot start · stop · help) ---------------------

    def active_campaign_id(self, guild_id: int) -> str | None:
        table = self.tables.get(guild_id)
        return table.campaign_id if table else None

    async def is_campaign_playing(self, guild_id: int, campaign_id: str) -> bool:
        """Running here, or saved and about to be resumed after a restart."""
        if self.active_campaign_id(guild_id) == campaign_id:
            return True
        saved = await self.sessions.get(guild_id)
        return saved is not None and saved.campaign_id == campaign_id

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

        try:
            screen_id = await ensure_dm_screen(self, interaction, campaign)
        except DMScreenError as exc:
            return False, str(exc)
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
        transcript_id, transcript_problem = await self._transcript_channel(guild, campaign, screen)

        await self.campaigns.set_dm_screen(guild.id, campaign.id, screen_id)
        await self.campaigns.set_last_voice_channel(guild.id, campaign.id, voice.id)
        campaign = await self.campaigns.mark_played(guild.id, campaign.id)
        started_at = int(time.time())
        table = Table(
            guild_id=guild.id,
            voice_channel_id=voice.id,
            screen_channel_id=screen_id,
            dm_user_id=user.id,
            segmenter=Segmenter(guild.id),
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            dm_user_ids=campaign.dm_user_ids,
            transcript_channel_id=transcript_id,
            started_at=started_at,
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
                    started_at=started_at,
                    notice_posted=False,
                )
            )
        except Exception:
            log.exception("Couldn't save the new session")
            return False, SAVE_FAILED
        try:
            await self.start_table(table)
        except Exception:
            log.exception("Couldn't start the session")
            try:
                await self.sessions.clear(guild.id, "the session couldn't start")
            except Exception:
                log.exception("Couldn't forget the session that failed to start")
            return False, SAVE_FAILED
        if transcript_problem:
            await self.post(screen_id, transcript_problem)
        transcript_line = (
            f"📜 Transcript (anyone in the server can read it): <#{transcript_id}>\n"
            if transcript_id
            else f"⚠️ No live transcript this time. See <#{screen_id}> for why.\n"
        )
        return True, (
            f"▶ Listening to **{campaign.name}** in {voice.mention}.\n"
            f"{transcript_line}🛡️ Notes for the DM: <#{screen_id}>"
            f"{ui_logic.screen_note(campaign.dm_screen_visibility)}\n"
            "Only people who said yes are recorded, the DM included."
        )

    async def stop_session(
        self,
        guild_id: int,
        user_id: int,
        is_server_manager: bool,
        *,
        campaign_id: str | None = None,
    ) -> str:
        """`campaign_id`: only stop if this campaign is the one being listened to (a Stop
        button on an older message must never end a newer session)."""
        async with self.session_lock(guild_id):
            table = self.tables.get(guild_id)
            if campaign_id is not None and (table is None or table.campaign_id != campaign_id):
                return screen_messages.NOT_LISTENING_NOW
            if table is None:
                # Maybe a saved session that hasn't been picked up again yet (DMbot is
                # restarting): stopping must still end it, or it would come back.
                return await self._stop_saved(guild_id, user_id, is_server_manager)
            if not (table.is_dm(user_id) or is_server_manager):
                return (
                    "Only the DM can stop the session. To stop recording *you*, press "
                    "**Stop recording me** in DMbot's private message, or use "
                    "`/consent revoke`."
                )
            # Forget the saved session first: if that fails, keep listening rather than
            # stop now and come back by surprise after the next restart.
            try:
                was_saved = await self.sessions.clear(guild_id, f"/dmbot stop by user {user_id}")
            except Exception:
                log.exception("Couldn't clear the saved session")
                return STOP_FAILED
            if not was_saved:
                # DMbot was listening, so this session should have been saved (#147).
                with log_context(guild_id=guild_id, campaign_id=table.campaign_id):
                    log.warning("Stopped a session that wasn't saved: it couldn't have resumed")
            await self.stop_table(guild_id, f"/dmbot stop by user {user_id}")
        name = f" to **{table.campaign_name}**" if table.campaign_name else ""
        saved = self.transcripts is not None and (
            table.transcript_session_id is not None or bool(table.unsaved)
        )
        download = (
            " Everyone recorded will get a private message with a button to download the "
            "transcript. Anyone in the server can also use `/transcript`."
            if saved
            else ""
        )
        return (
            f"Stopped listening{name}.{download} A short summary follows in the DM screen. "
            "See you next session! 👋"
        )

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
            lines.append(
                ui_logic.writing_status(  # this server's own queue (#173)
                    self.settings.transcription.engine,
                    p.backlog_of(guild_id),
                    p.latency_of.get(guild_id),
                )
            )
        if p.dropped or self.ears.rejected_frames or p.total_failures:
            log.info(
                "Status for guild %s: dropped=%d failures=%d bad_frames=%d backlog=%d",
                guild_id,
                p.dropped,
                p.total_failures,
                self.ears.rejected_frames,
                p.backlog_of(guild_id),
            )
            lines.append(
                "⚠️ Some speech was missed. If this keeps happening, use `/dmbot stop` "
                "then `/dmbot start`."
            )
        return lines

    # ---- resuming after a restart ------------------------------------------

    async def _stop_saved(self, guild_id: int, user_id: int, is_server_manager: bool) -> str:
        """`/dmbot stop` for a session that's saved but not running in this process."""
        try:
            saved = await self.sessions.get(guild_id)
            if saved is None:
                return "DMbot isn't listening right now."
            campaign = await self.campaigns.get(guild_id, saved.campaign_id)
            dms = campaign.dm_user_ids if campaign else frozenset()
            if not (user_id == saved.started_by or user_id in dms or is_server_manager):
                return (
                    "Only the DM can stop the session. To stop recording *you*, press "
                    "**Stop recording me** in DMbot's private message, or use "
                    "`/consent revoke`."
                )
            await self.sessions.clear(guild_id, f"/dmbot stop by user {user_id} before it resumed")
        except Exception:
            log.exception("Couldn't stop a saved session")
            return STOP_FAILED
        self._resume_when_available.discard(guild_id)
        # In case an ears from before the restart is still in the channel.
        await self.ears.send(leave_command(guild_id))
        return "Stopped. DMbot won't rejoin. See you next session! 👋"

    async def on_ready(self) -> None:
        # on_ready fires again after Discord reconnects; start resuming only once.
        if self._resume_started:
            return
        self._resume_started = True
        # A count, never names: answers "who has DMbot?" from the log (#426).
        log.info("Connected to %d server(s)", len(self.guilds))
        # Log (don't post: restarts would spam) servers where DMbot lacks something.
        for guild in self.guilds:
            me = cast(discord.Member | None, guild.me)  # None while the guild is loading
            with log_context(guild_id=guild.id):
                if me is None:
                    log.debug("Not checking permissions: server still loading")
                    continue
                gaps = install.missing(me.guild_permissions)
                if gaps:
                    log.warning("Missing permissions here: %s", ", ".join(gaps))
        self._background.append(asyncio.create_task(self._resume_with_retries(), name="resume"))

    async def _resume_with_retries(self) -> None:
        """Resume saved sessions, retrying servers that failed (database or Discord not
        ready yet). Gives up after the last delay; those sessions stay saved and a later
        restart tries again."""
        pending: set[int] | None = None  # None: all servers on this process's shards
        for delay in (0, *RESUME_RETRY_DELAYS_S):
            await asyncio.sleep(delay)
            try:
                pending = await self.resume_sessions(only=pending)
            except Exception:
                log.exception("Couldn't look up saved sessions; will retry")
                continue
            if not pending:
                return
        log.error("Gave up resuming %d session(s) for now", len(pending or ()))

    async def resume_sessions(self, only: set[int] | None = None) -> set[int]:
        """Rejoin the sessions that were running on this process's shards.

        Returns the servers that failed in a way worth retrying. Raises if the list of
        saved sessions can't be read at all.
        """
        guild_ids = await self.sessions.guilds_to_resume(self.settings.shards)
        if only is not None:
            guild_ids = [g for g in guild_ids if g in only]
        failed: set[int] = set()
        resumed = 0
        for guild_id in guild_ids:
            async with self.session_lock(guild_id):
                with log_context(guild_id=guild_id):
                    try:
                        started = await self._resume_one(guild_id)
                    except Exception:
                        log.exception("Couldn't resume this server's session; will retry")
                        failed.add(guild_id)
                        continue
            if started:
                resumed += 1
                await asyncio.sleep(RESUME_SPACING_S)
        if resumed:
            log.info("Resumed %d session(s)", resumed)
        return failed

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """DMbot was just added to a server: say hello, or say what it still needs."""
        with log_context(guild_id=guild.id):
            log.info("Joined a server; %s", await install.post_welcome(guild))

    async def on_guild_available(self, guild: discord.Guild) -> None:
        """Discord made a server available again: resume its session if one was waiting."""
        if guild.id not in self._resume_when_available:
            return
        self._resume_when_available.discard(guild.id)
        async with self.session_lock(guild.id):
            with log_context(guild_id=guild.id):
                try:
                    await self._resume_one(guild.id)
                except Exception:
                    log.exception("Couldn't resume this server's session")

    async def _resume_one(self, guild_id: int) -> bool:
        """Resume one server's saved session. Returns True if it was started again."""
        if guild_id in self.tables:
            return False  # already started again (e.g. by /dmbot start)
        saved = await self.sessions.get(guild_id)
        if saved is None:
            # Only the routing entry was left (e.g. the campaign was deleted, which
            # deletes its session). Nothing to say to anyone.
            await self.sessions.clear(guild_id, "nothing saved to resume")
            return False
        guild = self.get_guild(guild_id)
        if guild is None:
            await self.sessions.clear(guild_id, "not resumed: DMbot is no longer in this server")
            return False
        if guild.unavailable:
            # A Discord outage: channels aren't known yet. Keep the session; resume it
            # when Discord says the server is back (on_guild_available).
            log.info("Server unavailable; will resume when Discord makes it available")
            self._resume_when_available.add(guild_id)
            return False
        campaign = await self.campaigns.get(guild_id, saved.campaign_id)
        if campaign is None:  # can't normally happen: deleting a campaign deletes this
            await self.sessions.clear(guild_id, "not resumed: the campaign is gone")
            return False
        with log_context(campaign_id=campaign.id):
            return await self._resume_campaign(guild, saved, campaign)

    async def _resume_campaign(
        self, guild: discord.Guild, saved: SavedSession, campaign: Campaign
    ) -> bool:
        now = int(time.time())
        screen_id = self._usable_screen(guild, saved.screen_channel_id, campaign)
        if screen_id is None:
            # Nowhere to tell the DM anything, so don't record without them knowing.
            await self.sessions.clear(guild.id, "not resumed: no DM screen DMbot can post in")
            return False
        if now - saved.started_at > MAX_RESUME_AGE_S:
            await self.sessions.clear(
                guild.id, f"not resumed: started over {MAX_RESUME_AGE_S // 3600} hours ago"
            )
            await self.post(screen_id, resume_too_old_message(campaign.name))
            return False
        me = cast(discord.Member | None, guild.me)
        voice = guild.get_channel(saved.voice_channel_id)
        can_join = (
            me is not None
            and isinstance(voice, discord.VoiceChannel | discord.StageChannel)
            and voice.permissions_for(me).view_channel
            and voice.permissions_for(me).connect
        )
        if not can_join:
            await self.sessions.clear(guild.id, "not resumed: can't rejoin the voice channel")
            await self.post(
                screen_id, resume_no_voice_message(campaign.name, saved.voice_channel_id)
            )
            return False
        streak = await self.sessions.note_resume(guild.id, now, RESUME_STREAK_WINDOW_S)
        if streak > MAX_RESUMES_IN_A_ROW:
            log.error("Not resuming: restarted %d times in a row", streak - 1)
            await self.sessions.clear(guild.id, "not resumed: too many restarts in a row")
            await self.post(screen_id, resume_gave_up_message(campaign.name))
            return False
        recently = (
            saved.last_resumed_at is not None and now - saved.last_resumed_at < RESUME_QUIET_S
        )
        table = Table(
            guild_id=guild.id,
            voice_channel_id=saved.voice_channel_id,
            screen_channel_id=screen_id,
            dm_user_id=saved.started_by,
            segmenter=Segmenter(guild.id),
            notice_posted=saved.notice_posted,
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            dm_user_ids=campaign.dm_user_ids,
            resumed=True,
            announce_resume=not recently,
            transcript_channel_id=self._usable_transcript(guild, campaign),
            started_at=saved.started_at,
        )
        if campaign.transcript_channel_id is not None and table.transcript_channel_id is None:
            await self.post(
                screen_id, screen_messages.transcript_stopped(campaign.transcript_channel_id)
            )
        # Sends the allowlist, then the join. If ears isn't connected yet, it gets both
        # when it connects (_on_ears_link_change).
        await self.start_table(table)
        log.info("Resuming session (restart %d in a row)", streak)
        return True

    def _usable_screen(self, guild: discord.Guild, saved_id: int, campaign: Campaign) -> int | None:
        """The saved DM screen if DMbot can still post there, else the campaign's own."""
        me = guild.me
        for channel_id in (saved_id, campaign.dm_screen_channel_id):
            if channel_id is None:
                continue
            channel = self.get_channel(channel_id)
            if (
                me is not None
                and isinstance(channel, discord.abc.GuildChannel | discord.Thread)
                and not missing_post_permissions(
                    channel.permissions_for(me), in_thread=isinstance(channel, discord.Thread)
                )
            ):
                return channel_id
        return None

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

    async def post(self, channel_id: int, text: str, view: discord.ui.View | None = None) -> bool:
        """Send a message; returns False (and logs) if it could not be posted."""
        return await self.post_message(channel_id, text, view) is not None

    async def post_message(
        self, channel_id: int, text: str, view: discord.ui.View | None = None
    ) -> discord.Message | None:
        """Send a message; returns it, or None (and logs) if it could not be posted."""
        channel = self.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("Could not post to channel %s: channel not found", channel_id)
            return None
        try:
            # discord.py's types don't accept view=None, so only pass a real view.
            if view is None:
                return await channel.send(text, allowed_mentions=NO_PINGS)
            return await channel.send(text, allowed_mentions=NO_PINGS, view=view)
        except discord.HTTPException as exc:
            log.warning("Could not post to channel %s: %s", channel_id, exc)
            return None

    async def after_screen_change(self, campaign: Campaign, channel: discord.TextChannel) -> None:
        """Keep a live session in step after the DM changes who can see the DM screen."""
        table = self.tables.get(campaign.guild_id)
        if table is None or table.campaign_id != campaign.id:
            return
        table.screen_channel_id = channel.id  # the screen may have been re-created
        table.dm_user_ids = campaign.dm_user_ids
        if campaign.dm_screen_visibility == "peek" and not table.peek_offered:
            # Players got no Peek button if the notice was posted under another setting.
            # Once per session, so switching back and forth doesn't spam them.
            table.peek_offered = await self.post(
                table.voice_channel_id, screen_messages.peek_invite(), view=peek_view(campaign.id)
            )

    async def _peek_view(self, table: Table) -> discord.ui.View | None:
        """The Peek button for the players' notice, when the campaign allows peeking."""
        if table.campaign_id is None:
            return None
        campaign = await self.campaigns.get(table.guild_id, table.campaign_id)
        if campaign is None or campaign.dm_screen_visibility != "peek":
            return None
        return peek_view(campaign.id)

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
                table.totals.add_health(
                    message.user_id, message.frames_received, message.frames_expected
                )

    async def _on_status(self, table: Table, status: Status) -> None:
        if status.state == "joined":
            # A repeat "joined" (ears confirming again) has nothing new to say, but the
            # recording notice below is still retried if it hasn't been posted yet.
            repeat = table.listening
            table.listening = True
            # Ask everyone in the channel once, when a session starts. Not after a
            # restart: they were asked before it, and newcomers are asked as they join.
            first_join = not repeat and not table.resumed
            resumed_now = not repeat and table.resumed
            if (
                not repeat
                and table.transcript_channel_id is not None
                and (first_join or table.announce_resume)
            ):  # only once DMbot is really in voice
                table.transcript.add_divider(
                    transcript_lines.started(
                        table.campaign_name, int(time.time()), resumed=resumed_now
                    ),
                    at_ms=_now_ms(),
                )
            agreed = await self.consent.consenting(table.guild_id)
            here = self._people_in_voice(table)
            recorded = [uid for uid in here if uid in agreed]
            if not repeat:
                log.info("In the voice channel; %d of %d there opted in", len(recorded), len(here))
            if repeat:
                pass
            elif table.resumed:
                table.resumed = False
                if table.announce_resume:
                    await self._post_listening(
                        table, resumed_message(table.campaign_name, table.voice_channel_id)
                    )
            else:
                await self._post_listening(
                    table,
                    screen_messages.listening_message(
                        table.voice_channel_id,
                        table.campaign_name,
                        sorted(self.name_of(table.guild_id, uid) for uid in recorded),
                        sorted(
                            self.name_of(table.guild_id, uid) for uid in here if uid not in recorded
                        ),
                    ),
                )
            if not table.notice_posted:
                # Players learn they're being recorded from this notice, so a failure
                # must reach the DM; it is retried on the next join.
                peek = await self._peek_view(table)
                table.notice_posted = await self.post(
                    table.voice_channel_id,
                    "🔴 **DMbot is listening in this channel.** It gives the DM private "
                    "notes and never decides anything.\n"
                    "Everyone here gets a private message from DMbot asking if they agree "
                    "to be recorded. Only people who say yes are recorded.\n"
                    "No message from DMbot? Check your DMs and Message Requests, or type "
                    "`/consent give`. Change your mind any time with `/consent revoke`.",
                    view=peek,
                )
                table.peek_offered = table.notice_posted and peek is not None
                if table.notice_posted:
                    log.info("Recording notice posted in the voice channel's chat")
                    await self._remember_notice(table.guild_id)
                else:
                    log.warning("Recording notice NOT posted; telling the DM")
                    await self.post(
                        table.screen_channel_id, notice_failed_message(table.voice_channel_id)
                    )
            if first_join:
                self._ask_everyone_in_voice(table)
            elif not repeat and resumed_now:
                # After a restart only people whose yes no longer counts (the consent
                # wording changed) need asking: everyone else was asked before it.
                self._ask_everyone_in_voice(table, only_renewals=True)
        elif status.state in ("left", "error"):
            table.listening = False
            # The detail comes from ears' own fixed messages, never from users.
            log.warning("Voice %s: %s", status.state, status.detail or "no detail")
            detail = status.detail or "The voice connection ended."
            await self.post(table.screen_channel_id, f"⚠️ {detail}")

    def _all_tables(self) -> list[Table]:
        """Running sessions and stopped ones still finishing."""
        return [*self.tables.values(), *(t for ts in self._ending.values() for t in ts)]

    def _table_for(self, utterance: Utterance) -> Table | None:
        for table in [
            self.tables.get(utterance.guild_id),
            *self._ending.get(utterance.guild_id, []),
        ]:
            if table is not None and table.segmenter.session == utterance.session:
                return table
        return None

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
        """A piece of speech, written down. The pipeline has just re-checked consent."""
        # Only the session that heard it (a stopped one still finishing counts): speech
        # from one session must never land in the next (perhaps another campaign's).
        table = self._table_for(utterance)
        if table is None:
            return
        table.capture_log.add_utterance(utterance)
        table.totals.add_utterance(utterance)
        cleaned = text
        if text and table.name_lookup is not None:
            result = self._clean(table, text)
            cleaned = result.text
            self._offer_question(table, utterance.user_id, utterance.start_ms, text, result)
            running = self.tables.get(table.guild_id) is table  # not one still finishing
            if running and table.fix_notes.add(
                utterance.user_id, utterance.start_ms, text, result.fixes
            ):
                self._redraw_fix_notes(table)
            named = mentions(table.name_lookup, cleaned)  # once per name per line
            table.scene.note(named, utterance.user_id, time.monotonic())
            table.heard_counts.update((entity_id, utterance.user_id) for entity_id in named)
        if text:
            # After cleaning, so a line is never evidence about itself; even when the names
            # couldn't be loaded, a word said in lower case is a real word next time.
            table.vocabulary.note(utterance.user_id, text)
        if text and self.transcripts is not None:
            table.unsaved.add(Line(utterance.start_ms, utterance.user_id, text, cleaned or text))
        if text and len(table.heard) < HEARD_MAX:
            # The cleaned line: a known name misheard and fixed live isn't new (#394).
            table.heard.append((utterance.user_id, cleaned or text))
            if len(table.heard) == HEARD_MAX:
                log.info("Name scan: kept the first %d lines of this session", HEARD_MAX)
        if text and table.transcript_channel_id is not None:
            guild = self.get_guild(utterance.guild_id)
            member = guild.get_member(utterance.user_id) if guild else None
            name = member.display_name if member else None
            table.transcript.add(
                utterance.user_id,
                transcript_lines.speaker_name(name),
                cleaned or text,
                utterance.start_ms,
            )

    def _clean(self, table: Table, heard: str) -> Cleaned:
        """The line with misheard names fixed (#127), from the campaign's names as last
        loaded; as heard if cleaning fails. No await: the consent check just made still
        holds."""
        assert table.name_lookup is not None
        try:
            result = clean(
                table.name_lookup,
                heard,
                vocabulary=table.vocabulary,
                people=table.people,
                scene=table.scene.scene(time.monotonic()).keys(),
            )
        except Exception:
            now = time.monotonic()
            if now - self._clean_failed_at > HINTS_FAIL_LOG_S:
                self._clean_failed_at = now
                log.exception("Couldn't fix names in a line; kept it as heard")
            return Cleaned(heard, ())
        if result.fixes:
            log.debug("Fixed %d misheard name(s) in a line", len(result.fixes))
        return result

    def _offer_question(
        self, table: Table, speaker: int, started_ms: int, heard: str, result: Cleaned
    ) -> None:
        """Ask the DM "Did they mean…?" about this line, if there's something worth
        asking (#296): see `QuestionBook` for the limits. An unanswered one expires
        first. Only for the running session: a stopped one still finishing can't be
        answered any more."""
        if self.tables.get(table.guild_id) is not table or table.name_lookup is None:
            return
        now = time.monotonic()
        expired = table.questions.expire(now)
        if expired is not None:
            self._track(self._close_question(table, self._not_answered(table, expired)), "q")
        if not result.questions:
            return
        lookup, scene = table.name_lookup, table.scene.scene(now)
        matters = {
            entity_id
            for question in result.questions
            for entity_id, _ in question.options
            if entity_id in scene
            or getattr(lookup.entities.get(entity_id), "type", None) == PLAYER_CHARACTER
        }
        asked = table.questions.offer(
            speaker,
            result.questions,
            now,
            matters,
            started_ms=started_ms,
            line=heard,
            fixes=result.fixes,
        )
        if asked is not None:
            self._track(self._ask_dm(table, asked), "name-question")

    def _not_answered(self, table: Table, asked: name_questions.Asked) -> str:
        """The one line an unanswered question shrinks to: their words only while they
        are still recorded."""
        if not self.consent.has_consent(table.guild_id, asked.speaker):
            return name_questions.GONE
        return name_questions.not_answered_text(discord.utils.escape_markdown(asked.heard))

    async def _ask_dm(self, table: Table, asked: name_questions.Asked) -> None:
        """Post "Did they mean…?" to the DM screen (#296). Consent is checked again
        first: this runs after the line was delivered."""
        book = table.questions
        if not self.consent.has_consent(table.guild_id, asked.speaker):
            if book.is_open(asked.id):
                book.close(name_questions.STOPPED, time.monotonic())
            return
        name = self.name_of(table.guild_id, asked.speaker)
        speaker = "Someone" if name.startswith("<@") else discord.utils.escape_markdown(name)
        md = discord.utils.escape_markdown
        text = name_questions.question_text(speaker, md(asked.heard), md(asked.context))
        message = await self.post_message(
            table.screen_channel_id, text, question_view(table.guild_id, asked)
        )
        if message is None:
            if book.is_open(asked.id):  # not posted: free the slot, and ask again later
                book.close(name_questions.NOT_POSTED, time.monotonic())
            return
        if book.is_open(asked.id):
            table.question_message = message
            return
        # Closed while posting: say how, without their words if they stopped.
        closed = (
            name_questions.GONE
            if book.why_closed(asked.id) == name_questions.STOPPED
            else self._not_answered(table, asked)
        )
        with contextlib.suppress(discord.HTTPException):
            await message.edit(content=closed, view=None)

    async def _close_question(self, table: Table, text: str = name_questions.GONE) -> None:
        """Take the open question's buttons down, saying why (they stopped being
        recorded, nobody answered, or the session ended)."""
        message, table.question_message = table.question_message, None
        if message is not None:
            with contextlib.suppress(discord.HTTPException):
                await message.edit(content=text, view=None)

    def _redraw_fix_notes(self, table: Table) -> None:
        """Bring the "✏️ Name fixes to check" message up to date, soon. One redraw at a
        time and at most one waiting: a burst of fixes is one edit, not one each."""
        if table.fix_queued:
            return
        table.fix_queued = True
        self._track(self._show_fix_notes(table), "fix-notes")

    async def _show_fix_notes(self, table: Table) -> None:
        """Post or update the fixes message (#296): only in the DM screen, never the
        transcript channel; only lines of people still recorded (checked as it's drawn,
        after waiting for the last redraw); no Undo buttons once the session ended."""
        async with table.fix_lock:
            table.fix_queued = False
            notes = [
                n
                for n in table.fix_notes.shown()
                if self.consent.has_consent(table.guild_id, n.speaker)
            ]
            if not notes and table.fix_message is None:
                return
            names = {}
            for note in notes:
                name = self.name_of(table.guild_id, note.speaker)
                names[note.speaker] = (
                    "Someone" if name.startswith("<@") else discord.utils.escape_markdown(name)
                )
            text, shown = fix_notes.message_text(notes, names, transcript_lines.escape)
            view = None if table.fix_ended else fix_notes_view(table.guild_id, shown)
            if table.fix_message is not None:
                try:
                    await table.fix_message.edit(content=text, view=view, allowed_mentions=NO_PINGS)
                    return
                except discord.NotFound:
                    table.fix_message = None  # deleted, or a new DM screen: post again
                except discord.HTTPException:
                    return
            if not table.fix_ended:
                table.fix_message = await self.post_message(table.screen_channel_id, text, view)

    async def undo_fix(
        self, guild_id: int, note_id: str, user_id: int
    ) -> tuple[str, tuple[str, int] | None]:
        """The DM pressed Undo on a name fix (#296): the heard words go back in that line
        (saved, waiting, or posted in the last ~30 s), and a "keep as heard" rule is
        saved so they aren't fixed again. What to tell them, and what "Allow again"
        takes back (campaign, change). Consent is checked after every await."""
        table = next(
            (
                t
                for t in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]
                if t is not None and t.fix_notes.find(note_id) is not None
            ),
            None,
        )
        if table is None or table.campaign_id is None or self.memory is None:
            return fix_notes.EXPIRED, None
        if not table.is_dm(user_id):
            return fix_notes.ONLY_DM, None
        note = table.fix_notes.find(note_id)
        assert note is not None
        if not self.consent.has_consent(guild_id, note.speaker):
            self._redraw_fix_notes(table)  # their line shouldn't be listed any more
            return fix_notes.STOPPED, None
        undone = table.fix_notes.undo(note_id)  # before any await: undone once
        if not undone:
            return fix_notes.ALREADY, None
        try:
            written = await self.memory.add_correction(
                guild_id, table.campaign_id, note.fix.heard, action=KEEP, source=DM
            )
        except Exception:
            for n in undone:
                n.undone = False  # not saved: can be pressed again
            raise
        if self.lookup is not None:
            self.lookup.mark_stale(guild_id, table.campaign_id)
        allow = (table.campaign_id, written.batch) if written.batch is not None else None
        self._redraw_fix_notes(table)
        if not self.consent.has_consent(guild_id, note.speaker):
            return fix_notes.STOPPED, allow  # their words aren't put back anywhere
        await self._rewrite_line(
            table, note.speaker, note.started_ms, table.fix_notes.line_text(note)
        )
        md = discord.utils.escape_markdown
        return fix_notes.done_text(md(note.fix.heard), md(note.fix.written)), allow

    async def _rewrite_line(self, table: Table, speaker: int, started_ms: int, text: str) -> bool:
        """Change a line's words (#296, #503): saved, waiting to be saved, and in the
        transcript channel if it was posted in the last ~30 s. Only while the speaker is
        still recorded, checked again after every wait. True if the line changed: saved
        or waiting, or (with no saved copy that failed to change) the channel message. A
        database error is logged, and the channel is still edited."""
        guild_id, found, failed, edited = table.guild_id, False, False, False
        # The saved line: hold the save lock, so a batch being saved can't miss this.
        async with table.save_lock:
            if self.consent.has_consent(guild_id, speaker):
                found = table.unsaved.relabel(speaker, started_ms, text)
                session_id = table.transcript_session_id
                if not found and self.transcripts is not None and session_id is not None:
                    try:
                        found = bool(
                            await self.transcripts.relabel_line(
                                guild_id, session_id, speaker, started_ms, text
                            )
                        )
                    except Exception:
                        failed = True
                        log.exception("Couldn't change a saved line's words")
                    if not found and not failed:
                        log.warning("A name change found no saved line to change")
        # The channel: hold its lock, so a message being posted can't miss this either.
        async with table.transcript_lock:
            if self.consent.has_consent(guild_id, speaker):
                edit = table.transcript.relabel(speaker, started_ms, text, time.monotonic())
                if edit is not None:
                    message, content = edit
                    # Bounded: new lines wait for this lock (Discord may be slow). The saved
                    # line is already right, so a failure here doesn't change the answer.
                    try:
                        await asyncio.wait_for(
                            message.edit(content=content, allowed_mentions=NO_PINGS),
                            EDIT_TIMEOUT_S,
                        )
                        edited = True
                    except (discord.HTTPException, TimeoutError):
                        pass
                    except Exception:
                        log.exception("Couldn't edit a transcript message with a name change")
        return found or (edited and not failed)

    async def allow_fix_again(
        self, guild_id: int, campaign_id: str, batch: int, user_id: int
    ) -> str:
        """Take back the "keep as heard" rule an Undo saved (a press by mistake)."""
        campaign = await self.campaigns.get(guild_id, campaign_id)
        if campaign is None or self.memory is None:
            return fix_notes.EXPIRED
        if user_id not in campaign.dm_user_ids:
            return fix_notes.ONLY_DM
        try:
            await self.memory.undo(guild_id, campaign_id, batch)
        except MemoryRuleError:
            return name_questions.UNDO_FAILED
        if self.lookup is not None:
            self.lookup.mark_stale(guild_id, campaign_id)
        return fix_notes.ALLOWED

    def _session_table(self, guild_id: int, question_id: str) -> Table | None:
        """The table, running or still finishing, whose question this is."""
        for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
            if table is not None and (
                table.questions.is_open(question_id)
                or table.questions.why_closed(question_id) is not None
            ):
                return table
        return None

    def can_type_answer(
        self, guild_id: int, question_id: str, user_id: int
    ) -> tuple[str | None, str]:
        """Before the "Type it…" form opens: why it can't (closed, or not the DM), and
        the words heard, to start the form with (one letter to fix on a phone)."""
        table = self._session_table(guild_id, question_id)
        asked = table.questions.open if table is not None else None
        if table is None or asked is None or asked.id != question_id:
            return name_questions.EXPIRED, ""
        if not table.is_dm(user_id):
            return name_questions.ONLY_DM, ""
        if not self.consent.has_consent(guild_id, asked.speaker):
            return name_questions.GONE, ""  # their words aren't shown again
        return None, asked.heard

    async def _names_now(self, table: Table) -> CampaignLookup | None:
        """The campaign's names as they are now (a name may have been made secret since
        the session's copy), or None if they can't be loaded."""
        if self.lookup is None:
            return table.name_lookup
        if table.campaign_id is None:
            return None
        try:
            return await self.lookup.get(table.guild_id, table.campaign_id)
        except Exception:
            log.exception("Couldn't load the campaign's names to check an answer")
            return None

    def _typed_name(
        self, lookup: CampaignLookup | None, asked: name_questions.Asked, typed: str
    ) -> tuple[str | None, str] | str:
        """A name the DM typed (#503): (the known name's entry, how it's written), (None,
        the typed name) for a name DMbot doesn't know yet, or why it can't be used. A
        secret name, or one that would stand next to a secret in the line, is refused:
        the transcript is shared with the whole server."""
        why = name_questions.typed_problem(typed)
        if why is not None:
            return why
        if lookup is None:
            return name_questions.TYPED_CANT_CHECK
        chosen = self._known_typed(lookup, typed)
        if isinstance(chosen, str):
            return chosen
        written = with_ending(asked.heard, chosen[1])
        if asked.line and not safe_answer(
            lookup, asked.line, asked.fixes, asked.start, asked.end, written
        ):
            return name_questions.TYPED_SECRET
        return chosen

    def _known_typed(self, lookup: CampaignLookup, typed: str) -> tuple[str | None, str] | str:
        entries = lookup.by_key.get(name_key(typed), ())
        if any(e.secret for e in entries):  # never written in the transcript
            return name_questions.TYPED_SECRET
        ids = {e.entity_id for e in entries if e.entity_id in lookup.entities}
        if len(ids) > 1:
            return name_questions.TYPED_TWO
        if not ids:
            return None, typed
        (entity_id,) = ids
        name = own_name(lookup, entity_id, typed=True)
        return (entity_id, name) if name is not None else name_questions.TYPED_SECRET

    async def answer_name_question(
        self, guild_id: int, question_id: str, pick: str, user_id: int, typed: str | None = None
    ) -> tuple[str, bool, tuple[str, int] | None]:
        """The DM answered "Did they mean…?": save it for the campaign, so the same words
        are handled silently from now on, and fix the line that was asked about (#503).
        `typed`: the name typed in the "Type it…" form. What to tell them, whether the
        question is now closed (its message then shows the answer), and what Undo takes
        back (campaign, change). The question stays open while saving, so it can still
        be taken down if its speaker stops being recorded; consent is checked before and
        after every wait. A correction the DM made stays if the speaker later stops
        being recorded: the DM wrote it, and it names no one."""
        typed = " ".join((typed or "").split()) if pick == name_questions.TYPE else None
        closed = name_questions.EXPIRED
        if typed and name_questions.typed_problem(typed) is None:  # what they typed isn't lost
            closed = name_questions.too_late_text(discord.utils.escape_markdown(typed))
        table = self._session_table(guild_id, question_id)
        if table is None or table.campaign_id is None or self.memory is None:
            return closed, True, None
        book = table.questions
        if not book.is_open(question_id) or book.open is None:
            return closed, True, None
        if not table.is_dm(user_id):
            return name_questions.ONLY_DM, False, None
        chosen: tuple[str | None, str] | None = None
        if typed is not None and name_key(typed) == name_key(book.open.heard):
            pick = name_questions.KEEP  # typed just as heard: keep it
        elif typed is not None:
            lookup = await self._names_now(table)
            if not book.is_open(question_id) or book.open is None:  # closed meanwhile
                return closed, True, None
            found = self._typed_name(lookup, book.open, typed)
            if isinstance(found, str):  # can't be used: the question stays open
                return found, False, None
            chosen = found
        begun = book.begin(question_id)
        if begun is name_questions.Begin.BUSY:
            return name_questions.BUSY, False, None
        asked = book.open
        if begun is not name_questions.Begin.OK or asked is None:
            return name_questions.EXPIRED, True, None
        now = time.monotonic
        if not self.consent.has_consent(guild_id, asked.speaker):
            book.close(name_questions.STOPPED, now())
            table.question_message = None
            return name_questions.GONE, True, None
        keep = pick == name_questions.KEEP
        if chosen is None and not keep:
            if not (pick.isdigit() and int(pick) < len(asked.options)):
                book.close(name_questions.ANSWERED, now())
                table.question_message = None
                return name_questions.EXPIRED, True, None
            chosen = asked.options[int(pick)]
        campaign_id, name = table.campaign_id, ""
        try:
            if chosen is None:
                kept = await self.memory.add_correction(
                    guild_id, campaign_id, asked.heard, action=KEEP, source=DM
                )
                batch = kept.batch
            elif chosen[0] is None:  # a new name, to check in 📝 Check new names
                name = chosen[1]
                added = await self.memory.add_typed_name(guild_id, campaign_id, asked.heard, name)
                batch = added.batch
            else:
                entity_id, name = chosen
                fixed = await self.memory.add_correction(
                    guild_id, campaign_id, asked.heard, action=FIX, source=DM, entity_id=entity_id
                )
                batch = fixed.batch
        except MemoryRuleError:
            if chosen is not None and chosen[0] is None:  # a typed name it won't add
                book.failed(question_id)
                return name_questions.TYPED_REFUSED, False, None
            # The name was removed since: asking again won't help.
            if book.is_open(question_id):
                book.close(name_questions.ANSWERED, now())
                table.question_message = None
            return name_questions.NAME_GONE, True, None
        except Exception:
            book.failed(question_id)  # open again for another try, if still theirs
            raise
        if self.lookup is not None:
            self.lookup.mark_stale(guild_id, campaign_id)  # the next line uses the answer
        # No batch: it was already saved before, so there's nothing for Undo to take back.
        undo = (campaign_id, batch) if batch is not None else None
        if book.is_open(question_id):
            book.close(name_questions.ANSWERED, now())
            table.question_message = None
        # After the save: if they stopped being recorded meanwhile, their words stay down
        # (Stop already took the message down). A session that ended meanwhile is fine.
        if book.why_closed(question_id) == name_questions.STOPPED or not self.consent.has_consent(
            guild_id, asked.speaker
        ):
            return name_questions.GONE, True, undo
        heard = discord.utils.escape_markdown(asked.heard)
        if chosen is None:
            return name_questions.kept_text(heard), True, undo
        line = await self._fix_asked_line(table, asked, name, undo)
        if not self.consent.has_consent(guild_id, asked.speaker):
            return name_questions.GONE, True, undo
        md = discord.utils.escape_markdown
        answer = name_questions.fixed_text(
            heard, md(name), line_fixed=line is True, line_kept=line is False, new=chosen[0] is None
        )
        return answer, True, undo

    async def _fix_asked_line(
        self, table: Table, asked: name_questions.Asked, name: str, undo: tuple[str, int] | None
    ) -> bool | None:
        """Write the answer into the line that was asked about (#503); Undo of the answer
        puts it back (`answer_undone`). Never if that would put a secret name in the line
        (checked with the names as they are now). True if the line was fixed, False if it
        stays as heard, None if there's no line to fix. A failure here never loses the
        answer: it's saved already, and its message still gets its Undo."""
        if not asked.line:
            return None
        lookup = await self._names_now(table)
        notes, speaker, started = table.fix_notes, asked.speaker, asked.started_ms
        written = with_ending(asked.heard, name)
        fixes = notes.still_fixed(speaker, started, asked.fixes)
        if lookup is None or not safe_answer(
            lookup, asked.line, fixes, asked.start, asked.end, written
        ):
            return False  # the rule is saved; this line stays as heard
        batch = undo[1] if undo is not None else None  # None: nothing to undo
        answer = fix_notes.Answer(
            batch, speaker, started, asked.line, fixes, asked.start, asked.end, written
        )
        notes.answered(answer)
        text = notes.words_now(speaker, started, asked.line, asked.fixes)
        try:
            changed = await self._rewrite_line(table, speaker, started, text)
        except Exception:
            log.exception("Couldn't write an answer into the line it was about")
            changed = False
        if not changed:
            # The DM is told the line stays as heard: a later rewrite of it (a fix's
            # Undo) mustn't bring the answer in.
            with contextlib.suppress(ValueError):
                notes.answers.remove(answer)
        return changed

    async def answer_undone(self, guild_id: int, campaign_id: str, batch: int) -> None:
        """The DM undid an answer to "Did they mean…?" (the saved change is already taken
        back): put its line back, if this session still has it (#503)."""
        for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
            if table is None or table.campaign_id != campaign_id:
                continue
            answer = table.fix_notes.take_back(batch)
            if answer is None:
                continue
            text = table.fix_notes.words_now(
                answer.speaker, answer.started_ms, answer.heard, answer.fixes
            )
            await self._rewrite_line(table, answer.speaker, answer.started_ms, text)
            return

    async def _alert_dm(self, guild_id: int, message: str) -> None:
        table = self.tables.get(guild_id)
        if table is not None:
            await self.post(table.screen_channel_id, message)

    async def _name_hints(self, utterance: Utterance) -> list[str]:
        """Names speech-to-text should expect for this clip, most useful first, from the
        session that heard it (a stopped one still finishing keeps its own campaign's
        names): characters, the players, names said lately, names connected to them,
        then the rest (dmbot.memory.scene). Never secret names."""
        guild_id = utterance.guild_id
        table = self._table_for(utterance)
        people, absent = await self._hint_people(guild_id, table)
        if self.lookup is None or table is None or table.campaign_id is None:
            return [*people, *absent]
        try:
            lookup = await self.lookup.get(guild_id, table.campaign_id)
            if table.hint_parts is None or table.hint_parts.version != lookup.version:
                # Up to ~150 ms for a big campaign: off the event loop, once per change.
                table.hint_parts = await asyncio.to_thread(prepare_hints, lookup, time.time())
        except Exception:
            # Asked for every connection to speech-to-text: say so at most once a minute.
            now = time.monotonic()
            if now - self._hints_failed_at > HINTS_FAIL_LOG_S:
                self._hints_failed_at = now
                log.exception("Couldn't load the campaign's names for hints")
            # Never fix names from an old copy: a name may have just been made secret.
            table.name_lookup = None
            return [*people, *absent]
        table.name_lookup = lookup  # for matching written-down lines to the scene
        # Never "fixed" into a name, whether at the table or not.
        table.people = self._everyone_at_table(table, [*people, *absent])
        return scene_hints(
            lookup, table.hint_parts, table.scene, time.monotonic(), people=people, absent=absent
        )

    async def _hint_people(self, guild_id: int, table: Table | None) -> HintPeople:
        """Display names of people who agreed: (in the table's voice channel, not there).
        Kept for HINT_PEOPLE_S per server, and made again at once when anyone agrees or
        stops, so a revoked name is never sent as a hint (#173)."""
        users = await self.consent.consenting(guild_id)
        now = time.monotonic()
        voice_id = table.voice_channel_id if table is not None else None
        cached = self._hint_people_cache.get(guild_id)
        fresh = cached is not None and now - cached[0] < HINT_PEOPLE_S
        if cached is not None and fresh and cached[1:3] == (users, voice_id):
            return cached[3]
        voice = self.get_channel(voice_id) if voice_id is not None else None
        here = (
            {m.id for m in voice.members}
            if isinstance(voice, discord.VoiceChannel | discord.StageChannel)
            else set()
        )
        present: list[str] = []
        away: list[str] = []
        for user_id in sorted(users):
            name = self.name_of(guild_id, user_id)
            # A member DMbot can't look up comes back as "<@id>": no use as a hint, and
            # an outside service shouldn't get IDs.
            if not name.startswith("<@"):
                (present if user_id in here else away).append(name)
        split = (tuple(present), tuple(away))
        self._hint_people_cache[guild_id] = (now, users, voice_id, split)
        return split

    def _everyone_at_table(self, table: Table, consenting: list[str]) -> tuple[str, ...]:
        """Display names the name fixes must never change: people who agreed, the DM(s),
        and everyone in the voice channel (their names get said too), as of the last
        connection to speech-to-text. Used here only, never sent anywhere."""
        names = dict.fromkeys(consenting)
        voice = self.get_channel(table.voice_channel_id)
        members: list[discord.Member | None] = []
        if isinstance(voice, discord.VoiceChannel | discord.StageChannel):
            members += voice.members
        guild = self.get_guild(table.guild_id)
        if guild is not None:
            members += [guild.get_member(uid) for uid in {table.dm_user_id, *table.dm_user_ids}]
        for member in members:
            if member is not None and not member.bot:
                names.setdefault(member.display_name)
        return tuple(names)

    async def close_stale_flags(self, table: Table) -> None:
        """After a session: close memory flags whose problem is gone (#164), so the DM
        isn't asked about clashes an undo, a merge, an edit or a rejection ended."""
        if self.memory is None or table.campaign_id is None:
            return
        with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
            closed = await self.memory.resolve_stale_flags(table.guild_id, table.campaign_id)
            if closed.value:
                log.info("Closed %d memory flag(s) that no longer apply", len(closed.value))

    async def prune_old_changes(self, table: Table) -> None:
        """After a session: forget undo history older than MEMORY_CHANGELOG_KEEP_DAYS
        (#164), so the change log doesn't grow forever. Last, after anything that
        writes: those writes are new, so they're kept anyway."""
        if self.memory is None or table.campaign_id is None:
            return
        with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
            gone = await self.memory.prune_changes(table.guild_id, table.campaign_id)
            if gone:
                log.info("Deleted %d change-log row(s) too old to undo", gone)

    async def keep_heard_names(self, table: Table) -> None:
        """After a session: keep how often each known name was said, for ranking hints
        next time. Only lines of people who still agree, checked again right before
        writing; then the campaign's copy reloads before next use, to see the counts."""
        if self.memory is None or table.campaign_id is None or not table.heard_counts:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            agreed = await self.consent.consenting(gid)
            rows = [
                Heard(entity_id, speaker, times)
                for (entity_id, speaker), times in table.heard_counts.items()
                if speaker in agreed and self.consent.has_consent(gid, speaker)
            ]
            table.heard_counts.clear()
            started = table.started_at or int(time.time())
            kept = await self.memory.add_session_heard(gid, cid, started, rows)
            if self.lookup is not None:
                self.lookup.drop([cid])
            log.info("Kept how often %d name(s) were said", kept)

    async def suggest_names(self, table: Table) -> None:
        """After a session: suggest names DMbot heard but doesn't know, for the DM to
        check. Only lines from people who still agree; never anything DMbot already has
        an answer for (including names the DM said aren't names)."""
        if self.memory is None or table.campaign_id is None or not table.heard:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            agreed = await self.consent.consenting(gid)
            lines = [text for uid, text in table.heard if uid in agreed]
            speakers = {uid for uid, _ in table.heard}
            table.heard = []  # the session is over; free it
            if not lines:
                return
            # People at the table aren't story names: skip their whole display names and
            # each word of them ("Mia Stone" → "Mia", "Stone").
            users = speakers | agreed | {table.dm_user_id}
            users |= table.dm_user_ids
            skip = set(await self.memory.known_keys(gid, cid))
            for uid in users:
                name = self.name_of(gid, uid)
                if not name.startswith("<@"):
                    skip.add(name_key(name))
                    skip.update(name_key(word) for word in name.split())
            # One question per thing ("Oskar Vane", also "Vane"), grouped before the cap
            # (#394). Which known name each sounds like is worked out when the DM looks,
            # never saved: names can change, or be made secret, before then.
            found = group_alike(find_new_names(lines, skip, unlimited=True))[:MAX_SUGGESTIONS]
            added: list[str] = []
            for suggestion in found:
                try:
                    written = await self.memory.add_entity(
                        gid,
                        cid,
                        type="concept",
                        name=suggestion.name,
                        source="scan",
                        description=f"Heard {suggestion.times} times",
                    )
                except MemoryRuleError as exc:
                    log.info("Skipped a suggested name: %s", exc)
                    continue
                added.append(suggestion.name)
                for other in suggestion.also:  # suggested with it, confirmed with it
                    part = name_key(other) in name_key(suggestion.name)
                    try:
                        await self.memory.add_alias(
                            gid,
                            cid,
                            written.value.id,
                            other,
                            kind="short" if part else "misheard",
                            source="scan",
                        )
                    except MemoryRuleError as exc:
                        log.info("Skipped a name heard with a suggestion: %s", exc)
            log.info("After-session scan: %d line(s), %d new name(s)", len(lines), len(added))
            if self.lookup is not None and not any(
                t.campaign_id == cid for t in self.tables.values()
            ):
                self.lookup.drop([cid])  # the session's copy isn't needed any more
            if added:
                await self.post(
                    table.screen_channel_id,
                    after_session_text(added),
                    view=review_view(cid),
                )

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
                await self.post_summary(table)

    async def _transcript_poster(self) -> None:
        limit = asyncio.Semaphore(TRANSCRIPT_PARALLEL)

        async def one(table: Table) -> None:
            async with limit:
                with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
                    try:
                        await self.flush_transcript(table)
                    except Exception:  # never let one campaign stop everyone's transcript
                        log.exception("Couldn't post to the transcript channel")

        while True:
            await asyncio.sleep(TRANSCRIPT_FLUSH_S)
            await asyncio.gather(*(one(t) for t in self._all_tables()))

    async def flush_transcript(self, table: Table) -> None:
        """Post what's waiting for the transcript channel, in order.

        Consent is checked again for every line as each message is built, after every
        await. A line leaves the queue only once posted; a passing failure is retried
        next time. If the channel is gone or DMbot may no longer post there, the
        transcript stops for this session and the DM is told once.
        """
        gid = table.guild_id

        def allowed(user_id: int) -> bool:
            return self.consent.has_consent(gid, user_id)

        async with table.transcript_lock:
            while table.transcript_channel_id is not None:
                ready = table.transcript.next_message(allowed)
                if ready is None:
                    return
                text, count = ready
                result, sent = await self._post_transcript(table.transcript_channel_id, text)
                if result == "posted":
                    # The message is kept a little while: an Undo may edit it (#296).
                    table.transcript.posted(count, sent, now=time.monotonic())
                elif result == "retry":
                    return
                else:
                    lost = table.transcript_channel_id
                    table.transcript_channel_id = None
                    table.transcript.clear()
                    log.warning("Transcript channel %s is gone or closed to DMbot", lost)
                    await self.post(
                        table.screen_channel_id, screen_messages.transcript_stopped(lost)
                    )
                    return

    async def _post_transcript(
        self, channel_id: int, text: str
    ) -> tuple[str, discord.Message | None]:
        """Post to a transcript channel: "posted", "retry" (a passing problem) or "gone"
        (deleted, or DMbot may no longer post there)."""
        channel = self.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return "gone", None
        try:
            message = await asyncio.wait_for(
                # silent: no pop-up or phone notification for every line (Discord's
                # @silent). The channel still shows as unread.
                channel.send(text, allowed_mentions=NO_PINGS, suppress_embeds=True, silent=True),
                TRANSCRIPT_POST_TIMEOUT_S,
            )
        except (discord.NotFound, discord.Forbidden):
            return "gone", None
        except (discord.HTTPException, TimeoutError) as exc:
            log.warning("Transcript post to %s failed; will retry: %s", channel_id, exc)
            return "retry", None
        return "posted", message  # kept for a late fix: an Undo may edit it (#296)

    async def _transcript_channel(
        self, guild: discord.Guild, campaign: Campaign, screen: object
    ) -> tuple[int | None, str | None]:
        """The campaign's transcript channel, set up next to the DM screen, and a note for
        the DM if it couldn't be. A missing transcript channel never stops a session."""
        category = getattr(screen, "category", None)
        try:
            channel = await setup_transcript_channel(
                guild,
                campaign.id,
                self.campaigns,
                category=category if isinstance(category, discord.CategoryChannel) else None,
            )
        except TranscriptChannelError as exc:
            return None, screen_messages.transcript_failed(str(exc))
        except Exception:
            log.exception("Couldn't set up the transcript channel")
            return None, screen_messages.transcript_failed(screen_messages.TRANSCRIPT_NO_ANSWER)
        return channel.id, None

    def _usable_transcript(self, guild: discord.Guild, campaign: Campaign) -> int | None:
        """After a restart: the campaign's transcript channel, if it's still one and
        DMbot can still post there."""
        if campaign.transcript_channel_id is None:
            return None
        channel = self.get_channel(campaign.transcript_channel_id)
        me = guild.me
        if (
            isinstance(channel, discord.TextChannel)
            and is_transcript_name(channel.name)
            and me is not None
        ):
            perms = channel.permissions_for(me)
            if perms.view_channel and perms.send_messages:
                return channel.id
        return None

    # ---- stored transcripts (#41, #125) ------------------------------------

    async def _open_transcript(self, table: Table) -> bool:
        """Start (or, after a restart, pick up) the session's stored transcript. Tried
        at each save until it works, so a passing database problem loses nothing; the DM
        is told once if it fails."""
        if table.transcript_session_id is not None:
            return True
        if self.transcripts is None or table.campaign_id is None:
            return False
        try:
            table.transcript_session_id = await self.transcripts.open_session(
                table.guild_id,
                table.campaign_id,
                table.started_at or int(time.time()),
                self.settings.transcription.source,
            )
        except Exception:
            log.exception("Couldn't start saving the transcript; will retry")
            if not table.transcript_warned:
                table.transcript_warned = True
                await self.post(table.screen_channel_id, screen_messages.TRANSCRIPT_NOT_SAVED)
            return False
        return True

    async def save_transcript(self, table: Table) -> None:
        """Save the lines waiting for this session's stored transcript.

        Consent is checked when the batch is taken and again after it's saved: lines of
        someone who pressed Stop while they were being saved are taken back out. A failed
        save is retried next time.
        """
        if self.transcripts is None:
            return
        gid = table.guild_id
        async with table.save_lock:
            if not table.unsaved or not await self._open_transcript(table):
                return
            session_id = table.transcript_session_id
            assert session_id is not None
            agreed = await self.consent.consenting(gid)  # loads the list if it's missing
            batch = table.unsaved.take(lambda uid: uid in agreed)
            if not batch:
                return
            try:
                ids = await self.transcripts.add_lines(gid, session_id, batch)
            except Exception:
                log.exception("Couldn't save %d transcript line(s); will retry", len(batch))
                still = await self.consent.consenting(gid)
                table.unsaved.put_back([x for x in batch if x.user_id in still])
                self._note_dropped(table)
                return
            still = await self.consent.consenting(gid)
            stopped = [i for i, x in zip(ids, batch, strict=True) if x.user_id not in still]
            if stopped:
                await self.transcripts.remove_lines(gid, session_id, stopped)

    def _note_dropped(self, table: Table) -> None:
        if table.unsaved.dropped and not table.dropped_logged:
            table.dropped_logged = True
            log.error("Saving the transcript keeps failing; the oldest unsaved lines are lost")

    async def _transcript_saver(self) -> None:
        limit = asyncio.Semaphore(TRANSCRIPT_PARALLEL)

        async def one(table: Table) -> None:
            async with limit:
                with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
                    try:
                        await self.save_transcript(table)
                    except Exception:  # never let one campaign stop everyone's saves
                        log.exception("Couldn't save the transcript")

        while True:
            await asyncio.sleep(TRANSCRIPT_SAVE_S)
            await asyncio.gather(*(one(t) for t in self._all_tables()))

    async def finish_transcript(self, table: Table, ended_at: int | None = None) -> int:
        """After a session: save the last lines, mark it ended, and send the DM(s) and
        everyone recorded a private message with a download button. Returns how many
        private messages went out."""
        if self.transcripts is None:
            return 0
        gid = table.guild_id
        with log_context(guild_id=gid, campaign_id=table.campaign_id):
            await self.save_transcript(table)
            session_id = table.transcript_session_id
            if session_id is None:
                return 0  # never opened: nothing was saved
            if table.unsaved:
                log.warning("%d transcript line(s) couldn't be saved", len(table.unsaved))
                await self.post(table.screen_channel_id, screen_messages.TRANSCRIPT_END_LOST)
            try:
                await self.transcripts.end_session(gid, session_id, ended_at or int(time.time()))
                session = await self.transcripts.session(gid, session_id)
            except Exception:
                log.exception("Couldn't finish the stored transcript")
                return 0
            if session is None or session.lines == 0:
                return 0
            people = {table.dm_user_id, *table.dm_user_ids, *session.speakers}
            sent = 0
            for user_id in sorted(people):
                sent += await self._send_download(user_id, table, session_id)
            log.info("Transcript download offered privately to %d of %d", sent, len(people))
            return sent

    async def _send_download(self, user_id: int, table: Table, session_id: str) -> int:
        """1 if the private message went out; people with private messages off use
        `/transcript` instead."""
        try:
            user = self.get_user(user_id) or await self.fetch_user(user_id)
            if user.bot:
                return 0
            await user.send(
                ended_text(table.campaign_name),
                view=download_view(table.guild_id, session_id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            return 0
        return 1

    async def post_summary(self, table: Table) -> None:
        """Log one capture-check line (IDs and numbers); warn the DM screen only if audio
        went missing."""
        with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
            line = table.capture_log.log_line()  # IDs and numbers only; before render
            if line:
                log.info(line)
            text = table.capture_log.render(partial(self.name_of, table.guild_id), time.monotonic())
            if text:
                await self.post(table.screen_channel_id, text)


# ---- slash commands ----------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _bot(interaction: discord.Interaction) -> DMBot:
    return cast(DMBot, interaction.client)


consent_group = app_commands.Group(
    name="consent", description="Choose whether DMbot may record your voice", guild_only=True
)


@consent_group.command(
    name="give", description="Let DMbot record and transcribe your voice in this server"
)
async def consent_give(interaction: discord.Interaction) -> None:
    # Shows the same request as the private message, so everyone agrees to the same terms;
    # nothing is saved until they press I consent.
    bot = _bot(interaction)
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        status = await bot.consent.status(guild.id, [interaction.user.id])
        granted = status.granted.get(interaction.user.id)
        renewed = interaction.user.id in status.outdated
    except Exception:
        log.exception("Couldn't look up consent in guild %s", guild.id)
        await interaction.followup.send(
            "Sorry, DMbot couldn't check that just now. Please try again in a minute.",
            ephemeral=True,
        )
        return
    # status reads the database and skips unsaved revokes, so it is right even on a
    # fresh process whose cache hasn't loaded this server yet.
    if granted is not None:
        await interaction.followup.send(
            confirmed_text(guild.name, granted), view=stop_view(guild.id), ephemeral=True
        )
        return
    table = bot.tables.get(guild.id)
    voice = bot.get_channel(table.voice_channel_id) if table else None
    text = request_text(
        guild.name,
        voice=voice.name if isinstance(voice, discord.abc.GuildChannel) else None,
        dm=bot.name_of(guild.id, table.dm_user_id) if table else None,
        cloud=bot.sends_audio_out,
        renewed=renewed,
        company=bot.company,
    )
    await interaction.followup.send(
        text, view=request_view(guild.id, outside=bot.outside_engine), ephemeral=True
    )


@consent_group.command(
    name="revoke", description="Stop DMbot from recording your voice in this server"
)
async def consent_revoke(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    if interaction.guild is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    gid, uid = interaction.guild.id, interaction.user.id
    bot.stop_recording(gid, uid)  # before anything that can be slow or fail
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await bot.withdraw_consent(gid, uid)
    except Exception:
        log.exception("Couldn't save a consent revoke in guild %s", gid)
        await interaction.followup.send(
            "DMbot has stopped recording you. It couldn't save this yet, so it might "
            "record you again after a restart. Please run `/consent revoke` again in a "
            "minute.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        f"Done. DMbot won't record you anymore. {ALREADY_RECORDED}",
        ephemeral=True,
    )


async def run(settings: Settings) -> None:
    db = await Database.open(settings.database_url)
    try:
        transcriber = build_transcriber(settings.transcription)
        await transcriber.warm_up()  # load the Whisper model now, not on the first word
        campaigns = CampaignStore(db)
        campaigns.register_section(MemorySection())  # campaign memory goes in backups
        # DMBot sets this too; passing it here means the store never starts out wrong.
        consent = ConsentStore(db, outside=settings.transcription.outside_engine)
        bot = DMBot(
            settings,
            consent,
            campaigns,
            SessionStore(db),
            transcriber,
            MemoryStore(db, keep_days=settings.memory_keep_days),
            TranscriptStore(db),
        )
        _close_on_sigterm(bot)
        async with bot:
            await bot.start(settings.discord_token)
    finally:
        with contextlib.suppress(Exception):
            await db.close()


def _close_on_sigterm(bot: DMBot) -> None:
    """Kubernetes stops a pod with SIGTERM: shut down cleanly (leave voice, close the
    voice link) and keep running sessions saved, so the next pod picks them up. Speech
    still waiting to be transcribed is lost."""
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):  # not available on Windows
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.ensure_future(bot.close()))
