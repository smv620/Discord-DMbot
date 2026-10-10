"""Discord bot: sessions for each campaign, and the capture pipeline.

A session ("table") is one campaign being played in one voice channel. `/dmbot start`
(dmbot.ui.dmbot_commands) picks the campaign and channel; this module joins the voice
channel through ears, enforces consent, segments speech per speaker, and posts periodic
capture summaries to the campaign's DM screen.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import shutil
import signal
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, cast

import discord
from discord import app_commands
from discord.ext import commands

from dmbot import campaign_cap, entitlements, hours, install, plan_rules, retention, usage
from dmbot.ai import FEATURE_TIERS, AnthropicClient, Feature
from dmbot.ai_watch import AIWatch
from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.audio_check import AudioChecker, Verdict
from dmbot.campaigns import Campaign, CampaignStore
from dmbot.campaigns.models import DEFAULT_DM_SCREEN_LEVEL, CampaignError
from dmbot.capture_log import FRAMES_PER_S, CaptureLog, Due, SessionTotals
from dmbot.channel_access import (
    SAME_CHANNEL,
    STARTING_UP,
    join_blocked_message,
    missing_post_permissions,
    notice_failed_message,
    post_problems,
)
from dmbot.config import Settings
from dmbot.consent import ConsentMethod, ConsentStatus, ConsentStore
from dmbot.consent_dm import (
    CONSENT_BUTTONS,
    REASK_INTRO,
    confirmed_text,
    menu_view,
    nothing_to_stop_text,
    reminder_text,
    renewed_text,
    request_text,
    request_view,
    send_prompt,
    unreachable_text,
    warning_text,
    warning_view,
)
from dmbot.db import Database
from dmbot.dm_screen import (
    DMScreenError,
    HideButton,
    PeekButton,
    StopListeningButton,
    VisibilityButton,
    ensure_dm_screen,
    house_sync,
    peek_view,
    rules_cards,
)
from dmbot.dm_screen import clock as clock_screen
from dmbot.dm_screen import effects as timer_screen
from dmbot.dm_screen import house_voice as house_voice_screen
from dmbot.dm_screen import levels as screen_levels
from dmbot.dm_screen import messages as screen_messages
from dmbot.dm_screen.handover import (
    AcceptOfferButton,
    DeclineOfferButton,
    HandoverButton,
    NotNowButton,
    TakeOnButton,
    WithdrawOfferButton,
)
from dmbot.dm_screen.left_out import PutBackButton, left_out_view
from dmbot.dm_screen.name_questions import (
    FixUndoButton,
    NameAnswerUndoButton,
    NameQuestionButton,
    fix_notes_view,
    question_view,
)
from dmbot.dm_screen.pause import PauseButton
from dmbot.dm_screen.settings import (
    HouseFileButton,
    LevelButton,
    RuleLookupButton,
    RulesCardsButton,
    SettingsButton,
    SettingsVisibilityButton,
)
from dmbot.dm_screen.site_offers import SiteOffers
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
from dmbot.memory import document_reader
from dmbot.memory.backup import MemorySection
from dmbot.memory.lookup import CampaignLookup, LookupCache
from dmbot.memory.models import DM, FIX, KEEP, Heard, MemoryRuleError, name_key
from dmbot.memory.scan import MAX_SUGGESTIONS, find_new_names, group_alike
from dmbot.memory.scene import PLAYER_CHARACTER, HintParts, SceneTracker, mentions, scene_hints
from dmbot.memory.scene import prepare as prepare_hints
from dmbot.memory.sheet_refresh import hint_names as sheet_hint_names
from dmbot.memory.sheet_refresh import refresh as refresh_sheets
from dmbot.memory.sheet_store import SheetStore
from dmbot.memory.store import MemoryStore
from dmbot.retention import RetentionJob
from dmbot.rules import house_voice
from dmbot.rules import index as rules_index
from dmbot.rules.house import HouseRule, HouseRulesSection, HouseRuleStore
from dmbot.rules.house_file_link import HouseFileLinkStore
from dmbot.rules.spotter import Mention
from dmbot.sessions import SavedSession, SessionStore
from dmbot.sidebar.answer import PROMPT_VERSION as SIDEBAR_PROMPT_VERSION
from dmbot.sidebar.answer import Sidebar
from dmbot.sidebar.ask import AskLimiter
from dmbot.sidebar.service import Recent, SidebarService
from dmbot.test_recording import files as test_files
from dmbot.test_recording import library as test_library
from dmbot.test_recording.session import TestSession
from dmbot.test_recording.store import TestVoiceStore
from dmbot.timebot import durations
from dmbot.timebot import phrases as clock_phrases
from dmbot.timebot.effects import EffectsSection, EffectStore
from dmbot.timebot.store import ClockSection, ClockStore
from dmbot.transcript import fix_notes, left_out
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
from dmbot.transcript.export import clock
from dmbot.transcript.models import Line, TranscriptBuffer
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.stream import TranscriptStream
from dmbot.transcript.topic_ai import classify
from dmbot.transcript.topics import GAME, OFF_TOPIC, TopicWindow, Waiting, obviously_game
from dmbot.transcript.topics import marker as topic_marker
from dmbot.transcription.base import PlaceholderTranscriber, Transcriber, confidence_of
from dmbot.transcription.factory import build_transcriber
from dmbot.transcription.pipeline import TranscriptionPipeline, clip_budget_s, speech_sent_line
from dmbot.ui import logic as ui_logic
from dmbot.ui import rule_card
from dmbot.ui.dmbot_commands import _failed, dmbot_group
from dmbot.ui.house_rules import dmbot_house_rules  # noqa: F401 (registers it)
from dmbot.ui.name_card import UndoButton
from dmbot.ui.name_lists import UndoListButton
from dmbot.ui.names import ReviewButton, after_session_text, review_view
from dmbot.ui.optional_rules import dmbot_optional_rules  # noqa: F401 (registers it)
from dmbot.ui.rule_lookup import dmbot_rule  # noqa: F401 (registers it)
from dmbot.ui.sheets import MySheetButton
from dmbot.ui.transcripts import (
    DownloadButton,
    download_view,
    ended_no_download_text,
    ended_text,
    transcript_command,
)

RULES_CARD_DB_S = 5.0  # the longest a rules card waits for the house rules
log = logging.getLogger(__name__)

SUMMARY_INTERVAL_S = 15
# Lines for the transcript channel are grouped and posted this often (#124): well inside
# Discord's 5 messages per 5 seconds per channel, and still feels live.
TRANSCRIPT_FLUSH_S = 2.0
TRANSCRIPT_SAVE_S = 5.0  # stored transcript lines are saved in batches this often
HEARD_MAX = 20_000  # lines kept for the after-session name scan
HINTS_FAIL_LOG_S = 60.0
HINTS_WAIT_S = 1.5  # the longest a clip waits for the campaign's names to load
# The off-topic filter (#52): one call may take this long, or the window is kept as game
# talk; after this many failures in a row it rests this long.
TOPIC_CALL_TIMEOUT_S = 8.0
TOPIC_FAILURES_BEFORE_REST = 3
TOPIC_REST_S = 300.0
EDIT_TIMEOUT_S = 5.0  # a transcript message edit, at most (it holds the post lock)
PUT_BACK_TIMEOUT_S = 5.0  # saving a Put it back, at most (it holds the save lock; #677)
HINT_PEOPLE_S = 5.0  # who's in the voice channel, for name hints: looked at this often
HintPeople = tuple[tuple[str, ...], tuple[str, ...]]  # (at the table, agreed but not there)
TRANSCRIPT_POST_TIMEOUT_S = 10.0  # one stuck post can't hold the others up for long
TRANSCRIPT_PARALLEL = 10  # campaigns posting at once (one rate-limited channel can't stall all)
STOP_DRAIN_TIMEOUT_S = 120.0  # at stop, wait this long for the last words to be written
FINAL_FLUSH_TIMEOUT_S = 15.0  # at stop or shutdown, give up on posting after this
IDLE_SWEEP_INTERVAL_S = 1
METER_INTERVAL_S = 60  # how often listening minutes are written to the hours meter (#437)
METER_FINAL_TRIES = 3  # at a stop: the last minutes are written nowhere else
METER_FINAL_RETRY_S = 2
NAMES_WAIT_S = 1.0  # the sidebar waits this long for a campaign's names, then answers without
GATE_TIMEOUT_S = 2  # a button press must be answered within Discord's 3 s: fail open sooner
CLOCK_PHRASE_GAP_S = 300  # the same rest said twice within this is counted once (#965)
METER_CALL_TIMEOUT_S = 8  # one write of minutes; a stuck database must not hold the loop
RECORDED_CHECK_S = 2.0  # the ⚙️ Menu's database check: well inside Discord's 3 s
NO_PINGS = discord.AllowedMentions.none()
TEST_RECORDINGS_MIN_FREE = 1 << 30  # a test session isn't started with less than 1 GB free
TEST_SAVES_AT_ONCE = 8  # more than this waiting to be saved are dropped, and the gap noted
AUDIO_FRAME_MS = 20  # the segmenter's end is the last frame's start; it runs a frame longer

# For the DM: nothing they can do but wait (the server's log says why, #636).
EARS_DOWN = (
    "I haven't started: I can't hear voice channels right now. "
    "Try `/dmbot start` again in a few minutes."
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


# ears may warn again on every utterance while someone's audio keeps failing (#631):
# the DM is told at most this often per person.
VOICE_LOST_TELL_EVERY_S = 300
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
    audio_checker: AudioChecker = field(default_factory=AudioChecker)  # #699
    # Names said lately, for hints that follow the scene (#126, #127), and the campaign's
    # names as last loaded (matching a line needs them without waiting).
    scene: SceneTracker = field(default_factory=SceneTracker)
    name_lookup: CampaignLookup | None = None
    hint_parts: HintParts | None = None
    # Spell, feature and item names from the players' D&D Beyond sheets (#723).
    sheet_hints: tuple[str, ...] = ()
    sheet_task: asyncio.Task[None] | None = None  # reading them; cancelled at the end
    sheet_loads: int = 0  # each load of the names counts up; only the newest one is used
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
    fix_ended: bool = False  # the session is over: the lists stay, their buttons go
    # Lines the off-topic filter left out, each run with Put it back (#677), likewise.
    left_out: left_out.LeftOut = field(default_factory=left_out.LeftOut)
    left_out_message: discord.Message | None = None
    left_out_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    left_out_queued: bool = False
    people: tuple[str, ...] = ()
    # The whole session's numbers, for the summary when it ends (#109).
    totals: SessionTotals = field(default_factory=SessionTotals)
    listening: bool = False
    notice_posted: bool = False
    peek_offered: bool = False  # players have been shown the Peek button this session
    voice_lost_told: dict[int, float] = field(default_factory=dict)  # user → when (#631)
    campaign_id: str | None = None
    campaign_name: str = ""
    dm_user_ids: frozenset[int] = frozenset()
    screen_level: str = DEFAULT_DM_SCREEN_LEVEL  # how much DMbot says in the DM screen
    # Rules cards when a spell or creature is named (#931): on or off, the rulesets to look
    # for names in, and what was shown, ignored and when (so, at most, one a minute).
    rules_on: bool = False
    rules_rulesets: tuple[str, str] = ("2024", "2014")
    rules: rules_cards.RulesCards = field(default_factory=rules_cards.RulesCards)
    # A house rule the DM said at the table, offered with Save / Edit / Cancel (#953).
    house_voice: house_voice.HouseVoice = field(default_factory=house_voice.HouseVoice)
    # When a rest said at the table last moved the game clock, by kind (monotonic seconds):
    # the same phrase twice in a few minutes is one rest (#965).
    clock_said: dict[str, float] = field(default_factory=dict)
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
    # Listening minutes already written to the hours meter (#437). None until first read:
    # the stored number, so a restart counts none twice. `meter_base` is that number and
    # `meter_since` when this process began listening, so time while DMbot was down is
    # never billed. `ended_at` is the stop, past which no tick bills.
    metered_minutes: int | None = None
    meter_base: int = 0
    meter_since: int = 0
    ended_at: int | None = None
    meter_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    metering_closed: bool = False  # the session ended: nothing more is billed
    listening_from: int = 0  # Unix seconds; since this process took the session on
    after_restart: bool = False  # listening_from is a restart (`resumed` resets on join)
    transcript_session_id: str | None = None  # set at the first save
    unsaved: TranscriptBuffer = field(default_factory=TranscriptBuffer)
    transcript_warned: bool = False  # told the DM saving isn't working
    listening_message: discord.Message | None = None  # carries the Stop button (#108)
    dropped_logged: bool = False
    save_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # One capture check at a time (#699: its audio check awaits the AI).
    check_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The off-topic filter (#52): lines waiting to be labelled, and the names-scan text
    # each holds back until then (hidden lines never reach the helpers); calls and
    # tokens, for the session's stats line.
    topics: TopicWindow = field(default_factory=TopicWindow)
    held: dict[tuple[int, int], str] = field(default_factory=dict)
    topic_calls: int = 0
    topic_tokens: list[int] = field(default_factory=lambda: [0, 0])  # in, out
    topic_tasks: set[asyncio.Task[None]] = field(default_factory=set)  # awaited at the end
    topic_timers: set[asyncio.Task[None]] = field(default_factory=set)  # cancelled then
    topic_failures: int = 0  # in a row: after a few, the filter rests a while
    topic_paused_until: float = 0.0  # monotonic seconds
    hidden: set[tuple[int, int]] = field(default_factory=set)  # lines shown as a marker
    # The DM sidebar (#935): what was said lately (the scene for an answer), and the
    # one-question-a-minute limit on asking at the table.
    recent: Recent = field(default_factory=Recent.new)
    # Test recordings (#1019): the saved session, only in a test server.
    test_session: TestSession | None = None
    sidebar_limiter: AskLimiter = field(default_factory=AskLimiter)

    def is_dm(self, user_id: int) -> bool:
        return user_id == self.dm_user_id or user_id in self.dm_user_ids


class DMBotTree(app_commands.CommandTree["DMBot"]):
    """Tags every slash-command log line with the server it came from."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        set_log_context(guild_id=interaction.guild_id)
        return True

    async def on_error(
        self, interaction: discord.Interaction[DMBot], error: app_commands.AppCommandError, /
    ) -> None:
        # A command that broke after answering first ("thinking…", #537) would leave
        # that up for good: log it and say so privately, as menus do (#595).
        if interaction.command is not None and interaction.command._has_any_error_handlers():
            return  # the command answers its own errors (none do today), as discord.py does
        command = interaction.command.qualified_name if interaction.command else "a command"
        if interaction.type is discord.InteractionType.autocomplete:
            log.warning("Suggestions for /%s failed: %s", command, type(error).__name__)
            return  # nothing can be said to the person here
        if isinstance(error, app_commands.CommandNotFound | app_commands.CommandSignatureMismatch):
            # Their Discord still has the commands from before an update.
            log.warning("/%s isn't up to date for this person: %s", command, error)
        # No command has a check today. One that adds a check gives a refused check
        # (app_commands.CheckFailure) its own plain words, not "something broke" (#698).
        cause = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        await _failed(interaction, cause, f"/{command}")


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
        sheets: SheetStore | None = None,
        house_rules: HouseRuleStore | None = None,
        meter: usage.Meter | None = None,
        clocks: ClockStore | None = None,
        house_file_links: HouseFileLinkStore | None = None,
        effects: EffectStore | None = None,
        test_voice: TestVoiceStore | None = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True  # who is in which voice channel; not privileged
        # Messages sent to DMbot in a private chat (the DM sidebar, #935); not privileged,
        # and DMs are the one place message content needs no special permission.
        intents.dm_messages = True
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
        # Players' D&D Beyond sheets, per campaign (#723).
        self.sheets = sheets
        # A campaign's house rules, for `/dmbot houserules` (#865); None without a database.
        self.house_rules = house_rules
        # Each campaign's game clock (#965); None without a database.
        self.clocks = clocks
        # The linked house-rules file (#969), and what is waiting for a DM's press.
        self.house_file_links = house_file_links
        self.house_syncs = house_sync.Pendings()
        # Timed effects on that clock (#998); None without a database.
        self.effects = effects
        # The hours meter (#437 part 2): listening minutes are written here; None records
        # nothing (tests and tools that run no real sessions).
        self.meter = meter
        # The daily job that warns about and deletes campaigns past their keep date (#964).
        # Needs the meter (the owners' plans); without one nothing is ever deleted.
        self.retention: RetentionJob | None = None
        if meter is not None:
            self.retention = RetentionJob(
                campaigns=campaigns,
                standing_of=meter.retention_standing,
                guild_ids=lambda: [g.id for g in self.guilds],
                running=lambda: {t.campaign_id for t in self.tables.values() if t.campaign_id},
                send=self._dm_user,
                enforce=settings.enforce_plans,
                server_name=lambda gid: g.name if (g := self.get_guild(gid)) else "your server",
                site_url=settings.site_url,
            )

        # Tells the two admins when the AI account is out of funds, and logs a daily usage
        # line (#972). Every AI client below reports to it.
        admins: dict[str, int] = {}
        for role, user_id in (
            ("primary", settings.admin_primary_id),
            ("secondary", settings.admin_secondary_id),
        ):
            if user_id is not None and user_id not in admins.values():  # the same person twice
                admins[role] = user_id
        self.ai_watch = AIWatch(admins=admins, state_dir=settings.data_dir, send=self._tell_admin)
        # One AI client for every job (#1006); each job holds the client for its own tier
        # (dmbot.ai.FEATURE_TIERS), so no job names a model. None when no key is set.
        if settings.ai_model_notice:
            log.warning(settings.ai_model_notice)
        if settings.ai_key:
            log.info(
                "AI models in use: fast=%s careful=%s deep=%s",
                settings.ai_models.fast,
                settings.ai_models.careful,
                settings.ai_models.deep,
            )
        else:
            log.info("AI is off (no ANTHROPIC_API_KEY)")
        self.ai_client = (
            AnthropicClient(settings.ai_key, settings.ai_models, watch=self.ai_watch)
            if settings.ai_key
            else None
        )
        # AI text calls (a document into a names list).
        self.ai = self.ai_client.tier(FEATURE_TIERS[Feature.NAMES]) if self.ai_client else None
        # The off-topic filter (#52), the audio check and the DM sidebar.
        self.topic_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.TOPIC]) if self.ai_client else None
        )
        self.audio_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.AUDIO_CHECK]) if self.ai_client else None
        )
        # The DM sidebar's answer engine (#934), on that same smallest model. #935 calls
        # `bot.sidebar_answers.answer(...)` for voice memos and "hold on, I need to find…"; None
        # without an AI key.
        self.sidebar_answers = self._make_sidebar_answers()
        self._hints_failed_at = -HINTS_FAIL_LOG_S
        # Per server: (when, who agreed, (names at the table, names not there)).
        self._hint_people_cache: dict[
            int, tuple[float, frozenset[int], int | None, HintPeople]
        ] = {}
        self._clean_failed_at = -HINTS_FAIL_LOG_S
        # Stored session transcripts anyone in the server can download (#41, #125).
        self.transcripts = transcripts
        self.tables: dict[int, Table] = {}
        self.test_voice = test_voice  # the second yes, for test servers (#1019)
        self._test_key: bytes | None = None
        self._test_delete_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._test_pending = 0  # utterances waiting to be saved
        self.sidebar = SidebarService(self)
        if settings.sidebar_on:  # off until the answers have been read (#954)
            self.sidebar.answerer = self.sidebar_answers
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
        # recorded()'s database lookups still running, one per person: a press while one
        # runs waits on it rather than starting another (a stalled database would pile up).
        self._lookups: dict[tuple[int, int], asyncio.Task[ConsentStatus]] = {}
        self._asking: set[asyncio.Task[None]] = set()  # private-message rounds in flight
        self._finishing: set[asyncio.Task[None]] = set()  # stopped sessions winding down
        # The hours follow-up (warn, grace, stop) in flight, per server (#437).
        self._follow_ups: dict[int, asyncio.Task[None]] = {}
        # Stopped sessions still writing down their last words, by server.
        self._ending: dict[int, list[Table]] = {}
        self._session_locks: dict[int, asyncio.Lock] = {}
        self._resume_started = False
        self._closing = False
        # Servers whose saved session is waiting for Discord to make the server available.
        self._resume_when_available: set[int] = set()
        # Offers made on the website: this process sends those of its own servers (#690).
        self.site_offers = SiteOffers(
            campaigns,
            get_guild=self.get_guild,
            guild_ids=lambda: [g.id for g in self.guilds],
            wait_until_ready=self.wait_until_ready,
            spawn=self._track,
            post=self.post,  # the #dm-screen note on an accept made on the website
        )

    # ---- lifecycle ---------------------------------------------------------

    async def setup_hook(self) -> None:
        # The application ID comes from Discord at login; messages use it for the
        # one-click install link, and the log shows it to whoever runs DMbot.
        install.configure(self.application_id)
        log.info(
            "Install link (add DMbot to a server, or fix its permissions): %s",
            install.install_link(),
        )
        # Load the free rules once now, off the event loop: the first rules lookup (and its
        # list of names, which Discord gives 3 seconds) then finds them ready (#908). Broken
        # packaged data stops start-up here, on purpose: better now than at the table.
        await asyncio.to_thread(rules_index.srd)
        self.tree.add_command(dmbot_group)
        self.tree.add_command(consent_group)
        self.tree.add_command(transcript_command)
        # DM-screen buttons keep working after a restart.
        self.add_dynamic_items(PeekButton, HideButton, VisibilityButton, StopListeningButton)
        self.add_dynamic_items(
            SettingsButton,
            LevelButton,
            SettingsVisibilityButton,
            RuleLookupButton,
            RulesCardsButton,
            HouseFileButton,
        )
        # Hand-over (#437): on ⚙️ Settings, in private messages, and after /dmbot start.
        self.add_dynamic_items(
            HandoverButton,
            WithdrawOfferButton,
            AcceptOfferButton,
            DeclineOfferButton,
            TakeOnButton,
            NotNowButton,
            PauseButton,  # pause and unpause (#957)
        )
        # Consent buttons in private messages, likewise.
        self.add_dynamic_items(*CONSENT_BUTTONS)
        # "Check new names" on the DM screen after a session.
        self.add_dynamic_items(ReviewButton)
        self.add_dynamic_items(clock_screen.ClockButton, clock_screen.ClockUndoButton)  # #965
        self.add_dynamic_items(timer_screen.EffectButton)  # timed effects (#998)
        # Undo after forgetting a name (its card), after a restart too.
        self.add_dynamic_items(UndoButton, UndoListButton)
        # "Download transcript" in the private message when a session ends.
        self.add_dynamic_items(DownloadButton)
        self.add_dynamic_items(NameQuestionButton, NameAnswerUndoButton, FixUndoButton)
        self.add_dynamic_items(PutBackButton)  # lines left out as off-topic (#677)
        self.add_dynamic_items(house_sync.HouseSyncButton)  # the house-rules file (#969)
        self.add_dynamic_items(rules_cards.RulesCardButton)  # rules cards from the table (#931)
        self.add_dynamic_items(house_voice_screen.HouseVoiceButton)  # house rules said aloud (#953)
        # A player's 📜 My character sheet, in their private messages (#723).
        self.add_dynamic_items(MySheetButton)
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
            self._watched(self.pipeline.run(), "transcribe"),
            self._watched(self._idle_sweeper(), "idle-sweep"),
            self._watched(self._summary_poster(), "summaries"),
            self._watched(self._meter_loop(), "hours-meter"),
            *([self._watched(self._retention_loop(), "retention")] if self.retention else []),
            *(
                [self._watched(self._test_recordings_loop(), "test-recordings")]
                if self.settings.test_recording_guilds
                else []
            ),
            *(
                [asyncio.create_task(self.lookup.follow(self.memory.listen), name="names")]
                if self.lookup is not None and self.memory is not None
                else []
            ),
            asyncio.create_task(self._transcript_poster(), name="transcripts"),
            asyncio.create_task(self._transcript_saver(), name="transcript-saves"),
            self._watched(self.site_offers.follow(self.campaigns.listen), "site-offers"),
            self._watched(self.site_offers.every_hour(), "site-offer-sweeps"),
            self._watched(self.site_offers.follow_decided(self.campaigns.listen), "site-decisions"),
        ]

    async def close(self) -> None:
        if self._closing:  # SIGTERM and the normal exit can both call this
            return
        self._closing = True
        # A notice to the admins that is on its way gets a moment to finish (#972).
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.ai_watch.wait(), 3)
        document_reader.shutdown()  # files being read: end them, don't wait out their limit
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
        lookups = list(self._lookups.values())  # so the database can close at once
        await self.sidebar.close()
        for task in [*self._background, *self._asking, *lookups]:
            task.cancel()
        await asyncio.gather(*self._background, *self._asking, *lookups, return_exceptions=True)
        await self.pipeline.transcriber.close()
        if self.ai_client is not None:
            await self.ai_client.close()
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

    async def recorded(self, guild_id: int, user_id: int) -> bool:
        """Whether they may be recorded in this server now, for the ⚙️ Menu (Stop or I
        consent), Keep recording and /consent revoke. Quick, so Discord's 3 seconds hold:
        the cache first (exactly who is captured now); the database only when the cache
        says no (a fresh process may not have loaded this server), and only briefly. Unsure
        counts as recorded, so the way to stop is never hidden."""
        if self.consent.has_consent(guild_id, user_id):
            return True
        # Not wait_for: that waits for the cancelled query to clean up, which a stalled
        # database can stretch to 12 s (#834). A late lookup is cancelled but not waited
        # for: its cleanup runs alone, held in _lookups (even if this wait is cancelled)
        # until it ends, and a press meanwhile joins it.
        key = (guild_id, user_id)
        lookup = self._lookups.get(key)
        if lookup is None:
            lookup = asyncio.create_task(self.consent.status(guild_id, [user_id]))
            self._lookups[key] = lookup
            lookup.add_done_callback(partial(self._lookup_done, key))
        done, _ = await asyncio.wait({lookup}, timeout=RECORDED_CHECK_S)
        if not done:
            if not lookup.cancelling():  # once: a second cancel would cut its cleanup short
                lookup.cancel()  # so a stalled connection is let go, not held for minutes
            log.warning("Couldn't look up consent in guild %s in time", guild_id)
            return True
        if lookup.cancelled():  # by close(), or an earlier press's timeout
            return True
        try:
            status = lookup.result()
        except Exception:
            log.warning("Couldn't look up consent in guild %s", guild_id, exc_info=True)
            return True
        return user_id in status.granted

    def _lookup_done(self, key: tuple[int, int], task: asyncio.Task[ConsentStatus]) -> None:
        """A recorded() lookup ended: let the next press start a fresh one, and read its
        failure, since nobody may be waiting for it any more (else asyncio reports "Task
        exception was never retrieved")."""
        if self._lookups.get(key) is task:
            del self._lookups[key]
        if not task.cancelled():
            task.exception()  # read, so a late failure isn't reported as never retrieved

    def stop_recording(self, guild_id: int, user_id: int) -> None:
        """Stop capturing this player now, without waiting for anything.

        Callers run this before their first await; `withdraw_consent` repeats it.
        """
        self.consent.stop_now(guild_id, user_id)
        self._stop_saving_now(guild_id, user_id)  # no recording means no saved voice (#1019)
        for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
            if table is None:
                continue
            table.segmenter.drop(user_id)
            table.capture_log.forget(user_id)  # their lines and counts go now (#699)
            table.transcript.drop_speaker(user_id)  # words not posted yet are discarded
            table.heard = [h for h in table.heard if h[0] != user_id]  # and never scanned
            table.topics.drop_speaker(user_id)  # and never sent to the off-topic filter
            table.held = {k: v for k, v in table.held.items() if k[0] != user_id}
            table.unsaved.drop_speaker(user_id)  # and never saved
            table.recent.drop_speaker(user_id)  # and never part of a sidebar answer's scene
            table.scene.forget_speaker(user_id)  # and no longer shape the hints
            table.vocabulary.forget_speaker(user_id)  # or the name fixes
            if table.fix_notes.drop_speaker(user_id):  # their fixes leave the DM screen
                self._redraw_fix_notes(table)
            if table.left_out.drop_speaker(user_id):  # and their lines left out (#677)
                self._redraw_left_out(table)
            if table.questions.drop_speaker(user_id, time.monotonic()) is not None:
                self._track(self._close_question(table), "name-question")
            for key in [k for k in table.heard_counts if k[1] == user_id]:
                del table.heard_counts[key]

    # ---- test recordings (#1019; dmbot.test_recording) ------------------------------------

    def test_voice_listed(self, guild_id: int) -> bool:
        """A test server: the second question is asked, and sessions can be saved."""
        return self.test_voice is not None and guild_id in self.settings.test_recording_guilds

    def test_voice_saving(self, guild_id: int, user_id: int) -> bool:
        return self.test_voice is not None and self.test_voice.has(guild_id, user_id)

    async def grant_test_voice(self, guild_id: int, user_id: int) -> None:
        """They said yes to "Save my voice for tests", and are recorded."""
        if self.test_voice is None or not self.test_voice_listed(guild_id):
            return
        await self.test_voice.grant(guild_id, user_id)

    async def stop_saving_voice(self, guild_id: int, user_id: int) -> None:
        """ "Stop saving my voice": nothing more is saved at once, then the yes is removed
        and their files are deleted from every test session. They stay recorded as usual."""
        self._stop_saving_now(guild_id, user_id, delete=False)
        await self._delete_saved_voice(guild_id, user_id)

    def _stop_saving_now(self, guild_id: int, user_id: int, *, delete: bool = True) -> None:
        """No more of this person's voice is saved, from this moment (no waiting). `delete`:
        also start deleting what was saved, in the background."""
        if self.test_voice is None:
            return
        self.test_voice.stop_now(guild_id, user_id)
        now = int(time.time() * 1000)
        for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
            if table is not None and table.test_session is not None:
                table.test_session.stopped(user_id, now)
                table.test_session.forget(user_id)  # their files in this session go too
        if delete and self.settings.test_recordings_dir.exists():
            # Whether or not the server is still listed: what was saved is deleted.
            self._track(self._delete_saved_voice(guild_id, user_id), "test-voice-delete")

    async def _delete_saved_voice(self, guild_id: int, user_id: int) -> None:
        """Remove the files first, then the yes: a database that is slow or down never leaves
        a person's voice on disk. One at a time for a person; never raises (logged)."""
        if self.test_voice is None:
            return
        root = self.settings.test_recordings_dir
        async with self._test_delete_locks.setdefault((guild_id, user_id), asyncio.Lock()):
            if root.exists():
                try:
                    key = await asyncio.to_thread(test_files.load_key, root)
                    live = [
                        t.test_session.folder
                        for t in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]
                        if t is not None and t.test_session is not None
                    ]
                    await asyncio.to_thread(
                        partial(test_library.delete_person, root, key, guild_id, user_id, live=live)
                    )
                except Exception:
                    log.exception("Couldn't delete a person's saved test voice")
            try:
                await self.test_voice.revoke(guild_id, user_id)
            except Exception:
                log.exception("Couldn't remove a person's yes to saving their voice")

    async def _start_test_session(self, table: Table) -> None:
        """In a test server, start saving this session (and say so once in the DM screen)."""
        if not self.test_voice_listed(table.guild_id) or table.test_session is not None:
            return
        assert self.test_voice is not None
        root = self.settings.test_recordings_dir
        try:
            await self.test_voice.load(table.guild_id)
            if self._test_key is None:
                self._test_key = await asyncio.to_thread(test_files.load_key, root)
            free = await asyncio.to_thread(_free_bytes, root)
            if free < TEST_RECORDINGS_MIN_FREE:
                log.warning("Not saving this test session: %d MB free", free // 2**20)
                return
            engine, _, rest = self.settings.transcription.source.partition(" ")
            table.test_session = TestSession(
                root,
                self._test_key,
                table.guild_id,
                table.started_at or int(time.time()),
                settings={
                    "transcriber": f"{engine} {rest.split(' ')[0]}".strip(),
                    "ai_models": {
                        "fast": self.settings.ai_models.fast,
                        "careful": self.settings.ai_models.careful,
                        "deep": self.settings.ai_models.deep,
                    },
                    "sidebar_prompt": SIDEBAR_PROMPT_VERSION,
                    "sidebar_on": self.settings.sidebar_on,
                    "commit": test_files.short_commit(os.environ.get("GIT_COMMIT")),
                },
            )
        except Exception:
            log.exception("Couldn't start saving a test session")
            return
        await self.post(table.screen_channel_id, screen_messages.TEST_SESSION)

    async def _save_test_audio(self, table: Table, utterance: Utterance, text: str | None) -> None:
        session, voice = table.test_session, self.test_voice
        if session is None or voice is None:
            return
        guild_id, user_id = table.guild_id, utterance.user_id
        if self._test_pending >= TEST_SAVES_AT_ONCE:  # the disk can't keep up: drop, and say so
            session.gap()
            log.warning("Dropped a test recording: %d already waiting", self._test_pending)
            return
        self._test_pending += 1

        def allowed() -> bool:  # both yeses, checked again before and after the file is made
            return (
                table.test_session is session
                and self.consent.has_consent(guild_id, user_id)
                and voice.has(guild_id, user_id)
            )

        try:
            await session.add_utterance(
                user_id,
                is_dm=table.is_dm(user_id),
                start_ms=utterance.start_ms,
                end_ms=utterance.end_ms + AUDIO_FRAME_MS,
                pcm=utterance.pcm,
                text=text,
                allowed=allowed,
            )
        except Exception:
            log.exception("Couldn't save a test recording")
        finally:
            self._test_pending -= 1

    def _finish_test_session(self, table: Table, caught_up: bool) -> None:
        if table.test_session is None:
            return
        try:
            table.test_session.finish({"complete": caught_up})
        except Exception:
            log.exception("Couldn't finish a test session")

    async def _test_recordings_loop(self) -> None:
        """Daily: delete saved sessions nobody kept that are over 7 days old (#1019)."""
        while True:
            now = int(time.time())
            await asyncio.sleep(max(60, retention.next_run_after(now) - now))
            try:
                gone = await asyncio.to_thread(
                    test_library.cleanup, self.settings.test_recordings_dir, time.time()
                )
                if gone:
                    log.info("Deleted %d old test session(s)", gone)
            except Exception:
                log.exception("The daily test-recordings clean-up failed")

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

    @staticmethod
    def _watched(work: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
        """A background task that must run as long as DMbot does: if it ever ends
        without being cancelled, that's logged at ERROR (#499)."""
        task = asyncio.create_task(work, name=name)

        def ended(done: asyncio.Task[None]) -> None:
            if done.cancelled():
                return
            problem = done.exception()
            log.error("The %s task stopped: %r", name, problem or "it returned")

        task.add_done_callback(ended)
        return task

    def _track(self, work: Awaitable[Any], name: str) -> asyncio.Task[None] | None:
        """Run `work` in the background; close() cancels it. Failures are logged. The
        task, or None when DMbot is shutting down (the work is dropped)."""
        if self._closing:
            if inspect.iscoroutine(work):
                work.close()  # never started: no "never awaited" warning
            return None

        async def guarded() -> None:
            try:
                await work
            except Exception:
                log.exception("Background %s failed", name)

        task = asyncio.create_task(guarded(), name=name)
        self._asking.add(task)
        task.add_done_callback(self._asking.discard)
        return task

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
                    text = reminder_text(
                        server,
                        voice_name,
                        granted,
                        cloud=cloud,
                        company=company,
                        sheets=self.sheets is not None,
                    )
                    view = menu_view(gid, table.campaign_id)
                else:
                    text = request_text(
                        server,
                        voice=voice_name,
                        dm=dm_name,
                        cloud=cloud,
                        renewed=member.id in status.outdated,  # the "What's new" note
                        company=company,
                        test_voice=self.test_voice_listed(gid),
                    )
                    if cloud and member.id in status.other_company:
                        text = f"{REASK_INTRO}\n\n{text}"  # why they're asked again
                    view = request_view(
                        gid, outside=self.outside_engine, test_voice=self.test_voice_listed(gid)
                    )
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
        view = None
        if table.campaign_id:
            # ⚙️ Settings first (#515): the common tap isn't next to Stop's edge.
            view = discord.ui.View(timeout=None)
            view.add_item(SettingsButton(table.campaign_id))
            view.add_item(StopListeningButton(table.campaign_id))
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
        table.listening_from = int(time.time())
        table.after_restart = table.resumed
        self.pipeline.session_started(table.guild_id)  # told of an outage afresh (#470)
        await self._start_test_session(table)  # only in a test server (#1019)
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
        # In the background, never delaying the start: read the players' sheets again
        # (once a session; not again after a restart) and use their names as hints.
        table.sheet_task = self._track(
            self._sheet_hints(table, refresh=not table.resumed), "sheets"
        )
        return sent

    async def _secret_keys(self, guild_id: int, campaign_id: str) -> frozenset[str] | None:
        """The campaign's secret names, as keys (for leaving them out of a log line), or
        None if they can't be known just now."""
        if self.lookup is None:
            return frozenset()
        try:
            lookup = await self.lookup.get(guild_id, campaign_id)
        except Exception:
            return None
        return frozenset(name_key(n.text) for n in lookup.names if n.secret)

    def sheets_changed(self, guild_id: int, campaign_id: str) -> None:
        """A sheet was linked, read, typed or unlinked: a session running for that
        campaign uses the new names from its next clip on (#723)."""
        table = self.tables.get(guild_id)
        if table is not None and table.campaign_id == campaign_id:
            self._track(self._sheet_hints(table, refresh=False), "sheets")

    async def _sheet_hints(self, table: Table, *, refresh: bool) -> None:
        """Hints from the campaign's sheets: the kept ones at once, then, if `refresh`,
        again once each linked sheet has been read (#723)."""
        if self.sheets is None or table.campaign_id is None:
            return
        guild_id, campaign_id = table.guild_id, table.campaign_id
        table.sheet_loads += 1
        mine = table.sheet_loads

        def current() -> bool:  # stopped (or started again) meanwhile: leave it be
            return self.tables.get(guild_id) is table

        with log_context(guild_id=guild_id, campaign_id=campaign_id):
            try:
                kept = await self.sheets.sheets(guild_id, campaign_id)
                if table.sheet_loads == mine:  # a later change already loaded newer ones
                    table.sheet_hints = tuple(sheet_hint_names(kept))
                if refresh and current():
                    now = int(time.time())
                    found = await refresh_sheets(
                        self.sheets, guild_id, campaign_id, now, found=kept, still_wanted=current
                    )
                    changed_meanwhile = table.sheet_loads != mine
                    if changed_meanwhile:
                        # Load again, with what was just read too (the line below still
                        # goes in the log, once a session).
                        self.sheets_changed(guild_id, campaign_id)
                    else:
                        table.sheet_hints = tuple(sheet_hint_names(found))
                    linked = sum(s.url is not None for s in found)
                    # Once a session. Only names read from D&D Beyond (game words, at
                    # most 15), never one that is also a secret name: never what a player
                    # typed, a link or a character's name.
                    secret = await self._secret_keys(guild_id, campaign_id)
                    read = [
                        name
                        for name in sheet_hint_names(
                            [s for s in found if s.sheet and s.sheet.get("source") == "dndbeyond"]
                        )
                        if secret is not None and name_key(name) not in secret
                    ]
                    log.info(
                        "Character sheets: %d linked, %d names in the hints; from D&D Beyond: %s",
                        linked,
                        len(sheet_hint_names(found)),
                        ", ".join(read) or "none",
                    )
            except Exception as exc:  # never the text: it can quote a row (links, names)
                log.error("Couldn't load the campaign's character sheets (%s)", type(exc).__name__)

    async def stop_table(self, guild_id: int, reason: str) -> Table | None:
        """End a running session. `reason` goes in the log (IDs only, no names)."""
        table = self.tables.pop(guild_id, None)
        if table is None:
            return None
        with log_context(guild_id=guild_id, campaign_id=table.campaign_id):
            log.info("Session ended: %s", reason)
        if table.sheet_task is not None:  # no more reading sheets for it (#723)
            table.sheet_task.cancel()
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
        self._redraw_left_out(table)  # likewise the lines left out, and Put it back
        for utterance in table.segmenter.flush_all():
            self.pipeline.enqueue(utterance)
        # In the background: /dmbot stop must answer within Discord's 3 seconds. Kept
        # apart from other background work, so a shutdown right after a stop still
        # saves the end of the transcript.
        ended_at = int(time.time())
        table.ended_at = ended_at  # no tick bills past this (#437)
        task = asyncio.create_task(self.wind_down(table, ended_at), name="wind-down")
        self._finishing.add(task)
        task.add_done_callback(self._finishing.discard)
        # The last, rounded-up minutes of the hours meter (#437) in a task of their own, so
        # a slow database can never hold up saving the transcript.
        meter_task = asyncio.create_task(self._meter_final(table, ended_at), name="meter-final")
        self._finishing.add(meter_task)
        meter_task.add_done_callback(self._finishing.discard)
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
                log.info(
                    "%s%s",
                    speech_sent_line(
                        ended_at - table.listening_from, self.pipeline.sent_s_in.get(session, 0)
                    ),
                    " since the restart" if table.after_restart else "",
                )
                await self._finish_topics(table)  # bounded: saving comes first
                if table.topic_calls:  # for cost per session hour (#52)
                    log.info(
                        "Off-topic filter: %d calls, %d tokens in, %d out",
                        table.topic_calls,
                        *table.topic_tokens,
                    )
                await self._after_session(table, ended_at, caught_up)
                self._finish_test_session(table, caught_up)
                # After it: the end-of-session capture check runs the last audio checks.
                if checks := table.audio_checker.log_line():  # cost of #699's checks
                    log.info(checks)
            finally:
                ending = self._ending.get(gid, [])
                if table in ending:
                    ending.remove(table)
                if not ending:
                    self._ending.pop(gid, None)
                    if gid not in self.tables:  # no new session meanwhile: keep nothing
                        # After the drain: the last clips' hints refill it until then.
                        self._hint_people_cache.pop(gid, None)
                self.pipeline.missed_in.pop(session, None)
                self.pipeline.failed_in.pop(session, None)
                self.pipeline.sent_s_in.pop(session, None)

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
            screen_messages.Spoke(
                self.name_of(gid, user_id),
                total.seconds,
                total.percent,
                user_id in table.totals.flagged,
            )
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

    def may_stop(self, guild_id: int, user_id: int, is_server_manager: bool) -> bool:
        """Whether this person may stop the session running here: its campaign's DMs or
        a server manager. Asked before the Stop button's question (#554); `stop_session`
        checks again when it's answered."""
        table = self.tables.get(guild_id)
        return table is not None and (table.is_dm(user_id) or is_server_manager)

    def active_session(self, guild_id: int) -> tuple[int, str] | None:
        """The running session here (its id and campaign name), for the Stop button's
        question (#554): its "Yes, stop" stops only that session, never a later one."""
        table = self.tables.get(guild_id)
        return (table.segmenter.session, table.campaign_name) if table else None

    async def is_campaign_playing(self, guild_id: int, campaign_id: str) -> bool:
        """Running here, or saved and about to be resumed after a restart."""
        if self.active_campaign_id(guild_id) == campaign_id:
            return True
        saved = await self.sessions.get(guild_id)
        return saved is not None and saved.campaign_id == campaign_id

    async def _dm_user(self, user_id: int, text: str) -> bool:
        """A private message that may fail (messages off, left Discord): False if it did."""
        try:
            async with asyncio.timeout(GATE_TIMEOUT_S * 2):
                user = self.get_user(user_id) or await self.fetch_user(user_id)
                await user.send(text, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            return False
        return True

    async def _retention_loop(self) -> None:
        """Once a day, after hours (UTC), warn about and delete campaigns past their keep
        date (#964). Safe to run twice; a failed run is logged and tried again tomorrow."""
        assert self.retention is not None
        while True:
            now = int(time.time())
            await asyncio.sleep(max(60, retention.next_run_after(now) - now))
            try:
                await self.retention.run_once(int(time.time()))
            except Exception:
                log.exception("The daily retention job failed")

    async def _tell_admin(self, user_id: int, text: str) -> None:
        """A private message to one of DMbot's admins (#972). Raises if it can't be sent, so
        the watch tries again later and doesn't count it as told."""
        user = self.get_user(user_id) or await self.fetch_user(user_id)
        await user.send(text, allowed_mentions=discord.AllowedMentions.none())

    def session_lock(self, guild_id: int) -> asyncio.Lock:
        """Held while a session starts or stops, or a campaign is replaced, per server."""
        return self._session_locks.setdefault(guild_id, asyncio.Lock())

    def start_blocker(self, guild_id: int) -> str | None:
        """Why `/dmbot start` can't begin right now, or None if it can."""
        if not self.ears.connected:
            # Logged here too: if ears never connected, nothing else says so (#636).
            log.warning("Can't start in server %s: ears isn't connected", guild_id)
            return EARS_DOWN
        table = self.tables.get(guild_id)
        if table is not None:
            name = f"**{table.campaign_name}** " if table.campaign_name else ""
            return (
                f"DMbot is already running {name}in <#{table.voice_channel_id}>. "
                "Its DM can stop it with `/dmbot stop`."
            )
        return None

    async def plan_refusal(self, guild_id: int, campaign: Campaign, starter_id: int) -> str | None:
        """Why the campaign's owner's plan, hours or campaign count don't allow a start, in
        plain words for the person starting it, or None (#437). A paused campaign is refused
        whatever the plan; the rest only when DMBOT_ENFORCE_PLANS is on. Fails open: a
        database hiccup must never lock a table out of its game."""
        owner = campaign.owner_user_id
        if owner is not None and self.settings.enforce_plans and self.meter is not None:
            # A plan that shrank pauses the campaigns over its cap first, so this one may
            # just have been paused (#957).
            await self._settle_cap(guild_id, owner)
            try:
                campaign = await self.campaigns.get(guild_id, campaign.id) or campaign
            except Exception:
                log.exception("Couldn't reload the campaign after settling the cap")
        if campaign.paused:  # the owner's choice, or the plan's: not a plan check, so always
            return hours.PAUSED_OWNER if starter_id == owner else hours.PAUSED_OTHER
        if not self.settings.enforce_plans or self.meter is None:
            return None
        if owner is None:
            # A DM of the campaign gets a Take it on button with this (see the Start button).
            return hours.NO_OWNER_ASK if starter_id in campaign.dm_user_ids else hours.NO_OWNER
        try:
            async with asyncio.timeout(METER_CALL_TIMEOUT_S):
                check = await self.meter.check(guild_id, owner, int(time.time()))
        except Exception:  # a slow database (TimeoutError) too
            log.exception("Couldn't check the plan; starting anyway")
            return None
        refused = hours.refusal(
            check.verdict,
            is_owner=starter_id == owner,
            site_url=self.settings.site_url,
            can_change_plan=check.can_change_plan,
            renews=check.renews,
            extra_hours=check.extra_hours,
        )
        if refused is not None:
            return refused
        # The owner's plan and hours are fine; do they own more campaigns than the plan
        # covers? (They can always start one they own within the cap: `fits(0)`.)
        try:
            async with asyncio.timeout(METER_CALL_TIMEOUT_S):
                room = await self.meter.campaign_room(guild_id, owner, int(time.time()))
        except Exception:
            log.exception("Couldn't count the owner's campaigns; starting anyway")
            return None
        if room.fits(0) or room.cap is None:
            return None
        return hours.campaigns_refusal(
            room.cap,
            room.owned,
            is_owner=starter_id == owner,
            site_url=self.settings.site_url,
            can_change_plan=room.can_change_plan,
        )

    async def create_refusal(self, guild_id: int, user_id: int) -> str | None:
        """Why this person may not make one more campaign, or None (#437 part 2c). Only when
        DMBOT_ENFORCE_PLANS is on, and only for a plan that works and has a cap they are
        already at: someone with no plan can make a campaign (they pick a plan to start it),
        and making one past the cap would lock all their campaigns out of starting. Fails
        open, like the start check. (Two made at the same instant could both pass; the start
        check still refuses them until one is paused.)"""
        if not self.settings.enforce_plans or self.meter is None:
            return None
        await self._settle_cap(guild_id, user_id, 1.0)  # shrunk plan: pause extras (3 s deadline)
        try:
            async with asyncio.timeout(GATE_TIMEOUT_S):
                room = await self.meter.campaign_room(guild_id, user_id, int(time.time()))
        except Exception:
            log.exception("Couldn't count the person's campaigns; allowing it")
            return None
        if not room.works or room.cap is None or room.fits(1):
            return None
        return hours.campaigns_refusal(
            room.cap,
            room.owned,
            is_owner=True,
            site_url=self.settings.site_url,
            can_change_plan=room.can_change_plan,
            creating=True,
        )

    async def unpause_refusal(self, guild_id: int, user_id: int) -> str | None:
        """Why this owner can't unpause a campaign: their plan has no room for one more
        running campaign (#957), in the same words as the start refusal. Same switch and
        fail-open as `create_refusal`; the store checks again as the backstop."""
        if not self.settings.enforce_plans or self.meter is None:
            return None
        await self._settle_cap(guild_id, user_id, GATE_TIMEOUT_S)
        try:
            async with asyncio.timeout(GATE_TIMEOUT_S):
                room = await self.meter.campaign_room(guild_id, user_id, int(time.time()))
        except Exception:
            log.exception("Couldn't count the person's campaigns; allowing the unpause")
            return None
        if not room.works or room.cap is None or room.fits(1):
            return None
        return hours.campaigns_refusal(
            room.cap,
            room.owned,
            is_owner=True,
            site_url=self.settings.site_url,
            can_change_plan=room.can_change_plan,
            unpausing=True,
        )

    async def set_paused(
        self, guild_id: int, campaign_id: str, user_id: int, paused: bool
    ) -> Campaign:
        """Pause or unpause a campaign for its owner (#957). A campaign DMbot is listening
        to can't be paused (stop it first); an unpause that would go over the plan's cap
        says so in the plan's words. Raises CampaignError in plain words."""
        # Under the session lock, like a start, so a start can't slip in between the
        # check and the save.
        async with self.session_lock(guild_id):
            table = self.tables.get(guild_id)
            if paused and table is not None and table.campaign_id == campaign_id:
                raise CampaignError(
                    "DMbot is listening to this campaign now. Stop it with `/dmbot stop`, "
                    "then pause it."
                )
            if not paused:
                refused = await self.unpause_refusal(guild_id, user_id)
                if refused:
                    raise CampaignError(refused)
            return await self.campaigns.set_paused(
                guild_id, campaign_id, user_id, paused, int(time.time())
            )

    async def _settle_cap(
        self, guild_id: int, owner_id: int, wait: float = METER_CALL_TIMEOUT_S
    ) -> None:
        """Pause the owner's campaigns over their plan's cap, and tell them once in a
        private message which and how to change it (#957). Done when the bot next reads the
        plan (a start, a new campaign, an unpause) rather than from the website's webhook:
        the bot is the one that can message the owner, and a change that lands while it is
        down is still settled. Best effort and fail-open: a database hiccup never blocks."""
        if not self.settings.enforce_plans or self.meter is None:
            return
        try:
            async with asyncio.timeout(wait):
                live = [t.campaign_id for t in self.tables.values() if t.campaign_id]
                settled = await self.meter.settle_cap(guild_id, owner_id, int(time.time()), live)
        except Exception:
            log.exception("Couldn't settle the owner's campaign cap")
            return
        if settled is None or not settled.paused:
            return
        text = hours.paused_told(
            [discord.utils.escape_markdown(p.name) for p in settled.paused],
            settled.cap,
            site_url=self.settings.site_url,
            can_change_plan=settled.can_change_plan,
        )
        try:
            async with asyncio.timeout(GATE_TIMEOUT_S):
                user = self.get_user(owner_id) or await self.fetch_user(owner_id)
                await user.send(text, allowed_mentions=discord.AllowedMentions.none())
        except Exception:  # never let a note break a start (private messages off, slow, ...)
            # The pause is saved and the start refusal says it too.
            log.info("Couldn't tell an owner their campaigns were paused", exc_info=True)

    def _make_sidebar_answers(self) -> Sidebar | None:
        if self.ai_client is None:
            return None

        async def gate(campaign: Campaign, user_id: int) -> str | None:
            return await self.plan_gate("ai", campaign.guild_id, campaign, user_id)

        async def houses(campaign: Campaign) -> Sequence[HouseRule]:
            if self.house_rules is None:
                return []
            return await self.house_rules.list(campaign.guild_id, campaign.id)

        async def names(campaign: Campaign) -> CampaignLookup | None:
            if self.lookup is None:
                return None
            return await self.lookup.get_within(campaign.guild_id, campaign.id, NAMES_WAIT_S)

        ai = self.ai_client.tier(FEATURE_TIERS[Feature.SIDEBAR])
        return Sidebar(ai, rules_index.srd, gate=gate, houses=houses, names=names)

    async def plan_gate(
        self, action: plan_rules.Action, guild_id: int, campaign: Campaign, user_id: int
    ) -> str | None:
        """Why `user_id` may not do `action` (use the AI, make a copy or transcript, replace
        the campaign from a copy) with this campaign, in plain words for them, or None
        (#437 part 3). Judged by the campaign's owner's plan, so the whole table stops or goes
        together. Only when DMBOT_ENFORCE_PLANS is on. Fails open like `plan_refusal`: a
        database hiccup, or one slower than Discord's 3 seconds allow, must not lock the table
        out."""
        if not self.settings.enforce_plans or self.meter is None:
            return None
        owner = campaign.owner_user_id
        access = None
        if owner is not None:
            try:
                async with asyncio.timeout(GATE_TIMEOUT_S):
                    access = await self.meter.access(guild_id, owner, int(time.time()))
            except Exception:  # a slow database (TimeoutError) too
                log.exception("Couldn't check the plan; allowing it")
                return None
        return plan_rules.refusal(
            action,
            access,
            is_owner=user_id == owner,
            owner_known=owner is not None,
            site_url=self.settings.site_url,
        )

    async def plan_allows(
        self, action: plan_rules.Action, guild_id: int, campaign: Campaign
    ) -> bool:
        """Does the campaign's owner's plan allow `action`, as a plain yes or no (no words, so
        no question of who is asking)? Fails open (True) like `plan_gate`, and is True when
        plans aren't enforced. A campaign with no owner has no plan to allow anything."""
        if not self.settings.enforce_plans or self.meter is None:
            return True
        owner = campaign.owner_user_id
        access = None
        if owner is not None:
            try:
                async with asyncio.timeout(GATE_TIMEOUT_S):
                    access = await self.meter.access(guild_id, owner, int(time.time()))
            except Exception:
                log.exception("Couldn't check the plan; allowing it")
                return True
        return plan_rules.allowed(plan_rules.RULE_OF[action], access)

    async def restore_gate(
        self, guild_id: int, user_id: int, replacing: Campaign | None = None
    ) -> str | None:
        """Why this person may not load a copy, or None (#437 part 3). A copy loaded as a new
        campaign makes them its owner, so it is their own plan that has to include copies
        (the free slot is the store's check, `restore_needs_slot`), and they hear about it
        since it is theirs. A copy loaded over a campaign that has an owner keeps that owner
        (#609), so it is the campaign's owner's plan that counts, as for any copy: a co-DM
        with no plan may restore their paid owner's campaign. Over a campaign with no owner
        the restorer becomes it, which is the first case."""
        if not self.settings.enforce_plans or self.meter is None:
            return None
        if replacing is not None and replacing.owner_user_id is not None:
            return await self.plan_gate("restore", guild_id, replacing, user_id)
        try:
            async with asyncio.timeout(GATE_TIMEOUT_S):
                access = await self.meter.access(guild_id, user_id, int(time.time()))
        except Exception:
            log.exception("Couldn't check the plan; allowing it")
            return None
        return plan_rules.refusal("restore", access, is_owner=True, site_url=self.settings.site_url)

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
        refused = await self.plan_refusal(guild.id, campaign, user.id)
        if refused:
            return False, refused
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
            screen_level=campaign.dm_screen_level,
            rules_on=campaign.rules_cards,
            rules_rulesets=(campaign.target_ruleset, campaign.fallback_ruleset),
            transcript_channel_id=transcript_id,
            started_at=started_at,
        )
        if table.rules_on:
            self._warm_rules(table.rules_rulesets)
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
        self._check_house_file(campaign, screen_id)
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
        session: int | None = None,
    ) -> str:
        """`campaign_id`: only stop if this campaign is the one being listened to (a Stop
        button on an older message must never end a newer session). `session`: only stop
        that very session (the Stop button's question, #554)."""
        async with self.session_lock(guild_id):
            table = self.tables.get(guild_id)
            if campaign_id is not None and (table is None or table.campaign_id != campaign_id):
                return screen_messages.NOT_LISTENING_NOW
            if session is not None and (table is None or table.segmenter.session != session):
                return screen_messages.STOP_STALE
            if table is None:
                # Maybe a saved session that hasn't been picked up again yet (DMbot is
                # restarting): stopping must still end it, or it would come back.
                return await self._stop_saved(guild_id, user_id, is_server_manager)
            if not (table.is_dm(user_id) or is_server_manager):
                return screen_messages.ONLY_DM_STOPS
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
                return screen_messages.ONLY_DM_STOPS
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
        # A hand-over offer made on the website may be waiting for this server (#690).
        self._track(self.site_offers.sweep_server(guild.id), "offer-sweep")

    async def on_guild_available(self, guild: discord.Guild) -> None:
        """Discord made a server available again: resume its session if one was waiting."""
        if self.is_ready():  # offers made on the website while it was out (#690)
            self._track(self.site_offers.sweep_server(guild.id), "offer-sweep")
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
        if campaign.paused:
            # Paused (by the owner, or by a shrunk plan) while a session was saved: a paused
            # campaign never listens (#957), so a restart must not bring it back to life.
            await self.sessions.clear(guild.id, "not resumed: the campaign is paused")
            await self.post(screen_id, hours.paused_resume(campaign.name))
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
            screen_level=campaign.dm_screen_level,
            rules_on=campaign.rules_cards,
            rules_rulesets=(campaign.target_ruleset, campaign.fallback_ruleset),
            resumed=True,
            announce_resume=not recently,
            transcript_channel_id=self._usable_transcript(guild, campaign),
            started_at=saved.started_at,
        )
        if table.rules_on:
            self._warm_rules(table.rules_rulesets)
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
                    message.user_id,
                    message.frames_received,
                    message.frames_expected,
                    time.monotonic(),
                    decrypt_failures=message.decrypt_failures,
                    decode_errors=message.decode_errors,
                    link_dropped=message.link_dropped,
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
        elif status.state == "warning" and status.user_id is not None:
            # One speaker's audio failed; the session goes on (#631). IDs only in the log.
            log.warning("Voice warning for user %s: %s", status.user_id, status.detail or "")
            now = time.monotonic()
            last = table.voice_lost_told.get(status.user_id)
            if table.listening and (last is None or now - last >= VOICE_LOST_TELL_EVERY_S):
                table.voice_lost_told[status.user_id] = now
                name = self.name_of(table.guild_id, status.user_id)
                await self.post(table.screen_channel_id, screen_messages.voice_lost_message(name))
        elif status.state in ("left", "error"):
            table.listening = False
            # The detail comes from ears' own fixed messages, never from users.
            log.warning("Voice %s: %s", status.state, status.detail or "no detail")
            detail = status.detail or "The voice connection ended."
            await self.post(table.screen_channel_id, f"⚠️ {detail}")

    def _all_tables(self) -> list[Table]:
        """Running sessions and stopped ones still finishing."""
        return [*self.tables.values(), *(t for ts in self._ending.values() for t in ts)]

    # ---- the DM sidebar (#935; dmbot.sidebar.service) -----------------------------------

    async def on_message(self, message: discord.Message) -> None:
        """Only private messages reach here (the DM intent); the sidebar reads them."""
        try:
            await self.sidebar.on_dm_message(message)
        except Exception:
            log.exception("Couldn't handle a private message")

    def sidebar_has_consent(self, guild_id: int, user_id: int) -> bool:
        return self.consent.has_consent(guild_id, user_id)

    async def sidebar_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None:
        return await self.campaigns.get(guild_id, campaign_id)

    def sidebar_server_name(self, guild_id: int) -> str:
        guild = self.get_guild(guild_id)
        return guild.name if guild else "another server"

    def consent_request(self, table: Table) -> tuple[str, discord.ui.View]:
        """The question about recording, for someone who hasn't agreed yet."""
        guild = self.get_guild(table.guild_id)
        voice = self.get_channel(table.voice_channel_id)
        text = request_text(
            guild.name if guild else "your server",
            voice=voice.name if isinstance(voice, discord.abc.GuildChannel) else None,
            dm=self.name_of(table.guild_id, table.dm_user_id),
            cloud=self.sends_audio_out,
            company=self.company,
            test_voice=self.test_voice_listed(table.guild_id),
        )
        return text, request_view(
            table.guild_id,
            outside=self.outside_engine,
            test_voice=self.test_voice_listed(table.guild_id),
        )

    async def sidebar_transcribe(self, table: Table, utterance: Utterance) -> str | None:
        """A voice message written down by the table's speech-to-text, with the
        campaign's names as hints. Consent was checked by the caller, and is again."""
        hints = await self._name_hints(utterance)
        if self.settings.transcription.sends_audio_out:  # billed by the outside company
            self.pipeline.sent_s_in[utterance.session] += utterance.duration_s
        text = await asyncio.wait_for(
            self.pipeline.transcriber.transcribe(utterance, hints),
            clip_budget_s(utterance.duration_s),
        )
        return str(text) if text else None

    def sidebar_clean(self, table: Table, text: str) -> str:
        """Names fixed the way table speech is (only the ones DMbot is sure of)."""
        if table.name_lookup is None:
            return text
        return self._clean(table, text, unsure=False).text

    async def sidebar_send_dm(self, user_id: int, text: str) -> str:
        """ "sent", "forbidden" (their messages from DMbot are closed) or "failed"."""
        try:
            user = self.get_user(user_id) or await self.fetch_user(user_id)
            await user.send(text, allowed_mentions=NO_PINGS)
        except discord.Forbidden as exc:
            log.info("Couldn't message user %s privately: %s", user_id, exc)
            return "forbidden"
        except discord.HTTPException as exc:
            log.info("Couldn't message user %s privately: %s", user_id, exc)
            return "failed"
        return "sent"

    async def sidebar_tell_screen(self, table: Table, text: str) -> None:
        await self.post(table.screen_channel_id, text)

    def sidebar_stt(self) -> str:
        return self.settings.transcription.source

    def sidebar_save(self, table: Table, line: Line) -> None:
        """A sidebar line for the stored (raw) transcript; never the live channel."""
        if self.transcripts is not None and self.tables.get(table.guild_id) is table:
            table.unsaved.add(line)
        if table.test_session is not None and line.sidebar:  # what the session produced
            # Only for a speaker who said yes to saving (the session checks).
            table.test_session.shown(
                line.user_id, f"sidebar {line.sidebar}", line.started_ms, line.heard
            )

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
            # Fixes DMbot isn't sure of are never silent: made only when the DM screen
            # shows them with Undo (a running session, not quiet; #296, #504).
            noted = self.tables.get(table.guild_id) is table and screen_levels.allows(
                table.screen_level, screen_levels.FIX_NOTE
            )
            result = self._clean(table, text, unsure=noted)
            cleaned = result.text
            self._offer_question(table, utterance.user_id, utterance.start_ms, text, result)
            if noted and table.fix_notes.add(
                utterance.user_id, utterance.start_ms, text, result.fixes
            ):
                self._redraw_fix_notes(table)
            named = mentions(table.name_lookup, cleaned)  # once per name per line
            table.scene.note(named, utterance.user_id, time.monotonic())
            table.heard_counts.update((entity_id, utterance.user_id) for entity_id in named)
            names_said = len(named)
        else:
            names_said = 0
        if text:
            # After cleaning, so a line is never evidence about itself; even when the names
            # couldn't be loaded, a word said in lower case is a real word next time.
            table.vocabulary.note(utterance.user_id, text)
        if text:
            try:  # a card is never worth a lost line: nothing below may be skipped
                self._note_rules(table, utterance.user_id, str(cleaned or text))
            except Exception:
                log.exception("Rules cards: couldn't look at a line")
            try:
                if utterance.user_id in table.dm_user_ids:  # only a DM can start a proposal
                    self._note_house_rule(table, utterance.user_id, str(cleaned or text))
            except Exception:
                log.exception("House rules by voice: couldn't look at a line")
            if utterance.user_id in table.dm_user_ids and self.clocks is not None:
                self._track(  # only a DM's own line can move the clock
                    self._note_clock_phrase(table, utterance.user_id, str(cleaned or text)),
                    "clock-phrase",
                )
        if text and self.transcripts is not None:
            duration_ms = int(utterance.duration_s * 1000)
            table.unsaved.add(
                Line(utterance.start_ms, utterance.user_id, text, cleaned or text, duration_ms)
            )
        saving = (
            table.test_session is not None
            and self.test_voice is not None
            # Said yes to saving; _save_test_audio's allowed() re-checks this and the
            # recording consent before and after every step.
            and self.test_voice.has(table.guild_id, utterance.user_id)
        )
        if saving:
            self._track(
                self._save_test_audio(table, utterance, str(cleaned) if cleaned else None),
                "test-audio",
            )
        if text:
            table.recent.add(utterance.user_id, str(cleaned or text), time.monotonic())
            if self.tables.get(table.guild_id) is table:
                # Only the DM's own lines can start it; consent was just re-checked. Lines
                # the Cleaner tags as in character are not told apart yet (in_character).
                self.sidebar.ask_at_table(table, utterance.user_id, str(cleaned or text))
        if text and self._topic_pending(table, utterance, cleaned or text, names_said):
            pass  # held for the off-topic filter: the names scan gets it once labelled
        elif text and len(table.heard) < HEARD_MAX:
            # The cleaned line: a known name misheard and fixed live isn't new (#394).
            table.heard.append((utterance.user_id, cleaned or text))
            if len(table.heard) == HEARD_MAX:
                log.info("Name scan: kept the first %d lines of this session", HEARD_MAX)
        if text:
            # What the audio check reads if their audio rule fires (#699): the line as the
            # transcript has it (before the off-topic filter, which may hide it later),
            # with the engine's confidence; kept as plain text.
            table.capture_log.add_line(utterance.user_id, str(cleaned or text), confidence_of(text))
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

    def _note_rules(self, table: Table, user_id: int, line: str) -> None:
        """A spell, condition or creature named at the table (#931): a short card for the DM
        screen, if the campaign turned that on. Names only, no AI. Limits: one card a name a
        session, one a minute (the rest are dropped), and nothing once the session is over.
        The card is posted in the background; the speech pipeline never waits for it."""
        if (
            not table.rules_on
            or not table.listening
            or self.tables.get(table.guild_id) is not table
        ):
            return
        mentions = rules_cards.spotter_for(*table.rules_rulesets).find(line)
        if mentions and table.name_lookup is not None:
            # A character, NPC or place of this campaign called Sprite or Raven is a name
            # the table uses, not a rules question.
            known = table.name_lookup.by_key
            mentions = [
                m
                for m in mentions
                if name_key(m.said) not in known and name_key(m.entry.name) not in known
            ]
        mention = table.rules.pick(mentions, time.monotonic()) if mentions else None
        if mention is None:
            return
        before = table.rules.last_at  # given back if the card can't be shown
        card_id = table.rules.remember(mention, time.monotonic())
        self._track(self._post_rules_card(table, user_id, mention, card_id, before), "rules-card")

    async def _note_clock_phrase(self, table: Table, user_id: int, line: str) -> None:
        """A DM's clear "we take a short rest" / "you take a long rest" moves the game clock,
        if the DM has set one (#965): no AI, and a player's line never does. One short note
        on the DM screen with an Undo button."""
        rest = clock_phrases.find(line)
        if rest is None or table.campaign_id is None:
            return
        if not table.listening or self.tables.get(table.guild_id) is not table:
            return
        if user_id not in table.dm_user_ids or not self.consent.has_consent(
            table.guild_id, user_id
        ):
            return
        now = time.monotonic()
        last = table.clock_said.get(rest)
        if last is not None and now - last < CLOCK_PHRASE_GAP_S:
            return
        table.clock_said[rest] = now
        campaign = await self.campaigns.get(table.guild_id, table.campaign_id)
        if campaign is None or user_id not in campaign.dm_user_ids:
            return
        try:
            result = await clock_screen.press(self, campaign.guild_id, campaign.id, user_id, rest)
        except Exception:
            log.exception("The game clock couldn't take a rest said at the table")
            return
        if result.before is None or result.after is None:
            return  # no clock set yet: nothing to move
        stored = await self.clocks.get(campaign.guild_id, campaign.id) if self.clocks else None
        if stored is None:
            return
        await clock_screen.show(self, campaign, stored)
        await timer_screen.announce_due(self, campaign, stored.clock.minute)
        await clock_screen.say(
            self,
            campaign,
            [clock_screen.rest_note(rest, result.after), *result.lines],
            clock_screen.undo_id(result.before, result.after),
        )

    def _check_house_file(self, campaign: Campaign, screen_id: int) -> None:
        """At a session start: read the campaign's linked house-rules file, if any, and put
        what differs on the DM screen (#969). In the background; it never holds up the
        session, and says nothing when there is no link or nothing to change."""
        if self.house_file_links is None:
            return

        async def send(text: str, view: discord.ui.View) -> bool:
            posted = await self.post_message(screen_id, text, view if view.children else None)
            return posted is not None

        self._track(
            house_sync.check_linked(self, campaign, send, quiet_if_same=True), "house-file-check"
        )

    def sidebar_house_rule(self, table: Table, user_id: int, text: str) -> str:
        """A house rule typed to DMbot in the DM's private chat (#960): the same proposal,
        limits and checks as one said aloud. Says what happened (`house_voice.STARTED`,
        TOO_SOON, REPEAT or NOT_NOW)."""
        return self._note_house_rule(table, user_id, text, typed=True)

    def _note_house_rule(
        self, table: Table, user_id: int, line: str, *, typed: bool = False
    ) -> str:
        """A DM's line that starts "house rule: …" and the like (#953): offer to write it
        down, on the DM screen only. No AI. Nothing is saved without a DM pressing Save.
        One proposal a minute; the same words once a session. Says if one was started."""
        if not table.listening or self.tables.get(table.guild_id) is not table:
            return house_voice.NOT_NOW
        if user_id not in table.dm_user_ids or table.campaign_id is None:
            return house_voice.NOT_NOW
        found = house_voice.find(line)
        if found is None:  # (too few words to be a rule)
            return house_voice.NOT_NOW
        now_s = time.monotonic()
        said = table.house_voice.pick(found, now_s)
        if said is None:
            return table.house_voice.why_not(found, now_s)
        proposal_id, now, before = (
            house_voice_screen.new_id(),
            time.monotonic(),
            table.house_voice.last_at,
        )
        proposal = house_voice.Proposal(
            said,
            (),
            house_voice_screen.scenario_for(time.time(), typed=typed),
            table.transcript_session_id,
        )
        table.house_voice.remember(said, now, proposal_id, proposal)
        self._track(
            self._post_house_proposal(table, user_id, proposal_id, proposal, before, now),
            "house-rule-voice",
        )
        return house_voice.STARTED

    async def _post_house_proposal(
        self,
        table: Table,
        user_id: int,
        proposal_id: str,
        proposal: house_voice.Proposal,
        before: float | None,
        now: float,
    ) -> None:
        """Post the proposal in the DM screen only. A proposal that can't be shown gives its
        words and its minute back."""
        rules: list[HouseRule] = []
        unchecked = True
        if self.house_rules is not None and table.campaign_id is not None:
            try:  # this campaign's own house rules and no other's
                async with asyncio.timeout(RULES_CARD_DB_S):
                    rules = await self.house_rules.list(table.guild_id, table.campaign_id)
                unchecked = False
            except TimeoutError:
                log.warning("Reading house rules for a proposal took too long")
            except Exception:
                log.exception("Couldn't read house rules for a proposal")
        clashing = await asyncio.to_thread(  # the first build of the names takes ~0.15 s
            house_voice_screen.clashes, proposal.said, rules, *table.rules_rulesets
        )
        proposal = house_voice.Proposal(
            proposal.said, clashing, proposal.scenario, proposal.session_id, unchecked
        )
        # After the awaits: the DM's words are shown, so they must still be recorded and the
        # session still running.
        if (
            not self.consent.has_consent(table.guild_id, user_id)
            or not table.listening
            or self.tables.get(table.guild_id) is not table
        ):
            table.house_voice.forget(proposal_id, before, now)
            return
        table.house_voice.proposals[proposal_id] = proposal
        try:
            posted = await self.post_message(
                table.screen_channel_id,
                house_voice_screen.proposal_text(proposal),
                house_voice_screen.proposal_view(table.guild_id, proposal_id, proposal),
            )
        except Exception:
            log.exception("Couldn't post a house rule proposal")
            posted = None
        if posted is None:
            table.house_voice.forget(proposal_id, before, now)

    def _warm_rules(self, rulesets: tuple[str, str]) -> None:
        """Build the names to look for off the event loop (about 0.15 s), so the first line
        of a session with rules cards on doesn't wait for it."""
        self._track(asyncio.to_thread(rules_cards.spotter_for, *rulesets), "rules-cards-warm")

    async def _post_rules_card(
        self, table: Table, user_id: int, mention: Mention, card_id: str, before: float | None
    ) -> None:
        """Post the card in the DM screen only (never the transcript, never to players).
        A card that can't be shown gives its name and its minute back."""
        target, fallback = table.rules_rulesets
        srd = rules_index.srd()
        entry = mention.entry
        hit = srd.lookup(mention.said, target, fallback, kind=entry.kind)
        if hit is None or hit.entry != entry:  # the entry spotted is the entry shown
            hit = srd.lookup(entry.name, target, fallback, kind=entry.kind)
        rules: list[HouseRule] = []
        if hit is None:
            table.rules.forget(card_id, before)
            return
        if self.house_rules is not None and table.campaign_id is not None:
            try:  # this campaign's own house rules and no other's
                async with asyncio.timeout(RULES_CARD_DB_S):
                    rules = await self.house_rules.list(table.guild_id, table.campaign_id)
            except Exception:
                log.exception("Couldn't read house rules for a rules card")
        # After the awaits: the words heard are shown, so the speaker must still be recorded,
        # and the session must still be running with the setting on.
        if (
            not self.consent.has_consent(table.guild_id, user_id)
            or not table.rules_on
            or not table.listening
            or self.tables.get(table.guild_id) is not table
        ):
            table.rules.forget(card_id, before)
            return
        text = rule_card.alert_text(hit, mention.said, mention.heard, rules)
        timed = (
            hit.entry.kind == "spell"
            and durations.parse(str(hit.entry.details.get("duration", ""))).timed
        )  # a spell with a length gets Time it (#998)
        posted = await self.post_message(
            table.screen_channel_id,
            text,
            rules_cards.card_view(table.guild_id, card_id, timed=timed),
        )
        if posted is None:
            table.rules.forget(card_id, before)

    async def set_rules_cards(self, guild_id: int, campaign_id: str, on: bool) -> Campaign:
        """Turn rules cards on or off for a campaign (#931): saved, and a running session
        (or one still finishing) follows from now on. Under the session lock, like the
        level, so a session starting meanwhile can't miss it."""
        campaign = await self.campaigns.set_rules_cards(guild_id, campaign_id, on)
        async with self.session_lock(guild_id):
            for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
                if table is not None and table.campaign_id == campaign_id:
                    table.rules_on = campaign.rules_cards
                    if table.rules_on:
                        self._warm_rules(table.rules_rulesets)
        return campaign

    def _topic_pending(self, table: Table, utterance: Utterance, text: str, named: int) -> bool:
        """The off-topic filter (#52): a line not plainly about the game waits for the
        AI, a window at a time; True if it's waiting. No AI key, or a session still
        finishing: no filter, nothing is hidden."""
        if self.topic_ai is None or self.tables.get(table.guild_id) is not table:
            return False
        if time.monotonic() < table.topic_paused_until or obviously_game(text, named):
            return False
        key = (utterance.user_id, utterance.start_ms)
        table.held[key] = text
        first = not table.topics.lines
        waiting = Waiting(utterance.user_id, utterance.start_ms, text, utterance.duration_s)
        if table.topics.add(waiting, time.monotonic()):
            self._topic_task(table, self._label_topics(table, table.topics.take()))
        elif first:
            later = self._label_topics_later(table, table.topics.opened_at)
            self._topic_task(table, later, timer=True)
        return True

    def _topic_task(self, table: Table, work: Awaitable[None], *, timer: bool = False) -> None:
        """Labelling runs in the background; the end of the session waits for it. A
        timer (a window that may not fill) is only cancelled then: the last window is
        labelled anyway."""
        task = self._track(work, "topics")
        if task is not None:
            tasks = table.topic_timers if timer else table.topic_tasks
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def _finish_topics(self, table: Table) -> None:
        """At the end of a session: wait (bounded) for windows still being labelled,
        then label the last one, so the names scan and the saved transcript see them.
        On shutdown there's no time: the waiting lines are kept as game talk."""
        for timer in list(table.topic_timers):
            timer.cancel()
        pending = [t for t in table.topic_tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=TOPIC_CALL_TIMEOUT_S + 2)
        lines = table.topics.take()
        if lines and not self._closing:
            await self._label_topics(table, lines)
        for waiting in lines if self._closing else []:
            said = table.held.pop((waiting.speaker, waiting.started_ms), None)
            recorded = self.consent.has_consent(table.guild_id, waiting.speaker)
            if said is not None and recorded and len(table.heard) < HEARD_MAX:
                table.heard.append((waiting.speaker, said))

    async def _label_topics_later(self, table: Table, opened_at: float | None) -> None:
        """A window that doesn't fill is asked about once its first line has waited."""
        await asyncio.sleep(table.topics.window_s)
        if table.topics.lines and table.topics.opened_at == opened_at:
            await self._label_topics(table, table.topics.take())

    async def _label_topics(self, table: Table, lines: list[Waiting]) -> None:
        """Ask the AI about a window (one call, bounded) and act on its answer: the
        names scan gets the lines that aren't off-topic, lines that aren't game talk are
        saved so, and off-topic ones still in the channel's edit window become markers
        (each message edited once). If the call fails or is slow, every line counts as
        game talk (when unsure, keep it); after a few failures in a row the filter rests.
        Consent is checked again after every wait."""
        if not lines or self.topic_ai is None:
            return
        # Right before anything is sent: the lines may have waited a while, and a stop
        # can reach the consent cache from elsewhere (CLAUDE.md: after every wait).
        stopped = [w for w in lines if not self.consent.has_consent(table.guild_id, w.speaker)]
        for waiting in stopped:
            table.held.pop((waiting.speaker, waiting.started_ms), None)
        lines = [w for w in lines if w not in stopped]
        if not lines:
            return
        try:
            topics, reply = await asyncio.wait_for(
                classify(self.topic_ai, [w.text for w in lines]), TOPIC_CALL_TIMEOUT_S
            )
            table.topic_calls += 1
            table.topic_tokens[0] += reply.input_tokens
            table.topic_tokens[1] += reply.output_tokens
            table.topic_failures = 0
        except Exception as exc:
            table.topic_failures += 1
            if table.topic_failures >= TOPIC_FAILURES_BEFORE_REST:
                table.topic_paused_until = time.monotonic() + TOPIC_REST_S
                table.topic_failures = 0
                log.warning("Off-topic filter: resting after failures (%s)", type(exc).__name__)
            else:
                log.info("Off-topic filter: couldn't ask (%s); kept the lines", type(exc).__name__)
            topics = [GAME] * len(lines)
        hide: list[Waiting] = []
        for waiting, topic in zip(lines, topics, strict=True):
            said = table.held.pop((waiting.speaker, waiting.started_ms), None)
            if not self.consent.has_consent(table.guild_id, waiting.speaker):
                continue  # stopped meanwhile: nothing about their words
            if topic != OFF_TOPIC and said is not None and len(table.heard) < HEARD_MAX:
                table.heard.append((waiting.speaker, said))
            if topic != GAME:
                await self._save_topic(table, waiting, topic)
            if topic == OFF_TOPIC:
                hide.append(waiting)
        if hide:
            await self._hide_lines(table, hide, lines)

    async def _save_topic(self, table: Table, waiting: Waiting, topic: str) -> None:
        """Save a line's topic, waiting or saved. Only while its speaker is recorded."""
        guild_id, speaker, started = table.guild_id, waiting.speaker, waiting.started_ms
        async with table.save_lock:
            if self.consent.has_consent(guild_id, speaker):
                found = table.unsaved.set_topic(speaker, started, topic)
                session_id = table.transcript_session_id
                if not found and self.transcripts is not None and session_id is not None:
                    try:
                        await self.transcripts.set_topic(
                            guild_id, session_id, speaker, started, topic
                        )
                    except Exception:
                        log.exception("Couldn't save a line's topic")

    async def _hide_lines(
        self, table: Table, lines: list[Waiting], checked: list[Waiting] | None = None
    ) -> None:
        """Off-topic lines in the live channel become their markers: every line first,
        then each message edited once (one window could otherwise edit one message six
        times, while new lines wait for this lock). The DM screen lists them, with Put
        it back (#677), while the session runs. `checked`: all the lines of that check,
        so another line said in between ends a run."""
        async with table.transcript_lock:
            edits: dict[int, tuple[transcript_lines.Editable, str]] = {}
            hidden = []
            for waiting in lines:
                if not self.consent.has_consent(table.guild_id, waiting.speaker):
                    continue
                hidden.append(waiting)
                key = (waiting.speaker, waiting.started_ms)
                if table.transcript.can_change(*key, time.monotonic()):
                    table.hidden.add(key)  # a marker in the channel (too late: its words)
                edit = table.transcript.relabel(
                    waiting.speaker,
                    waiting.started_ms,
                    topic_marker(waiting.seconds),
                    time.monotonic(),
                )
                if edit is not None:
                    edits[id(edit[0])] = edit  # the latest content has every change
            # Before any await: a Stop now finds them listed, and takes them down.
            running = self.tables.get(table.guild_id) is table and not table.fix_ended
            # Listed only with a transcript store: else there's no cleaned transcript to
            # put lines back into.
            stored = self.transcripts is not None
            if hidden and running and stored and table.left_out.add(hidden, checked or lines):
                self._redraw_left_out(table)
            for message, content in edits.values():
                with contextlib.suppress(discord.HTTPException, TimeoutError):
                    await asyncio.wait_for(
                        message.edit(content=content, allowed_mentions=NO_PINGS),
                        EDIT_TIMEOUT_S,
                    )

    def _clean(self, table: Table, heard: str, *, unsure: bool) -> Cleaned:
        """The line with misheard names fixed (#127), from the campaign's names as last
        loaded; as heard if cleaning fails. `unsure`: also fixes from names DMbot only
        suggested and close look-alikes (shown with Undo). No await: the consent check just
        made still holds."""
        assert table.name_lookup is not None
        try:
            result = clean(
                table.name_lookup,
                heard,
                vocabulary=table.vocabulary,
                people=table.people,
                scene=table.scene.scene(time.monotonic()).keys(),
                unsure=unsure,
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
        if not screen_levels.allows(table.screen_level, screen_levels.QUESTION):
            return  # quiet: the words stay as heard (after expiring any still open)
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
        changed = await self._rewrite_line(
            table, note.speaker, note.started_ms, table.fix_notes.line_text(note)
        )
        if not self.consent.has_consent(guild_id, note.speaker):
            return fix_notes.STOPPED, allow  # stopped meanwhile: nothing about their line
        md = discord.utils.escape_markdown
        text = fix_notes.done_text(md(note.fix.heard), md(note.fix.written), line_kept=not changed)
        return text, allow

    async def _rewrite_line(self, table: Table, speaker: int, started_ms: int, text: str) -> bool:
        """Change a line's words (#296, #503): saved, waiting to be saved, and in the
        transcript channel if it was posted in the last ~30 s. Only while the speaker is
        still recorded, checked again after every wait. True if the line changed: saved
        or waiting, or the channel message. All or nothing: if the saved line can't be
        changed (a database error, logged), the channel isn't either, so "That line stays
        as heard" is true everywhere."""
        guild_id, found, edited = table.guild_id, False, False
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
                        log.exception("Couldn't change a saved line's words")
                        return False
                    if not found:
                        log.warning("A name change found no saved line to change")
        # The channel: hold its lock, so a message being posted can't miss this either.
        async with table.transcript_lock:
            # A line shown as an off-topic marker stays one (#52): an Undo never brings
            # the chat back into the channel.
            hidden = (speaker, started_ms) in table.hidden
            if self.consent.has_consent(guild_id, speaker) and not hidden:
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
        return found or edited

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

    def _redraw_left_out(self, table: Table) -> None:
        """Bring the "🙈 Left out as off-topic" message up to date, soon (#677): one
        redraw at a time and at most one waiting, as for the name fixes."""
        if table.left_out_queued:
            return
        table.left_out_queued = True
        self._track(self._show_left_out(table), "left-out")

    async def _show_left_out(self, table: Table) -> None:
        """Post or update the left-out message (#677): only in the DM screen, at every
        level (it's the DM's only chance to undo; edited in place, it never pings); only
        lines of people still recorded (checked as it's drawn); no buttons once the
        session ended."""
        async with table.left_out_lock:
            table.left_out_queued = False
            runs = [
                r
                for r in table.left_out.shown()
                if self.consent.has_consent(table.guild_id, r.speaker)
            ]
            if not runs and table.left_out_message is None:
                return
            names = {}
            for run in runs:
                name = self.name_of(table.guild_id, run.speaker)
                names[run.speaker] = (
                    "Someone" if name.startswith("<@") else discord.utils.escape_markdown(name)
                )
            start_ms = table.started_at * 1000

            def when(started_ms: int) -> str:
                return clock((started_ms - start_ms) / 1000)

            text, shown = left_out.message_text(
                runs, names, when, transcript_lines.escape, ended=table.fix_ended
            )
            view = None if table.fix_ended else left_out_view(table.guild_id, shown)
            if table.left_out_message is not None:
                try:
                    await table.left_out_message.edit(
                        content=text, view=view, allowed_mentions=NO_PINGS
                    )
                    return
                except discord.NotFound:
                    table.left_out_message = None  # deleted, or a new DM screen: post again
                except discord.HTTPException:
                    return
            if not table.fix_ended:
                table.left_out_message = await self.post_message(
                    table.screen_channel_id, text, view
                )

    async def put_back(self, guild_id: int, run_id: str, user_id: int) -> str:
        """The DM pressed Put it back (#677): that run's lines are game talk again, saved
        or waiting to be saved, in the live channel if still recent, and in the names
        scan (once). Only for the running session; what to tell them. Consent is
        checked after every await."""
        table = self.tables.get(guild_id)
        run = table.left_out.find(run_id) if table is not None and not table.fix_ended else None
        if table is None or run is None:
            return left_out.EXPIRED
        if not table.is_dm(user_id):
            return left_out.ONLY_DM
        if not self.consent.has_consent(guild_id, run.speaker):
            self._redraw_left_out(table)  # their lines shouldn't be listed any more
            return left_out.STOPPED
        if run.put_back:
            return left_out.ALREADY
        run.put_back = True  # before any await: put back once
        if not await self._restore_topics(table, run.lines):
            run.put_back = False  # not saved: can be pressed again
            return left_out.NOT_SAVED
        self._redraw_left_out(table)
        if not self.consent.has_consent(guild_id, run.speaker):
            return left_out.STOPPED  # stopped meanwhile: nothing about their lines
        # What the names scan would have had (#52), now: before the channel's awaits, so
        # a /dmbot stop meanwhile still scans them (it reads these at the end).
        for line in run.lines:
            if len(table.heard) < HEARD_MAX:
                table.heard.append((line.speaker, self._words_now(table, line)))
        name = self.name_of(guild_id, run.speaker)
        who = "Someone" if name.startswith("<@") else discord.utils.escape_markdown(name)
        if self.tables.get(guild_id) is not table or table.fix_ended:
            # Stopped meanwhile: the saved lines are right; the channel is winding down.
            return left_out.done_text(run.number, who, in_channel=None)
        in_channel = await self._show_lines_again(table, run.lines)
        if not self.consent.has_consent(guild_id, run.speaker):
            return left_out.STOPPED
        return left_out.done_text(run.number, who, in_channel=in_channel)

    async def _restore_topics(self, table: Table, lines: list[Waiting]) -> bool:
        """One person's lines left out become game talk again, waiting or saved (one
        statement, bounded: it holds the save lock). False if saving failed or was too
        slow (logged); if some were saved first, pressing again is harmless. A speaker
        who stopped meanwhile: nothing is changed, and True (`put_back` says why)."""
        guild_id, speaker = table.guild_id, lines[0].speaker
        async with table.save_lock:
            if not self.consent.has_consent(guild_id, speaker):
                return True
            saved = [
                line.started_ms
                for line in lines
                if not table.unsaved.set_topic(speaker, line.started_ms, GAME)
            ]
            session_id = table.transcript_session_id  # under the lock: a save may set it
            if not saved:
                return True
            if self.transcripts is None or session_id is None:
                # Out of the waiting buffer, but no stored transcript to change: never
                # say they're back.
                log.warning("Put it back found no stored transcript for %d lines", len(saved))
                return False
            try:
                changed = await asyncio.wait_for(
                    self.transcripts.set_topics(guild_id, session_id, speaker, saved, GAME),
                    PUT_BACK_TIMEOUT_S,
                )
            except Exception:
                log.exception("Couldn't put lines left out as off-topic back")
                return False
            if changed < len(saved):
                log.warning("Put it back found %d of %d saved lines", changed, len(saved))
        return True

    async def _show_lines_again(self, table: Table, lines: list[Waiting]) -> bool | None:
        """The live channel shows one person's lines again instead of their markers,
        each message edited once. True if every marker could be changed (only possible
        about 30 s after a line is posted); None if none of them was a marker there (too
        late when hidden, or no channel). Consent is checked before every edit: a Stop
        meanwhile leaves the rest as markers."""
        speaker = lines[0].speaker
        markers = [x for x in lines if (x.speaker, x.started_ms) in table.hidden]
        if not markers:
            return None
        changed = True
        async with table.transcript_lock:
            if not self.consent.has_consent(table.guild_id, speaker):
                return False
            now = time.monotonic()
            edits: dict[int, tuple[transcript_lines.Editable, str, list[Waiting]]] = {}
            for line in markers:
                table.hidden.discard((line.speaker, line.started_ms))
                if not table.transcript.can_change(line.speaker, line.started_ms, now):
                    changed = False
                    continue
                words = self._words_now(table, line)
                edit = table.transcript.relabel(line.speaker, line.started_ms, words, now)
                if edit is not None:  # None: still waiting, it goes out with its words
                    its = edits.get(id(edit[0]), (edit[0], "", []))[2]
                    edits[id(edit[0])] = (edit[0], edit[1], [*its, line])
            pending = list(edits.values())
            for i, (message, content, _) in enumerate(pending):
                if not self.consent.has_consent(table.guild_id, speaker):
                    for _, _, rest in pending[i:]:  # never shown again: markers they stay
                        for line in rest:
                            table.transcript.relabel(
                                line.speaker, line.started_ms, topic_marker(line.seconds), now
                            )
                    return False
                try:
                    await asyncio.wait_for(
                        message.edit(content=content, allowed_mentions=NO_PINGS),
                        EDIT_TIMEOUT_S,
                    )
                except (discord.HTTPException, TimeoutError):
                    changed = False
        return changed

    @staticmethod
    def _words_now(table: Table, line: Waiting) -> str:
        """A line's words now: as cleaned, with any name fix the DM undid or answered
        since (#296, #503)."""
        notes, key = table.fix_notes, (line.speaker, line.started_ms)
        note = next((n for n in notes.notes if n.line == key), None)
        if note is not None:
            return notes.line_text(note)
        answer = next((a for a in notes.answers if a.line == key), None)
        if answer is not None:
            return notes.words_now(line.speaker, line.started_ms, answer.heard, answer.fixes)
        return line.text

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

    async def answer_undone(self, guild_id: int, campaign_id: str, batch: int) -> bool | None:
        """The DM undid an answer to "Did they mean…?" (the saved change is already taken
        back): put its line back, if this session still has it (#503). True if it was put
        back; False if it couldn't be (#589: the save failed, or no copy of it was left to
        change); None if there's nothing to say: no line this session knows (too old, or
        after a restart), or its speaker stopped being recorded (checked again after the
        wait, on every path)."""
        for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
            if table is None or table.campaign_id != campaign_id:
                continue
            answer = table.fix_notes.take_back(batch)
            if answer is None:
                continue
            text = table.fix_notes.words_now(
                answer.speaker, answer.started_ms, answer.heard, answer.fixes
            )
            try:
                changed = await self._rewrite_line(table, answer.speaker, answer.started_ms, text)
            except Exception:
                log.exception("Couldn't put a line back after undoing an answer")
                changed = False
            if not self.consent.has_consent(guild_id, answer.speaker):
                return None  # nothing about their line
            return changed
        return None

    async def set_screen_level(
        self, guild_id: int, campaign_id: str, level: str, *, was: str
    ) -> Campaign:
        """How much DMbot says in this campaign's DM screen (#504, #515): saved, and a
        running session (or one still finishing) follows the change from now on. Under
        the session lock, so a session starting meanwhile can't miss it (#553). A change
        from `was` (the level the DM saw) is noted in the DM screen, so a co-DM knows why
        DMbot went quiet."""
        campaign = await self.campaigns.set_dm_screen_level(guild_id, campaign_id, level)
        async with self.session_lock(guild_id):
            for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
                if table is not None and table.campaign_id == campaign_id:
                    table.screen_level = campaign.dm_screen_level
        if was != campaign.dm_screen_level and campaign.dm_screen_channel_id is not None:
            # Always posted: a note about the level, not something the level governs.
            await self.post(
                campaign.dm_screen_channel_id,
                screen_messages.level_changed(campaign.dm_screen_level),
            )
        return campaign

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
            # Never held up for long (every clip of the session waits here): while the
            # names reload, the clip goes with people-only hints and the load carries on.
            found = await self.lookup.get_within(guild_id, table.campaign_id, HINTS_WAIT_S)
            if found is None:  # still loading, or failed (logged by the cache)
                table.name_lookup = None  # never fix names from an old copy
                table.hint_parts = None
                return [*people, *absent]
            lookup = found
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
            lookup,
            table.hint_parts,
            table.scene,
            time.monotonic(),
            people=people,
            absent=absent,
            sheet=table.sheet_hints,
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

    async def _meter_loop(self) -> None:
        """Once a minute, write each running session's listening minutes to the hours
        meter (#437), so a crash or restart loses at most the last minute. One table at a
        time: with many servers, a burst of connections would starve the pool."""
        while True:
            await asyncio.sleep(METER_INTERVAL_S)
            for table in list(self.tables.values()):
                warn = None
                try:
                    async with asyncio.timeout(METER_CALL_TIMEOUT_S):
                        _, warn = await self._meter_write(table)
                except TimeoutError:  # a slow database delays this table, not the others
                    log.error("The listening minutes took too long to write down")
                if warn is not None:
                    self._start_follow_up(table, warn)

    def _forget_follow_up(self, guild_id: int, task: asyncio.Task[None]) -> None:
        if self._follow_ups.get(guild_id) is task:  # not a newer one's entry
            del self._follow_ups[guild_id]

    def _start_follow_up(self, table: Table, warn: tuple[usage.Standing, int]) -> None:
        """Act on where the hours stand in a task of its own, one at a time per server: a
        slow Discord post, database call or held session lock must not hold up the other
        tables' minutes. If last minute's is still working, this one is skipped; the next
        tick looks again, and a spent grace or a stop is found from the numbers each time."""
        running = self._follow_ups.get(table.guild_id)
        if running is not None and not running.done():
            return
        task = self._track(self._meter_follow_up(table, warn), "hours follow-up")
        if task is not None:
            self._follow_ups[table.guild_id] = task
            # Finished tasks aren't kept: one entry per server that has ever been metered
            # would otherwise sit here for as long as DMbot runs.
            task.add_done_callback(partial(self._forget_follow_up, table.guild_id))

    async def meter_table(
        self, table: Table, now: int | None = None, *, final: bool = False
    ) -> bool:
        """Write the minutes (`_meter_write`), then act on where the hours stand
        (`_meter_follow_up`). The final charge at a stop and the tests use this; the tick
        loop calls the two apart, the second in a task of its own, so that stopping a
        session is never inside the time limit on writing minutes and a slow stop never holds
        up the other tables."""
        ok, warn = await self._meter_write(table, now, final=final)
        if warn is not None:
            await self._meter_follow_up(table, warn)
        return ok

    async def _meter_write(
        self, table: Table, now: int | None = None, *, final: bool = False
    ) -> tuple[bool, tuple[usage.Standing, int] | None]:
        """Record the minutes this session has run and not yet written down, for the
        campaign's owner right now (a hand-over mid-session moves the later minutes to
        the new owner). `final` is the stop: it closes the meter for this session, so a
        tick that was already waiting can never bill time after the end. Never raises
        (the meter must not stop a game); returns whether it worked, and where the owner's
        hours stand if a warning or the cap may need acting on."""
        if self.meter is None or table.campaign_id is None or table.started_at <= 0:
            return True, None
        with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
            warn: tuple[usage.Standing, int] | None = None
            try:
                async with table.meter_lock:
                    # A tick that was already waiting when the session stopped must not
                    # bill time after its end: the stop settles the rest.
                    if table.metering_closed or (
                        not final and self.tables.get(table.guild_id) is not table
                    ):
                        return True, None
                    stamp = int(time.time()) if now is None else now
                    if table.ended_at is not None:  # a tick that was mid-flight at the stop
                        stamp = min(stamp, table.ended_at)
                    if table.metered_minutes is None:  # first time (or after a restart)
                        table.metered_minutes = await self.meter.recorded(
                            table.guild_id, table.campaign_id, table.started_at
                        )
                        # What was stored is the base; only time this process has been
                        # listening adds to it, so a gap while DMbot was down isn't billed.
                        table.meter_base = table.metered_minutes
                        table.meter_since = max(table.started_at, table.listening_from)
                    target = table.meter_base + hours.minutes_used(table.meter_since, stamp)
                    owed = max(0, target - table.metered_minutes)
                    if owed > 0:
                        campaign = await self.campaigns.get(table.guild_id, table.campaign_id)
                        if campaign is not None and campaign.owner_user_id is None:
                            # Nobody's hours to spend yet: keep the minutes in the session
                            # record only, and move on, so whoever takes the campaign on is
                            # billed from then and never for these.
                            await self.meter.add_unowned(
                                guild_id=table.guild_id,
                                campaign_id=table.campaign_id,
                                session_started_at=table.started_at,
                                minutes=owed,
                                now=stamp,
                            )
                            table.metered_minutes += owed
                        elif campaign is not None and campaign.owner_user_id is not None:
                            standing = await self.meter.add(
                                guild_id=table.guild_id,
                                campaign_id=table.campaign_id,
                                owner_user_id=campaign.owner_user_id,
                                session_started_at=table.started_at,
                                minutes=owed,
                                now=stamp,
                            )
                            # If the commit succeeded but its reply was lost, the next tick
                            # adds these minutes again: rare, and over rather than under.
                            table.metered_minutes += owed
                            if self.settings.enforce_plans and not final:
                                warn = (standing, campaign.owner_user_id)
                    if final:
                        table.metering_closed = True
                return True, warn
            except Exception:
                log.exception("Couldn't write down the listening minutes")
                return False, None

    async def _meter_follow_up(self, table: Table, warn: tuple[usage.Standing, int]) -> None:
        """After the minutes are safely written: warn at 80% and 90%, and at the cap let
        the session finish or stop it. Apart from the write so a slow Discord post can
        neither undo minutes nor cut a stop short; a warning that fails to post is not
        retried (the mark was crossed once), and one step failing never skips the other."""
        with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
            if self.tables.get(table.guild_id) is not table or table.metering_closed:
                return  # stopped since the minutes were written: say and do nothing
            standing = warn[0]
            # A tick that passes 90% and the cap together says only the cap's notice: "less
            # than half an hour left" followed at once by "used up" would contradict itself.
            if (
                hours.cap_action(
                    standing.access, standing.used_after, standing.grace_session, table.started_at
                )
                == "none"
            ):
                try:
                    await self._warn_hours(table, standing)
                except Exception:
                    log.exception("Couldn't warn about the hours")
            try:
                await self._enforce_cap(table, *warn)
            except Exception:
                log.exception("Couldn't act on the hours cap")

    async def _warn_hours(self, table: Table, standing: usage.Standing) -> None:
        """Tell the DM screen when the owner's hours pass 80% or 90% (#437). Each mark is
        said once, because it is found by comparing the minutes before and after."""
        mark = hours.warning_crossed(standing.access, standing.used_before, standing.used_after)
        cap = hours.standing(standing.access, standing.used_after).left_minutes
        if mark is not None and cap is not None:
            await self.post(
                table.screen_channel_id,
                hours.warning_text(
                    cap, mark, self.settings.site_url, standing.buys_hours, standing.renews
                ),
            )

    async def _enforce_cap(self, table: Table, standing: usage.Standing, owner: int) -> None:
        """At the cap, let this session finish (up to 2 hours, once a month), then stop it
        (#437). Never touches a session that is under the cap. Needs the meter and
        DMBOT_ENFORCE_PLANS; the caller has already left the meter lock, because stopping
        starts the session's final charge. Enforcement runs on a tick that bills a new
        minute, so after a restart it is a minute late, which doesn't matter."""
        assert self.meter is not None
        action = hours.cap_action(
            standing.access, standing.used_after, standing.grace_session, table.started_at
        )
        if action == "start_grace":
            # Under the session lock with the table checked again, so a stop that came in
            # since the minutes were written can't spend the owner's grace on a dead session.
            async with self.session_lock(table.guild_id):
                if self.tables.get(table.guild_id) is not table:
                    return
                # Bounded like every other call made under this lock: a slow database
                # must not hold up /dmbot stop and Start for the server. A timeout is
                # logged by the caller and the session keeps going; the next tick asks again.
                async with asyncio.timeout(METER_CALL_TIMEOUT_S):
                    got = await self.meter.start_grace(
                        table.guild_id, owner, standing.month, table.started_at
                    )
            action = "in_grace" if got else "stop"
            if got:
                ends_at = hours.grace_ends_at(
                    standing.access, standing.used_after, int(time.time())
                )
                await self.post(
                    table.screen_channel_id,
                    hours.grace_started_text(
                        ends_at, self.settings.site_url, standing.buys_hours, standing.renews
                    ),
                )
        elif action == "in_grace" and hours.stop_warning_due(
            standing.access,
            standing.grace_session,
            table.started_at,
            standing.used_before,
            standing.used_after,
        ):
            await self.post(
                table.screen_channel_id,
                hours.stop_soon_text(self.settings.site_url, standing.buys_hours, standing.renews),
            )
        if action == "stop":
            await self._stop_for_hours(table, standing)

    async def _stop_for_hours(self, table: Table, standing: usage.Standing) -> None:
        """End the session because the hours are used up: forget the saved session, stop
        listening, and say why on the DM screen. Same order as `/dmbot stop`: the saved
        session goes first, so if that fails the session keeps going (the table is still
        there, and the next tick tries again) rather than stopping now and coming back by
        surprise after a restart. Once it is stopped the notice is always posted."""
        async with self.session_lock(table.guild_id):
            if self.tables.get(table.guild_id) is not table:
                return  # already stopped by someone
            try:
                # Bounded: this runs under the session lock, which /dmbot stop and Start wait
                # on. A timeout is an error here too, so the next tick tries again.
                async with asyncio.timeout(METER_CALL_TIMEOUT_S):
                    await self.sessions.clear(table.guild_id, "the owner's hours are used up")
            except Exception:
                log.exception("Couldn't clear the saved session; will try to stop again")
                return
            # As in /dmbot stop, if stopping itself fails after this, the saved session is
            # already gone: a restart before the next tick then ends the session, which is
            # what the hours ask for anyway.
            await self.stop_table(table.guild_id, "the owner's hours are used up")
        await self.post(
            table.screen_channel_id,
            hours.stopped_text(self.settings.site_url, standing.buys_hours, standing.renews),
        )

    async def _meter_final(self, table: Table, ended_at: int) -> None:
        """The last, rounded-up minutes at a stop: tried a few times, since nothing else
        will write them once the session is gone."""
        for attempt in range(METER_FINAL_TRIES):
            try:
                async with asyncio.timeout(METER_CALL_TIMEOUT_S):
                    if await self.meter_table(table, ended_at, final=True):
                        return
            except TimeoutError:
                log.error("The listening minutes took too long to write down")
            if attempt + 1 < METER_FINAL_TRIES:
                await asyncio.sleep(METER_FINAL_RETRY_S)
        table.metering_closed = True  # give up: a late tick must not bill past the end

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
            # Together: a table's audio check can wait on the AI (#699), bounded.
            await asyncio.gather(*(self._summary_one(t) for t in list(self.tables.values())))

    async def _summary_one(self, table: Table) -> None:
        """One table's capture check; its failure never stops the others' (or later ones)."""
        try:
            await self.post_summary(table)
        except Exception:
            with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
                log.exception("Capture check failed")

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
            notice = await self._no_download_notice(table)
            if notice is not None:
                recipients, note = notice
                sent = 0
                for user_id in sorted(recipients):
                    sent += await self._send_download(
                        user_id, table, session_id, blocked=True, note=note
                    )
                log.info("Transcript not offered (plan): told %d person(s)", sent)
                return sent
            sent = 0
            for user_id in sorted(people):
                sent += await self._send_download(user_id, table, session_id)
            log.info("Transcript download offered privately to %d of %d", sent, len(people))
            return sent

    async def _no_download_notice(self, table: Table) -> tuple[set[int], str] | None:
        """With the campaign's plan not including copies (Try It, #938) the download buttons
        would be refused on every press. Players have nothing to act on, so only the owner is
        told, once, why there is no transcript (or, with no owner, the DMs are told to take the
        campaign on). Returns who to tell and what, or None when downloads are fine. Fails
        open like `plan_gate`: any error leaves the buttons in place, since this runs after
        the session was ended and must never stop the end-of-session message. The plan is
        asked at most twice: whether it is blocked, and what the owner is told."""
        if table.campaign_id is None:
            return None
        gid = table.guild_id
        try:
            campaign = await self.campaigns.get(gid, table.campaign_id)
            if campaign is None or await self.plan_allows("transcript", gid, campaign):
                return None
            owner_id = campaign.owner_user_id
            if owner_id is None:
                return {table.dm_user_id, *table.dm_user_ids}, plan_rules.NO_OWNER
            note = await self.plan_gate("transcript", gid, campaign, owner_id)
            return {owner_id}, note or plan_rules.NO_OWNER
        except Exception:
            log.exception("Couldn't check whether the plan has downloads; offering them")
            return None

    async def _send_download(
        self,
        user_id: int,
        table: Table,
        session_id: str,
        *,
        blocked: bool = False,
        note: str | None = None,
    ) -> int:
        """1 if the private message went out; people with private messages off use
        `/transcript` instead. `blocked`: the plan has no downloads, so no buttons; `note` is
        the line for the owner only."""
        try:
            user = self.get_user(user_id) or await self.fetch_user(user_id)
            if user.bot:
                return 0
            if blocked:
                await user.send(
                    ended_no_download_text(table.campaign_name, note or plan_rules.NO_OWNER),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return 1
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
        went missing and their lines read garbled (#699), or the loss was large."""
        async with table.check_lock:  # the periodic check and the end-of-session one
            with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
                line = table.capture_log.log_line()  # IDs and numbers only; before due()
                if line:
                    log.info(line)
                await self._audio_checks(table)

    async def _audio_checks(self, table: Table) -> None:
        gid = table.guild_id
        dues = [
            due
            for due in table.capture_log.due(time.monotonic())
            if self.consent.has_consent(gid, due.user_id)  # stopped: never read or named
        ]
        if not dues:
            return

        async def confirmed(due: Due) -> bool:
            # Consent again: a stop can land between scheduling and this task's turn, and
            # their lines must not reach the AI then (CLAUDE.md, after every async step).
            if not self.consent.has_consent(gid, due.user_id):
                return False
            if due.large:
                verdict = Verdict(True, "large loss")
            else:
                verdict = await table.audio_checker.check(due, self.audio_ai, time.monotonic())
            log.info(
                "Audio check for user %s: %s (%s; %d%% got through, %.1f s lost)",
                due.user_id,
                "warn" if verdict.garbled else "reads fine",
                verdict.how,
                due.percent,
                due.lost / FRAMES_PER_S,
            )
            return verdict.garbled

        # Together: one slow AI answer mustn't hold up the others (each is bounded).
        results = await asyncio.gather(*(confirmed(due) for due in dues))
        # Consent again after the wait, for everyone, before naming anyone (CLAUDE.md).
        warn = [
            due
            for due, ok in zip(dues, results, strict=True)
            if ok and self.consent.has_consent(gid, due.user_id)
        ]
        text = table.capture_log.warn(partial(self.name_of, gid), warn, time.monotonic())
        table.totals.flagged.update(due.user_id for due in warn)
        if text:
            await self.post(table.screen_channel_id, text)


# ---- slash commands ----------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _free_bytes(root: Path) -> int:
    test_files.ensure_root(root)
    return shutil.disk_usage(root).free


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
            confirmed_text(
                guild.name,
                granted,
                sheets=bot.sheets is not None,
                test_voice=bot.test_voice_listed(guild.id),
            ),
            view=menu_view(guild.id),
            ephemeral=True,
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
        test_voice=bot.test_voice_listed(guild.id),
    )
    await interaction.followup.send(
        text,
        view=request_view(
            guild.id, outside=bot.outside_engine, test_voice=bot.test_voice_listed(guild.id)
        ),
        ephemeral=True,
    )


@consent_group.command(
    name="revoke", description="Stop DMbot from recording your voice in this server"
)
async def consent_revoke(interaction: discord.Interaction) -> None:
    # One warning first, as from the ⚙️ Menu (#807): nothing stops until Yes, which stops
    # at once and everywhere (consent_dm.StopYesButton).
    bot = _bot(interaction)
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    if not await bot.recorded(guild.id, interaction.user.id):
        # Nothing to warn about. Clear anything left over all the same, quietly.
        bot.stop_recording(guild.id, interaction.user.id)
        await interaction.response.send_message(
            nothing_to_stop_text(guild.name), ephemeral=True, allowed_mentions=NO_PINGS
        )
        with contextlib.suppress(Exception):
            await bot.withdraw_consent(guild.id, interaction.user.id)
        return
    await interaction.response.send_message(
        warning_text(guild.name, saving=bot.test_voice_saving(guild.id, interaction.user.id)),
        view=warning_view(guild.id),
        ephemeral=True,
    )


async def run(settings: Settings) -> None:
    entitlements.configure_free_users(settings.free_users)  # #771
    db = await Database.open(settings.database_url)
    try:
        transcriber = build_transcriber(settings.transcription)
        await transcriber.warm_up()  # load the Whisper model now, not on the first word
        # With plans enforced, a hand-over, take-over or restore also needs room under the
        # owner's campaign cap (#437 part 2c); otherwise a plan that works is enough.
        campaigns = (
            CampaignStore(
                db, has_free_slot=campaign_cap.has_room_for_one_more, restore_needs_slot=True
            )
            if settings.enforce_plans
            else CampaignStore(db)
        )
        campaigns.register_section(MemorySection())  # campaign memory goes in backups
        campaigns.register_section(HouseRulesSection())  # and so do house rules (#865)
        campaigns.register_section(ClockSection())  # and the game clock (#965)
        campaigns.register_section(EffectsSection())  # and its timed effects (#998)
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
            SheetStore(db),
            HouseRuleStore(db),
            usage.Meter(db),
            ClockStore(db),
            HouseFileLinkStore(db),
            EffectStore(db),
            TestVoiceStore(db),
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
