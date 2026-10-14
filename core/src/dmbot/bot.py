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
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, cast

import discord
from discord import app_commands
from discord.ext import commands

from dmbot import (
    campaign_cap,
    entitlements,
    fightbot,
    hours,
    install,
    plan_rules,
    retention,
    sheet_reread,
    table_chat,
    trial_jobs,
    usage,
)
from dmbot.ai import FEATURE_TIERS, AnthropicClient, Feature
from dmbot.ai_watch import AIWatch
from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.audio_check import AudioChecker, Verdict
from dmbot.campaigns import Campaign, CampaignStore
from dmbot.campaigns.models import DEFAULT_DM_SCREEN_LEVEL
from dmbot.capture_log import FRAMES_PER_S, CaptureLog, Due, SessionTotals
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
from dmbot.consent_site import SiteStops
from dmbot.db import Database
from dmbot.dm_screen import (
    HideButton,
    PeekButton,
    StopListeningButton,
    VisibilityButton,
    house_sync,
    rules_cards,
)
from dmbot.dm_screen import clock as clock_screen
from dmbot.dm_screen import effects as timer_screen
from dmbot.dm_screen import house_voice as house_voice_screen
from dmbot.dm_screen import messages as screen_messages
from dmbot.dm_screen.dmbchat_channel import DmbchatChannelError, setup_dmbchat_channel
from dmbot.dm_screen.gametime_channel import GametimeChannelError, setup_gametime_channel
from dmbot.dm_screen.handover import (
    AcceptOfferButton,
    DeclineOfferButton,
    HandoverButton,
    NotNowButton,
    TakeOnButton,
    WithdrawOfferButton,
)
from dmbot.dm_screen.left_out import PutBackButton
from dmbot.dm_screen.name_questions import (
    FixUndoButton,
    NameAnswerUndoButton,
    NameQuestionButton,
)
from dmbot.dm_screen.pause import PauseButton
from dmbot.dm_screen.settings import (
    HouseFileButton,
    InviteButton,
    LevelButton,
    RuleLookupButton,
    RulesCardsButton,
    SettingsButton,
    SettingsVisibilityButton,
    SideInitiativeButton,
)
from dmbot.dm_screen.site_offers import SiteOffers
from dmbot.dm_screen.speaker_question import SpeakerQuestionButton
from dmbot.dm_screen.table_chat_panel import TableChatButton
from dmbot.dm_screen.transcript_channel import (
    TranscriptChannelError,
    is_transcript_name,
    setup_transcript_channel,
)
from dmbot.ears.protocol import (
    allowlist_command,
    join_command,
    leave_command,
)
from dmbot.ears.server import EarsServer
from dmbot.fightbot import FightState
from dmbot.logs import log_context, set_log_context
from dmbot.mail import Mailer
from dmbot.memory import definitions, document_reader
from dmbot.memory.backup import MemorySection
from dmbot.memory.lookup import CampaignLookup, LookupCache
from dmbot.memory.models import name_key
from dmbot.memory.scene import HintParts, SceneTracker
from dmbot.memory.sheet_questions import Questions as SheetQuestions
from dmbot.memory.sheet_refresh import hint_names as sheet_hint_names
from dmbot.memory.sheet_refresh import refresh as refresh_sheets
from dmbot.memory.sheet_store import CharacterSheet, SheetStore
from dmbot.memory.sheet_watch import SheetWatch
from dmbot.memory.store import MemoryStore
from dmbot.memory.table_names import NameThenAnswer
from dmbot.retention import RetentionJob
from dmbot.rules import house_voice
from dmbot.rules import index as rules_index
from dmbot.rules.house import HouseRulesSection, HouseRuleStore
from dmbot.rules.house_file_link import HouseFileLinkStore
from dmbot.sessions import SessionStore
from dmbot.sidebar import knowledge
from dmbot.sidebar.answer import PROMPT_VERSION as SIDEBAR_PROMPT_VERSION
from dmbot.sidebar.ask import AskLimiter
from dmbot.sidebar.service import Recent, SidebarService
from dmbot.test_recording import files as test_files
from dmbot.test_recording import library as test_library
from dmbot.test_recording.session import TestSession
from dmbot.test_recording.store import TestVoiceStore
from dmbot.timebot.effects import EffectsSection, EffectStore
from dmbot.timebot.store import ClockSection, ClockStore
from dmbot.transcript import fix_notes, left_out, speaker_ai
from dmbot.transcript import questions as name_questions
from dmbot.transcript import speaker_question as speaker_q
from dmbot.transcript import stream as transcript_lines
from dmbot.transcript.cleaner import (
    Vocabulary,
)
from dmbot.transcript.models import TranscriptBuffer
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.stream import TranscriptStream
from dmbot.transcript.topics import TopicWindow
from dmbot.transcription.base import PlaceholderTranscriber, Transcriber
from dmbot.transcription.factory import build_transcriber
from dmbot.transcription.pipeline import TranscriptionPipeline, speech_sent_line
from dmbot.ui.dmbot_commands import _failed, dmbot_group
from dmbot.ui.house_rules import dmbot_house_rules  # noqa: F401 (registers it)
from dmbot.ui.name_card import UndoButton
from dmbot.ui.name_lists import UndoListButton
from dmbot.ui.names import ReviewButton
from dmbot.ui.optional_rules import dmbot_optional_rules  # noqa: F401 (registers it)
from dmbot.ui.people_review import PeopleReviewButton
from dmbot.ui.rule_lookup import dmbot_rule  # noqa: F401 (registers it)
from dmbot.ui.sheets import MySheetButton, OpenCardButton
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
SPEAKER_WAIT_S = 1.0  # the longest the names are waited for, before a label is saved
EDIT_TIMEOUT_S = 5.0  # a transcript message edit, at most (it holds the post lock)
PUT_BACK_TIMEOUT_S = 5.0  # saving a Put it back, at most (it holds the save lock; #677)
HINT_PEOPLE_S = 5.0  # who's in the voice channel, for name hints: looked at this often
HintPeople = tuple[tuple[str, ...], tuple[str, ...]]  # (at the table, agreed but not there)
TRANSCRIPT_POST_TIMEOUT_S = 10.0  # one stuck post can't hold the others up for long
TRANSCRIPT_PARALLEL = 10  # campaigns posting at once (one rate-limited channel can't stall all)
STOP_DRAIN_TIMEOUT_S = 120.0  # at stop, wait this long for the last words to be written
FINAL_FLUSH_TIMEOUT_S = 15.0  # at stop or shutdown, give up on posting after this
IDLE_SWEEP_INTERVAL_S = 1
FORGET_TIMEOUT_S = 10  # removing learned names must not hold up stopping a recording
METER_INTERVAL_S = 60  # how often listening minutes are written to the hours meter (#437)
METER_FINAL_TRIES = 3  # at a stop: the last minutes are written nowhere else
METER_FINAL_RETRY_S = 2
NAMES_WAIT_S = 1.0  # the sidebar waits this long for a campaign's names, then answers without
GATE_TIMEOUT_S = 2  # a button press must be answered within Discord's 3 s: fail open sooner
CHAT_BLOCKED_TTL_S = 60  # how long the list of channels never read as chat is kept (#1118)
CLOCK_PHRASE_GAP_S = 300  # the same rest said twice within this is counted once (#965)
COMBAT_PHRASE_GAP_S = 15  # the same combat words twice (voice and chat) within this: once (#1127)
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
    # Reading linked D&D Beyond sheets again during the session (#1128): the linked sheets as
    # last read, the schedule, and the loop that runs it (cancelled at the end).
    sheet_links: dict[str, CharacterSheet] = field(default_factory=dict)
    sheet_watch: SheetWatch = field(default_factory=SheetWatch)
    sheet_questions: SheetQuestions = field(default_factory=SheetQuestions)
    watch_task: asyncio.Task[None] | None = None
    # FightBot's memory for this session and its loop (#1129).
    fight: FightState = field(default_factory=FightState)
    fight_task: asyncio.Task[None] | None = None
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
    # When a player's "let's take a rest" last became a question for the DM (monotonic
    # seconds): at most one every few minutes (#1117).
    clock_suggested_at: float | None = None
    # The last clock change if it was a rest said at the table: (kind, minutes it moved the
    # clock, the clock minute after it, monotonic seconds). A later "you finish your long
    # rest" or "an hour later" that it already covers adds nothing (#1117). Dropped when the
    # clock is no longer where the rest left it (a button, an Undo, another phrase).
    clock_spoken_rest: tuple[str, int, int, float] | None = None
    # Typed chat at the table (#1118): whether it is read this session, and the DM's one
    # extra text channel (the voice channel's own chat is always read when it is on).
    chat_on: bool = True
    chat_channel_id: int | None = None
    chat_notice_posted: bool = False  # the one line in the extra channel, once a session
    # The campaign's DMbot chat channel (#1187): where the table asks DMbot and reads the answers.
    dmbchat_channel_id: int | None = None
    # People who typed there without having agreed, already shown the agree button this session.
    dmbchat_nudged: set[int] = field(default_factory=set)
    # Questions per person (at most ASK_PER_PERSON_PER_MINUTE a minute), so one player can't use
    # up the table's questions.
    person_limits: dict[int, AskLimiter] = field(default_factory=dict)
    typed_rate: table_chat.TypedRate = field(default_factory=table_chat.TypedRate)
    clock_waiting: int = 0  # clock-phrase checks running now (capped; #1154)
    # The campaign's D&D Beyond invite link, for the Join button on the messages players see
    # this session (#1119); None when the DM set none.
    dndbeyond_invite: str | None = None
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
    # Who answered to which name this session ("a name, then an answer", #1188); in memory
    # only, and nothing a speaker said stays once they stop being recorded.
    name_answers: NameThenAnswer = field(default_factory=NameThenAnswer)
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
    # Who the DM was speaking as (#53): the DM's lines waiting for the AI, a window at a
    # time, and the same bookkeeping as the off-topic filter.
    speakers: TopicWindow = field(default_factory=TopicWindow)
    speaker_questions: speaker_q.SpeakerBook = field(default_factory=speaker_q.SpeakerBook)
    speaker_message: discord.Message | None = None
    speaker_book: speaker_ai.NameBook = field(default_factory=speaker_ai.NameBook)
    speaker_calls: int = 0
    speaker_tokens: list[int] = field(default_factory=lambda: [0, 0])  # in, out
    speaker_tasks: set[asyncio.Task[None]] = field(default_factory=set)  # awaited at the end
    speaker_timers: set[asyncio.Task[None]] = field(default_factory=set)  # cancelled then
    speaker_failures: int = 0
    speaker_paused_until: float = 0.0  # monotonic seconds
    # The DM sidebar (#935): what was said lately (the scene for an answer), and the
    # safety limit on questions (6 a minute, 120 a session; #1040), spoken and typed.
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
        # Typed chat at the table (#1118): messages in servers, and their text (the privileged
        # Message Content intent). DMbot reads only the table voice channel's chat and the one
        # extra channel the DM picked, and drops every other message first.
        intents.guild_messages = True
        intents.message_content = True
        silence_voice_warnings()  # before super().__init__, which logs them
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            shard_count=settings.shards.count,
            shard_ids=list(settings.shards.ids),
            tree_cls=DMBotTree,
            # No message cache (#1118): with the guild-messages intent every message in every
            # server would be kept in memory, including those of people who never agreed and
            # channels that are not table chat. Nothing here reads cached messages.
            max_messages=None,
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
        # The last day of Try It and the extension (#1229): only with a database, and the mail
        # sender (which sends nothing until SMTP is set up) for the emails.
        self.trial_jobs: trial_jobs.TrialJobs | None = None
        if meter is not None:
            self.trial_jobs = trial_jobs.TrialJobs(
                meter.db,
                Mailer(
                    meter.db,
                    settings.mail,
                    site_url=settings.site_url,
                    link_secret=settings.link_token_secret,
                ),
                enforce_plans=settings.enforce_plans,
                site_url=settings.site_url,
                link_secret=settings.link_token_secret,
                send_dm=self._dm_user,
                name_of=self._discord_name,
                skip=settings.free_users,
            )
        # The daily job that warns about and deletes campaigns past their keep date (#964).
        # Needs the meter (the owners' plans); without one nothing is ever deleted.
        self.retention: RetentionJob | None = None
        if meter is not None:
            self.retention = RetentionJob(
                campaigns=campaigns,
                standing_of=meter.retention_standing,
                guild_ids=campaigns.guilds_with_campaigns,
                forget_guild=campaigns.forget_guild_if_empty,
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
        # A player's sheet document read into the sheet fields (#1125).
        self.sheet_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.SHEET]) if self.ai_client else None
        )
        # FightBot's reminders to players in combat (#1129), on the smallest model.
        self.fight_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.FIGHTBOT]) if self.ai_client else None
        )
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
        # Channels of any campaign that must never be read as table chat, by server, with when
        # the answer runs out (monotonic seconds) (#1118).
        self._chat_blocked: dict[int, tuple[float, frozenset[int]]] = {}
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
        # Loops that last as long as a session (#1128, #1129): kept apart from `_asking`, which
        # tests and `close()` wait on until it is empty.
        self._loops: set[asyncio.Task[None]] = set()
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
        # Stops made on the website (#1151): the same stop as the Stop button, for our servers.
        self.site_stops = SiteStops(
            is_ours=lambda guild_id: self.get_guild(guild_id) is not None,
            consent=self.consent,
            stop=self.withdraw_consent,  # stops capture, tells the DM, pushes ears, saves
            spawn=self._track,
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
        # DMbot's knowledge of itself (#1195): read its documents now, not on the first question.
        await asyncio.to_thread(knowledge.load)
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
            SideInitiativeButton,
            HouseFileButton,
            InviteButton,
            TableChatButton,
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
        self.add_dynamic_items(ReviewButton, PeopleReviewButton)
        self.add_dynamic_items(  # #965, #1117
            clock_screen.ClockButton, clock_screen.ClockUndoButton, clock_screen.SuggestionButton
        )
        self.add_dynamic_items(clock_screen.CombatUndoButton)  # #1127
        self.add_dynamic_items(fightbot.FightButton)  # #1129
        self.add_dynamic_items(timer_screen.EffectButton)  # timed effects (#998)
        # Undo after forgetting a name (its card), after a restart too.
        self.add_dynamic_items(UndoButton, UndoListButton)
        # "Download transcript" in the private message when a session ends.
        self.add_dynamic_items(DownloadButton)
        self.add_dynamic_items(
            NameQuestionButton, NameAnswerUndoButton, FixUndoButton, SpeakerQuestionButton
        )
        self.add_dynamic_items(PutBackButton)  # lines left out as off-topic (#677)
        self.add_dynamic_items(house_sync.HouseSyncButton)  # the house-rules file (#969)
        self.add_dynamic_items(rules_cards.RulesCardButton)  # rules cards from the table (#931)
        self.add_dynamic_items(house_voice_screen.HouseVoiceButton)  # house rules said aloud (#953)
        # A player's 📜 My character sheet, in their private messages (#723).
        self.add_dynamic_items(MySheetButton, OpenCardButton)  # #723, #1119
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
            *([self._watched(self._trial_loop(), "trial-jobs")] if self.trial_jobs else []),
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
            self._watched(self.site_stops.follow(self.campaigns.listen), "site-consent-stops"),
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
        for task in [*self._background, *self._asking, *self._loops, *lookups]:
            task.cancel()
        await asyncio.gather(
            *self._background, *self._asking, *self._loops, *lookups, return_exceptions=True
        )
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
            table.name_answers.drop_speaker(user_id)  # nor part of who answers to what
            table.topics.drop_speaker(user_id)  # and never sent to the off-topic filter
            table.held = {k: v for k, v in table.held.items() if k[0] != user_id}
            table.unsaved.drop_speaker(user_id)  # and never saved
            table.recent.drop_speaker(user_id)  # and never part of a sidebar answer's scene
            table.sheet_watch.drop_speaker(user_id)  # no sheet is read again for what they said
            table.sheet_questions.drop_speaker(user_id)  # nor asked about it (#1128)
            table.scene.forget_speaker(user_id)  # and no longer shape the hints
            table.vocabulary.forget_speaker(user_id)  # or the name fixes
            if table.fix_notes.drop_speaker(user_id):  # their fixes leave the DM screen
                self._redraw_fix_notes(table)
            if table.left_out.drop_speaker(user_id):  # and their lines left out (#677)
                self._redraw_left_out(table)
            if table.questions.drop_speaker(user_id, time.monotonic()) is not None:
                self._track(self._close_question(table), "name-question")
            if table.speaker_questions.drop_speaker(user_id, time.monotonic()) is not None:
                self._track(self._close_speaker_question(table, speaker_q.GONE), "speaker-q")
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
        if self.memory is not None:  # the names learned for them go too (#1188)
            try:
                async with asyncio.timeout(FORGET_TIMEOUT_S):
                    await self.memory.forget_learned(guild_id, user_id)
            except Exception:
                # Never blocks stopping; the learned names are only waiting questions.
                log.exception("Could not remove the names learned for a stopped speaker")
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

    def _track_loop(self, work: Awaitable[Any], name: str) -> asyncio.Task[None] | None:
        """Run a loop that lasts as long as a session; the session's end and close() cancel
        it. Not part of `_asking`: nothing waits for it to finish."""
        if self._closing:
            if inspect.iscoroutine(work):
                work.close()
            return None

        async def guarded() -> None:
            try:
                await work
            except Exception:
                log.exception("Background %s failed", name)

        task = asyncio.create_task(guarded(), name=name)
        self._loops.add(task)
        task.add_done_callback(self._loops.discard)
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
                    view = menu_view(gid, table.campaign_id, invite=table.dndbeyond_invite)
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
                        gid,
                        outside=self.outside_engine,
                        test_voice=self.test_voice_listed(gid),
                        invite=table.dndbeyond_invite,
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
        self._track(self._fight_left_over(table), "fight-left-over")  # #1173
        table.watch_task = self._track_loop(sheet_reread.run(self, table), "sheet-watch")  # #1128
        table.fight_task = self._track_loop(fightbot.run(self, table), "fightbot")  # #1129
        return sent

    async def _fight_left_over(self, table: Table) -> None:
        """A session starts with a fight still running from last time: one line on the DM
        screen with End combat (#1173). Never ended without the DM, because a session can stop
        in the middle of a fight. Not after a restart in the middle of a session."""
        if self.clocks is None or table.campaign_id is None or table.resumed:
            return
        stored = await self.clocks.get(table.guild_id, table.campaign_id)
        if stored is None or stored.clock.combat_round is None:
            return
        if self.tables.get(table.guild_id) is not table:
            return  # the session ended while the clock was read: no line on a closed table
        view = discord.ui.View(timeout=None)
        view.add_item(clock_screen.ClockButton(table.campaign_id, "cend"))
        await self.post(
            table.screen_channel_id,
            f"⚔️ A fight from last time is still running (round {stored.clock.combat_round}). "
            "Press **End combat** if it is over.",
            view,
        )

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
                    sheet_reread.track(table, kept)
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
                        sheet_reread.track(table, found)
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
        if table.watch_task is not None:  # nor re-reading them (#1128)
            table.watch_task.cancel()
        if table.fight_task is not None:  # nor reminding players (#1129)
            table.fight_task.cancel()
        await self.ears.send(leave_command(guild_id))
        # Speech still being heard or written down is finished, not dropped (#109): the
        # session stays "ending" until the pipeline has caught up.
        self._ending.setdefault(guild_id, []).append(table)
        # An unanswered "Did they mean…?" ends too (an answer being saved still finishes).
        if not table.questions.answering:
            ended = table.questions.close(name_questions.ENDED, time.monotonic())
            if ended is not None:
                self._track(self._close_question(table, self._not_answered(table, ended)), "q")
        speaker_book = table.speaker_questions  # likewise "Who was speaking just now?"
        if not speaker_book.answering and speaker_book.close(speaker_q.ENDED, time.monotonic()):
            self._track(
                self._close_speaker_question(table, speaker_q.not_answered_text()), "speaker-q"
            )
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
                await self._finish_speakers(table)
                if table.speaker_calls:  # for cost per session hour (#53)
                    log.info(
                        "Speaker labels: %d calls, %d tokens in, %d out",
                        table.speaker_calls,
                        *table.speaker_tokens,
                    )
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
            ("who is who", lambda: self.learn_people(table)),
            ("people check", lambda: self.check_people(table)),
            ("definitions", lambda: self.recheck_definitions(table)),
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

    async def _dm_user(self, user_id: int, text: str) -> trial_jobs.DmResult:
        """A private message to a person who is not at a table now. "closed" when Discord
        refuses for good (messages closed, no shared server); "retry" for anything that may
        pass. Never raises."""
        try:
            user = await self.fetch_user(user_id)
            await user.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return "closed"
        except discord.NotFound:
            return "closed"
        except Exception:
            return "retry"
        return "sent"

    async def _discord_name(self, user_id: int) -> str:
        try:
            user = await self.fetch_user(user_id)
        except discord.HTTPException:
            return "there"
        return user.display_name

    async def _trial_loop(self) -> None:
        """Every hour: the last-day messages and the automatic extensions (#1229). Waits a
        few minutes first so a restart loop can't send anything twice in a hurry (each message
        is also recorded, so none goes out twice anyway)."""
        await asyncio.sleep(300)
        while True:
            try:
                if self.trial_jobs is not None:
                    await self.trial_jobs.run()
            except Exception:
                log.exception("The Try It jobs failed")
            await asyncio.sleep(3600)

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

    async def _gametime_channel(
        self, guild: discord.Guild, campaign: Campaign, screen: object
    ) -> str | None:
        """The players' game-time channel (#1116), made or repaired at every start, with the
        players' pinned clock in it if the game clock is set. Returns a note for the DM if it
        couldn't be set up; a failure here never stops a session."""
        category = getattr(screen, "category", None)
        try:
            channel = await setup_gametime_channel(
                guild,
                campaign.id,
                self.campaigns,
                category=category if isinstance(category, discord.CategoryChannel) else None,
            )
        except GametimeChannelError as exc:
            return screen_messages.gametime_failed(str(exc))
        except Exception:
            log.exception("Couldn't set up the game-time channel")
            return screen_messages.gametime_failed(screen_messages.TRANSCRIPT_NO_ANSWER)
        try:
            stored = await self.clocks.get(guild.id, campaign.id) if self.clocks else None
            fresh = await self.campaigns.get(guild.id, campaign.id)
            if stored is not None and fresh is not None and fresh.gametime_channel_id == channel.id:
                await clock_screen.show_players(self, fresh, stored)
                # Redraws the DM's pinned clock too, so an older one loses its rest buttons.
                await clock_screen.show(self, fresh, stored)
        except Exception:
            log.exception("Couldn't put the players' clock in the game-time channel")
        return None

    async def _dmbchat_channel(
        self, guild: discord.Guild, campaign: Campaign, screen: object
    ) -> tuple[int | None, str | None]:
        """The table's DMbot chat channel (#1187), made or repaired at every start: its id, or a
        note for the DM if it couldn't be set up. A failure here never stops a session."""
        category = getattr(screen, "category", None)
        try:
            channel = await setup_dmbchat_channel(
                guild,
                campaign.id,
                self.campaigns,
                category=category if isinstance(category, discord.CategoryChannel) else None,
            )
        except DmbchatChannelError as exc:
            return None, screen_messages.dmbchat_failed(str(exc))
        except Exception:
            log.exception("Couldn't set up the DMbot chat channel")
            return None, screen_messages.dmbchat_failed(screen_messages.TRANSCRIPT_NO_ANSWER)
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
    # nothing is saved until they press the consent button.
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
            view=menu_view(
                guild.id,
                invite=bot.tables[guild.id].dndbeyond_invite if guild.id in bot.tables else None,
            ),
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
            guild.id,
            outside=bot.outside_engine,
            test_voice=bot.test_voice_listed(guild.id),
            invite=table.dndbeyond_invite if table else None,
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
        await definitions.seed(db)  # the official relationships, by version (#1189)
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
