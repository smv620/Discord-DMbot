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
import re
import shutil
import signal
import time
from collections.abc import (
    Awaitable,
    Callable,
    Collection,
    Coroutine,
    Hashable,
    Iterable,
    Sequence,
)
from dataclasses import replace
from difflib import SequenceMatcher
from functools import partial
from pathlib import Path
from typing import Any, cast

import discord
from discord import app_commands
from discord.ext import commands

from dmbot import (
    accounts,
    alerts,
    campaign_cap,
    entitlements,
    features,
    fightbot,
    hours,
    images,
    install,
    lore_upload,
    mail,
    owner_presence,
    plan_rules,
    retention,
    sheet_reread,
    table_chat,
    trial_jobs,
    usage,
)
from dmbot.account_move import AccountMoves
from dmbot.ai import (
    FEATURE_TIERS,
    OUTPUT_FLOOR,
    AIModelTier,
    AnthropicClient,
    Feature,
    GeminiClient,
)
from dmbot.ai_watch import AIWatch
from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.audio_check import Verdict
from dmbot.campaigns import Campaign, CampaignStore, web_backup, web_line_delete, web_restore
from dmbot.campaigns.models import (
    SHARED_STORY,
    STORY_GUIDE,
    CampaignError,
)
from dmbot.campaigns.moves import HandoverMover
from dmbot.campaigns.web_delete import Notice, WebDeleteNotices
from dmbot.capture_log import FRAMES_PER_S, Due
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
    resume_view,
    send_prompt,
    unreachable_text,
    warning_text,
    warning_view,
)
from dmbot.consent_site import SiteStops
from dmbot.db import Database, DatabaseError
from dmbot.discord_limits import tree_problems
from dmbot.dm_questions import CLAIMS, FAMILY, NAMES, ORDER, Ask, combine, view_of
from dmbot.dm_screen import (
    DMScreenError,
    HideButton,
    PeekButton,
    StopListeningButton,
    VisibilityButton,
    delete_campaign,
    dm_bot_buttons,
    ensure_dm_screen,
    game_masters,
    house_file_web,
    house_sync,
    import_button,
    links_ready,
    pack_suggestion,
    pack_update,
    peek_view,
    rules_cards,
    secret_posts,
    set_suggestion,
    set_update,
    update_notes,
    web_settings,
)
from dmbot.dm_screen import clock as clock_screen
from dmbot.dm_screen import effects as timer_screen
from dmbot.dm_screen import house_voice as house_voice_screen
from dmbot.dm_screen import import_time as import_time_screen
from dmbot.dm_screen import levels as screen_levels
from dmbot.dm_screen import messages as screen_messages
from dmbot.dm_screen import time_suggest as time_suggest_screen
from dmbot.dm_screen.adventure_card import AddButton as AdventureAddButton
from dmbot.dm_screen.adventure_card import (
    AdventureButton,
    finished_text,
)
from dmbot.dm_screen.adventure_card import BackToFilesButton as BackToAdventureButton
from dmbot.dm_screen.adventure_card import MatchButton as MatchAdventureButton
from dmbot.dm_screen.adventure_card import ReadButton as ReadAdventureButton
from dmbot.dm_screen.adventure_card import ReadNoButton as ReadAdventureNoButton
from dmbot.dm_screen.adventure_card import ReadYesButton as ReadAdventureYesButton
from dmbot.dm_screen.adventure_card import RefreshButton as RefreshAdventureButton
from dmbot.dm_screen.adventure_card import RemoveButton as RemoveAdventureButton
from dmbot.dm_screen.adventure_card import RemoveNoButton as RemoveAdventureNoButton
from dmbot.dm_screen.adventure_card import RemoveYesButton as RemoveAdventureYesButton
from dmbot.dm_screen.adventure_card import SeeButton as SeeAdventureButton
from dmbot.dm_screen.backstory_button import BackstoryButton
from dmbot.dm_screen.channel import setup_dm_screen, update_help_card
from dmbot.dm_screen.dm_bot_channel import DmBotChannelError, setup_dm_bot_channel
from dmbot.dm_screen.dmbchat_channel import DmbchatChannelError, setup_dmbchat_channel
from dmbot.dm_screen.game_masters import GameMastersButton
from dmbot.dm_screen.handover import (
    AcceptOfferButton,
    DeclineOfferButton,
    HandoverButton,
    NotNowButton,
    TakeOnButton,
    WithdrawOfferButton,
)
from dmbot.dm_screen.import_button import ImportButton
from dmbot.dm_screen.left_out import PutBackButton, left_out_view
from dmbot.dm_screen.name_questions import (
    FixUndoButton,
    NameAnswerUndoButton,
    NameQuestionButton,
    fix_notes_view,
    question_view,
)
from dmbot.dm_screen.names_button import NamesButton
from dmbot.dm_screen.npc_pages import NpcRosterStore, pass_delay
from dmbot.dm_screen.npc_pages import refresh as refresh_npc_pages
from dmbot.dm_screen.pause import PauseButton
from dmbot.dm_screen.peek_notices import PeekNotices
from dmbot.dm_screen.peek_notices import notice as peek_notice
from dmbot.dm_screen.quiet_stop import (
    CANCELLED,
    KeepListeningButton,
    idle_warning_text,
    keep_view,
    stopped_dm_text,
    stopped_player_text,
    stopped_text,
)
from dmbot.dm_screen.recap_panel import RecapsButton
from dmbot.dm_screen.recap_style import RecapStyleButton
from dmbot.dm_screen.retired_channels import remove_old_channels
from dmbot.dm_screen.settings import (
    BackToSettingsButton,
    FeaturesButton,
    HomebrewButton,
    HouseFileButton,
    InviteButton,
    LevelButton,
    ManagementMenuButton,
    QuietStopButton,
    RuleLookupButton,
    RulesCardsButton,
    SettingsButton,
    SettingsVisibilityButton,
    SharedRulesButton,
    VisibilityMenuButton,
)
from dmbot.dm_screen.site_offers import SiteOffers
from dmbot.dm_screen.speaker_question import SpeakerQuestionButton
from dmbot.dm_screen.speaker_question import question_view as speaker_question_view
from dmbot.dm_screen.story_mode import (
    StoryButton,
    StoryChoiceButton,
    StoryNoButton,
    StoryYesButton,
)
from dmbot.dm_screen.table_chat_panel import TableChatButton
from dmbot.dm_screen.table_pause import PauseTableButton, PauseTableForButton, ResumeTableButton
from dmbot.dm_screen.transcript_channel import (
    TranscriptChannelError,
    is_transcript_name,
    refresh_card,
    setup_transcript_channel,
)
from dmbot.dm_screen.transcript_keep import (
    KeepChoiceButton,
    KeepNoButton,
    KeepYesButton,
    TranscriptsButton,
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
from dmbot.fightbot import FightState
from dmbot.image_desk import ImageDesk, ImageUseStore
from dmbot.install_record import InstallStore
from dmbot.logs import log_context, set_log_context
from dmbot.mail import Mailer
from dmbot.map_notes import MapNotesSection, MapNoteStore
from dmbot.members import MemberDirectory
from dmbot.memory import (
    contradiction_run,
    definitions,
    document_reader,
    embed_sources,
    embedding,
    extractor,
    lore_context,
    lore_queue,
    meaning,
    plan_reader,
    report_make,
    scan_kinds,
    voyage,
    web_actions,
    web_report_requests,
    web_sheets,
)
from dmbot.memory import health as memory_health
from dmbot.memory import notify as memory_notify
from dmbot.memory import (
    sheets as sheets_memory,
)
from dmbot.memory.adventure import AdventureSection, AdventureStore
from dmbot.memory.backstory_links import BackstoryLinks
from dmbot.memory.backup import MemorySection
from dmbot.memory.claims import ClaimStore
from dmbot.memory.continuity import ContinuityStore
from dmbot.memory.contradiction_grades import MAJOR, GradeStore
from dmbot.memory.exposures import (
    ExposureStore,
    dedupe,
    describe,
    find_exposures,
    secret_connections,
)
from dmbot.memory.heard_backup import HeardSection
from dmbot.memory.importance import ImportanceCache
from dmbot.memory.importance_backup import ImportanceSection
from dmbot.memory.live_heads_up import heads_up as live_heads_up_for
from dmbot.memory.lookup import CampaignLookup, LookupCache
from dmbot.memory.models import (
    DM,
    FIX,
    KEEP,
    PROPOSED,
    Heard,
    MemoryRuleError,
    lookup_key,
    name_key,
)
from dmbot.memory.ontology import Tally
from dmbot.memory.places import find_spots, spread
from dmbot.memory.presession import PreSession
from dmbot.memory.recall import PLAYERS, AsOf, Recall, RecallService
from dmbot.memory.report_backup import ReportAnswersSection
from dmbot.memory.report_store import ReportStore
from dmbot.memory.revealed_aloud import said, table_lines, unrevealed_names
from dmbot.memory.scan import MAX_SUGGESTIONS, Suggestion, find_new_names, group_alike
from dmbot.memory.scene import Found, find_mentions, mentions, scene_hints
from dmbot.memory.scene import prepare as prepare_hints
from dmbot.memory.secret_aloud import MAX_ASKED, said_lines, secret_names, spoken
from dmbot.memory.secret_reveal import revealed_in
from dmbot.memory.sheet_refresh import hint_names as sheet_hint_names
from dmbot.memory.sheet_refresh import refresh as refresh_sheets
from dmbot.memory.sheet_store import SheetStore
from dmbot.memory.sounds import sound_codes
from dmbot.memory.store import MemoryStore
from dmbot.memory.story_plan import PlanStore, SpendSection
from dmbot.memory.story_watch import StoryWatch
from dmbot.memory.table_names import NameThenAnswer
from dmbot.memory.vectors import VectorStore
from dmbot.mentions import NO_PINGS
from dmbot.off_the_record import NOT_TOLD, WAKE_MINUTES, OffTheRecord, Paused, paused_text
from dmbot.recaps import discord as recaps_discord
from dmbot.recaps import feedback as recap_feedback
from dmbot.recaps import hold as recap_hold
from dmbot.recaps import name_answers, web_style
from dmbot.recaps.accepted import AcceptedSection, AcceptedStore
from dmbot.recaps.audio.eleven import ElevenLabs, HttpTransport
from dmbot.recaps.audio.files import AudioFiles
from dmbot.recaps.audio.worker import AudioWorker
from dmbot.recaps.discord import (
    HINTS_WAIT_S,
    LORE_SWEEP_EVERY_S,
    NAME_ANSWERS_PER_ROUND,
    WEB_STYLE_PER_ROUND,
    RecapFeatures,
)
from dmbot.recaps.notes import SessionNotesSection
from dmbot.recaps.pipeline import RecapWorker
from dmbot.recaps.plot import PlotRunner, PlotStore
from dmbot.recaps.plot_backup import PlotFineSection, PlotIssuesSection
from dmbot.recaps.plot_talk_run import TalkRunner, TalkStore
from dmbot.recaps.requests import RequestStore
from dmbot.recaps.store import RecapRightsSection, RecapSection, RecapStore
from dmbot.recaps.style import NotesSection, NoteStore, StyleSection, StyleStore
from dmbot.retention import RetentionJob
from dmbot.retention_channels import ChannelCounts, HasChannels, retire_channels
from dmbot.rules import house_voice, optional, rule_card, set_sync, web_sharing
from dmbot.rules import index as rules_index
from dmbot.rules.book_rank import book_is_legacy, book_wins
from dmbot.rules.followed import followed_rules, own_first
from dmbot.rules.homebrew import Homebrew, HomebrewSection, HomebrewStore
from dmbot.rules.homebrew_import import HomebrewImporter
from dmbot.rules.homebrew_packs import HomebrewPackStore, HomebrewReader
from dmbot.rules.homebrew_sources import SiteApprovalsSection, SourceStore
from dmbot.rules.homebrew_spotter import HomebrewMention, HomebrewSpotter
from dmbot.rules.house import HouseRule, HouseRulesSection, HouseRuleStore
from dmbot.rules.house_file_link import HouseFileLinkStore
from dmbot.rules.house_file_queue import HouseFileQueue
from dmbot.rules.optional import OptionalRule
from dmbot.rules.pack_suggestions import PackSuggestionStore
from dmbot.rules.rule_set_backup import RuleSetsSection
from dmbot.rules.rule_sets import FollowedSetsSection, RuleSetStore
from dmbot.rules.spotter import Mention
from dmbot.rules.wins import RuleRef, Win, WinsSection, WinsStore
from dmbot.sessions import SavedSession, SessionStore
from dmbot.sidebar import knowledge
from dmbot.sidebar import rule_clash as sidebar_rule_clash
from dmbot.sidebar.answer import PROMPT_VERSION as SIDEBAR_PROMPT_VERSION
from dmbot.sidebar.answer import Sidebar
from dmbot.sidebar.ask import asks_off_record, is_table_question, request_in
from dmbot.sidebar.people import lines as people_lines
from dmbot.sidebar.service import SidebarService
from dmbot.table import HintPeople, Table
from dmbot.test_recording import files as test_files
from dmbot.test_recording import library as test_library
from dmbot.test_recording.session import TestSession
from dmbot.test_recording.store import TestVoiceStore
from dmbot.timebot import clock as clock_game
from dmbot.timebot import combat_phrases, durations, import_time, where_are_we
from dmbot.timebot import phrases as clock_phrases
from dmbot.timebot import suggest as time_suggest
from dmbot.timebot.effects import EffectsSection, EffectStore
from dmbot.timebot.store import ClockSection, ClockStore
from dmbot.transcript import (
    fix_notes,
    left_out,
    lookback,
    name_pass_run,
    rank_options,
    refix,
    speaker_ai,
    web_line_fixes,
)
from dmbot.transcript import name_pass as name_pass_logic
from dmbot.transcript import pronounced as pronounced_aloud
from dmbot.transcript import questions as name_questions
from dmbot.transcript import speaker_question as speaker_q
from dmbot.transcript import spelled as spelled_out
from dmbot.transcript import stream as transcript_lines
from dmbot.transcript import suggestions as line_suggestions
from dmbot.transcript.attendance import session_speakers
from dmbot.transcript.cleaner import (
    DM_EDIT,
    IMAGE_EDIT,
    SOUND,
    Cleaned,
    Fix,
    clean,
    matcher_names,
    own_name,
    safe_answer,
    text_names_a_secret,
    with_ending,
)
from dmbot.transcript.close_calls import CloseCalls
from dmbot.transcript.export import clock
from dmbot.transcript.import_job import STALE_SCAN_S, ImportJob, ImportStore, Ready
from dmbot.transcript.import_map import ImportSpeakersSection
from dmbot.transcript.improvement import OptOutStore
from dmbot.transcript.keep import KeepJob
from dmbot.transcript.models import (
    NARRATING,
    OFF_RECORD,
    OFF_RECORD_WORDS,
    Line,
    TranscriptSession,
    line_ref,
)
from dmbot.transcript.name_pass_log import NamePassLog
from dmbot.transcript.name_pass_log import undo_for_web as name_pass_undo_for_web
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.suggestions import SuggestionStore
from dmbot.transcript.topic_ai import classify
from dmbot.transcript.topics import GAME, OFF_TOPIC, Waiting, obviously_game
from dmbot.transcript.topics import marker as topic_marker
from dmbot.transcript.waiting_questions import (
    ANSWER_NOT_SAVED,
    ANSWERED,
    KEPT,
    MAX_PER_SESSION,
    Unanswered,
    WaitingQuestionStore,
    find_once,
    fix_words,
)
from dmbot.transcript.waiting_questions import (
    Waiting as WaitingQuestion,
)
from dmbot.transcription.base import (
    PlaceholderTranscriber,
    Transcriber,
    confidence_of,
    engine_of,
)
from dmbot.transcription.factory import build_transcriber
from dmbot.transcription.pipeline import TranscriptionPipeline, clip_budget_s, speech_sent_line
from dmbot.ui import (
    contradiction_notes,
    image_fix,
    looked_at,
    name_pass_undo,
    player_id,
    recap_audio,
    shared_rules_defs,
)
from dmbot.ui import guide as guide_screen
from dmbot.ui import logic as ui_logic
from dmbot.ui import sheets as sheets_ui
from dmbot.ui.claims_review import ClaimsReviewButton, claims_review_view
from dmbot.ui.claims_review import question_text as claims_question
from dmbot.ui.claims_review import waiting_text as claims_waiting_text
from dmbot.ui.continuity import ContinuityButton, continuity_view
from dmbot.ui.continuity import waiting_text as continuity_waiting_text
from dmbot.ui.decision_lines import DecisionLines
from dmbot.ui.dmbot_commands import _failed, dmbot_group
from dmbot.ui.exposure_summary import (
    MAX_LISTED,
    RecantButton,
    SeenButton,
    summary_text,
    summary_view,
)
from dmbot.ui.guide import GuideButton
from dmbot.ui.heard_review import HeardReviewButton, heard_review_view
from dmbot.ui.heard_review import waiting_text as heard_waiting_text
from dmbot.ui.homebrew import dmbot_homebrew  # noqa: F401 (registers it)
from dmbot.ui.house_rules import dmbot_house_rules  # noqa: F401 (registers it)
from dmbot.ui.import_command import dmbot_import  # noqa: F401 (registers it)
from dmbot.ui.line_fix import (
    MORE_WAITING,
    WAITING_SHOWN,
    FixLineButton,
    LineFixButton,
    dm_text,
    fix_line_view,
    suggestion_view,
)
from dmbot.ui.live_heads_up import LiveHeadsUpButton, heads_up_text, heads_up_view
from dmbot.ui.migrate import dmbot_migrate  # noqa: F401 (registers it)
from dmbot.ui.name_card import (
    FinishPastButton,
    UndoButton,
    UndoConnectionButton,
    UndoManyButton,
    UndoRunButton,
    UndoSpellingButton,
)
from dmbot.ui.name_lists import UndoListButton
from dmbot.ui.name_unnamed import UndoNamingButton
from dmbot.ui.names import (
    SPELLED_OUT,
    RecapNamesButton,
    ReviewButton,
    after_session_text,
    held_back_text,
    private_after_session_text,
    review_view,
)
from dmbot.ui.optional_rules import dmbot_optional_rules  # noqa: F401 (registers it)
from dmbot.ui.people_review import PeopleReviewButton, people_review_view, waiting_text
from dmbot.ui.recap_again import (
    dmbot_recap,  # noqa: F401 (registers it)
)
from dmbot.ui.recap_upload import recap_upload_command  # noqa: F401 (registers it)
from dmbot.ui.revealed_report import UndoRevealedButton, report_text, revealed_view
from dmbot.ui.rule_clash import ClashView
from dmbot.ui.rule_lookup import dmbot_rule  # noqa: F401 (registers it)
from dmbot.ui.secret_aloud import (
    SecretAloudButton,
    more_text,
    question_text,
    secret_aloud_view,
)
from dmbot.ui.secret_reveal import RevealButton, reveal_view
from dmbot.ui.secret_reveal import question_text as reveal_question
from dmbot.ui.sheets import MySheetButton, OpenCardButton
from dmbot.ui.story_watch import KnowButton, WordsButton, message_text, watch_view
from dmbot.ui.threads import ThreadListButton, ThreadsButton, threads_view
from dmbot.ui.threads import waiting_text as threads_waiting_text
from dmbot.ui.transcripts import (
    DownloadButton,
    download_view,
    ended_no_download_text,
    ended_text,
    lookup_members,
    transcript_command,
)

DMBCHAT_STATEMENT_WORDS = 4  # a DM's message this long is a statement; shorter is a reply
# Added to a question that had to stay on the DM screen (#3210), so a DM whose private messages are
# closed learns why it is there and how to get it privately.
NO_PRIVATE_MESSAGE = (
    "\n_DMbot couldn't message you privately. Allow messages from this server in your Discord "
    "privacy settings._"
)
DMBCHAT_HINT = (
    "To tell DMbot something about the story, write it in your private chat with DMbot. "
    "Players can read this chat."
)
REPORT_CALLS = 8  # AI calls a session may spend on the after-session report (grading and sorting)
MEANING_RUN_CALLS = 50  # Voyage AI calls one after-session run may make (64 texts each)
CONTRADICTION_CALLS = 3  # AI calls a session may spend grading contradictions (12 clashes each)
MAX_SPEAKER_NAMES = 25  # people looked up for one session in a go (the download's own cap)
RULES_CARD_DB_S = 5.0  # the longest a rules card waits for the house rules
log = logging.getLogger(__name__)

SUMMARY_INTERVAL_S = 15
# Lines for the transcript channel are grouped and posted this often (#124): well inside
# Discord's 5 messages per 5 seconds per channel, and still feels live.
TRANSCRIPT_FLUSH_S = 2.0
TRANSCRIPT_SAVE_S = 5.0  # stored transcript lines are saved in batches this often
SCENE_FRESH_S = 600.0  # a changed piece older than this doesn't put its names in the scene
PIECE_WORDS_MAX = 3000  # pieces whose words the helpers' copies can be brought up to date for
SPELLED_MAX = 20  # names spelled out and kept in one session (#2168)
PRONOUNCED_MAX = 40  # (name, sound, person) counts kept in one session (#2171)
# A plain word counts as how a name is said only when it is more alike than this to the name ...
SOUND_CLOSE = 0.6
# ... and at most this many letters longer or shorter ("Fin" and "Final" are two apart).
SOUND_LENGTH_GAP = 1
REPLY_SCANS_PER_MINUTE = 10  # reads of a chat to find a deleted picture's reply, per server
REPLY_SCAN_LIMIT = 50  # messages after the picture that are read for it
IMAGE_REPLIES_KEPT = 200  # pictures whose reply is remembered, so a delete can take it back
DELETED_KEPT_S = 600.0  # how long a deleted message's id is remembered (#2450)
HEARD_MAX = 20_000  # lines kept for the after-session name scan
SAID_AS_MIN_FIXES = 3  # the cleaner fixed a name from the same spelling this often (#2171)
SAID_AS_HEARD_MAX = 80  # the longest other spelling kept for it
HEARD_FORMS_MAX = 1000  # (name, spelling, person) counts kept in a session
HINTS_FAIL_LOG_S = 60.0
SWEEP_PACE_S = 2.0  # campaigns are looked at this far apart when the bot starts (rate limits)
LINKS_READY_SETTLE_S = 5.0  # a burst of name answers is one look at the waiting connections
NPCS_PASSES = 60  # (a pass a minute at most: an hour of a busy campaign)

# The off-topic filter (#52): one call may take this long, or the window is kept as game
# talk; after this many failures in a row it rests this long.
TOPIC_CALL_TIMEOUT_S = 8.0
EARLY_CLOSE_GAP_S = (
    10.0  # a spoken look-up closes the speaker-label window early at most this often (#1624)
)
TOPIC_FAILURES_BEFORE_REST = 3
TOPIC_REST_S = 300.0
SPEAKER_WAIT_S = 1.0  # the longest the names are waited for, before a label is saved
EDIT_TIMEOUT_S = 5.0  # a transcript message edit, at most (it holds the post lock)
PUT_BACK_TIMEOUT_S = 5.0  # saving a Put it back, at most (it holds the save lock; #677)
HINT_PEOPLE_S = 5.0  # who's in the voice channel, for name hints: looked at this often
TRANSCRIPT_POST_TIMEOUT_S = 10.0  # one stuck post can't hold the others up for long
TRANSCRIPT_PARALLEL = 10  # campaigns posting at once (one rate-limited channel can't stall all)
STOP_DRAIN_TIMEOUT_S = 120.0  # at stop, wait this long for the last words to be written
FINAL_FLUSH_TIMEOUT_S = 15.0  # at stop or shutdown, give up on posting after this
IDLE_SWEEP_INTERVAL_S = 1
OWNER_CHECK_DAYS = 120  # the retention window: older campaigns are not looked at (#1214)
OWNER_CHECK_GAP_S = 0.5  # between campaigns in the daily look
OWNER_START_WAIT_S = 2  # at a start the table is waiting
OWNER_ASK_EVERY_S = 3600  # Discord is asked about one campaign's owner at most this often
OWNER_LOOK_TIMEOUT_S = 6  # asking Discord about an owner must not hold up a start
MOVED_CHECKED_MAX = 100_000  # people remembered as checked; cleared past this
FORGET_TIMEOUT_S = 10  # removing learned names must not hold up stopping a recording
DISPLAY_NAME_REFRESH_S = 86400  # a person's Discord name is kept on their account once a day
DISPLAY_NAME_TIMEOUT_S = 3  # for the background update
NAMES_NOTED_MAX = 5000  # people remembered as just noted; the oldest half go past this
PRESESSION_NOTE_WAIT_S = 15.0  # the longest the pre-session note may take (#1403)
SECRET_QUESTION_WAITING = (
    "🤫 DMbot couldn't send you a question about a secret name. Turn on Direct Messages in this "
    "server's Privacy Settings (tap the server name) and it will ask next session."
)
CLAIMS_PREVIEW = 5  # things to check the DM-only summary spells out; the rest are in the check
SUMMARY_WAITING = (
    "🤫 DMbot couldn't send you the after-session summary of secrets said at the table, so nothing "
    "was shown to your players. Turn on Direct Messages in this server's Privacy Settings (tap the "
    "server name) and it will send it after the next session."
)
SUMMARY_PARTIAL = (
    "🤫 Some of the secrets in your after-session summary could not be shown to your players "
    "yet, so they are still secret. DMbot will try again after the next session."
)
IMPORT_ROUND_S = 30
IMPORT_SCAN_TRIES = 3  # an imported session's name scan is let go after this many failures
IDLE_CHECK_S = 60  # how often a running session is checked for being empty or quiet (#2034)
METER_INTERVAL_S = 60  # how often listening minutes are written to the hours meter (#437)
METER_FINAL_TRIES = 3  # at a stop: the last minutes are written nowhere else
METER_FINAL_RETRY_S = 2
MEANING_WAIT_S = (
    1.5  # the lead search by meaning: a database read and two calls to Voyage AI (#2664)
)
RECALL_WAIT_S = 0.5  # and this long for the confirmed facts around them (#1370): optional context
NAMES_WAIT_S = 1.0  # the sidebar waits this long for a campaign's names, then answers without
PLAN_READS_AT_ONCE = 2  # adventures read at the same time, bot-wide: the AI and the bill are shared
WEB_DOCUMENT_COOLDOWN_S = 60  # per person and campaign: each website document read is an AI read
GATE_TIMEOUT_S = 2  # a button press must be answered within Discord's 3 s: fail open sooner
CHAT_BLOCKED_TTL_S = 60  # how long the list of channels never read as chat is kept (#1118)
CLOCK_PHRASE_GAP_S = 300  # the same rest said twice within this is counted once (#965)
COMBAT_PHRASE_GAP_S = 15  # the same combat words twice (voice and chat) within this: once (#1127)
METER_CALL_TIMEOUT_S = 8  # one write of minutes; a stuck database must not hold the loop
RECORDED_CHECK_S = 2.0  # the 🗂️ Menu's database check: well inside Discord's 3 s
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


def _ms(seconds: float) -> int:
    """Whole milliseconds, never negative."""
    return max(0, int(seconds * 1000))


def consent_timing_line(
    people: int, reminders: int, status_ms: int, send_ms: list[int], since_start_ms: int | None
) -> str:
    """One INFO line per consent ask (#1706): how many people, how long the consent look-up and
    the private messages took, and how long after the session began. Numbers only: no name, no
    id, no words. A slow button for a player can then be placed (a late ask, a slow look-up, or
    Discord taking long to open the DM)."""
    since = "unknown" if since_start_ms is None else f"{since_start_ms}ms"
    slowest = max(send_ms, default=0)
    return (
        f"Consent asked: {people} people ({reminders} reminders), status {status_ms}ms, "
        f"sent {sum(send_ms)}ms in total (slowest {slowest}ms), since the session start {since}"
    )


def _joined_at(guild: discord.Guild) -> int | None:
    """When the bot joined this server, as Discord says (None while the server is still loading)."""
    me = cast(discord.Member | None, getattr(guild, "me", None))
    return int(me.joined_at.timestamp()) if me is not None and me.joined_at else None


class DMBotTree(app_commands.CommandTree["DMBot"]):
    """Tags every slash-command log line with the server it came from."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        set_log_context(guild_id=interaction.guild_id)
        self.client.tell_if_moved_later(interaction.user.id)
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


def _fixed(texts: list[str]) -> Callable[[], Awaitable[list[str]]]:
    """Words already read (an imported session's lines were filtered to the people who agree
    when they were read, just before)."""

    async def read() -> list[str]:
        return texts

    return read


class DMBot(RecapFeatures, commands.AutoShardedBot):
    """One process serving the shards in `settings.shards` (one shard by default).

    Per-server state (sessions, locks, loops) only exists for servers on these shards,
    so nothing runs twice when several processes split the shards between them.
    """

    # A class-level default so tests that build a bot without __init__ still see "no pages" (#2265).
    _npcs_roster: NpcRosterStore | None = None

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
        rule_sets: RuleSetStore | None = None,
        recaps: RecapStore | None = None,
        suggestions: SuggestionStore | None = None,
        homebrew: HomebrewStore | None = None,
        heard_questions: WaitingQuestionStore | None = None,
        improvement: OptOutStore | None = None,
        sources: SourceStore | None = None,
        importer: HomebrewImporter | None = None,
        adventure: AdventureStore | None = None,
        plan: PlanStore | None = None,
        npcs_roster: NpcRosterStore | None = None,
        recap_styles: StyleStore | None = None,
        recap_notes: NoteStore | None = None,
        installs: InstallStore | None = None,
        recap_holds: recap_hold.HoldStore | None = None,
        backstory_links: BackstoryLinks | None = None,
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
            # Nothing in DMbot is meant to ping (#1834). Campaign data is posted as typed, so a
            # name like @everyone must never notify anyone; a call's own allowed_mentions is
            # merged over this default, so the NO_PINGS arguments keep working.
            allowed_mentions=NO_PINGS,
        )
        self.settings = settings
        # With an outside speech-to-text company, only yeses given knowing that count
        # (#170). Set here, from the same settings, so the two can never disagree.
        consent.outside = settings.transcription.outside_engine
        self.consent = consent
        # Off the record (#2466): a person's pause, or the DM's pause of the whole table.
        self.off_the_record = OffTheRecord(
            consent,
            push=self.push_allowlist,
            say=self._say_off_the_record,
            name_of=self._off_the_record_name,
            listening=lambda guild_id: guild_id in self.tables,
            drop=self._drop_in_flight,
            save=self._save_pauses,
            gap=self._off_record_gap,
        )
        self.campaigns = campaigns
        self.sessions = sessions
        # Campaign memory (#126): names the DM gives, suggestions after each session, and
        # an in-memory copy of each campaign's names, kept up to date by notifications.
        self.memory = memory
        # Secrets said at the table, recorded for the DM's summary; nothing applies them yet (#2410)
        self.exposures = ExposureStore(memory._db) if memory is not None else None
        self.lookup = (
            LookupCache(memory, in_use=self._campaign_has_a_table) if memory is not None else None
        )
        # Players' D&D Beyond sheets, per campaign (#723).
        self.sheets = sheets
        # A campaign's house rules, for `/dmbot houserules` (#865); None without a database.
        self.house_rules = house_rules
        # Each campaign's game clock (#965); None without a database.
        self.clocks = clocks
        # The linked house-rules file (#969), and what is waiting for a DM's press.
        self.house_file_links = house_file_links
        # The connections a backstory reading found, waiting for the DM (#2380).
        self.backstory_links = backstory_links
        self.rule_sets = rule_sets  # shared house rules (#1190)
        self.recaps = recaps  # the cinematic recap of each session (#1382)
        self.recap_requests = RequestStore(recaps.db) if recaps is not None else None  # (#2113)
        # The DM's answers to a recap's names to check, from its page (#2113).
        self.name_answers = name_answers.AnswerStore(recaps.db) if recaps is not None else None
        # What DMs did on the website's All names page, waiting to be applied (#2294).
        self.name_actions = web_actions.ActionStore(recaps.db) if recaps is not None else None
        # The lore DMs uploaded on the website, waiting to be read (#3022), and the reads running.
        self.lore_uploads = lore_queue.LoreQueue(recaps.db) if recaps is not None else None
        self._lore_reads: dict[int, asyncio.Task[None]] = {}  # server -> its read in progress
        self._lore_swept: dict[int, float] = {}  # server -> when its queue was last swept
        # The website's House-rules file requests (#3087), one at a time per server like lore.
        self.house_file_queue = HouseFileQueue(recaps.db) if recaps is not None else None
        self._house_file_runs: dict[int, asyncio.Task[None]] = {}  # server -> its request running
        self._house_file_swept: dict[int, float] = {}  # server -> when its queue was last swept
        # What DMs chose on the website's Settings page, waiting to be applied (#2463).
        self.web_settings = web_settings.SettingStore(recaps.db) if recaps is not None else None
        # What DMs chose on the website's rule sharing section, waiting to be applied (#2793).
        self.web_rule_actions = web_sharing.ActionStore(recaps.db) if recaps is not None else None
        self._rule_tasks: dict[int, asyncio.Task[None]] = {}  # one running choice a server
        self._report_tasks: dict[int, asyncio.Task[None]] = {}  # one running report a server
        self._put_back_tasks: dict[int, asyncio.Task[None]] = {}  # one read a server (#2587)
        self._sheet_tasks: dict[int, asyncio.Task[None]] = {}  # one running choice a server (#2817)
        self._web_document_last: dict[tuple[int, int, str], float] = {}  # website document reads
        # "Make the report again", asked for on the website (#2805).
        self.report_requests = (
            web_report_requests.RequestStore(recaps.db) if recaps is not None else None
        )
        # What players and DMs chose on the website's character sheets page (#2817).
        self.web_sheets = web_sheets.ActionStore(recaps.db) if recaps is not None else None
        # A line fixed from the website's Transcripts page (#2883).
        self.web_line_fixes = web_line_fixes.ActionStore(recaps.db) if recaps is not None else None
        self._fix_tasks: dict[int, asyncio.Task[None]] = {}  # one running fix a server
        self.peek_notices = PeekNotices(recaps.db) if recaps is not None else None
        self.web_delete_notices = WebDeleteNotices(recaps.db) if recaps is not None else None
        self.web_backups = web_backup.WebBackups(recaps.db) if recaps is not None else None
        self.web_line_deletions = (
            web_line_delete.WebLineDeletions(recaps.db) if recaps is not None else None
        )
        # Copies restored from the website, waiting for the bot to apply them (#2746).
        self.web_restores = web_restore.WebRestores(recaps.db) if recaps is not None else None
        self._clip_sweep_rounds = 0
        self._voice_tasks: dict[
            int, asyncio.Task[None]
        ] = {}  # a spoken note being written down, by server (#2161)
        self._voice_next = 0  # where the next voice round starts looking, so servers take turns
        self.recap_styles = recap_styles  # the examples for how recaps sound (#2075)
        # The website's Recap style choices (#3060), applied with the stores above.
        self.web_recap_style = web_style.StyleQueue(recaps.db) if recaps is not None else None
        self._web_style_swept: dict[int, float] = {}  # server -> when its queue was last swept
        self.installs = installs  # which servers the bot is in, and when it left (#2163)
        self.recap_holds = recap_holds  # recaps waiting for the DM's name answers (#2169)
        self.recap_feedback_limit = recap_feedback.FeedbackLimit()
        self.recap_notes = recap_notes  # the DM's standing style notes (#2076)
        # How connected each name is in The truth, to rank the recap's beats (#2411); refreshed
        # after each session.
        self.importance = ImportanceCache(memory) if memory is not None else None
        self.suggestions = suggestions  # players' suggested line fixes (#1491)
        self.homebrew = homebrew  # a campaign's own homebrew entries (#1520)
        self.sources: SourceStore | None = (
            sources  # which sites a campaign approved, imported pages (#1633)
        )
        self.importer: HomebrewImporter | None = (
            importer  # adds homebrew from a published page's link (#1634)
        )
        self.adventure = adventure  # the DM's adventure text, per campaign (#1698)
        self.plan = plan  # the story plan read from it (#1714)
        # The check of the table against that plan (#1729): reads only, no AI.
        self.watch = StoryWatch(plan.db) if plan is not None else None
        # Reads going now, by (server, document): kept so the card can say "reading" and a second
        # press can't start another, and so the tasks aren't garbage collected mid-read.
        self.plan_reads: dict[tuple[int, str], asyncio.Task[None]] = {}
        self.plan_waiting: set[tuple[int, str]] = set()  # asked for, waiting for one of the slots
        # When each going read was asked for and last moved forward (monotonic seconds), for the
        # card's "running for" and "last change" (#2564). Memory only, like the read itself.
        self.plan_clocks: dict[tuple[int, str], plan_reader.ReadClock] = {}
        self._plan_slots = asyncio.Semaphore(PLAN_READS_AT_ONCE)
        # "Did they mean…?" questions left unanswered at a session's end (#1572).
        self.heard_questions = heard_questions
        # The last entries a DM removed, per campaign, so the list can put them back (memory only).
        self.homebrew_removed: dict[tuple[int, str], list[Any]] = {}
        self._short_asks: dict[str, int] = {}  # campaign -> session its open question is about
        # Each imported session's name-pass report, kept until the import's last session is
        # scanned and then posted as one (#2649).
        self._import_reports: dict[tuple[int, str], list[name_pass_logic.Report]] = {}
        self._import_names: dict[str, list[str]] = {}  # campaign -> names its import scans added
        self._import_unhonoured: dict[str, list[str]] = {}  # campaign -> why recaps weren't made
        # campaign -> the sessions of its import whose recap was queued at once (told in one line)
        self._audio_seen: dict[tuple[int, str, int], tuple[int, int]] = {}
        self._name_pass_asks: dict[str, list[tuple[str, int]]] = {}  # unsure words (#2628)
        self.audio_waits = recap_audio.AudioWaits()  # the live "Making the audio" lines (#2621)
        self._import_queued: dict[str, list[int]] = {}
        # campaign -> how many of its import's sessions had their name check given up (told once;
        # best effort across a restart, the website's progress reads the saved flag)
        self._import_unchecked: dict[str, int] = {}
        # import id -> the sessions of a held group released by answers so far (one line at the end)
        self._group_made: dict[str, list[int]] = {}
        # The earliest hold each import has had (memory only): its waiting emails count their days
        # from it, so answering the oldest session does not start the count over (#2237).
        self._group_started: dict[tuple[int, str, str], float] = {}
        # One lock for each import: release + record + pop of its "writing" line are one step, so
        # a press, a round and an expiry never each tell the group (#2174).
        self._group_locks: dict[str, asyncio.Lock] = {}
        self.decision_lines = DecisionLines()  # DM-screen lines to clear once decided (#1509)
        self.house_syncs = house_sync.Pendings()
        # The NPC pages' view (#2265) hears about every change to the campaign's memory. The
        # change notice carries only the campaign id, so the server is remembered when the
        # campaign is first seen (the start-up sweep, a session start) or found by looking.
        self._npcs_sync: dict[str, asyncio.Task[None]] = {}
        self._npcs_dirty: set[str] = set()
        self._npcs_last: dict[str, float] = {}  # when a campaign's last pass began (monotonic)
        self._npcs_guilds: dict[str, int] = {}
        # Waiting backstory connections that may have become ready (#2650): the campaigns to look
        # at, so a burst of name answers is one look a few seconds on.
        self._links_ready_pending: set[str] = set()
        self._links_guilds: dict[str, int] = {}  # campaign -> its server, once found
        self._npcs_recall: RecallService | None = None
        self._npcs_roster = npcs_roster  # what each NPC's page shows, and its notes (#2265)
        # Timed effects on that clock (#998); None without a database.
        self.effects = effects
        # The hours meter (#437 part 2): listening minutes are written here; None records
        # nothing (tests and tools that run no real sessions).
        self.meter = meter
        # The last day of Try It and the extension (#1229): only with a database, and the mail
        # sender (which sends nothing until SMTP is set up) for the emails.
        self.trial_jobs: trial_jobs.TrialJobs | None = None
        self.mailer: Mailer | None = None
        self.account_moves: AccountMoves | None = None  # moving to a new Discord account (#1230)
        self._moved_tasks: set[asyncio.Task[None]] = set()
        # People already checked for a "your account moved" notice since this process
        # started: a command from them costs nothing more. An account that moves is taken
        # out (after_account_move), so it is checked again.
        self._moved_checked: set[int] = set()
        # When each person's Discord name was last kept on their account (#1242): at most
        # once a day, in this process only (a restart simply checks again).
        self._names_noted: dict[int, float] = {}
        self.members = MemberDirectory(meter.db) if meter is not None else None  # #2141
        self._name_tasks: set[asyncio.Task[None]] = set()
        if meter is not None:
            self.account_moves = AccountMoves(meter.db)
            self.mailer = Mailer(
                meter.db,
                settings.mail,
                site_url=settings.site_url,
                link_secret=settings.link_token_secret,
            )
            self.trial_jobs = trial_jobs.TrialJobs(
                meter.db,
                self.mailer,
                enforce_plans=settings.enforce_plans,
                site_url=settings.site_url,
                link_secret=settings.link_token_secret,
                send_dm=self._dm_trial_user,
                name_of=self._discord_name,
                skip=settings.free_users,
            )
        # The daily job that warns about and deletes campaigns past their keep date (#964).
        # Needs the meter (the owners' plans); without one nothing is ever deleted.
        self._owner_asked: dict[str, float] = {}  # campaign id -> when Discord was last asked
        self.retention: RetentionJob | None = None
        # The transcripts a DM asked to be kept only so long are deleted in the same daily run
        # (#1533). It needs the transcript store; the DM's choice applies on any plan.
        self.keep_job: KeepJob | None = None
        if transcripts is not None:
            self.keep_job = KeepJob(
                campaigns=campaigns,
                transcripts=transcripts,
                guild_ids=campaigns.guilds_with_campaigns,
                post_screen=self._post_to_screen,
            )
        if meter is not None:
            self.retention = RetentionJob(
                campaigns=campaigns,
                standing_of=meter.retention_standing,
                guild_ids=campaigns.guilds_with_campaigns,
                forget_guild=campaigns.forget_guild_if_empty,
                running=lambda: {t.campaign_id for t in self.tables.values() if t.campaign_id},
                send=self._dm_user,
                retire_channels=self._retire_campaign_channels,
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
        # Owner alerts (#1907): email first; a private message is the way when mail is what broke.
        if (alerter := alerts.current()) is not None:
            alerter.set_tell_owner(self._tell_app_owner)
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
        gemini = (
            GeminiClient(settings.gemini_key, watch=self.ai_watch)
            if settings.ai_provider_creative == "gemini" and settings.gemini_key
            else None
        )
        if gemini is not None:
            log.warning(
                "The recap writer is %s (Gemini): GEMINI_API_KEY must belong to a Google account "
                "with billing on, or Google may use the recaps' text (the free tier's terms).",
                settings.ai_models.creative,
            )
        self.ai_client = (
            AnthropicClient(settings.ai_key, settings.ai_models, watch=self.ai_watch, gemini=gemini)
            if settings.ai_key
            else None
        )
        # AI text calls (a document into a names list).
        self.ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.NAMES], Feature.NAMES)
            if self.ai_client
            else None
        )
        # The sentences around the names a DM's text suggested, held in memory for the review
        # (#2561); never stored.
        self.lore_context = lore_context.Held()
        # The names in a DM's own campaign background (#2167): suggestions only, on the fast tier.
        self.background_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.NAMES_BACKGROUND], Feature.NAMES_BACKGROUND)
            if self.ai_client
            else None
        )
        # Who "she" is in a lore read (#2790): the small model, only with DMBOT_WHO_IS_IT on.
        self.who_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.WHO_IS_IT], Feature.WHO_IS_IT)
            if self.ai_client and settings.who_is_it
            else None
        )
        # Pass 1 of that read (#2454): the careful model reads the whole text into a plain list.
        self.background_read_ai = (
            self.ai_client.tier(
                FEATURE_TIERS[Feature.NAMES_BACKGROUND_READ], Feature.NAMES_BACKGROUND_READ
            )
            if self.ai_client
            else None
        )
        # The story-plan reader (#1714): the AI reads an adventure once, on a DM's press.
        self.plan_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.PLAN_READER], Feature.PLAN_READER)
            if self.ai_client
            else None
        )
        # A player's sheet document read into the sheet fields (#1125).
        self.sheet_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.SHEET], Feature.SHEET)
            if self.ai_client
            else None
        )
        # FightBot's reminders to players in combat (#1129), on the smallest model.
        self.fight_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.FIGHTBOT], Feature.FIGHTBOT)
            if self.ai_client
            else None
        )
        # The story extractor's reader after a session (#1363). Built only when the switch is on,
        # so with it off there is no client to call; the tier is a backend setting.
        self.extractor_ai = (
            self.ai_client.tier(
                AIModelTier(settings.story_extractor_tier), Feature.EXTRACTOR, cache_system=True
            )
            if self.ai_client and settings.story_extractor_on
            else None
        )
        # PlotBot's name pass on a finished transcript (#2628): which list name a word means.
        self.name_pass_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.NAME_PASS], Feature.NAME_PASS)
            if self.ai_client
            else None
        )
        # The cinematic recap (#1382): the first draft on the fast tier, one more try on the
        # careful one. Built only with the switch on, so off means no client to call.
        self.recap_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.RECAP], Feature.RECAP)
            if self.ai_client and settings.recaps_on
            else None
        )
        self.recap_retry_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.RECAP_RETRY], Feature.RECAP_RETRY)
            if self.ai_client and settings.recaps_on
            else None
        )
        # The recap's two models (#2074): a creative one writes the prose, a small one shapes it.
        self.recap_write_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.RECAP_WRITE], Feature.RECAP_WRITE)
            if self.ai_client and settings.recaps_on
            else None
        )
        self.recap_format_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.RECAP_FORMAT], Feature.RECAP_FORMAT)
            if self.ai_client and settings.recaps_on
            else None
        )
        # Sorts a DM's feedback on a recap (#2076): one small fast-tier call a DM presses for.
        self.recap_feedback_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.RECAP_FEEDBACK], Feature.RECAP_FEEDBACK)
            if self.ai_client and settings.recaps_on
            else None
        )
        self.recap_worker: RecapWorker | None = None
        if self.recap_ai is not None and self.recap_retry_ai is not None and recaps is not None:
            self.recap_worker = RecapWorker(
                recaps,
                self.recap_ai,
                self.recap_retry_ai,
                load=self.recap_inputs,
                consenting=lambda gid: lambda: self.consent.consenting(gid),
                allowed=self.recap_allowed,
                notify=self.recap_told,
                unchecked=self.recap_unchecked,
                allowance=self.recap_text_allowance,
                names_ready=self.recap_names_ready,
                creative=self.recap_write_ai,
                formatting=self.recap_format_ai,
                story=self.recap_story,
                critics=True,  # the critics read each draft until they agree (#2255)
                reads=settings.recap_writer_reads,
                final_read=settings.recap_final_read,
                structured_split=settings.recap_structured_output_split,
                structured_beats=settings.recap_structured_output_beats,
            )
        # PlotBot's check of each ready recap against the earlier accepted ones (#2122): advice for
        # the DM's recap page only. Off unless DMBOT_PLOT_CHECK is on (it needs the recaps).
        self.plot_runner: PlotRunner | None = (
            PlotRunner(
                PlotStore(recaps.db),
                AcceptedStore(recaps.db),
                self.ai_client.tier(FEATURE_TIERS[Feature.PLOT_CHECK], Feature.PLOT_CHECK),
            )
            if settings.plot_check_on and self.ai_client and recaps is not None
            else None
        )
        # The DM's talk with PlotBot about an issue (#2123): the page queues the DM's words, this
        # answers them. Same switch as the checks.
        self.plot_talk: TalkRunner | None = (
            TalkRunner(
                TalkStore(recaps.db),
                memory,
                self.ai_client.tier(FEATURE_TIERS[Feature.PLOT_TALK], Feature.PLOT_TALK),
            )
            if settings.plot_check_on
            and self.ai_client
            and recaps is not None
            and memory is not None
            else None
        )
        # The recap's narration and music (#1383): a second job after the text is ready. Built
        # only with the switch on and a key and voice set; with none, recaps go out text-only.
        self.recap_audio_files = AudioFiles(settings.data_dir / "recap-audio")
        self._eleven_http: HttpTransport | None = None
        self.audio_worker: AudioWorker | None = None
        if self.recap_worker is not None and recaps is not None and settings.recap_audio_on:
            self._eleven_http = HttpTransport(settings.elevenlabs_key)
            self.audio_worker = AudioWorker(
                recaps,
                ElevenLabs(
                    self._eleven_http,
                    voice_id=settings.elevenlabs_voice_id,
                    speech_model=settings.elevenlabs_speech_model,
                ),
                self.recap_audio_files,
                load=self.recap_audio_inputs,
                consenting=self.consent.consenting,
                allowed=self.recap_audio_allowed,
                switched=self.recap_audio_switched,
                allowance=self.recap_audio_allowance,
                max_credits=settings.recap_audio_max_credits,
                voice_tags=settings.recap_voice_tags,
            )
        # The off-topic filter (#52), the audio check and the DM sidebar.
        self.topic_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.TOPIC], Feature.TOPIC)
            if self.ai_client
            else None
        )
        self.rule_conflict_ai = (  # a saved house rule that disagrees with another (#2284)
            self.ai_client.tier(FEATURE_TIERS[Feature.RULE_CONFLICTS], Feature.RULE_CONFLICTS)
            if self.ai_client
            else None
        )
        self.house_file_ai = (  # house rules found in any file or link (#2377)
            self.ai_client.tier(FEATURE_TIERS[Feature.HOUSE_RULES], Feature.HOUSE_RULES)
            if self.ai_client
            else None
        )
        # Which disagreeing house rule wins, as the DM decided; set where the database is known.
        self.house_wins: WinsStore | None = None
        self.time_ai = (  # a time jump suggested from vague narration (#1758)
            self.ai_client.tier(FEATURE_TIERS[Feature.TIME_SUGGEST], Feature.TIME_SUGGEST)
            if self.ai_client
            else None
        )
        self.import_time_ai = (  # the game time an imported session ended at (#2979), background
            self.ai_client.tier(FEATURE_TIERS[Feature.IMPORT_TIME], Feature.IMPORT_TIME)
            if self.ai_client
            else None
        )
        # Pictures posted in the table chat (#2450): off unless the backend switch is on.
        # Messages deleted lately, so a picture still being described when its poster deletes it
        # is not written down afterwards (#2450); id → when.
        self._deleted_messages: dict[int, float] = {}
        # the "Text in this image" replies by the picture's message, so deleting the picture takes
        # its words out of the chat too (#2450); the newest few, in memory only
        self._image_replies: dict[int, list[discord.Message]] = {}
        self._reply_scans: dict[int, list[float]] = {}  # when each server's chat was last read
        self._chat_channels: dict[int, tuple[float, frozenset[int]]] = {}
        self.map_notes: MapNoteStore | None = None  # set where the database is known (#2450)
        self.image_desk: ImageDesk | None = (
            ImageDesk(
                self.ai_client.tier(FEATURE_TIERS[Feature.IMAGE], Feature.IMAGE),
                self.ai_client.tier(FEATURE_TIERS[Feature.IMAGE_MAP], Feature.IMAGE_MAP),
                per_day=settings.table_images_per_day,
            )
            if self.ai_client and settings.table_images_on
            else None
        )
        self.audio_ai = (
            self.ai_client.tier(FEATURE_TIERS[Feature.AUDIO_CHECK], Feature.AUDIO_CHECK)
            if self.ai_client
            else None
        )
        # Meaning search (#2664): built only with the switch on and the Voyage key set. With
        # neither, nothing is sent anywhere and the sidebar and recall work from names alone.
        self._voyage_http: voyage.HttpTransport | None = None
        self.voyage: voyage.Voyage | None = None
        self.meaning_seeds: meaning.MeaningSeeds | None = None
        self.meaning_calls = meaning.CallCounter(time.time)
        if memory is not None and settings.meaning_search_on:
            self._voyage_http = voyage.HttpTransport(settings.voyage_key)
            self.voyage = voyage.Voyage(
                self._voyage_http,
                embed_model=settings.voyage_embed_model,
                rerank_model=settings.voyage_rerank_model,
            )
            self.meaning_seeds = meaning.MeaningSeeds(
                memory._db,
                VectorStore.of(memory),
                self.voyage,
                settings.voyage_embed_model,
                allowed=self.meaning_allowed,
                count_call=self.meaning_calls.count,
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
        # Cleaning the sessions a DM imported from a file (#2105), in the background.
        self.import_job: ImportJob | None = (
            ImportJob(
                ImportStore(transcripts.db), self.lookup.get, self.topic_ai, consent.consenting
            )
            if transcripts is not None and self.lookup is not None
            else None
        )
        self.tables: dict[int, Table] = {}
        # Channels of any campaign that must never be read as table chat, by server, with when
        # the answer runs out (monotonic seconds) (#1118).
        self._chat_blocked: dict[int, tuple[float, frozenset[int]]] = {}
        self.test_voice = test_voice  # the second yes, for test servers (#1019)
        # Who chose not to have their words used to improve DMbot (#1609): the recorder asks.
        self.improvement = improvement
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
        # Private-message rounds and the website's background work in flight.
        self._asking: set[asyncio.Task[None]] = set()
        # Loops that last as long as a session (#1128, #1129): kept apart from `_asking`, which
        # tests and `close()` wait on until it is empty.
        self._loops: set[asyncio.Task[None]] = set()
        self._finishing: set[asyncio.Task[None]] = set()  # stopped sessions winding down
        # The hours follow-up (warn, grace, stop) in flight, per server (#437).
        self._follow_ups: dict[int, asyncio.Task[None]] = {}
        # Stopped sessions still writing down their last words, by server.
        self._ending: dict[int, list[Table]] = {}
        self._starting: dict[int, Table] = {}  # sessions being resumed after a restart (#2466)
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
            owner_mail=self.email_hand_over_owner,  # the owner also hears by email (#2532)
            site_url=self.settings.site_url,
            # a hand-over to another server: its audio files go with it, and the caches that are
            # kept by campaign id alone are dropped
            mover=HandoverMover(
                campaigns,
                clock=time.time,
                consenting=lambda guild: consent.agreed(guild),  # who agreed there, as a restore
                audio=self.recap_audio_files,
                forgotten=self._forget_moved,
                busy=self._winding_down,
            ),
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
        log.info(features.features_line(self.settings))  # what is switched on (#1831)
        # DMbot's knowledge of itself (#1195): read its documents now, not on the first question.
        await asyncio.to_thread(knowledge.load)
        register_commands(self.tree)
        # DM-screen buttons keep working after a restart.
        self.add_dynamic_items(PeekButton, HideButton, VisibilityButton, StopListeningButton)
        self.add_dynamic_items(PauseTableButton, PauseTableForButton, ResumeTableButton)  # #2466
        self.add_dynamic_items(
            dm_bot_buttons.OpenButton, dm_bot_buttons.HelpButton, dm_bot_buttons.RuleButton
        )  # #1666, #1691
        self.add_dynamic_items(looked_at.LookedButton, looked_at.OpenCard)  # #1957
        self.add_dynamic_items(
            SettingsButton,
            LevelButton,
            QuietStopButton,
            SettingsVisibilityButton,
            VisibilityMenuButton,
            ManagementMenuButton,
            BackToSettingsButton,
            RuleLookupButton,
            RulesCardsButton,
            HouseFileButton,
            SharedRulesButton,
            HomebrewButton,
            InviteButton,
            TableChatButton,
            RecapsButton,
            RecapStyleButton,  # the one recap choice left in Discord (#2986)
            GameMastersButton,  # who the DMs are (#1600)
        )
        # Hand-over (#437): on ⚙️ Manage DMbot, in private messages, and after /dmbot start.
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
        self.add_dynamic_items(
            ReviewButton, PeopleReviewButton, ClaimsReviewButton, ContinuityButton
        )
        self.add_dynamic_items(
            ThreadsButton, ThreadListButton
        )  # the quiet story threads (#1662, #1675)
        self.add_dynamic_items(UndoNamingButton)  # Undo of giving an unnamed person a name (#2790)
        self.add_dynamic_items(LiveHeadsUpButton)  # the live heads-up's three answers (#2068)
        self.add_dynamic_items(KeepListeningButton)  # the automatic stop's warning (#2034)
        self.add_dynamic_items(HeardReviewButton)  # unanswered "Did they mean…?" (#1572)
        self.add_dynamic_items(SecretAloudButton)  # the secret-said-aloud question (#1407)
        self.add_dynamic_items(RevealButton)  # "Is it revealed now?" to the DM (#2088)
        self.add_dynamic_items(RecantButton)  # ↩️ Recant in the after-session summary (#2410)
        self.add_dynamic_items(SeenButton)  # 👍 Got it: the mark of every summary variant
        self.add_dynamic_items(KnowButton, WordsButton)  # Watch the story (#1729)
        self.add_dynamic_items(GuideButton)  # Guide me (#1731)
        self.add_dynamic_items(LineFixButton, FixLineButton)  # a player's line fix (#1491)
        self.add_dynamic_items(image_fix.FixTextButton)  # the words in a picture (#2450)
        self.add_dynamic_items(  # #965, #1117
            clock_screen.ClockButton, clock_screen.ClockUndoButton, clock_screen.SuggestionButton
        )
        self.add_dynamic_items(clock_screen.CombatUndoButton)  # #1127
        self.add_dynamic_items(time_suggest_screen.TimeJumpButton)  # #1758
        self.add_dynamic_items(import_time_screen.ImportTimeButton)  # #2979
        self.add_dynamic_items(clock_screen.ClockPicker, clock_screen.SplitUndoButton)  # #1607
        self.add_dynamic_items(delete_campaign.DeleteButton)  # #1608
        self.add_dynamic_items(fightbot.FightButton)  # #1129
        self.add_dynamic_items(timer_screen.EffectButton)  # timed effects (#998)
        # Undo after forgetting a name (its card), after a restart too.
        self.add_dynamic_items(
            UndoButton,
            UndoConnectionButton,
            UndoRevealedButton,
            UndoManyButton,
            UndoListButton,
            UndoSpellingButton,
            FinishPastButton,
            UndoRunButton,
        )
        # "Download transcript" in the private message when a session ends.
        self.add_dynamic_items(DownloadButton)
        self.add_dynamic_items(
            NameQuestionButton, NameAnswerUndoButton, FixUndoButton, SpeakerQuestionButton
        )
        self.add_dynamic_items(PutBackButton)  # lines left out as off-topic (#677)
        self.add_dynamic_items(set_update.SetUpdateButton)  # shared rules changed (#1191)
        # how long transcripts are kept (#1533); named one by one so the registration test sees them
        self.add_dynamic_items(TranscriptsButton, KeepChoiceButton, KeepYesButton, KeepNoButton)
        self.add_dynamic_items(ImportButton)  # 📥 Import sessions (#2154)
        import_button.use_switch(self.settings.imports_enabled_for)  # (hidden where off)
        self.add_dynamic_items(NamesButton)  # 📇 Names, the way into /dmbot names (#1986)
        self.add_dynamic_items(
            BackstoryButton
        )  # 📜 Upload campaign lore, from the Main Menu (#2380)
        self.add_dynamic_items(links_ready.ReadyButton)  # connections became ready (#2650)
        # the Shared story switch (#1692), named one by one so the registration test sees them
        self.add_dynamic_items(StoryButton, StoryChoiceButton, StoryYesButton, StoryNoButton)
        # giving DMbot an adventure (#1698), named one by one so the registration test sees them
        self.add_dynamic_items(
            AdventureButton, AdventureAddButton, RemoveAdventureButton,
            RemoveAdventureYesButton, RemoveAdventureNoButton,
            ReadAdventureButton, ReadAdventureYesButton, ReadAdventureNoButton,
            RefreshAdventureButton, SeeAdventureButton, BackToAdventureButton,
            MatchAdventureButton,
        )  # fmt: skip
        self.add_dynamic_items(pack_update.PackUpdateButton)  # a followed pack changed (#1527)
        self.add_dynamic_items(pack_suggestion.PackSuggestionButton)  # an entry suggested (#1537)
        self.add_dynamic_items(set_suggestion.SuggestionButton)  # a table's suggestion (#1259)
        self.add_dynamic_items(house_sync.HouseSyncButton)  # the house-rules file (#969)
        self.add_dynamic_items(rules_cards.RulesCardButton)  # rules cards from the table (#931)
        self.add_dynamic_items(house_voice_screen.HouseVoiceButton)  # house rules said aloud (#953)
        # A player's 📜 My character sheet, in their private messages (#723).
        self.add_dynamic_items(MySheetButton, OpenCardButton)  # #723, #1119
        self.add_dynamic_items(FeaturesButton)  # what is switched on (#1831)
        await recaps_discord.setup(self)  # the recap buttons (#2129)
        self.add_dynamic_items(name_pass_undo.UndoNamePassButton)  # Undo of the name pass (#2628)
        await self._sync_commands()
        await self.ears.start()
        self._background = [
            self._watched(self.pipeline.run(), "transcribe"),
            self._watched(self._idle_sweeper(), "idle-sweep"),
            self._watched(self._summary_poster(), "summaries"),
            self._watched(self._meter_loop(), "hours-meter"),
            self._watched(self._idle_loop(), "idle-stop"),  # #2034
            *([self._watched(self._trial_loop(), "trial-jobs")] if self.trial_jobs else []),
            *self.recap_tasks(),
            *(
                [self._watched(self._plot_loop(), "plot-checks")]
                if self.plot_runner is not None
                else []
            ),
            *(
                [self._watched(self._plot_talk_loop(), "plot-talk")]
                if self.plot_talk is not None
                else []
            ),
            *(
                [self._watched(self._import_loop(), "session-imports")]
                if self.import_job is not None
                else []
            ),
            *(
                [self._watched(self._retention_loop(), "retention")]
                if self.retention or self.keep_job
                else []
            ),
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
            *([self._watched(self._follow_npcs(), "npc-pages")] if self.memory is not None else []),
            asyncio.create_task(self._transcript_poster(), name="transcripts"),
            asyncio.create_task(self._transcript_saver(), name="transcript-saves"),
            self._watched(self.site_offers.follow(self.campaigns.listen), "site-offers"),
            self._watched(self.site_offers.every_hour(), "site-offer-sweeps"),
            self._watched(self.site_offers.follow_decided(self.campaigns.listen), "site-decisions"),
            self._watched(self.site_offers.follow_moves(self.campaigns.listen), "campaign-moves"),
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
        # Website lore uploads being read: each puts its claim back to waiting (the next start reads
        # it again) and is waited for here, so the write is not lost with the process (#3022).
        reads = list(getattr(self, "_lore_reads", {}).values())  # a test stub skips __init__
        reads += list(getattr(self, "_house_file_runs", {}).values())  # (#3087, the same way)
        for read in reads:
            read.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.gather(*reads, return_exceptions=True), 5)
        for reading in list(self.plan_reads.values()):
            reading.cancel()  # a read in progress is stopped; it carries on when a DM presses again
        if self._eleven_http is not None:
            with contextlib.suppress(Exception):
                await self._eleven_http.close()
        await self.close_meaning()
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
        # After the loops are cancelled, so a clip one of them started meanwhile is stopped too.
        clips = list(self._voice_tasks.values())
        for task in clips:
            task.cancel()
        await asyncio.gather(
            *self._background,
            *self._asking,
            *self._loops,
            *lookups,
            *clips,
            return_exceptions=True,
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
        already = self.consent.agreed_now(guild_id, user_id)  # (off the record is still agreed)
        recorded = await self.consent.grant(guild_id, user_id, method=method, outside_to=outside_to)
        log.info("Consent given: user %s", user_id)
        # Only from the cache: a person not in it is picked up at their next start or consent
        # (no member intent is asked for just for this).
        guild = self.get_guild(guild_id)
        member = guild.get_member(user_id) if guild is not None else None
        if member is not None:
            self.note_display_name(user_id, member.global_name or member.name)
            if user_id in recorded:  # and the import search can offer them (#2141)
                self.note_member(guild_id, member)
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

    def note_display_name(self, user_id: int, name: str | None) -> None:
        """Keep this person's Discord name on their account, at most once a day (#1242).
        Names only, for an account that exists. It runs in the background, so a consent or a
        start (both inside Discord's three seconds) never waits for the database on its
        account, and a consent that was just given is not re-read after an await."""
        if self.meter is None or not name:
            return
        now = time.monotonic()
        last = self._names_noted.get(user_id)
        if last is not None and now - last < DISPLAY_NAME_REFRESH_S:
            return
        if len(self._names_noted) >= NAMES_NOTED_MAX:  # bounded: the oldest half go
            for key in sorted(self._names_noted, key=self._names_noted.__getitem__)[
                : NAMES_NOTED_MAX // 2
            ]:
                del self._names_noted[key]
        self._names_noted[user_id] = now
        task = asyncio.create_task(self._keep_display_name(self.meter.db, user_id, name))
        self._name_tasks.add(task)
        task.add_done_callback(self._name_tasks.discard)

    def note_member(self, guild_id: int, member: discord.abc.User) -> None:
        """Keep a recorded member's names in the server's directory for the website's import
        search (#2141). Only call it for someone whose consent is current. In the background and
        never raising: a failure is retried the next time they are seen."""
        directory = self.members
        if directory is None:
            return
        task = asyncio.create_task(self._keep_member(directory, guild_id, member))
        self._name_tasks.add(task)
        task.add_done_callback(self._name_tasks.discard)

    async def _keep_member(
        self, directory: MemberDirectory, guild_id: int, member: discord.abc.User
    ) -> None:
        try:
            async with asyncio.timeout(DISPLAY_NAME_TIMEOUT_S):
                await directory.remember(
                    guild_id, member.id, getattr(member, "global_name", None), member.name
                )
        except Exception as exc:  # the type only: a database error's text can quote a name
            log.warning("Could not keep a member in the directory: %s", type(exc).__name__)

    async def _keep_display_name(self, db: Database, user_id: int, name: str) -> None:
        """The background part: one short UPDATE. Never raises; on a failure the person is
        tried again the next time they are seen."""
        try:
            async with asyncio.timeout(DISPLAY_NAME_TIMEOUT_S):
                await accounts.remember_display_name(db, user_id, name)
        except Exception:
            self._names_noted.pop(user_id, None)  # try again next time
            log.warning("Could not keep a Discord name on an account", exc_info=True)

    async def recorded(self, guild_id: int, user_id: int) -> bool:
        """Whether they may be recorded in this server now, for the 🗂️ Menu (Stop or I
        consent), Keep recording and /consent revoke. Quick, so Discord's 3 seconds hold:
        the cache first (exactly who is captured now); the database only when the cache
        says no (a fresh process may not have loaded this server), and only briefly. Unsure
        counts as recorded, so the way to stop is never hidden."""
        if self.consent.has_consent(guild_id, user_id) or self.consent.agreed_now(
            guild_id, user_id
        ):
            return True  # (off the record is still recorded-with-consent: the Menu offers Resume)
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

    async def _save_pauses(self, guild_id: int, paused: dict[str, Any]) -> None:
        await self.sessions.save_pauses(guild_id, paused)

    def table_is_paused(self, guild_id: int) -> bool:
        return self.consent.table_paused(guild_id)

    async def _say_off_the_record(self, guild_id: int, text: str) -> bool:
        """One plain line in the table's DMbot chat, so nobody guesses who is recorded."""
        table = self.tables.get(guild_id)
        return table is not None and await self.sidebar_post_chat(table, text)

    def _off_the_record_name(self, guild_id: int, user_id: int) -> str:
        guild = self.get_guild(guild_id)
        member = guild.get_member(user_id) if guild is not None else None
        return member.display_name if member is not None else "Someone"

    def _drop_in_flight(self, guild_id: int, user_id: int | None) -> None:
        """A pause starts: the words being said right now, and the ones not posted yet, are
        thrown away, so the moment before the button is not kept either. Lines already in the
        transcript stay (they were recorded while the person was on the record); words already
        held back for a question or a spelling are not used while they are off it, because every
        use checks consent again."""
        table = self.tables.get(guild_id)
        if table is None:
            return
        people = [user_id] if user_id is not None else list(self.consent.agreed_set(guild_id))
        for person in people:
            table.segmenter.drop(person)
            table.transcript.drop_speaker(person)

    def _off_record_gap(self, guild_id: int, user_id: int, began: float, ended: float) -> None:
        """A stretch off the record is over: its marker goes in the session's transcript."""
        # (a session being resumed after a restart is not in `tables` yet: `_starting` has it)
        table = self.tables.get(guild_id) or self._starting.get(guild_id)
        if table is not None:
            self._add_gaps(table, [(user_id, began, ended)])

    def _add_gaps(self, table: Table, gaps: Sequence[tuple[int, float, float]]) -> None:
        """One marker line for each person for each stretch (0: the table's pause, for everyone
        who agreed). Under a second is not worth a line. There is no speech in it: nothing was
        captured."""
        if self.transcripts is None:
            return
        for who, began, ended in gaps:
            length = int((ended - began) * 1000)
            if length < 1000:
                continue
            people = self._at_the_table(table) if who == 0 else [who]
            for person in people:
                table.unsaved.add(
                    Line(
                        int(began * 1000),
                        person,
                        OFF_RECORD_WORDS,
                        OFF_RECORD_WORDS,
                        length,
                        sidebar=OFF_RECORD,
                    )
                )

    def _at_the_table(self, table: Table) -> list[int]:
        """Who was in the voice channel and agreed, for the marker of the table's break: not
        everyone in the server who ever agreed (the transcript is open to the whole server)."""
        channel = self.get_channel(table.voice_channel_id)
        here = {m.id for m in getattr(channel, "members", ())}
        return sorted(here & self.consent.agreed_set(table.guild_id))

    def _wake_off_the_record(self, guild_id: int, user_id: int) -> None:
        """ "Hey DMbot, off the record", said or typed: this person only, for five minutes. The
        line itself is kept out of every transcript by the caller. They are off the record in core
        at once, with no await, so a line that arrives a moment later is already dropped; the
        rest (saving, ears, the table's line, their private note) follows."""
        refused = self.off_the_record.start_pause(
            guild_id, user_id, WAKE_MINUTES, never_shorten=True
        )
        if refused is not None:
            return  # not recorded, not listening, or already off the record for longer
        self._track(self._pause_by_phrase(guild_id, user_id), "off-the-record")

    async def _pause_by_phrase(self, guild_id: int, user_id: int) -> None:
        with log_context(guild_id=guild_id):
            done = await self.off_the_record.announce_pause(
                guild_id, user_id, WAKE_MINUTES, said_it=True
            )
            text = (
                paused_text(done.until)
                + " What you said just now was not saved."
                + ("" if done.told else NOT_TOLD)
            )
            await self._dm_message(user_id, text, resume_view(guild_id))

    async def pause_recording(self, guild_id: int, user_id: int, minutes: int | None) -> Paused:
        return await self.off_the_record.pause(guild_id, user_id, minutes)

    async def resume_recording(self, guild_id: int, user_id: int) -> bool:
        return await self.off_the_record.resume(guild_id, user_id)

    def is_paused(self, guild_id: int, user_id: int) -> bool:
        return self.consent.is_paused(guild_id, user_id)

    def stop_recording(self, guild_id: int, user_id: int) -> None:
        """Stop capturing this player now, without waiting for anything.

        Callers run this before their first await; `withdraw_consent` repeats it.
        """
        self.consent.stop_now(guild_id, user_id)
        self.off_the_record.forget(guild_id, user_id)  # a stopped person's pause timer goes too
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
            table.heard_forms = {k: v for k, v in table.heard_forms.items() if k[3] != user_id}
            table.held_spelled = {k: v for k, v in table.held_spelled.items() if k[0] != user_id}
            table.spelled = [
                x for x in table.spelled if x[1] != user_id
            ]  # and nothing they spelled
            table.spelling.forget(user_id)  # nor the words they said just before
            table.pronounced = {k: v for k, v in table.pronounced.items() if k[2] != user_id}
            table.pronouncing.forget(user_id)  # nor a name they had just said, for the next sound
            table.held_pronounced = {
                k: v for k, v in table.held_pronounced.items() if k[0] != user_id
            }
            table.spelled_hints = tuple(w for w, _, _ in table.spelled)[-SPELLED_MAX:]  # nor hints
            table.unsaved.drop_speaker(user_id)  # and never saved
            table.recent.drop_speaker(user_id)  # and never part of a sidebar answer's scene
            table.sheet_watch.drop_speaker(user_id)  # no sheet is read again for what they said
            table.sheet_questions.drop_speaker(user_id)  # nor asked about it (#1128)
            table.scene.forget_speaker(user_id)  # and no longer shape the hints
            table.vocabulary.forget_speaker(user_id)  # or the name fixes
            table.last_piece.pop(user_id, None)  # and the look-back pass
            table.piece_words = {k: v for k, v in table.piece_words.items() if k[0] != user_id}
            if table.fix_notes.drop_speaker(user_id):  # their fixes leave the DM screen
                self._redraw_fix_notes(table)
            if table.left_out.drop_speaker(user_id):  # and their lines left out (#677)
                self._redraw_left_out(table)
            table.unanswered = [a for a in table.unanswered if a.speaker != user_id]  # #1572
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
        if self.improvement is None:
            # A path that can't honour the improvement opt-out must not run (#1609).
            log.error("Test recordings need the improvement opt-out store; not saving")
            return
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
                    "story_extractor_on": self.settings.story_extractor_on,
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
        # The improvement opt-out (#1609): a person who chose it is not saved, and what was
        # saved of them earlier in this session goes. Read now (a few seconds old at most). If the
        # choice can't be read nothing is saved: better a gap than their words against their
        # wish.
        if self.improvement is None:  # (no session starts without it; belt and braces)
            return
        try:
            out = await self.improvement.is_opted_out_recently(user_id)
        except Exception:
            log.exception("Couldn't read the improvement opt-out; not saving a test recording")
            return
        if out:
            if session.has(user_id):
                session.forget(user_id)
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

    async def _purge_opted_out(self, session: TestSession) -> None:
        """When a test session ends (#1609): read the improvement choice again, fresh, for
        everyone saved in it, and drop anyone who opted out meanwhile, even if they never spoke
        again. A choice that can't be read drops that person: better a gap than their words
        against their wish."""
        for user_id in session.people():
            try:
                out = self.improvement is None or await self.improvement.is_opted_out(user_id)
            except Exception:
                log.exception("Couldn't read the improvement opt-out; dropping a saved speaker")
                out = True
            if out:
                session.forget(user_id)

    async def _finish_test_session(self, table: Table, caught_up: bool) -> None:
        if table.test_session is None:
            return
        try:
            await self._purge_opted_out(table.test_session)
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
        was_recorded = self.consent.agreed_now(guild_id, user_id)  # (a paused person is told too)
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
            if self.heard_questions is not None:  # and the questions kept about their words (#1572)
                try:
                    async with asyncio.timeout(FORGET_TIMEOUT_S):
                        await self.heard_questions.forget_speaker(guild_id, user_id)
                except Exception:
                    log.exception("Could not remove the waiting questions of a stopped speaker")
            try:  # and what the story extractor proposed from their words (#1297)
                async with asyncio.timeout(FORGET_TIMEOUT_S):
                    await ClaimStore(self.memory).forget_speaker(guild_id, user_id)
            except Exception:
                log.exception("Could not remove the story claims of a stopped speaker")
        try:
            return await self.consent.revoke(guild_id, user_id)
        finally:
            if self.members is not None:  # no longer offered by name in the import search (#2141)
                try:
                    async with asyncio.timeout(FORGET_TIMEOUT_S):
                        await self.members.forget(guild_id, user_id)
                except Exception as exc:  # the type only: a database error can quote a row
                    log.warning(
                        "Could not remove a stopped speaker from the member directory: %s",
                        type(exc).__name__,
                    )
            # Again, in case a grant that was saving meanwhile sent ears an older list.
            with contextlib.suppress(Exception):
                await self.push_allowlist(guild_id)

    async def count_my_lines(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """(lines, sessions) this person has in this server's transcripts, for the warning
        before "Delete my past transcripts" (#1532)."""
        if self.transcripts is None:
            return 0, 0
        counted = await self.transcripts.person_counts(guild_id, user_id)
        return counted.lines, counted.sessions

    async def delete_my_lines(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """Delete my past transcripts (#1532): this person's words from every transcript in this
        server, and what exists only because of them, in one transaction (see
        `TranscriptStore.delete_person`). Raises if it failed, and then nothing is deleted.
        Returns (lines, sessions) removed. They are then taken out of any alias's "who uses it"
        (best effort; the next press tries it again)."""
        if self.transcripts is None:
            return 0, 0
        tables = [t for t in (self.tables.get(guild_id), *self._ending.get(guild_id, [])) if t]
        async with contextlib.AsyncExitStack() as stack:
            # A running session's save is held while this runs, and what this person has said
            # and not yet saved is dropped, so a batch can't write their lines back afterwards.
            for table in tables:
                await stack.enter_async_context(table.save_lock)
                table.unsaved.drop_speaker(user_id)
            gone = await self.transcripts.delete_person(guild_id, user_id)
        log.info("Deleted past transcripts: %s lines in %s sessions", gone.lines, gone.sessions)
        if self.memory is not None:
            try:  # after the lines: a failure here costs only this tidy-up, never the delete
                await self.memory.forget_alias_user(guild_id, user_id)
            except Exception:
                log.exception("Couldn't take a deleted person out of an alias's user field")
        return gone.lines, gone.sessions

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

    def track(self, work: Awaitable[Any], name: str) -> asyncio.Task[None] | None:
        """`_track` for code outside the bot class (a button's own follow-up work)."""
        return self._track(work, name)

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
        began = time.monotonic()  # for the timing line (#1706): numbers only
        with log_context(guild_id=gid, campaign_id=table.campaign_id):
            try:
                status = await self.consent.status(gid, [m.id for m in people])
            except Exception:
                log.exception("Couldn't look up consent; they'll be asked when they rejoin")
                table.asked.difference_update(m.id for m in people)
                return
            status_ms = _ms(time.monotonic() - began)
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
            send_ms: list[int] = []  # how long each private message took to go
            reminders = 0
            for member in people:
                if self.tables.get(gid) is not table:
                    return  # the session ended: say nothing more
                # Re-checked now, after the awaits above: a revoke may have landed.
                granted = times.get(member.id)
                server = member.guild.name
                if granted is not None and self.consent.has_consent(gid, member.id):
                    self.note_member(gid, member)  # refreshed at each session start (#2141)
                    text = reminder_text(
                        server,
                        voice_name,
                        granted,
                        cloud=cloud,
                        company=company,
                        sheets=self.sheets is not None,
                    )
                    view = menu_view(gid, table.campaign_id, invite=table.dndbeyond_invite)
                    reminders += 1
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
                sending = time.monotonic()
                result = await send_prompt(member, text, view)
                send_ms.append(_ms(time.monotonic() - sending))
                if result != "sent":
                    table.asked.discard(member.id)  # try again if they rejoin
                    (dms_off if result == "dms_off" else failed).append(member.display_name)
            log.info(
                consent_timing_line(
                    len(people),
                    reminders,
                    status_ms,
                    send_ms,
                    _ms(time.time() - table.started_at) if table.started_at else None,
                )
            )
            renewed = [m.display_name for m in people if m.id in renew]
            notes = [n for n in (renewed_text(renewed), unreachable_text(dms_off, failed)) if n]
            if notes and self.tables.get(gid) is table:
                await self.post(table.screen_channel_id, "\n".join(notes))

    async def _post_listening(self, table: Table, text: str) -> None:
        """The DM screen's "listening" message, with a Stop listening button (#108). The
        button comes off when the session ends, so old messages can't be pressed."""
        view = None
        if table.campaign_id:
            # (Settings moved to the DM Bot channel, #1666: only the in-game control stays.)
            view = discord.ui.View(timeout=None)
            view.add_item(PauseTableButton(table.campaign_id))  # off the record (#2466)
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
            off = self.consent.agreed_now(member.guild.id, member.id) and not recorded
            text = (
                screen_messages.joined_recorded_message(member.display_name)
                if recorded
                else screen_messages.joined_off_the_record_message(member.display_name)
                if off
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
        self._track(self._pre_session_note(table), "pre-session-note")  # #1403
        self._track(self._waiting_recaps(table), "waiting-recaps")  # #1474
        self._track(self._expire_short_asks(table), "expire-short-asks")  # #2104
        self._track(self._waiting_recap_names(table), "waiting-recap-names")  # #2027
        self._track(self._waiting_suggestions(table), "waiting-suggestions")  # #1504
        table.watch_task = self._track_loop(sheet_reread.run(self, table), "sheet-watch")  # #1128
        table.fight_task = self._track_loop(fightbot.run(self, table), "fightbot")  # #1129
        if table.where_task is not None:  # started again for the same table: one loop only
            table.where_task.cancel()
        table.where_task = self._track_loop(where_are_we.run(self, table), "where-are-we")  # #1738
        return sent

    async def _pre_session_note(self, table: Table) -> None:
        """A session starts: a few lines on the DM screen from what the DM confirmed (#1403) —
        the last recap, what is open about the names heard last time, what was lost or ended, and
        how many things said at the table are waiting to be checked. DM screen only; no AI call.
        Once a session (not again after a restart), for a campaign whose plan includes story
        memory (closed if that can't be checked, like the extractor), with the switch on, and
        only when something is confirmed. A failure costs the note, never the session."""
        if (
            not self.settings.presession_note
            or self.memory is None
            or table.campaign_id is None
            or table.resumed
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            try:
                campaign = await self.campaigns.get(gid, cid)
                if campaign is None or not await self.plan_allows(
                    "story", gid, campaign, fail_open=False
                ):
                    return
                agreed = await self.consent.consenting(gid)
                presession = PreSession(
                    self.memory, RecallService(self.memory), ClaimStore(self.memory), self.recaps
                )
                async with asyncio.timeout(PRESESSION_NOTE_WAIT_S):  # never a stuck connection
                    note = await presession.note(
                        gid,
                        cid,
                        now=int(time.time()),
                        opted_in=agreed,
                        stalled_after=self.settings.thread_stalled_sessions,
                    )
                if note is None or self.tables.get(gid) is not table:
                    return  # nothing confirmed, or the session ended meanwhile
                view = claims_review_view(cid) if note.waiting else None
                if note.quiet:  # 🧵 Threads: the DM's list, in play first (#1675)
                    view = view or discord.ui.View(timeout=None)
                    view.add_item(ThreadListButton(cid))
                if await self.guide_button_allowed(gid, campaign):  # 🧭 What's next? (#1731)
                    view = view or discord.ui.View(timeout=None)
                    view.add_item(GuideButton(cid))
                await self.post(table.screen_channel_id, note.text, view=view)
                log.info("Pre-session note: %d line(s)", len(note.lines))  # counts only
            except Exception:
                log.exception("The pre-session note failed")

    async def guide_button_allowed(self, guild_id: int, campaign: Campaign) -> bool:
        """May the pre-session note carry 🧭 What's next? (#1731)? The same three checks a press
        makes: the switch is on **Guide me**, a DM said here they have the right (a confirmation
        from a backup is not one), and the owner's plan allows it (closed, like the reader)."""
        if campaign.story_mode != STORY_GUIDE:
            return False
        try:
            return bool(
                await self.campaigns.story_confirmed(guild_id, campaign.id)
                and await self.plan_allows("adventure", guild_id, campaign, fail_open=False)
            )
        except Exception:  # a read that failed costs the button, never the whole note
            log.exception("Couldn't check the Guide me button")
            return False

    async def _waiting_suggestions(self, table: Table) -> None:
        """A session starts and players' suggested line fixes from before still wait for the DM
        (#1504): the DM screen shows the oldest three again, as the same line with the same two
        buttons, and says how many more there are. Otherwise they would scroll away and be
        dropped unseen. Not after a restart in the middle of a session, nothing for an answered
        or dropped one or for a speaker who is no longer recorded, and nothing to the players.
        A failure costs the lines, never the session."""
        if self.suggestions is None or table.campaign_id is None or table.resumed:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            try:
                waiting = [
                    w
                    for w in await self.suggestions.waiting(gid, cid)
                    if self.consent.has_consent(gid, w.user_id)
                ]
                guild = self.get_guild(gid)
                for w in waiting[:WAITING_SHOWN]:
                    if self.tables.get(gid) is not table:
                        return  # the session ended meanwhile
                    if not self.consent.has_consent(gid, w.user_id):
                        continue  # stopped while the lines were going up
                    member = guild.get_member(w.user_id) if guild is not None else None
                    who = member.display_name if member is not None else "A player"
                    await self.post_message(
                        table.screen_channel_id,
                        dm_text(who, w.heard, w.text, w.suggested),
                        suggestion_view(w.id),
                        remember=("fix", w.id),
                    )
                more = len(waiting) - WAITING_SHOWN
                if more > 0 and self.tables.get(gid) is table:
                    await self.post_message(table.screen_channel_id, MORE_WAITING.format(n=more))
            except Exception:
                log.exception("The waiting-suggestions lines failed")

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
        # Every pause ends with the session (#2466); the stretches still on are marked in its
        # transcript, which is saved after this.
        self._add_gaps(table, self.off_the_record.end_session(guild_id))
        with log_context(guild_id=guild_id, campaign_id=table.campaign_id):
            log.info("Session ended: %s", reason)
        if table.campaign_id:  # the end of a session is when the most changed (#2265)
            self._npcs_changed(table.campaign_id)
        if table.sheet_task is not None:  # no more reading sheets for it (#723)
            table.sheet_task.cancel()
        if table.watch_task is not None:  # nor re-reading them (#1128)
            table.watch_task.cancel()
        if table.fight_task is not None:  # nor reminding players (#1129)
            table.fight_task.cancel()
        if table.where_task is not None:  # nor saying where the game is (#1738)
            table.where_task.cancel()
        await self.ears.send(leave_command(guild_id))
        # Speech still being heard or written down is finished, not dropped (#109): the
        # session stays "ending" until the pipeline has caught up.
        self._ending.setdefault(guild_id, []).append(table)
        # An unanswered "Did they mean…?" ends too (an answer being saved still finishes).
        if not table.questions.answering:
            ended = table.questions.close(name_questions.ENDED, time.monotonic())
            if ended is not None:
                self._keep_unanswered(table, ended)
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
                if table.time_suggest.calls:  # counts only, never words (#1758)
                    log.info(
                        "Time suggestions: %d calls, %d tokens in, %d out",
                        table.time_suggest.calls,
                        table.time_suggest.tokens_in,
                        table.time_suggest.tokens_out,
                    )
                if table.rank_calls:  # counts only, never a line or a name (#2048)
                    log.info(
                        "Did-they-mean ranking: %d calls, %d likely, %d none, %d errors; "
                        "the DM picked the likely name in %d of %d answers",
                        table.rank_calls,
                        table.rank_likely,
                        table.rank_none,
                        table.rank_errors,
                        table.rank_picked,
                        table.rank_answered,
                    )
                if table.topic_calls:  # for cost per session hour (#52)
                    log.info(
                        "Off-topic filter: %d calls, %d tokens in, %d out",
                        table.topic_calls,
                        *table.topic_tokens,
                    )
                await self._after_session(table, ended_at, caught_up)
                await self._finish_test_session(table, caught_up)
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
            # After the stored transcript: its lines say who took part (#3036).
            ("the party", lambda: self.link_the_party(table)),
            ("stop button", lambda: self._remove_stop_button(table)),
            ("capture check", lambda: self.post_summary(table)),
            ("final transcript", channel),
            ("summary", lambda: self.post_session_summary(table, ended_at, caught_up, sent)),
            ("names heard", lambda: self.keep_heard_names(table)),
            ("name scan", lambda: self.suggest_names(table)),
            # After the scan: a spelling can belong to a name the scan just suggested (#2168).
            ("spelled names", lambda: self.suggest_spelled(table)),
            # What a person said aloud beats the cleaner's indirect signal: the first offer for a
            # name stays (#2171).
            ("said aloud", lambda: self.suggest_pronounced(table)),
            ("say it this way", lambda: self.suggest_said_as(table)),
            ("close calls", lambda: self.log_close_calls(table)),
            ("who is who", lambda: self.learn_people(table)),
            ("unanswered questions", lambda: self.save_unanswered(table)),
            ("people check", lambda: self.check_people(table)),
            ("definitions", lambda: self.recheck_definitions(table)),
            ("stale flags", lambda: self.close_stale_flags(table)),
            ("old undo history", lambda: self.prune_old_changes(table)),
            # The names' importance for the recap's beats (#2411), from the story as it is now.
            ("name importance", lambda: self.refresh_importance(table)),
            # Queues or holds the recap (#2169): held at once while the extractor is still to run.
            ("session recap", lambda: self.recap_session(table)),
            # Slow when on (minutes of AI calls): after the quick steps; the steps after it need it.
            ("story extractor", lambda: self.extract_claims(table, ended_at)),
            # After the extractor (its readings are beliefs too); off unless the switch is on.
            ("contradiction grades", lambda: self.grade_contradictions(table)),
            # The report for the website (#2661): reuses those grades, then sorts the differences.
            ("after-session report", lambda: self.make_report(table)),
            # The names the extractor proposed are name questions too (#2169): count again.
            ("recap names", lambda: self.recheck_recap_hold(table)),
            ("what was said", lambda: self.note_claims(table)),
            ("names said at the table", lambda: self.reveal_names_said(table)),
            # (replaces the secret-name question: the summary says what was shown, with Recant)
            ("secret exposures", lambda: self.record_exposures(table)),
            ("secret summary", lambda: self.send_exposure_summary(table)),
            ("new names", lambda: self.post_new_names(table)),
            ("new connections", lambda: self.post_claims(table)),
            ("family links", lambda: self.warn_about_family(table)),
            # The questions above go out as one private message (#3210).
            ("questions for the DM", lambda: self.send_collected_asks(table)),
            ("heads-ups", lambda: self.warn_about_continuity(table)),
            # The biggest clashes PlotBot graded, up to three; no AI call here (#2660)
            ("big clashes", lambda: self.tell_contradictions(table)),
            ("quiet threads", lambda: self.warn_about_threads(table)),
            (
                "story watch",
                lambda: self.watch_story(
                    table.guild_id, table.campaign_id, table.screen_channel_id
                ),
            ),
            ("steward numbers", lambda: self.log_steward_tally(table)),
            ("memory health", lambda: self.log_memory_health(table, ended_at)),
            # Last, on purpose: up to `MEANING_RUN_CALLS` calls to another company, and nothing
            # above reads the numbers, so a slow or failing call never delays what players see
            # (#2664). Off unless the switch is on and the key is set.
            ("meaning search numbers", lambda: self.embed_campaign(table)),
        ]
        table.asks = []  # the steps hand their questions over; one step sends them (#3210)
        try:
            for name, step in steps:
                try:
                    await step()
                except Exception:
                    log.exception("After the session: %s failed", name)
        finally:
            leftover, table.asks = table.asks or [], None
            if leftover:  # the sending step itself failed: still ask, so nothing is lost
                try:
                    await self.send_asks(table, leftover)
                except Exception:
                    log.exception("After the session: the questions could not be sent")

    async def refresh_importance(self, table: Table) -> None:
        """After a session (#2411): read how connected each name is again, so the recap that is
        about to be written ranks its beats by the story as it is now."""
        if self.importance is not None and table.campaign_id is not None:
            await self.importance.refresh(table.guild_id, table.campaign_id)

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
        session_id = table.transcript_session_id
        suggested = waiting = 0
        if session_id is not None and self.suggestions is not None:
            try:
                suggested, waiting = await self.suggestions.session_counts(gid, session_id)
            except Exception:
                log.exception("Couldn't count the session's suggested fixes")
        edited = sum(
            1 for n in table.fix_notes.notes if n.fix.how in (DM_EDIT, IMAGE_EDIT) and not n.undone
        )
        unanswered = sum(
            1
            for why in table.questions.closed_why.values()
            if why in (name_questions.EXPIRED_WHY, name_questions.ENDED)
        )
        text = screen_messages.session_summary(
            table.campaign_name,
            table.started_at or ended_at,
            ended_at,
            spoke,
            problems,
            downloads_sent=downloads_sent,
            names=screen_messages.names_line(
                table.names_fixed,
                table.names_asked,
                unanswered,
                edited,
                suggested,
                waiting,
            ),
        )
        if session_id is not None and self.suggestions is not None:
            # With ✏️ Fix a line (#1499): the DM can correct any line, from the same picker.
            await self.post_message(
                table.screen_channel_id, text, fix_line_view(gid, session_id, dm=True)
            )
            return
        await self.post(table.screen_channel_id, text)

    # ---- sessions (used by /dmbot start · stop · help) ---------------------

    def _campaign_has_a_table(self, guild_id: int, campaign_id: str) -> bool:
        """Is a table of this campaign running here, or still finishing? The names cache keeps
        such a copy however long it has sat unused (#1883): the next clip asks for it at once."""
        tables = [self.tables.get(guild_id), *self._ending.get(guild_id, [])]
        return any(t is not None and t.campaign_id == campaign_id for t in tables)

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
        date (#964), and delete the transcripts older than a campaign's own limit (#1533). Safe to
        run twice; a failed run is logged and tried again tomorrow."""
        state_dir = self.settings.data_dir
        # A bot that was down at 03:00 would skip that day: if the last run was over a day
        # ago, run once shortly after start-up (#989).
        if retention.catch_up_due(retention.read_last_run(state_dir), int(time.time())):
            await self.wait_until_ready()
            await asyncio.sleep(retention.CATCH_UP_DELAY_S)
            await self._retention_once(state_dir)
        while True:
            now = int(time.time())
            await asyncio.sleep(max(60, retention.next_run_after(now) - now))
            await self._retention_once(state_dir)

    async def _retention_once(self, state_dir: Path) -> None:
        """The plan job and the transcript job are independent: one failing never stops the
        other. The run counts as done (for the catch-up check) when the plan job ran, or when
        there is no plan job."""
        ran = self.retention is None
        if self.retention is not None:
            try:
                await self.retention.run_once(int(time.time()))
                ran = True
            except Exception:
                log.exception("The daily retention job failed")
        if self.keep_job is not None:  # a campaign's own limit (#1533)
            try:
                await self.keep_job.run_once(int(time.time()))
            except Exception:
                log.exception("The daily keep-transcripts job failed")
        if self._npcs_roster is not None:  # notes on names unlisted for 30 days (#2265)
            try:
                await self._npcs_roster.sweep(
                    await self.campaigns.guilds_with_campaigns(), int(time.time())
                )
            except Exception:
                log.exception("The daily NPC-notes clean-up failed")
        if self.sources is not None:  # pages nothing uses any more (#1646)
            try:
                await self.sources.sweep_pages(await self.campaigns.guilds_with_campaigns())
            except Exception:
                log.exception("The daily homebrew-page clean-up failed")
        if not ran:
            return
        retention.write_last_run(state_dir, int(time.time()))
        try:
            await self.check_owners_daily()
        except Exception:
            log.exception("The daily owner check failed")

    async def _post_to_screen(self, campaign: Campaign, text: str) -> bool:
        """Say something on a campaign's DM screen (for the daily jobs); False if it has none
        or the post failed."""
        if campaign.dm_screen_channel_id is None:
            return False
        return await self.post(campaign.dm_screen_channel_id, text)

    async def _tell_admin(self, user_id: int, text: str) -> None:
        """A private message to one of DMbot's admins (#972). Raises if it can't be sent, so
        the watch tries again later and doesn't count it as told."""
        user = self.get_user(user_id) or await self.fetch_user(user_id)
        await user.send(text, allowed_mentions=discord.AllowedMentions.none())

    async def _tell_app_owner(self, text: str) -> None:
        """A private message to whoever owns the Discord application (a team's owner for a
        team app). Raises if it can't be sent; the alerter logs that."""
        info = await self.application_info()
        # By id, as a plain user: a team member can't be sent to directly.
        owner_id = info.team.owner_id if info.team is not None and info.team.owner_id else None
        user = self.get_user(owner_id or info.owner.id) or await self.fetch_user(
            owner_id or info.owner.id
        )
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

    async def check_owner_present(
        self, campaign: Campaign, *, wait: float = OWNER_LOOK_TIMEOUT_S, force: bool = False
    ) -> Campaign:
        """Is the campaign's owner still in the server (#1214)? Asked only when DMbot already
        has a reason to look: a start, opening Settings, and the daily job. Never raises; the
        campaign as it is now comes back (ownerless if it just became so)."""
        owner = campaign.owner_user_id
        guild = self.get_guild(campaign.guild_id)
        if owner is None or guild is None:
            return campaign
        gid, cid = campaign.guild_id, campaign.id
        cached = guild.get_member(owner) is not None  # no call to Discord at all
        if not cached and not force:
            asked = self._owner_asked.get(cid)
            if asked is not None and time.monotonic() - asked < OWNER_ASK_EVERY_S:
                return campaign  # asked lately: a start never waits on Discord for this
        try:
            if cached:
                seen = owner_presence.HERE
            else:
                self._owner_asked[cid] = time.monotonic()
                async with asyncio.timeout(wait):  # only the asking of Discord
                    seen = await owner_presence.look(guild.fetch_member, self.fetch_user, owner)
            now = int(time.time())
            step = owner_presence.decide(campaign.owner_missing_since, seen, now)
            if step.action == owner_presence.MARK:
                await self.campaigns.note_owner_presence(gid, cid, owner, missing_since=now)
            elif step.action == owner_presence.CLEAR:
                await self.campaigns.note_owner_presence(gid, cid, owner, missing_since=None)
            elif step.action == owner_presence.ORPHAN and await self.campaigns.orphan_missing_owner(
                gid,
                cid,
                owner,
                now,
                still_missing_since=campaign.owner_missing_since,
                any_state=seen == owner_presence.ACCOUNT_DELETED,
            ):
                log.info("A campaign's owner is gone from Discord; it is ownerless now")
                for dm in sorted(campaign.dm_user_ids - {owner}):  # the owner can't read it
                    await self._dm_user(dm, f"**{campaign.name}**: {owner_presence.TELL_DMS}")
            return await self.campaigns.get(gid, cid) or campaign
        except Exception:
            log.exception("Couldn't check whether a campaign's owner is still here")
            return campaign

    async def check_owners_daily(self) -> None:
        """Once a day, with the retention job: look at the owner of each campaign played in
        the last 120 days, one at a time (#1214)."""
        now = int(time.time())
        for guild_id in await self.campaigns.guilds_with_campaigns():
            if self.get_guild(guild_id) is None:
                continue
            for campaign in await self.campaigns.list_campaigns(guild_id):
                if (
                    campaign.owner_user_id is None
                    or campaign.last_active_at < now - OWNER_CHECK_DAYS * 86400
                ):
                    continue
                await self.check_owner_present(campaign, force=True)
                await asyncio.sleep(OWNER_CHECK_GAP_S)  # one Discord call at a time, spaced

    async def plan_refusal(self, guild_id: int, campaign: Campaign, starter_id: int) -> str | None:
        """Why the campaign's owner's plan, hours or campaign count don't allow a start, in
        plain words for the person starting it, or None (#437). A paused campaign is refused
        whatever the plan; the rest only when DMBOT_ENFORCE_PLANS is on. Fails open: a
        database hiccup must never lock a table out of its game."""
        # A gone owner gets Take it on (#1214); at a start, a cached owner costs nothing and
        # Discord is asked at most once an hour for a campaign, for at most 2 seconds.
        campaign = await self.check_owner_present(campaign, wait=OWNER_START_WAIT_S)
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

    async def all_house_rules(self, guild_id: int, campaign_id: str) -> list[HouseRule]:
        """The campaign's own house rules, then any set it follows (#1190): own rules first,
        as the precedence in CLAUDE.md says. Every advisor reads rules through here, so a
        followed rule is always cited with its set."""
        own: list[HouseRule] = []
        if self.house_rules is not None:
            own = await self.house_rules.list(guild_id, campaign_id)
        return own_first(own, await followed_rules(self.rule_sets, guild_id, campaign_id))

    def _make_sidebar_answers(self) -> Sidebar | None:
        if self.ai_client is None:
            return None

        async def gate(campaign: Campaign, user_id: int) -> str | None:
            return await self.plan_gate("ai", campaign.guild_id, campaign, user_id)

        async def houses(campaign: Campaign) -> Sequence[HouseRule]:
            return await self.all_house_rules(campaign.guild_id, campaign.id)

        async def brews(campaign: Campaign) -> Sequence[Homebrew]:
            # The campaign's own entries that are on (the reader leaves out those the DM turned
            # off, #1629), and the followed packs' (#1520).
            if self.homebrew_reader is None:
                return []
            return await self.homebrew_reader.list(campaign.guild_id, campaign.id)

        async def names(campaign: Campaign) -> CampaignLookup | None:
            if self.lookup is None:
                return None
            return await self.lookup.get_within(campaign.guild_id, campaign.id, NAMES_WAIT_S)

        async def people(campaign: Campaign) -> list[str]:
            # Optional context: a slow database must not eat the answer's time (#1188).
            async with asyncio.timeout(NAMES_WAIT_S):
                return await self.sidebar_people(campaign)

        async def settled(campaign: Campaign) -> Sequence[Win]:
            if self.house_wins is None:
                return []
            return await self.house_wins.all(campaign.guild_id, campaign.id)

        recall_service = RecallService(self.memory) if self.memory is not None else None

        async def recall(campaign: Campaign, seeds: Sequence[str]) -> Recall | None:
            # The audience is "players", not the DM screen, on purpose (#1370): a sidebar reply can
            # reach the raw transcript, which everyone who may read transcripts may read (#933).
            # So recall adds no secret fact, no secret name and no entry note. (The older "campaign
            # names" section is separate and unchanged.) Changing this later is a decision about
            # the transcript, not a refactor. No transcript reader is given, so no line is quoted
            # either (that needs its own decision).
            if recall_service is None:
                return None
            table = self.tables.get(campaign.guild_id)
            live = table is not None and table.campaign_id == campaign.id
            async with asyncio.timeout(RECALL_WAIT_S):  # optional context: never eat the answer
                return await recall_service.recall(
                    campaign.guild_id,
                    campaign.id,
                    seeds=seeds,
                    audience=PLAYERS,
                    as_of=AsOf(session_at=table.started_at or None) if live and table else AsOf(),
                    budget=self.settings.sidebar_recall_budget,
                    hops=1,
                )

        ai = self.ai_client.tier(FEATURE_TIERS[Feature.SIDEBAR], Feature.SIDEBAR)
        careful = self.ai_client.tier(FEATURE_TIERS[Feature.SIDEBAR_RETRY], Feature.SIDEBAR_RETRY)
        return Sidebar(
            ai,
            rules_index.srd,
            gate=gate,
            houses=houses,
            names=names,
            people=people,
            recall=recall if self.settings.sidebar_recall else None,
            meaning=self.meaning_leads if recall_service is not None else None,
            brews=brews,
            second_try=careful,
            settled=settled,
        )

    def sidebar_clash_view(
        self,
        campaign: Campaign,
        clash: tuple[sidebar_rule_clash.Side, sidebar_rule_clash.Side],
        retry: Callable[[], Awaitable[None]],
    ) -> discord.ui.View:
        """The two buttons under the DM's "which one applies?" (#2284); built here so the sidebar
        imports nothing from `ui` (#2827)."""

        async def record(winner: RuleRef, loser: RuleRef, by: int) -> bool:
            return await self.sidebar_record_win(campaign, winner, loser, by)

        return ClashView(clash, dm_ids=campaign.dm_user_ids, record=record, retry=retry)

    def sidebar_looked_view(self, key: str) -> discord.ui.View:
        """The DM's "📖 What I looked at" under a rules answer, for the list kept under `key`."""
        return looked_at.view(key)

    async def sidebar_record_win(
        self, campaign: Campaign, winner: RuleRef, loser: RuleRef, by: int
    ) -> bool:
        """The DM chose which of two disagreeing house rules applies (#2284); False if there is
        nowhere to keep it or it failed (the DM is told, and nothing was decided)."""
        if self.house_wins is None:
            return False
        try:  # who is a DM now, not when the question was asked
            now = await self.campaigns.get(campaign.guild_id, campaign.id)
        except Exception:
            log.exception("Couldn't re-read the campaign for a house-rule choice")
            return False
        if now is None or by not in now.dm_user_ids:
            return False
        try:
            await self.house_wins.record(campaign.guild_id, campaign.id, winner, loser, by)
        except Exception:
            log.exception("Couldn't store which house rule applies")
            return False
        return True

    async def sidebar_people(self, campaign: Campaign) -> list[str]:
        """Who is who at this table, as short lines of names (never Discord IDs) for the
        sidebar's answers (#1188). Only people who still agree to be recorded, checked after
        the read so no one is named after they stopped."""
        if self.memory is None:
            return []
        rows = await self.memory.table_people(campaign.guild_id, campaign.id)
        agreed = await self.consent.consenting(campaign.guild_id)
        table = self.tables.get(campaign.guild_id)
        if table is not None and table.campaign_id == campaign.id:
            starter: int | None = table.dm_user_id  # who started this session
            # "The DM" is the DM who is here: the one who has spoken, or the last to speak when
            # two have (the same answer the learner gives, #1547); the starter when no DM has.
            # Nobody is left out as the caller: a DM asking "what did the DM say" means themself.
            here = table.name_answers.session_dm(0, int(time.time() * 1000))
            if here is not None and here in agreed:
                starter = here
        else:
            starter = next(iter(campaign.dm_user_ids)) if len(campaign.dm_user_ids) == 1 else None
        return people_lines(
            rows,
            agreed,
            campaign.dm_user_ids,
            lambda user_id: self.name_of(campaign.guild_id, user_id),
            dm_word_user=starter,
        )

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
        self,
        action: plan_rules.Action,
        guild_id: int,
        campaign: Campaign,
        *,
        fail_open: bool = True,
    ) -> bool:
        """Does the campaign's owner's plan allow `action`, as a plain yes or no (no words, so
        no question of who is asking)? Fails open (True) like `plan_gate`, and is True when
        plans aren't enforced. A campaign with no owner has no plan to allow anything.
        `fail_open=False` (the story extractor, #1367: a background job, the biggest spend) makes
        a plan that can't be checked a no."""
        if not self.settings.enforce_plans or self.meter is None:
            return True
        owner = campaign.owner_user_id
        access = None
        if owner is not None:
            try:
                async with asyncio.timeout(GATE_TIMEOUT_S):
                    access = await self.meter.access(guild_id, owner, int(time.time()))
            except Exception:
                log.exception(
                    "Couldn't check the plan; %s it", "allowing" if fail_open else "not running"
                )
                return fail_open
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
        self.note_display_name(user.id, user.global_name or user.name)  # not the nickname
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
        dmbchat_id, dmbchat_problem = await self._dmbchat_channel(guild, campaign, screen)
        dm_bot_problem = await self._dm_bot_channel(guild, campaign, screen)
        await self._retire_old_channels(guild, campaign)
        await self._tell_players_the_clock(
            guild.id, campaign.id
        )  # (only if they would see a change)
        self._npcs_guilds[campaign.id] = guild.id
        self._npcs_changed(campaign.id)  # the pages' view is brought up to date at every start

        campaign = await self.campaigns.record_start(guild.id, campaign.id, screen_id, voice.id)
        started_at = int(time.time())
        table = Table(
            guild_id=guild.id,
            voice_channel_id=voice.id,
            screen_channel_id=screen_id,
            dm_user_id=user.id,
            segmenter=Segmenter(guild.id),
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            fight=FightState.starting_from(campaign.initiative_style),  # (#1784)
            dm_user_ids=campaign.dm_user_ids,
            screen_level=campaign.dm_screen_level,
            rules_on=campaign.rules_cards,
            rules_rulesets=(campaign.target_ruleset, campaign.fallback_ruleset),
            transcript_channel_id=transcript_id,
            dndbeyond_invite=campaign.dndbeyond_invite,
            chat_on=campaign.table_chat,
            chat_channel_id=campaign.table_chat_channel_id,
            dmbchat_channel_id=dmbchat_id,
            started_at=started_at,
        )
        if table.rules_on:
            self._warm_rules(table.rules_rulesets, table)
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
        if dmbchat_problem:
            await self.post(screen_id, dmbchat_problem)
        if dm_bot_problem:
            await self.post(screen_id, dm_bot_problem)
        self._check_house_file(campaign, screen_id)
        self._track(update_notes.check_all(self, campaign, screen_id), "shared-rules-update")
        trial_line = ""
        # Told once, privately, at their next start (#1229). It is marked told as it is read,
        # before the reply is sent: a reply that fails to send loses this one line, which is
        # better than telling the same person twice, and the start itself is not held up.
        if self.meter is not None:
            try:
                trial_line = (
                    await trial_jobs.take_extension_notice(self.meter.db, user.id, int(time.time()))
                    or ""
                )
                if self.account_moves is not None:  # an old account that moved (#1230)
                    moved = await self.account_moves.take_moved_notice(user.id)
                    trial_line = "\n\n".join(x for x in (trial_line, moved) if x)
            except Exception:
                log.exception("Couldn't check for a Try It extension notice")
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
            + (f"\n\n{trial_line}" if trial_line else "")
        )

    async def stop_session(
        self,
        guild_id: int,
        user_id: int,
        is_server_manager: bool,
        *,
        campaign_id: str | None = None,
        session: int | None = None,
        auto: str | None = None,
    ) -> str:
        """`campaign_id`: only stop if this campaign is the one being listened to (a Stop
        button on an older message must never end a newer session). `session`: only stop
        that very session (the Stop button's question, #554). `auto`: why DMbot is stopping it
        itself (#2034): nobody is here. It goes through this same path as the Stop button."""
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
            who = f"by itself ({auto})" if auto else f"by user {user_id}"
            try:
                was_saved = await self.sessions.clear(guild_id, f"/dmbot stop {who}")
            except Exception:
                log.exception("Couldn't clear the saved session")
                return STOP_FAILED
            if not was_saved:
                # DMbot was listening, so this session should have been saved (#147).
                with log_context(guild_id=guild_id, campaign_id=table.campaign_id):
                    log.warning("Stopped a session that wasn't saved: it couldn't have resumed")
            await self.stop_table(guild_id, f"/dmbot stop {who}")
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
        # A reconnect after a long outage can skip join events: every connect makes sure each
        # server has its row, with the real join date (#2166).
        first = not self._resume_started
        if self.installs is not None:
            self._track(self._note_present(first), "installs")
        # on_ready fires again after Discord reconnects; start resuming only once.
        if not first:
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
        self._background.append(
            asyncio.create_task(self._start_up_campaign_sweep(), name="campaign-sweep")
        )
        self._background.append(
            asyncio.create_task(self._name_old_speakers(), name="speaker-names")
        )

    async def _note_present(self, first: bool) -> None:
        """Write a row for every server the bot is in; at start-up also mark the ones it was
        removed from while it was down (only then: a reconnect's list may still be filling)."""
        assert self.installs is not None
        servers = [(g.id, _joined_at(g)) for g in self.guilds]
        await self.installs.present(servers, gone=lambda gid: self.get_guild(gid) is None)
        if first:
            try:
                # The servers are read again here, after the writes above, so one added meanwhile
                # is not marked left; an empty list marks nothing.
                marked = await self.installs.left_except([g.id for g in self.guilds])
            except Exception as exc:  # the type only
                log.warning("Couldn't mark servers left while offline: %s", type(exc).__name__)
            else:
                if marked:
                    log.info("Servers left while offline: %d", marked)  # a count

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

    async def _sync_commands(self) -> None:
        """Register the slash commands (#1767). Always globally, so every server that adds DMbot
        has `/dmbot`: a customer's server is never the dev server. When `DISCORD_DEV_GUILD_ID` is
        set the commands are also copied to that server, because a guild's commands update at
        once while global ones can take a while to show up, which is what a developer wants."""
        if 0 in self.settings.shards.ids:
            # Registered once for the whole bot, not per shard: only the pod serving shard 0
            # does it, so many pods don't all sync at startup.
            registered: int | None = None
            try:  # advisory only: a bug in the check must not stop the sync below
                for problem in tree_problems(
                    [c.to_dict(self.tree) for c in self.tree.get_commands()]
                ):
                    log.error("Slash command over a Discord limit: %s", problem)
            except Exception:
                log.exception("Couldn't check the slash commands against Discord's limits")
            try:
                registered = len(await self.tree.sync() or [])
            except discord.HTTPException as exc:
                # (a rate limit must not stop the bot starting, or the dev server's copy.) Every
                # server would have no /dmbot, so say what Discord refused: its status, its code
                # and its text, which names the field ("In 0.options.2.description: ...").
                log.error(
                    "Couldn't register the global slash commands: HTTP %s, Discord error %s: %s",
                    exc.status,
                    exc.code,
                    exc.text,
                )
        if self.settings.dev_guild_id and 0 in self.settings.shards.ids:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        if 0 in self.settings.shards.ids and registered is not None:
            # One line, so a deploy can be confirmed from the log alone.
            copied = " and copied to the dev server" if self.settings.dev_guild_id else ""
            log.info("Slash commands: %d global commands registered%s", registered, copied)
        # One-time cleanup (DISCORD_CLEAR_GUILD_COMMANDS): servers that still show the old
        # per-server copy next to the global commands. Never the dev server, which keeps its copy.
        if 0 in self.settings.shards.ids:
            for guild_id in self.settings.clear_guild_commands:
                if guild_id == self.settings.dev_guild_id:
                    log.warning(
                        "Not clearing the commands in %s: it is DISCORD_DEV_GUILD_ID", guild_id
                    )
                    continue
                old = discord.Object(id=guild_id)
                # The server's name first, so the log shows it is the one that was meant.
                log.info(
                    "Clearing the old per-server commands in %s (%s)",
                    guild_id,
                    await self._guild_name(guild_id),
                )
                try:
                    self.tree.clear_commands(guild=old)
                    await self.tree.sync(guild=old)
                except discord.HTTPException:
                    log.warning("Couldn't clear the old commands in %s", guild_id, exc_info=True)
                else:
                    log.info("Server %s: old per-server commands removed", guild_id)

    async def _guild_name(self, guild_id: int) -> str:
        """The server's name if DMbot is in it, else "not in this server". The server list is
        not filled yet when commands are registered at start, so it is asked of Discord."""
        found = self.get_guild(guild_id)
        if found is None:
            try:
                found = await self.fetch_guild(guild_id)
            except discord.HTTPException:
                return "not in this server"
        return str(found.name)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """DMbot was just added to a server: say hello, or say what it still needs."""
        if self.installs is not None:
            # First: "Link this server" needs the row (#2163), and the welcome may fail. A kick
            # that follows at once wins: the write is skipped if the bot is gone by its turn.
            self._track(
                self.installs.joined(
                    guild.id,
                    _joined_at(guild),
                    gone=lambda: self.get_guild(guild.id) is None,
                ),
                "install-joined",
            )
        with log_context(guild_id=guild.id):
            log.info("Joined a server; %s", await install.post_welcome(guild))
        # A hand-over offer made on the website may be waiting for this server (#690).
        self._track(self.site_offers.sweep_server(guild.id), "offer-sweep")

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """DMbot was removed from a server (or the server was deleted): note when. A server that
        is merely unavailable in a Discord outage is a different event and is not marked."""
        if self.installs is not None:
            self._track(self._note_left(guild.id), "install-left")

    async def _note_left(self, guild_id: int) -> None:
        """Mark the server left, unless it came back already (a kick and a quick re-add: the
        join's write may land first, and it must not be undone)."""
        assert self.installs is not None
        with log_context(guild_id=guild_id):
            if self.get_guild(guild_id) is not None:
                return
            # And again once this server's turn comes: a join queued behind may have come back.
            await self.installs.left(guild_id, back=lambda: self.get_guild(guild_id) is not None)

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
            fight=FightState.starting_from(campaign.initiative_style),  # (#1784)
            dm_user_ids=campaign.dm_user_ids,
            screen_level=campaign.dm_screen_level,
            rules_on=campaign.rules_cards,
            rules_rulesets=(campaign.target_ruleset, campaign.fallback_ruleset),
            resumed=True,
            announce_resume=not recently,
            transcript_channel_id=self._usable_transcript(guild, campaign),
            dndbeyond_invite=campaign.dndbeyond_invite,
            chat_on=campaign.table_chat,
            chat_channel_id=campaign.table_chat_channel_id,
            # After a restart: still there, or the next /dmbot start makes it again.
            dmbchat_channel_id=(
                campaign.dmbchat_channel_id
                if campaign.dmbchat_channel_id is not None
                and self.get_channel(campaign.dmbchat_channel_id) is not None
                else None
            ),
            started_at=saved.started_at,
        )
        if table.rules_on:
            self._warm_rules(table.rules_rulesets, table)
        if campaign.transcript_channel_id is not None and table.transcript_channel_id is None:
            await self.post(
                screen_id, screen_messages.transcript_stopped(campaign.transcript_channel_id)
            )
        # Off the record (#2466): a pause survives a restart. It is put back before the allowlist
        # goes to ears, so nobody who asked not to be recorded is let in.
        await self.consent.agreed(guild.id)  # (loaded: a pause is only for someone who agreed)
        self._starting[guild.id] = table  # so a pause that ended while down can mark the transcript
        try:
            await self.off_the_record.restore(guild.id, saved.paused)
            # Sends the allowlist, then the join. If ears isn't connected yet, it gets both
            # when it connects (_on_ears_link_change).
            await self.start_table(table)
        finally:
            self._starting.pop(guild.id, None)
        await self.off_the_record.say_expired(guild.id, saved.paused)
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

    async def post(
        self,
        channel_id: int,
        text: str,
        view: discord.ui.View | None = None,
        *,
        remember: Hashable | None = None,
        secret_of: tuple[int, str] | None = None,
    ) -> bool:
        """Send a message; returns False (and logs) if it could not be posted. `secret_of`: the
        (server, campaign) of a question that shows a secret name, remembered so it can be
        pulled back if the DM screen opens up (#2097)."""
        sent = await self.post_message(channel_id, text, view, remember=remember)
        if sent is not None and secret_of is not None:
            # Asked again after the send: if the screen opened up meanwhile, pull it back at once.
            campaign = await self.campaigns.get(*secret_of)
            if campaign is None or campaign.dm_screen_visibility != "private":
                await secret_posts.hide_one(sent)
            else:
                secret_posts.remember(*secret_of, sent)
        return sent is not None

    async def post_message(
        self,
        channel_id: int,
        text: str,
        view: discord.ui.View | None = None,
        *,
        remember: Hashable | None = None,
    ) -> discord.Message | None:
        """Send a message; returns it, or None (and logs) if it could not be posted. `remember`:
        a DM-screen line with buttons for this item, kept in memory so its buttons can come off
        when the item is decided somewhere else (#1509)."""
        channel = self.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("Could not post to channel %s: channel not found", channel_id)
            return None
        try:
            # discord.py's types don't accept view=None, so only pass a real view.
            if view is None:
                sent = await channel.send(text, allowed_mentions=NO_PINGS)
            else:
                sent = await channel.send(text, allowed_mentions=NO_PINGS, view=view)
        except discord.HTTPException as exc:
            log.warning("Could not post to channel %s: %s", channel_id, exc)
            return None
        if remember is not None:
            self.decision_lines.remember(remember, sent)
        return sent

    async def after_invite_change(self, campaign: Campaign) -> None:
        """A running session shows the DM's new D&D Beyond invite link from now on, and the
        transcript channel's pinned card follows (#1119). A removed link must not linger: a DM
        removes one because it leaked or the table is full."""
        table = self.tables.get(campaign.guild_id)
        if table is not None and table.campaign_id == campaign.id:
            table.dndbeyond_invite = campaign.dndbeyond_invite
        guild = self.get_guild(campaign.guild_id)
        if guild is not None and campaign.transcript_channel_id is not None:
            try:
                await refresh_card(guild, campaign)
            except Exception:
                log.exception("Couldn't refresh the transcript channel's card")

    def after_game_masters_change(self, campaign: Campaign) -> None:
        """A campaign's game masters changed (#1600): a running session of it takes the new set at
        once, whether or not the DM screen's permissions could be updated."""
        table = self.tables.get(campaign.guild_id)
        if table is not None and table.campaign_id == campaign.id:
            table.dm_user_ids = campaign.dm_user_ids

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
                typed = " (what they say, and what they type in this chat)" if table.chat_on else ""
                table.notice_posted = await self.post(
                    table.voice_channel_id,
                    "🔴 **DMbot is listening in this channel.** It gives the DM private "
                    "notes and never decides anything.\n"
                    "Everyone here gets a private message from DMbot asking if they agree "
                    f"to be recorded. Only people who say yes are recorded{typed}.\n"
                    "No message from DMbot? Check your DMs and Message Requests, or type "
                    "`/consent give`. Change your mind any time with `/consent revoke`.",
                    view=peek,
                )
                table.peek_offered = table.notice_posted and peek is not None
                if table.notice_posted:
                    log.info("Recording notice posted in the voice channel's chat")
                    await self._remember_notice(table.guild_id)
                    await self._tell_chat_channel(table)
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
        """A private message goes to the DM sidebar. A message in a server is typed chat at
        the table (#1118): read only if it is in the table's chat and passes the checks."""
        if message.guild is not None:
            try:
                if self._hear_dmbchat(message):
                    return
                if await self._chat_channel_allowed(message):
                    self._hear_typed(message)
            except Exception:
                log.exception("Couldn't handle a typed message")
            return
        try:
            await self.sidebar.on_dm_message(message)
        except Exception:
            log.exception("Couldn't handle a private message")

    def _hear_dmbchat(self, message: discord.Message) -> bool:
        """A message in this session's DMbot chat channel (#1187): True if it was one (read or
        not), so no other path looks at it. Every message there is a question for DMbot, from
        anyone who agreed to be recorded; bots, DMbot itself and commands are never answered.
        Only this campaign's channel counts: another campaign's chat in the same server has a
        different id and is never read."""
        guild = message.guild
        table = self.tables.get(guild.id) if guild is not None else None
        if table is None or message.channel.id != table.dmbchat_channel_id:
            return False
        author = message.author
        if (
            not table.listening
            or author.bot
            or (self.user is not None and author.id == self.user.id)
            or message.webhook_id is not None
            or message.type not in table_chat.READ_TYPES
            or getattr(message, "interaction_metadata", None) is not None
        ):
            return True
        text = table_chat.clean(message.clean_content)  # names, not <@123> markup
        if (
            text is not None
            and asks_off_record(text)
            and self.consent.agreed_now(
                table.guild_id, author.id
            )  # paused or not: it only asks for a pause
        ):
            self._wake_off_the_record(table.guild_id, author.id)  # not a question (#2466)
        elif text is not None:
            if (
                table.is_dm(author.id)
                and self.consent.has_consent(table.guild_id, author.id)
                and not is_table_question(text)
            ):
                # A DM's statement (#3209): the players read this channel, so it is never a table
                # question and never stored from here, whatever it says. The DM is told once.
                self._hint_story_goes_private(table, author.id, text)
            else:
                self.sidebar.ask_in_chat(table, author.id, text)
        return True

    def _hint_story_goes_private(self, table: Table, user_id: int, text: str) -> None:
        """One private line to a DM who typed a story statement in the table's chat: story goes in
        the private chat with DMbot. Once a session each, and not for a short reply ("yes",
        "correct"), which gets nothing (it answers a question that lives in the private chat)."""
        if user_id in table.dmbchat_hinted or len(text.split()) < DMBCHAT_STATEMENT_WORDS:
            return
        table.dmbchat_hinted.add(user_id)
        self._track(self.sidebar_send_dm(user_id, DMBCHAT_HINT), "dmbchat-hint")

    async def sidebar_post_chat(self, table: Table, text: str) -> bool:
        """Post in the session's DMbot chat channel (#1187); False if there is none."""
        if table.dmbchat_channel_id is None:
            return False
        return await self.post(table.dmbchat_channel_id, text)

    async def sidebar_table_name(self, table: Table, user_id: int) -> str:
        """How the table's answer names someone: their linked character's name when a sheet is
        linked, else their server nickname (#1187)."""
        name = self.name_of(table.guild_id, user_id)
        if self.sheets is not None and table.campaign_id is not None:
            try:
                for sheet in await self.sheets.sheets(table.guild_id, table.campaign_id):
                    if sheet.played_by == user_id:
                        return str(sheet.name)
            except Exception:
                log.exception("Couldn't look up a character name; using the nickname")
        # A DM keeps the nickname: a DM may "play" an NPC, and the table must not learn which
        # one the DM is voicing from the name on their question (#1188).
        if self.memory is not None and table.campaign_id is not None and not table.is_dm(user_id):
            try:  # the character they play (the `plays` link), even with no sheet linked
                async with asyncio.timeout(NAMES_WAIT_S):
                    people = await self.memory.table_people(table.guild_id, table.campaign_id)
                for played, characters, _ in people:
                    if played == user_id and characters:
                        return characters[-1]
            except Exception:
                log.exception("Couldn't look up who plays whom; using the nickname")
        return name

    async def sidebar_links(self, table: Table) -> tuple[str | None, tuple[str, ...]]:
        """The links this campaign has shared (#1187), and only this campaign's: its house-rules
        file of record, which the table's answers may show (the owner's decision, 2026-10-14;
        the file's own docstring says the link is otherwise private), and nothing else yet.
        dndbeyond.com is allowed without being listed."""
        house = None
        if self.house_file_links is not None and table.campaign_id is not None:
            try:
                found = await self.house_file_links.get(table.guild_id, table.campaign_id)
                house = found.link if found is not None else None
            except Exception:
                log.exception("Couldn't read the house-rules link; showing no link")
        return house, ()

    async def sidebar_nudge_chat(self, table: Table, user_id: int) -> None:
        """Someone typed in the DMbot chat before agreeing to be recorded: one short reply with
        the agree button, once a session. What they typed is not kept or sent anywhere."""
        if table.dmbchat_channel_id is None:
            return
        view = request_view(
            table.guild_id,
            outside=self.outside_engine,
            test_voice=self.test_voice_listed(table.guild_id),
            invite=table.dndbeyond_invite,
        )
        await self.post(
            table.dmbchat_channel_id,
            f"{discord.utils.escape_markdown(self.name_of(table.guild_id, user_id))}: to ask "
            "DMbot, first say yes to being recorded with the button below. No pressure, you can "
            "still play. DMbot did not keep what you typed.",
            view,
        )

    def sidebar_has_consent(self, guild_id: int, user_id: int) -> bool:
        if self.consent.has_consent(guild_id, user_id):
            return True
        # A DM who is off the record (or whose table is) can still ask DMbot in the DM sidebar:
        # that is a question to DMbot, not the table's speech (#2466).
        table = self.tables.get(guild_id)
        return (
            table is not None
            and (user_id == table.dm_user_id or user_id in table.dm_user_ids)
            and self.consent.agreed_now(guild_id, user_id)
        )

    def sidebar_paused(self, guild_id: int, user_id: int) -> bool:
        """Off the record (their own pause, or the table's): agreed, but not recorded now."""
        return self.consent.agreed_now(guild_id, user_id) and not self.consent.has_consent(
            guild_id, user_id
        )

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
            invite=table.dndbeyond_invite,
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

    async def sidebar_send_dm(
        self, user_id: int, text: str, view: discord.ui.View | None = None
    ) -> str:
        """ "sent", "forbidden" (their messages from DMbot are closed) or "failed"."""
        try:
            user = self.get_user(user_id) or await self.fetch_user(user_id)
            if view is None:
                await user.send(text, allowed_mentions=NO_PINGS)
            else:
                sent = await user.send(text, view=view, allowed_mentions=NO_PINGS)
                keep = getattr(view, "keep_message", None)  # a view that edits itself later (#2284)
                if keep is not None:
                    keep(sent)
        except discord.Forbidden as exc:
            log.info("Couldn't message user %s privately: %s", user_id, exc)
            return "forbidden"
        except discord.HTTPException as exc:
            log.info("Couldn't message user %s privately: %s", user_id, exc)
            return "failed"
        return "sent"

    async def sidebar_guide(self, table: Table, user_id: int) -> str | None:
        """Guide me's answer for the DM sidebar's "what's next?" (#1739): the same text as the
        🧭 button, or None when the campaign's switch isn't on Guide me, no DM's own confirmation
        stands, or the owner's plan doesn't allow it (so the sidebar answers as it always did)."""
        if table.campaign_id is None:
            return None
        campaign = await self.campaigns.get(table.guild_id, table.campaign_id)
        if campaign is None or user_id not in campaign.dm_user_ids:
            return None
        if not await self.guide_button_allowed(table.guild_id, campaign):
            return None
        # In a fight "what's next?" means whose turn it is: the usual answer, not the book.
        if self.clocks is not None and await self.clocks.in_combat(table.guild_id, campaign.id):
            return None
        return await guide_screen.answer_text(self, campaign)

    async def sidebar_tell_screen(self, table: Table, text: str) -> None:
        await self.post(table.screen_channel_id, text)

    async def _dm_message(
        self, user_id: int, text: str, view: discord.ui.View | None
    ) -> discord.Message | None:
        """A private message that can be edited later (a view's buttons are taken away when they
        time out). None when it could not be sent."""
        try:
            user = self.get_user(user_id) or await self.fetch_user(user_id)
            if view is None:
                return await user.send(text, allowed_mentions=NO_PINGS)
            return await user.send(text, view=view, allowed_mentions=NO_PINGS)
        except discord.HTTPException as exc:
            log.info("Couldn't message user %s privately: %s", user_id, exc)
            return None

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
        if text and asks_off_record(text):
            # "Hey DMbot, off the record" (#2466): this person goes off the record and the line
            # that said so is in neither transcript, nor anything fed from it. Not waited on a
            # speaker label: a wrongly paused person only presses Resume, while waiting keeps
            # them recorded.
            if self.tables.get(table.guild_id) is table:  # (a session that is ending pauses no one)
                self._wake_off_the_record(table.guild_id, utterance.user_id)
                # Words of theirs that were written down before this line arrived are not kept.
                table.unsaved.drop_since(utterance.user_id, utterance.start_ms)
            return
        cleaned = text
        if text and table.name_lookup is not None:
            # Fixes DMbot isn't sure of are never silent: made only when the DM screen
            # shows them with Undo (a running session, not quiet; #296, #504).
            noted = self.tables.get(table.guild_id) is table and screen_levels.allows(
                table.screen_level, screen_levels.FIX_NOTE
            )
            result = self._clean(table, text, unsure=noted, count=True)
            cleaned = result.text
            line_fixes = result.fixes
            table.names_fixed += len(result.fixes)
            seam = self._look_back(table, utterance, text, result)
            self._offer_question(table, utterance.user_id, utterance.start_ms, text, result, seam)
            kept = table.last_piece.get(utterance.user_id)
            if kept is None or kept.started_ms <= utterance.start_ms:  # (a slow clip arrives late)
                table.last_piece[utterance.user_id] = lookback.Piece(
                    utterance.start_ms, utterance.end_ms, text, result
                )
            if noted and table.fix_notes.add(
                utterance.user_id, utterance.start_ms, text, result.fixes
            ):
                self._redraw_fix_notes(table)
            found_names = find_mentions(table.name_lookup, cleaned)
            named = {f.entity_id for f in found_names}  # once per name per line
            table.scene.note(named, utterance.user_id, time.monotonic())
            table.heard_counts.update((entity_id, utterance.user_id) for entity_id in named)
            names_said = len(named)
        else:
            names_said = 0
            line_fixes = ()
            found_names = []
        # Talk not about the game never feeds hints or suggestions (#1748): a line the off-topic
        # filter holds for a label is left out, as for the word list.
        # One answer for the whole line, so the callers below can't disagree (#1748).
        holds = bool(text and self._topic_holds(table, cleaned or text, names_said))
        if text and self.tables.get(table.guild_id) is table:
            # Every line goes to the watcher (memory only); what it finds waits with a held line.
            self._note_spelling(
                table,
                utterance.user_id,
                str(cleaned or text),
                held_key=(utterance.user_id, utterance.start_ms) if holds else None,
            )
        if text and self.tables.get(table.guild_id) is table:
            # Every line goes to the watcher (memory only); what it finds waits with a held line.
            self._note_pronounced(
                table,
                utterance.user_id,
                str(cleaned or text),
                found_names,
                str(text),
                held_key=(utterance.user_id, utterance.start_ms) if holds else None,
            )
        if line_fixes and not holds:  # (a held line is not counted: when unsure, offer less)
            self._count_heard_forms(table, utterance.user_id, line_fixes)
        if text and not holds:
            # After cleaning, so a line is never evidence about itself; even when the names
            # couldn't be loaded, a word said in lower case is a real word next time. A line
            # waiting for the off-topic filter adds its words only once labelled game talk
            # (`_label_topics`, `put_back`): talk not about the game never feeds them (#1748).
            table.vocabulary.note(utterance.user_id, text)
        if text:
            # A line the off-topic filter holds for a label is not offered a time suggestion now:
            # it is, if it turns out to be game talk (`_label_topics`). The same rule that holds
            # it for the names scan (`_topic_holds`) decides (#1758).
            self._act_on_line(
                table,
                utterance.user_id,
                str(cleaned or text),
                held=holds,
            )
        labelling = False  # is this line waiting for its speaker label? (#1624)
        if text and self.transcripts is not None:
            duration_ms = int(utterance.duration_s * 1000)
            table.unsaved.add(
                Line(utterance.start_ms, utterance.user_id, text, cleaned or text, duration_ms)
            )
            labelling = self._speaker_pending(table, utterance, str(cleaned or text))
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
            table.piece_words[utterance.user_id, utterance.start_ms] = (
                str(cleaned or text),
                time.monotonic(),
            )
            if len(table.piece_words) > PIECE_WORDS_MAX:  # (oldest first: dicts keep order)
                del table.piece_words[next(iter(table.piece_words))]
            if self.tables.get(table.guild_id) is table:
                self._feed_name_answers(
                    table, utterance.user_id, utterance.start_ms, str(cleaned or text)
                )
                # Only the DM's own lines can start it; consent was just re-checked. A line said
                # as a named NPC never does (#1624): when it clearly asks DMbot something and
                # its speaker label is on the way, the question waits for the label.
                asked_line = str(cleaned or text)
                if labelling and request_in(asked_line) is not None:
                    self._ask_when_labelled(
                        table, utterance.user_id, utterance.start_ms, asked_line
                    )
                else:
                    self.sidebar.ask_at_table(table, utterance.user_id, asked_line)
        if text and self._topic_pending(table, utterance, cleaned or text, holds):
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
            table.capture_log.add_line(
                utterance.user_id, str(cleaned or text), confidence_of(text), engine=engine_of(text)
            )
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

    def _act_on_line(
        self, table: Table, user_id: int, line: str, *, typed: bool = False, held: bool = False
    ) -> None:
        """What a line of table talk can set off, said or typed (#1118): a rules card, a house
        rule, the game clock. The one place both ways in share, so a typed "we take a long
        rest" does what the spoken one does. Consent was checked by the caller."""
        try:  # a change at the table that D&D Beyond should show: read that sheet again (#1128)
            sheet_reread.note_line(table, user_id, line)
        except Exception:
            log.exception("Sheet re-reads: couldn't look at a line")
        try:  # how the table runs initiative (#1771): only times FightBot's reminders
            heard_here = not held and (user_id in table.dm_user_ids or not typed)
            if heard_here and fightbot.note_line(
                table,
                user_id,
                line,
                is_dm=user_id in table.dm_user_ids,
                consenting=lambda u: self.consent.has_consent(table.guild_id, u),
            ):
                self._track(self.fight_style_changed(table), "fight-style")
                self._track(self._remember_style(table), "fight-style-kept")
        except Exception:
            log.exception("FightBot: couldn't look at a line")
        try:  # a card is never worth a lost line: nothing below may be skipped
            self._note_rules(table, user_id, line)
        except Exception:
            log.exception("Rules cards: couldn't look at a line")
        try:
            if user_id in table.dm_user_ids:  # only a DM can start a proposal
                self._note_house_rule(table, user_id, line)
        except Exception:
            log.exception("House rules by voice: couldn't look at a line")
        try:  # a DM saying one of their secret names: ask at once if it is revealed now (#2088)
            if self.settings.secret_reveal_ask and not held and table.is_dm(user_id):
                self._track(self._reveal_ask(table, user_id, line), "secret-reveal-ask")
        except Exception:
            log.exception("Secret reveal: couldn't look at a line")
        if self.clocks is not None:
            if typed and table.clock_waiting >= table_chat.CLOCK_WAITING_MAX:
                return  # typed flood: skip this clock phrase, never speech (#1154)
            table.clock_waiting += 1
            task = self._track(  # a DM's words move the clock; a player's only ask the DM
                self._note_clock_phrase(table, user_id, line, held=held), "clock-phrase"
            )
            if task is None:
                table.clock_waiting -= 1
            else:
                task.add_done_callback(
                    lambda _t: setattr(table, "clock_waiting", table.clock_waiting - 1)
                )

    async def _reveal_ask(self, table: Table, speaker: int, line: str) -> None:
        """The DM just said one of their secret names (#2088, docs/PLAN.md, "When a secret is
        revealed"): ask them at once, once for each name in a session, whether it is revealed now.
        Code only, no AI. Never on Quiet (the after-session question covers it then), only while
        the DM is recorded and the session runs, and nothing is written without their press. On the
        DM screen when only the DM can read it; as a private message to the DM when the players
        may peek at it. A failure costs the question, never the line. The log gets no name."""
        gid, cid = table.guild_id, table.campaign_id
        if (
            self.memory is None
            or self.lookup is None
            or cid is None
            or self.tables.get(gid) is not table
            or not screen_levels.allows(table.screen_level, screen_levels.QUESTION)
            or not self.consent.has_consent(gid, speaker)
        ):
            return
        reveal = table.reveal
        with log_context(guild_id=gid, campaign_id=cid):
            try:
                now = time.monotonic()
                if not reveal.fresh(now):  # the secret names change rarely: read once a minute
                    reveal.names, reveal.read_at = await secret_names(self.memory, gid, cid), now
                if not reveal.names:
                    return
                known = await self.lookup.get(gid, cid)
                allowed = matcher_names(known)[1]
                found = revealed_in(line, reveal.names, allowed, reveal)
                if found is None:
                    return
                campaign = await self.campaigns.get(gid, cid)
                posted = closed = False
                try:
                    if (
                        campaign is None
                        or speaker not in campaign.dm_user_ids
                        or not self.consent.has_consent(gid, speaker)
                        or self.tables.get(gid) is not table
                        or table.campaign_id != cid
                        or not screen_levels.allows(table.screen_level, screen_levels.QUESTION)
                    ):
                        return
                    alone = campaign.dm_screen_visibility != "private"
                    text = reveal_question(found.text, found.entries, private=alone)
                    view = reveal_view(cid, found.alias_id, gid)
                    if alone:  # players may peek at the screen: the DM's own messages, not there
                        sent = await self.sidebar_send_dm(speaker, text, view)
                        posted = sent == "sent"
                        if sent == "forbidden" and not reveal.told_waiting:
                            # Their private messages are closed (not a send that merely failed,
                            # which may work next time and needs no setting changed): say so on
                            # the screen, no name, once.
                            reveal.told_waiting = await self.post(
                                table.screen_channel_id, SECRET_QUESTION_WAITING
                            )
                        # Their private messages are closed: the key stays claimed, so each later
                        # line naming it doesn't fetch, send and log again for the whole session.
                        closed = sent == "forbidden"
                    else:
                        posted = bool(
                            await self.post(
                                table.screen_channel_id,
                                text,
                                view=view,
                                secret_of=(gid, cid),  # pulled back if the screen opens up (#2097)
                            )
                        )
                finally:
                    if not posted and not closed:
                        reveal.asked.discard(found.key)  # nothing was said: it may ask again
                if posted:
                    log.info("Secret reveal: asked")  # no name, no words
            except Exception:
                log.exception("The secret reveal question failed")

    async def _remember_style(self, table: Table) -> None:
        """Keep the initiative style a DM's words set, for the next session (#1784). Written only
        when what is kept changes, never on every line heard."""
        if table.campaign_id is None:
            return
        fight = table.fight
        kept = fight.style.to_keep(fight.saved)
        if kept == fight.saved:
            return
        before = fight.saved
        fight.saved = kept  # (before the await, so two quick lines don't both write it)
        try:
            await self.campaigns.set_initiative_style(table.guild_id, table.campaign_id, kept)
        except Exception:
            log.warning("Couldn't keep the initiative style", exc_info=True)
            fight.saved = before  # tried again with the next DM line

    async def fight_style_changed(self, table: Table) -> None:
        """The initiative style FightBot heard changed (#1771): the DM's combat line shows it.
        The pinned clock is edited in place; nothing is posted, and players see nothing."""
        if (
            self.clocks is None
            or table.campaign_id is None
            or self.tables.get(table.guild_id) is not table
        ):
            return
        stored = await self.clocks.get(table.guild_id, table.campaign_id)
        campaign = await self.campaigns.get(table.guild_id, table.campaign_id)
        if stored is None or campaign is None or stored.clock.combat_round is None:
            return
        await clock_screen.show(self, campaign, stored)

    async def _chat_channel_allowed(self, message: discord.Message) -> bool:
        """False for a message in the DM's extra chat channel when that channel is any
        campaign's DM screen, transcript or game-time channel in this server (#1118). Only the
        extra channel is asked about (the voice channel's own chat can't be one), and the
        answer is kept for a minute, so most messages cost nothing here."""
        guild = message.guild
        table = self.tables.get(guild.id) if guild is not None else None
        if (
            guild is None
            or table is None
            or table.chat_channel_id is None
            or message.channel.id != table.chat_channel_id
            or message.channel.id == table.voice_channel_id
        ):
            return True
        now = time.monotonic()
        cached = self._chat_blocked.get(guild.id)
        if cached is None or cached[0] < now:
            blocked = await self.campaigns.special_channel_ids(guild.id)
            cached = (now + CHAT_BLOCKED_TTL_S, blocked)
            self._chat_blocked[guild.id] = cached
        return message.channel.id not in cached[1]

    def _hear_typed(self, message: discord.Message) -> None:
        """A message typed in a server (#1118). Only the table voice channel's chat and the
        DM's one extra channel, only while listening, only from someone who agreed, never a
        bot or a command: everything else is dropped before any work is done."""
        guild = message.guild
        table = self.tables.get(guild.id) if guild is not None else None
        if table is None or table.campaign_id is None or not table.listening or not table.chat_on:
            return
        if not table_chat.is_table_chat(
            message.channel.id, table.voice_channel_id, table.chat_channel_id
        ) or message.channel.id in (table.screen_channel_id, table.transcript_channel_id):
            return  # this campaign's DM screen and transcript channel are never read as chat
        # (any other campaign's are refused when the DM picks the channel and by
        # `_chat_channel_allowed`, which asks the database)
        author = message.author
        if (
            author.bot
            or (self.user is not None and author.id == self.user.id)
            or message.webhook_id is not None
            or message.type not in table_chat.READ_TYPES
            or getattr(message, "interaction_metadata", None) is not None
        ):
            return
        if not self.consent.has_consent(table.guild_id, author.id):
            return  # nothing of a message, picture or words, is read for someone who didn't agree
        start_ms = int(message.created_at.timestamp() * 1000)
        links = (
            self._image_links(message)
            if self.image_desk is not None
            and self.consent.has_image_consent(table.guild_id, author.id)
            else []
        )
        if links:
            self._track(
                self._hear_images(
                    table, author.id, start_ms, message.id, links, message.channel.id
                ),
                "table-images",
            )
        text = table_chat.clean(message.clean_content)  # names, not <@123> markup
        if text is None:
            return
        self._deliver_typed(table, author.id, text, start_ms, message.id)

    @staticmethod
    def _image_links(message: discord.Message) -> list[str]:
        """The links of the pictures on a message: by what Discord says the file is, within the
        size DMbot reads. Only links; the pictures are fetched later, after the plan check."""
        return [
            a.url
            for a in message.attachments
            if (a.content_type or "").startswith("image/") and a.size <= images.MAX_BYTES
        ][: images.MAX_PER_MESSAGE]

    async def _maps_may_be_kept(self, guild_id: int, campaign_id: str) -> bool:
        """Has this campaign's DM confirmed the right to use shared material (the Shared story
        switch's warning; CLAUDE.md, IP)? A map may be from a published adventure, so its places
        are kept only then (#2450). A failure to read the record is a no."""
        try:
            return any(
                c["purpose"] == SHARED_STORY
                for c in await self.campaigns.confirmations(guild_id, campaign_id)
            )
        except Exception:
            log.exception("Table image: couldn't read the right-to-use record")
            return False

    async def _hear_images(
        self,
        table: Table,
        user_id: int,
        start_ms: int,
        message_id: int,
        links: list[str],
        channel_id: int = 0,
    ) -> None:
        """A picture in the table chat becomes an (image) line (#2450). Nothing is fetched or sent
        to the AI unless the poster agreed, the campaign's plan has AI and the day's limit is not
        used; consent is checked again after the slow steps, so someone who stopped meanwhile
        gets no line. The picture is read once and kept nowhere."""
        desk, gid, cid = self.image_desk, table.guild_id, table.campaign_id

        def still_ok() -> bool:
            # Asked before every download and AI call: the yes must be to wording that mentions
            # pictures (IMAGE_TERMS_VERSION), still given, the session still going, and the
            # message still there.
            return (
                self._still_recorded(table, user_id)
                and self.consent.has_image_consent(gid, user_id)
                and message_id not in self._deleted_messages
            )

        if desk is None or cid is None or not still_ok():
            return
        campaign = await self.campaigns.get(gid, cid)
        if campaign is None or not await self.plan_allows("ai", gid, campaign, fail_open=False):
            return
        reads = await desk.describe(gid, cid, links, still_ok)
        if not still_ok():
            return
        maps = [r for r in reads if r is not None and r.kind == images.MAP]
        if maps and self.map_notes is not None:
            # Under the save lock, as "delete my past transcripts" takes it, and with the consent
            # asked once more right before, so a note can't be written after its poster deleted.
            # Only where the DM confirmed the right to use shared material (the Shared story
            # switch's confirmation): without it the picture gets its short line and no more.
            async with table.save_lock:
                if not await self._maps_may_be_kept(gid, cid):
                    maps = []
                for a_map in maps:
                    if not still_ok():
                        break
                    try:
                        await self.map_notes.add(gid, cid, user_id, a_map, message_id)
                    except Exception as exc:  # the transcript line matters more than the note
                        log.warning(
                            "Table image: couldn't keep a map's description (%s)",
                            type(exc).__name__,
                        )
        for n, read in enumerate(reads):
            self._deliver_typed(
                table,
                user_id,
                images.line_text(read),
                start_ms + n,
                message_id,
                image=True,
            )
            await self._say_image_text(table, user_id, start_ms + n, channel_id, read, message_id)

    async def _say_image_text(
        self,
        table: Table,
        user_id: int,
        started_ms: int,
        channel_id: int,
        read: images.Read | None,
        message_id: int = 0,
    ) -> None:
        """A picture of writing: the words DMbot read, said in the chat it was posted in, with
        ✏️ Fix for the poster or a DM (#2450). Only the words of the picture, quoted: never DM
        screen content. Not said when the poster no longer agrees or the session is over."""
        session_id = table.transcript_session_id
        if (
            read is None
            or read.kind != images.TEXT
            or not read.text
            or not channel_id
            or session_id is None
            or (message_id and message_id in self._deleted_messages)  # the picture is gone
            or not self._still_recorded(table, user_id)
            or not self.consent.has_image_consent(table.guild_id, user_id)
        ):
            return
        sent = await self._post_reply(
            channel_id,
            message_id,
            image_fix.reply_text(read.text),
            image_fix.fix_view(session_id, user_id, started_ms),
        )
        if sent is None or not message_id:
            return
        if message_id in self._deleted_messages:  # deleted while the reply was being sent
            await self._delete_reply(sent)
            return
        self._image_replies.setdefault(message_id, []).append(sent)
        while len(self._image_replies) > IMAGE_REPLIES_KEPT:  # the oldest picture's go first
            del self._image_replies[next(iter(self._image_replies))]

    async def _post_reply(
        self, channel_id: int, to_message_id: int, text: str, view: discord.ui.View
    ) -> discord.Message | None:
        """Answer a message with a Discord reply, so the answer carries a reference to it and a
        delete can find it again after a restart (#2522). It still posts if that message is
        already gone. None (and logged) if it could not be posted."""
        channel = self.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("Could not post to channel %s: channel not found", channel_id)
            return None
        try:
            if not to_message_id:
                return await channel.send(text, allowed_mentions=NO_PINGS, view=view)
            reference = discord.MessageReference(
                message_id=to_message_id, channel_id=channel_id, fail_if_not_exists=False
            )
            return await channel.send(
                text, allowed_mentions=NO_PINGS, view=view, reference=reference
            )
        except discord.HTTPException as exc:
            log.warning("Could not post to channel %s: %s", channel_id, exc)
            return None

    async def _delete_reply(self, reply: discord.Message) -> None:
        """Take one of DMbot's own replies back. Never raises (it may be gone already)."""
        try:
            await reply.delete()
        except discord.HTTPException:
            log.info("Table image: a reply could not be deleted")

    async def _forget_image_replies(
        self, message_ids: Collection[int], guild_id: int | None = None, channel_id: int = 0
    ) -> None:
        """Pictures were deleted: so are the words DMbot said about them (#2450). A deleted
        picture leaves nothing in the chat, as it leaves nothing in the unsaved transcript. A reply
        remembered in memory goes at once; for the rest (after a restart, or past the newest
        `IMAGE_REPLIES_KEPT`) the chat just after the picture is read for DMbot's own reply that
        references it (#2522)."""
        missing = set()
        for message_id in message_ids:
            replies = self._image_replies.pop(message_id, [])
            for reply in replies:
                await self._delete_reply(reply)
            if not replies:
                missing.add(message_id)
        if missing and guild_id is not None and channel_id:
            await self._delete_referencing_replies(guild_id, channel_id, missing)

    async def _delete_referencing_replies(
        self, guild_id: int, channel_id: int, message_ids: set[int]
    ) -> None:
        """Look through the next messages of a table chat for DMbot's own replies to these
        (deleted) messages, and delete them. Only DMbot's own messages are ever touched, only in a
        channel that is read as table chat, and at most `REPLY_SCANS_PER_MINUTE` times a minute for
        a server, since most deleted messages have no reply."""
        channel = self.get_channel(channel_id)
        if self.user is None or not isinstance(channel, discord.abc.Messageable):
            return
        if not await self._is_table_chat_channel(guild_id, channel):
            return
        now = time.monotonic()
        recent = [t for t in self._reply_scans.get(guild_id, []) if now - t < 60]
        if len(recent) >= REPLY_SCANS_PER_MINUTE:
            self._reply_scans[guild_id] = recent
            return
        self._reply_scans[guild_id] = [*recent, now]
        try:
            async for found in channel.history(
                limit=REPLY_SCAN_LIMIT, after=discord.Object(id=min(message_ids))
            ):
                ref = found.reference
                if (
                    found.author.id == self.user.id
                    and ref is not None
                    and ref.message_id in message_ids
                ):
                    await self._delete_reply(found)
        except discord.HTTPException:
            log.info("Table image: the chat could not be read for a reply to a deleted picture")

    async def _is_table_chat_channel(self, guild_id: int, channel: discord.abc.Messageable) -> bool:
        """Is this a channel DMbot reads as table chat: a voice channel's own chat, or the extra
        channel a campaign picked. The extra channels are kept for a minute."""
        if isinstance(channel, discord.VoiceChannel | discord.StageChannel):
            return True
        now = time.monotonic()
        cached = self._chat_channels.get(guild_id)
        if cached is None or cached[0] < now:
            cached = (
                now + CHAT_BLOCKED_TTL_S,
                await self.campaigns.table_chat_channel_ids(guild_id),
            )
            self._chat_channels[guild_id] = cached
        return getattr(channel, "id", 0) in cached[1]

    def _deliver_typed(
        self,
        table: Table,
        user_id: int,
        text: str,
        start_ms: int,
        message_id: int = 0,
        *,
        image: bool = False,
    ) -> None:
        """A typed line goes where a spoken one goes (clock phrases, house rules, names heard,
        the "Hey DMbot" sidebar, the transcript), minus what only audio has: no cleaning, no
        audio check. In the transcript it is tagged (typed), in both versions; typing clearly not
        about the game goes through the same off-topic filter as talk and is left out of the
        cleaned version (#2447, item 6). A described picture (`image`) is only ever a record: its
        words are data from the AI about a stranger's file, so they reach the names scan and the
        transcript but never run a clock phrase, a house rule, the sidebar, the topic filter or
        the off-the-record words."""
        if not image and asks_off_record(text):  # the line is not kept; off the record (#2466)
            self._wake_off_the_record(table.guild_id, user_id)
            return
        now = time.monotonic()
        table.idle.activity(now)  # typed chat at the table is not silence (#2034)
        listeners = True if image else table.typed_rate.allow(user_id, now)  # (the day limits it)
        if not listeners and table.typed_rate.should_log(now):
            # No text, no names: only that it happened (#1154).
            log.info("Typed chat: a flood of lines; extra lines skip the listeners")
        names_said = 0
        if listeners:
            if not image:  # a description's words are not the poster's own talk
                table.vocabulary.note(user_id, text)
            if table.name_lookup is not None:
                named = mentions(table.name_lookup, text)
                names_said = len(named)
                if not image:
                    table.scene.note(named, user_id, now)
                table.heard_counts.update((entity_id, user_id) for entity_id in named)
            if not image:
                self._act_on_line(table, user_id, text, typed=True)
        if self.transcripts is not None:
            table.unsaved.add(
                Line(start_ms, user_id, text, text, typed=True, image=image, message_id=message_id)
            )
            if listeners and not image and self._topic_holds(table, text, names_said):
                # Its words already reached the names scan above; the label only decides whether
                # the cleaned transcript shows the line (no `held` entry: nothing to put back).
                self._topic_wait(table, user_id, start_ms, text, 0.0, typed=True)
        if not listeners or image:
            return  # raw transcript only: no recent lines, sidebar or name scan (#1154)
        table.recent.add(user_id, text, time.monotonic(), typed=True)
        if self.tables.get(table.guild_id) is table:
            # Same clock as the spoken path (Unix milliseconds); consent was just checked.
            self._feed_name_answers(table, user_id, start_ms, text)
            self.sidebar.ask_at_table(table, user_id, text, typed=True)
        if len(table.heard) < HEARD_MAX:
            table.heard.append((user_id, text))

    async def _tell_chat_channel(self, table: Table) -> None:
        """One line in the DM's extra Table chat channel when listening starts, so nobody finds
        out later that it is read (#1118). The voice channel's own chat has the recording
        notice. Once a session; a failure is only logged."""
        extra = table.chat_channel_id
        if not table.chat_on or extra is None or extra == table.voice_channel_id:
            return
        if table.chat_notice_posted:
            return
        table.chat_notice_posted = bool(
            await self.post(
                extra,
                "💬 DMbot is reading this chat for the game while it listens. "
                "Only people who agreed are read.",
            )
        )

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        """Several typed messages deleted at once (a moderator's purge): the same as one."""
        for message_id in payload.message_ids:
            self._note_deleted(message_id)
        table = self.tables.get(payload.guild_id) if payload.guild_id is not None else None
        if table is not None:  # first: nothing awaited stands between a delete and its line going
            for message_id in payload.message_ids:
                table.unsaved.drop_message(message_id)
        if self.map_notes is not None and payload.guild_id is not None:
            for message_id in payload.message_ids:
                await self._forget_map_notes(payload.guild_id, message_id)
        # last: it reads the chat (a message the cache knows had no picture has no reply to find)
        plain = {m.id for m in payload.cached_messages if not m.attachments}
        self._track(
            self._forget_image_replies(
                set(payload.message_ids) - plain, payload.guild_id, payload.channel_id
            ),
            "picture-replies",
        )

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """A typed message deleted before its line was saved is taken back (#1118). A line
        already saved stays in the raw transcript, as PLAN says. DMbot's reply about a picture
        goes with the picture."""
        self._note_deleted(payload.message_id)
        table = self.tables.get(payload.guild_id) if payload.guild_id is not None else None
        if table is not None:  # first: nothing awaited stands between a delete and its line going
            table.unsaved.drop_message(payload.message_id)
        if self.map_notes is not None and payload.guild_id is not None:
            await self._forget_map_notes(payload.guild_id, payload.message_id)
        cached = payload.cached_message  # last: the reply look-up reads the chat
        if cached is None or cached.attachments:  # (one the cache knows had no picture: no reply)
            self._track(
                self._forget_image_replies(
                    [payload.message_id], payload.guild_id, payload.channel_id
                ),
                "picture-replies",
            )

    async def _forget_map_notes(self, guild_id: int, message_id: int) -> None:
        """A deleted picture takes the map notes made from it (#2450)."""
        try:
            assert self.map_notes is not None
            await self.map_notes.forget_message(guild_id, message_id)
        except Exception:
            log.exception("Table image: couldn't delete a deleted picture's map notes")

    def _note_deleted(self, message_id: int) -> None:
        """Remember a deleted message for a few minutes (a picture takes seconds to describe)."""
        now = time.monotonic()
        self._deleted_messages = {
            m: t for m, t in self._deleted_messages.items() if now - t < DELETED_KEPT_S
        }
        self._deleted_messages[message_id] = now

    def after_chat_change(self, campaign: Campaign) -> None:
        """A running session reads typed chat from now on as the DM just set it (#1118)."""
        table = self.tables.get(campaign.guild_id)
        if table is not None and table.campaign_id == campaign.id:
            changed = (table.chat_on, table.chat_channel_id) != (
                campaign.table_chat,
                campaign.table_chat_channel_id,
            )
            table.chat_on = campaign.table_chat
            table.chat_channel_id = campaign.table_chat_channel_id
            if changed and table.listening:
                # Turned on, or a different channel: that channel hasn't been told yet.
                table.chat_notice_posted = False
                self._track(self._tell_chat_channel(table), "chat-notice")

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
        mentions: list[Mention | HomebrewMention] = list(
            rules_cards.spotter_for(*table.rules_rulesets).find(line)
        )
        if mentions and table.name_lookup is not None:
            # A character, NPC or place of this campaign called Sprite or Raven is a name
            # the table uses, not a rules question.
            known = table.name_lookup.by_key
            mentions = [
                m
                for m in mentions
                if name_key(m.said) not in known and name_key(m.name) not in known
            ]
        own = table.homebrew_spotter.find(line) if table.homebrew_spotter is not None else []
        if own and table.homebrew_spotter is not None:
            # The campaign's own homebrew comes before the book, and a homebrew name shadows the
            # book's (the card is for the DM screen only). A shared book's entry
            # ranks in its edition's place, so one that comes after the free rules' entry of the
            # same name steps aside and the free rules' card stands (#1569).
            target, fallback = table.rules_rulesets
            behind = self._books_behind(table.homebrew_spotter, own, target, fallback)
            own = [m for m in own if rules_index.normalize(m.name) not in behind]
            shadowed = table.homebrew_spotter.names - behind
            mentions = [
                m
                for m in mentions
                if rules_index.normalize(m.said) not in shadowed
                and rules_index.normalize(m.name) not in shadowed
            ]
            mentions = [*own, *mentions]
        mention = table.rules.pick(mentions, time.monotonic()) if mentions else None
        if mention is None:
            return
        before = table.rules.last_at  # given back if the card can't be shown
        card_id = table.rules.remember(mention, time.monotonic())
        self._track(self._post_rules_card(table, user_id, mention, card_id, before), "rules-card")

    async def _note_clock_phrase(
        self, table: Table, user_id: int, line: str, *, held: bool = False
    ) -> None:
        """What is said at the table that bears on the game clock (#965, #1117): no AI, and
        only people who agreed to be recorded. A campaign's DM clearly saying a rest starts or
        finishes, that the next morning has come, or how much time has passed ("an hour
        later") moves the clock, with a short note on the DM screen and an Undo button. A
        player suggesting a rest moves nothing: it asks the DM, with one button, at most once
        every few minutes. Vague narration is ignored."""
        if table.campaign_id is None or not table.listening:
            return
        if self.tables.get(table.guild_id) is not table:
            return
        if not self.consent.has_consent(table.guild_id, user_id):
            return
        if user_id not in table.dm_user_ids:
            await self._suggest_rest(table, user_id, line)
            return
        fight = combat_phrases.find_combat(line)
        if fight is not None:  # combat words are never also a clock phrase
            await self._note_combat_phrase(table, user_id, line, fight)
            return
        said = clock_phrases.find_clock(line)
        if said is None:
            if not held:  # (a held line waits for its label, then `_suggest_after_label`)
                await self._maybe_suggest_time(table, user_id, line)  # (#1758) a suggestion at most
            return
        now = time.monotonic()
        last = table.clock_said.get(said.key)
        if last is not None and now - last < CLOCK_PHRASE_GAP_S:
            return  # a rest said as starting and finishing, or the same words twice: once
        # Marked at once, before any await: the same words arriving by voice and by chat a
        # moment apart must count once (#1173). Taken back on every path that changes nothing.
        table.clock_said[said.key] = now
        marked = [said.key]  # grows if a rest's amount changes the key (taken back with it)
        moved = False
        try:
            moved = await self._clock_phrase_moves(table, user_id, line, said, now, marked)
        finally:
            if not moved:
                for key in marked:
                    table.clock_said.pop(key, None)

    async def _maybe_suggest_time(self, table: Table, user_id: int, line: str) -> None:
        """Vague narration that probably passes game time ("you walk for a while"), which the
        strict phrases don't move the clock for (#1758): at most a suggestion on the DM screen
        with one button, never a change of the clock. Only a DM's line, only while the switch is
        on, the clock is set and not in a fight, the party is not split, and the owner's plan
        allows the AI (closed when it can't be checked). One AI call for the one line and the
        game time, nothing else. Consent and the session are checked again after every wait."""
        if (
            not self.settings.time_suggestions
            or self.time_ai is None
            or self.clocks is None
            or table.campaign_id is None
        ):
            return
        cue = time_suggest.cue_of(line)
        state = table.time_suggest
        now = time.monotonic()
        # (the in-memory DM set first: a player's line costs no database read at all)
        if cue is None or user_id not in table.dm_user_ids or not state.allows(cue, now):
            return
        gid, cid = table.guild_id, table.campaign_id
        campaign = await self.campaigns.get(gid, cid)
        if campaign is None or user_id not in campaign.dm_user_ids:
            return
        stored = await self.clocks.get(gid, cid)
        if stored is None or stored.split or clock_game.in_combat(stored.clock):
            return
        if not await self.plan_allows("ai", gid, campaign, fail_open=False):
            return
        if not self._still_recorded(table, user_id) or not state.allows(cue, now):
            return
        state.asked(cue, now)  # counted before the wait: a slow answer can't be asked twice
        try:
            reply = await asyncio.wait_for(
                self.time_ai.complete(
                    time_suggest.SYSTEM,
                    time_suggest.prompt(line, clock_game.label(stored.clock.minute)),
                    max_tokens=time_suggest.ANSWER_TOKENS,
                ),
                time_suggest.TIMEOUT_S,
            )
        except Exception as exc:
            state.failed(now)
            log.info("Time suggestion: couldn't ask (%s)", type(exc).__name__)
            return
        state.answered(reply.input_tokens, reply.output_tokens)
        suggestion = time_suggest.read_answer(reply.text)
        if suggestion is None or campaign.dm_screen_channel_id is None:
            return
        # Again after the wait: the clock as it is now, then (last, right before posting) the
        # person, who must still be one of the DMs, and the session.
        again = await self.clocks.get(gid, cid)
        if again is None or again.split or clock_game.in_combat(again.clock):
            return
        if not self._still_recorded(table, user_id) or user_id not in table.dm_user_ids:
            return
        try:
            await self.post_message(
                campaign.dm_screen_channel_id,
                time_suggest_screen.suggestion_text(line, suggestion.minutes),
                time_suggest_screen.suggestion_view(cid, suggestion.minutes),
            )
        except Exception:
            log.warning("Time suggestion: couldn't post it", exc_info=True)  # (never the line)

    async def _suggest_after_label(self, table: Table, user_id: int, line: str) -> None:
        """A DM's held line was labelled game talk: the same checks `_note_clock_phrase` makes
        for a line that was not held, then the suggestion (#1758). A line the strict phrases or
        the combat phrases match is not a vague one: the clock phrase path has it already."""
        if not self._still_recorded(table, user_id):
            return
        if clock_phrases.find_clock(line) is not None or combat_phrases.find_combat(line):
            return
        await self._maybe_suggest_time(table, user_id, line)

    def _still_recorded(self, table: Table, user_id: int) -> bool:
        return (
            self.tables.get(table.guild_id) is table
            and table.listening
            and self.consent.has_consent(table.guild_id, user_id)
        )

    async def _clock_phrase_moves(
        self,
        table: Table,
        user_id: int,
        line: str,
        said: clock_phrases.Said,
        now: float,
        marked: list[str],
    ) -> bool:
        """The rest of `_note_clock_phrase`: True once the clock was moved. `marked` lists
        the window keys set so far, so the caller can take them all back."""
        assert table.campaign_id is not None
        campaign = await self.campaigns.get(table.guild_id, table.campaign_id)
        if campaign is None or user_id not in campaign.dm_user_ids:
            return False
        covered = await self._less_what_a_rest_covers(table, campaign, said, now)
        if covered is None:
            return False
        said = covered
        # Consent and the session are checked again after the awaits above, as for a voice line.
        if (
            self.tables.get(table.guild_id) is not table
            or not table.listening
            or not self.consent.has_consent(table.guild_id, user_id)
        ):
            return False
        if await self._split_holds_phrase(campaign):
            return False  # (#1607) the party is split: nothing moves, the DM was told once
        table.clock_said[said.key] = now  # (the amount a rest covered may have changed the key)
        marked.append(said.key)
        try:
            result = await clock_screen.press(
                self,
                campaign.guild_id,
                campaign.id,
                user_id,
                said.action,
                minutes=said.minutes,
                refuse_if_split=True,  # (#1607) checked again under the lock, not only above
            )
        except Exception:
            log.exception("The game clock couldn't take what was said at the table")
            return False
        if (
            result.before is None
            or result.after is None
            or result.after.minute == result.before.minute
        ):
            return False  # no clock set yet, or already there ("at dawn" at dawn): nothing to say
        table.clock_spoken_rest = (
            (said.action, result.after.minute - result.before.minute, result.after.minute, now)
            if said.action in ("short", "long")
            else None
        )
        stored = await self.clocks.get(campaign.guild_id, campaign.id) if self.clocks else None
        if stored is None:
            return True
        await clock_screen.show(self, campaign, stored)
        await clock_screen.update_players(self, campaign, said.action, result)  # #1116
        await timer_screen.announce_due(self, campaign, stored.clock.minute)
        await clock_screen.say(
            self,
            campaign,
            [
                clock_screen.rest_note(said.action, result.after, said.minutes),
                # What was heard, so the DM can tell a misheard line from a real one: dropped
                # (the clock change stays) if consent was withdrawn during the awaits (#1173).
                *self._heard_quote(table, user_id, line),
                *result.lines,
            ],
            clock_screen.undo_id(result.before, result.after),
        )
        return True

    def _heard_quote(self, table: Table, user_id: int, line: str) -> list[str]:
        """The "(heard: …)" line for a DM-screen note, or nothing if the speaker no longer
        agrees to be recorded or the session is over: checked right before it is said."""
        if self.tables.get(table.guild_id) is not table or not self.consent.has_consent(
            table.guild_id, user_id
        ):
            return []
        return [f"(heard: “{discord.utils.escape_markdown(' '.join(line.split())[:80])}”)"]

    async def _note_combat_phrase(
        self, table: Table, user_id: int, line: str, fight: combat_phrases.CombatSaid
    ) -> None:
        """A DM clearly saying combat starts, which round it is, or that it is over (#1127):
        the combat state follows, with one DM-screen line and an Undo. Only the campaign's DMs
        (checked here and again where the state is changed); a player's words never reach
        this. Nothing happens when no clock is set up yet or the state already matches."""
        now = time.monotonic()
        last = table.clock_said.get(fight.key)
        if last is not None and now - last < COMBAT_PHRASE_GAP_S:
            return
        # Marked before any await, so the same words by voice and by chat count once (#1173);
        # taken back on every path that changes nothing.
        table.clock_said[fight.key] = now
        changed = False
        try:
            changed = await self._combat_phrase_changes(table, user_id, line, fight, now)
        finally:
            if not changed:
                table.clock_said.pop(fight.key, None)

    async def _combat_phrase_changes(
        self,
        table: Table,
        user_id: int,
        line: str,
        fight: combat_phrases.CombatSaid,
        now: float,
    ) -> bool:
        """The rest of `_note_combat_phrase`: True once the combat state was changed."""
        assert table.campaign_id is not None
        campaign = await self.campaigns.get(table.guild_id, table.campaign_id)
        if campaign is None or user_id not in campaign.dm_user_ids:
            return False
        # Consent and the session are checked again after the await above, as for a voice line.
        if (
            self.tables.get(table.guild_id) is not table
            or not table.listening
            or not self.consent.has_consent(table.guild_id, user_id)
        ):
            return False
        if await self._split_holds_phrase(campaign):
            return False  # (#1607) the party is split: which group's fight? The buttons say
        action = {"start": "cstart", "next": "cnext", "round": "cround", "end": "cend"}[fight.kind]
        try:
            result = await clock_screen.press(
                self,
                campaign.guild_id,
                campaign.id,
                user_id,
                action,
                set_to=fight.number,
                refuse_if_split=True,
            )
        except CampaignError:
            return False  # no longer a DM: said quietly, not an error
        except Exception:
            log.exception("Combat couldn't take what was said at the table")
            return False
        if result.before is None or result.after is None or result.after == result.before:
            return False  # nothing changed: don't burn the window
        stored = await self.clocks.get(campaign.guild_id, campaign.id) if self.clocks else None
        if stored is None:
            return True
        await clock_screen.show(self, campaign, stored)
        await clock_screen.update_players(self, campaign, action, result, announce=False)
        await timer_screen.announce_due(self, campaign, stored.clock.minute)
        await clock_screen.say(
            self,
            campaign,
            [
                clock_screen.combat_note(action, result.before, result.after),
                *self._heard_quote(table, user_id, line),
                *result.lines,
            ],
            combat_undo=clock_screen.combat_undo_id(result.before, result.after),
        )
        return True

    async def _split_holds_phrase(self, campaign: Campaign) -> bool:
        """True while the party is split (#1607): a spoken rest, time or round moves nothing,
        because DMbot can't know which group the DM meant. The DM screen says so once per
        split. A clock that can't be read counts as not split (nothing is held back)."""
        if self.clocks is None:
            return False
        try:
            stored = await self.clocks.get(campaign.guild_id, campaign.id)
            if stored is None or not stored.split:
                return False
            if await self.clocks.claim_phrase_notice(campaign.guild_id, campaign.id):
                await clock_screen.say(self, campaign, [clock_screen.SPLIT_PHRASE])
        except Exception:
            log.exception("Couldn't check whether the party is split")
            return False
        return True

    async def _less_what_a_rest_covers(
        self, table: Table, campaign: Campaign, said: clock_phrases.Said, now: float
    ) -> clock_phrases.Said | None:
        """A rest said at the table, then its finish or an amount that it already covers, must
        not move the clock twice (#1117). If the last clock change was a spoken rest and the
        clock is still where it left it: a finish of the same kind adds nothing, however long
        ago; an amount said soon after adds only what is more than the rest took. Anything
        else, or a clock that has since moved, is taken as said."""
        spoken = table.clock_spoken_rest
        if spoken is None:
            return said
        kind, took, minute, at = spoken
        stored = await self.clocks.get(campaign.guild_id, campaign.id) if self.clocks else None
        if stored is None or stored.clock.minute != minute:
            table.clock_spoken_rest = None  # a button, an Undo or another phrase moved it
            return said
        if said.ends and said.action == kind:
            return None
        # (A spoken rest followed by a skip of days is taken as said, in full: the skip is a new cut
        # of the scene, not the rest's own length, #1594.)
        if said.action == "advance" and said.minutes is not None and now - at < CLOCK_PHRASE_GAP_S:
            extra = said.minutes - took
            if extra <= 0:
                return None
            return clock_phrases.Said("advance", extra)
        return said

    async def _suggest_rest(self, table: Table, user_id: int, line: str) -> None:
        """A player's "let's take a short rest": one line on the DM screen with **Short rest**
        and **Not now**. Nothing moves until a DM presses, and only if the DM has set a clock
        (otherwise there is nothing for the button to move)."""
        kind = clock_phrases.suggested_rest(line)
        if kind is None or table.campaign_id is None or self.clocks is None:
            return
        now = time.monotonic()
        if (
            table.clock_suggested_at is not None
            and now - table.clock_suggested_at < clock_screen.SUGGEST_GAP_S
        ):
            return
        table.clock_suggested_at = now  # before the awaits: two players at once ask once
        campaign = await self.campaigns.get(table.guild_id, table.campaign_id)
        if (
            campaign is None
            or campaign.dm_screen_channel_id is None
            or await self.clocks.get(campaign.guild_id, campaign.id) is None
        ):
            table.clock_suggested_at = None  # nothing was asked: don't hold the next one off
            return
        name = discord.utils.escape_markdown(self.name_of(table.guild_id, user_id))
        posted = await self.post_message(
            campaign.dm_screen_channel_id,
            f"🛌 {name} suggests a {kind} rest.",
            clock_screen.suggestion_view(campaign.id, kind),
        )
        if posted is None:
            table.clock_suggested_at = None  # the DM never saw it: don't hold the next one off

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
        if table.campaign_id is not None:
            try:  # this campaign's own house rules, then its followed set; no other's
                async with asyncio.timeout(RULES_CARD_DB_S):
                    rules = await self.all_house_rules(table.guild_id, table.campaign_id)
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

    def _warm_rules(self, rulesets: tuple[str, str], table: Table | None = None) -> None:
        """Build the names to look for off the event loop (about 0.15 s), so the first line
        of a session with rules cards on doesn't wait for it. With the table, read the
        campaign's own homebrew names too (#1524)."""
        self._track(asyncio.to_thread(rules_cards.spotter_for, *rulesets), "rules-cards-warm")
        if table is not None:
            self._track(self._load_homebrew_spotter(table), "homebrew-spotter")

    async def _load_homebrew_spotter(self, table: Table) -> None:
        """Read this campaign's homebrew names for its rules cards (#1524). Only this
        campaign's own; a failure costs the homebrew cards, never the session."""
        if self.homebrew_reader is None or table.campaign_id is None:
            return
        table.homebrew_read += 1
        mine = table.homebrew_read
        try:
            entries = await self.homebrew_reader.list(table.guild_id, table.campaign_id)
        except Exception:
            log.exception("Couldn't read the homebrew names for the rules cards")
            return
        if mine != table.homebrew_read:  # a newer read started meanwhile: its result stands
            return
        table.homebrew_spotter = HomebrewSpotter.from_entries(entries) if entries else None

    @property
    def homebrew_reader(self) -> HomebrewReader | None:
        """What a campaign reads: its own homebrew, then its followed packs' in the DM's order
        (#1527). Built on use from the two stores, so it always matches them."""
        if self.homebrew is None:
            return None
        packs = self.rule_sets if isinstance(self.rule_sets, HomebrewPackStore) else None
        return HomebrewReader(self.homebrew, packs, self.sources)

    def homebrew_changed(self, guild_id: int, campaign_id: str) -> None:
        """A DM added, changed, removed or loaded homebrew (#1524): a session running for that
        campaign reads the new names from now on."""
        for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
            if table is not None and table.campaign_id == campaign_id and table.rules_on:
                self._track(self._load_homebrew_spotter(table), "homebrew-spotter")

    async def _post_rules_card(
        self,
        table: Table,
        user_id: int,
        mention: Mention | HomebrewMention,
        card_id: str,
        before: float | None,
    ) -> None:
        """Post the card in the DM screen only (never the transcript, never to players).
        A card that can't be shown gives its name and its minute back."""
        if isinstance(mention, HomebrewMention):
            await self._post_homebrew_card(table, user_id, mention, card_id, before)
            return
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
        if table.campaign_id is not None:
            try:  # this campaign's own house rules, then its followed set; no other's
                async with asyncio.timeout(RULES_CARD_DB_S):
                    rules = await self.all_house_rules(table.guild_id, table.campaign_id)
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

    @staticmethod
    def _books_behind(
        spotter: HomebrewSpotter, found: Sequence[HomebrewMention], target: str, fallback: str
    ) -> frozenset[str]:
        """Normalized names of the shared-book entries just found that rank behind the free rules'
        entry of the same name (#1569): they give way to its card."""
        srd = rules_index.srd()
        behind: set[str] = set()
        for mention in found:
            entry = spotter.entry(mention.name)
            if entry is None or not entry.from_book:
                continue
            hit = srd.lookup(mention.name, target, fallback, kind=mention.kind)
            if not book_wins(entry, hit, target, fallback):
                behind.add(rules_index.normalize(mention.name))
        return frozenset(behind)

    async def _post_homebrew_card(
        self,
        table: Table,
        user_id: int,
        mention: HomebrewMention,
        card_id: str,
        before: float | None,
    ) -> None:
        """The card for one of the campaign's own homebrew entries (#1524), on the DM screen only:
        the entry is read again (it may have been removed or changed since the spotter was built),
        and the speaker must still be recorded and the session still running."""
        if self.homebrew_reader is None or table.campaign_id is None:
            table.rules.forget(card_id, before)
            return
        rules: list[HouseRule] = []
        try:
            async with asyncio.timeout(RULES_CARD_DB_S):
                own = await self.homebrew_reader.find(
                    table.guild_id, table.campaign_id, mention.name
                )
                rules = await self.all_house_rules(table.guild_id, table.campaign_id)
        except Exception:
            log.exception("Couldn't read the homebrew for a rules card")
            own = None
        if (
            own is None
            or not self.consent.has_consent(table.guild_id, user_id)
            or not table.rules_on
            or not table.listening
            or self.tables.get(table.guild_id) is not table
        ):
            table.rules.forget(card_id, before)
            return
        text = rule_card.homebrew_alert_text(
            own,
            table.campaign_name,
            mention.said,
            mention.heard,
            rules,
            legacy=book_is_legacy(own, table.rules_rulesets[0]),
        )
        posted = await self.post_message(
            table.screen_channel_id, text, rules_cards.card_view(table.guild_id, card_id)
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
                        self._warm_rules(table.rules_rulesets, table)
        return campaign

    def _topic_holds(self, table: Table, text: str, named: int) -> bool:
        """Would the off-topic filter hold this line for a label? One rule for the line's
        hold, the names scan and the word list, so they can't disagree (#1748). No AI key, a
        session finishing, a resting filter and a line plainly about the game are never held
        (when unsure, keep). The cost of holding the words with the line: a held line's words
        reach the word list when its window is labelled, up to a window later. And it is the
        cleaned line's words (a name fix included) that count, as for the names scan. The
        callers run in one synchronous stretch (no await between), so they agree. A typed line
        (#2447) is judged by the same rule but holds no words: its words reach the names scan and
        the word list when it is typed, and the label only decides whether the cleaned transcript
        shows it. Typing over the rate limit skips the filter, so it always shows."""
        if self.topic_ai is None or self.tables.get(table.guild_id) is not table:
            return False
        return not (time.monotonic() < table.topic_paused_until or obviously_game(text, named))

    def _topic_wait(
        self,
        table: Table,
        user_id: int,
        started_ms: int,
        text: str,
        seconds: float,
        *,
        typed: bool = False,
    ) -> None:
        """Put a line in the off-topic filter's window (#52); the window is asked about when it
        fills or has waited long enough."""
        first = not table.topics.lines
        waiting = Waiting(user_id, started_ms, text, seconds, typed)
        if table.topics.add(waiting, time.monotonic()):
            self._topic_task(table, self._label_topics(table, table.topics.take()))
        elif first:
            later = self._label_topics_later(table, table.topics.opened_at)
            self._topic_task(table, later, timer=True)

    def _topic_pending(self, table: Table, utterance: Utterance, text: str, holds: bool) -> bool:
        """The off-topic filter (#52): a line not plainly about the game waits for the
        AI, a window at a time; True if it's waiting. No AI key, or a session still
        finishing: no filter, nothing is hidden. `holds` is the line's one answer from
        `_topic_holds` (worked out once, so the callers can't disagree)."""
        if not holds:
            return False
        key = (utterance.user_id, utterance.start_ms)
        table.held[key] = text
        self._topic_wait(table, utterance.user_id, utterance.start_ms, text, utterance.duration_s)
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
            # (shutdown: no vocabulary note; it lives in memory and is going away)
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
            table.held_spelled.pop((waiting.speaker, waiting.started_ms), None)
            table.held_pronounced.pop((waiting.speaker, waiting.started_ms), None)
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
            # (a typed line holds no words: a spoken line may share its key)
            key = None if waiting.typed else (waiting.speaker, waiting.started_ms)
            said = table.held.pop(key, None) if key else None
            spelling = table.held_spelled.pop(key, None) if key else None
            sounds = table.held_pronounced.pop(key, None) if key else None
            if not self.consent.has_consent(table.guild_id, waiting.speaker):
                continue  # stopped meanwhile: nothing about their words
            if topic != OFF_TOPIC and spelling:
                self._record_spelled(table, waiting.speaker, spelling)
            if topic != OFF_TOPIC and sounds:
                self._count_pronounced(table, waiting.speaker, sounds)
            if topic != OFF_TOPIC and said is not None and len(table.heard) < HEARD_MAX:
                table.heard.append((waiting.speaker, said))
            if topic != OFF_TOPIC and said is not None and waiting.speaker in table.dm_user_ids:
                self._track(  # game talk by a DM: now it may be offered a time suggestion (#1758)
                    self._suggest_after_label(table, waiting.speaker, said), "time-suggest"
                )
            try:
                if (
                    topic != OFF_TOPIC
                    and said is not None
                    and self.settings.secret_reveal_ask
                    and table.is_dm(waiting.speaker)
                ):
                    # A held DM line that turned out to be game talk is asked about like any
                    # other (#2101); `_reveal_ask` checks consent and the table again itself.
                    # A line labelled after the session has ended is not asked, on purpose: the
                    # after-session question covers it, and `_reveal_ask` finds no table then.
                    self._track(self._reveal_ask(table, waiting.speaker, said), "secret-reveal-ask")
            except Exception:  # never costs the rest of the window its labels
                log.exception("Secret reveal: couldn't look at a held line")
            if topic != OFF_TOPIC and said is not None:
                # Game talk: its words count now. A window the filter couldn't judge (a slow
                # or failed call, a resting filter) arrives here as game talk, as everywhere.
                table.vocabulary.note(waiting.speaker, said)
            if topic != GAME:
                await self._save_topic(table, waiting, topic)
            if topic == OFF_TOPIC:
                hide.append(waiting)
        if hide:
            await self._hide_lines(table, hide, lines)

    def _speaker_pending(self, table: Table, utterance: Utterance, text: str) -> bool:
        """Who the DM was speaking as (#53): the DM's own lines, not other people's, wait
        for the AI a window at a time. No AI key, no names to choose from, a session
        already finishing, or a resting helper: no label (never a guess in the file). True if the
        line is now waiting to be labelled."""
        if (
            self.topic_ai is None
            or utterance.user_id not in table.dm_user_ids
            or table.name_lookup is None
            or self.tables.get(table.guild_id) is not table
            or time.monotonic() < table.speaker_paused_until
            or not table.speaker_book.allowed(table.name_lookup)
        ):
            return False
        first = not table.speakers.lines
        waiting = Waiting(utterance.user_id, utterance.start_ms, text, utterance.duration_s)
        if table.speakers.add(waiting, time.monotonic()):
            self._speaker_task(table, self._label_speakers(table, table.speakers.take()))
        elif first:
            later = self._label_speakers_later(table, table.speakers.opened_at)
            self._speaker_task(table, later, timer=True)
        return True

    def _ask_when_labelled(self, table: Table, user_id: int, started_ms: int, line: str) -> None:
        """The DM's spoken look-up (#1624). The line's speaker label comes from an AI call over a
        window of the DM's lines, so it is not known when the line is first handled. For a line
        that clearly asks DMbot something (and not more than once in EARLY_CLOSE_GAP_S), the window
        is closed at once and the question waits, bounded, for that call; the label is taken from
        the call itself, so a line already saved to the database is still judged. No label in time
        or not sure enough: asked as before. Called with the line already in its window and before
        anything awaits, so no labelling that includes it can end unseen."""
        now = time.monotonic()
        if now - table.early_close_at < EARLY_CLOSE_GAP_S:  # not another AI call this soon
            self.sidebar.ask_at_table(table, user_id, line)
            return
        table.early_close_at = now
        key = (user_id, started_ms)
        waiter: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        table.label_waits[key] = waiter
        task = self._track(
            self._ask_after_label(table, user_id, key, line, waiter), "sidebar-after-label"
        )
        if task is not None:  # the end of the session waits for it like any labelling window
            table.speaker_tasks.add(task)
            task.add_done_callback(table.speaker_tasks.discard)

    async def _ask_after_label(
        self,
        table: Table,
        user_id: int,
        key: tuple[int, int],
        line: str,
        waiter: asyncio.Future[str],
    ) -> None:
        entity = ""
        try:
            async with asyncio.timeout(TOPIC_CALL_TIMEOUT_S * 2 + 2):
                lines = table.speakers.take()  # (maybe this line, maybe it is already on its way)
                if lines and not self._closing:
                    await self._label_speakers(table, lines)
                entity = await waiter
        except Exception:
            log.info("Sidebar: couldn't wait for the speaker label; asking without it")
        finally:
            table.label_waits.pop(key, None)
        # The session may have moved on, or the DM stopped being recorded, while it waited.
        if self.tables.get(table.guild_id) is not table or not self.consent.has_consent(
            table.guild_id, user_id
        ):
            return
        self.sidebar.ask_at_table(table, user_id, line, entity=entity)

    @staticmethod
    def _label_known(table: Table, speaker: int, started_ms: int, entity: str) -> None:
        """A line's label is known (or known to be none): a look-up waiting for it goes on."""
        waiter = table.label_waits.get((speaker, started_ms))
        if waiter is not None and not waiter.done():
            waiter.set_result(entity)

    def _speaker_task(self, table: Table, work: Awaitable[None], *, timer: bool = False) -> None:
        task = self._track(work, "speakers")
        if task is not None:
            tasks = table.speaker_timers if timer else table.speaker_tasks
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def _finish_speakers(self, table: Table) -> None:
        """At the end of a session: wait (bounded) for windows being labelled, then label
        the last one. On shutdown there's no time: those lines get no label."""
        for timer in list(table.speaker_timers):
            timer.cancel()
        pending = [t for t in table.speaker_tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=TOPIC_CALL_TIMEOUT_S + 2)
        lines = table.speakers.take()
        if lines and not self._closing:
            await self._label_speakers(table, lines)

    async def _label_speakers_later(self, table: Table, opened_at: float | None) -> None:
        await asyncio.sleep(table.speakers.window_s)
        if table.speakers.lines and table.speakers.opened_at == opened_at:
            lines = table.speakers.take()
            # Awake and working: the end of the session waits for it like any window
            # instead of cancelling it mid-call with the other timers.
            this = asyncio.current_task()
            if this is not None and this in table.speaker_timers:
                table.speaker_timers.discard(this)
                table.speaker_tasks.add(this)
                this.add_done_callback(table.speaker_tasks.discard)
            await self._label_speakers(table, lines)

    async def _current_names(self, table: Table) -> CampaignLookup | None:
        """The campaign's names as they are now (a name may just have been made secret), or
        None if they can't be had quickly."""
        if self.lookup is None or table.campaign_id is None:
            return None
        try:
            return await self.lookup.get_within(table.guild_id, table.campaign_id, SPEAKER_WAIT_S)
        except Exception:
            log.warning("Speaker labels: couldn't load the names", exc_info=True)
            return None

    async def _label_speakers(self, table: Table, lines: list[Waiting]) -> None:
        """`_label_speakers_now`, and whatever happens, every line in the window is known to have
        its answer by the end (a look-up waiting for a label goes on with "none", #1624)."""
        try:
            await self._label_speakers_now(table, lines)
        finally:
            for waiting in lines:
                self._label_known(table, waiting.speaker, waiting.started_ms, "")

    async def _label_speakers_now(self, table: Table, lines: list[Waiting]) -> None:
        """Ask the AI about a window of the DM's lines (one call, bounded) and save the
        labels it is sure of. A "maybe", a slow or failed call, or a name that is no longer
        allowed when the answer comes back: no label. Consent is checked again after every
        wait, and so are the names, right before a label is saved."""
        if not lines or self.topic_ai is None:
            return
        lines = [w for w in lines if self.consent.has_consent(table.guild_id, w.speaker)]
        lookup = await self._current_names(table)
        if not lines or lookup is None:
            return
        last_said = {e: times[-1][0] for e, times in table.scene.said.items() if times}
        try:
            guesses, reply = await asyncio.wait_for(
                speaker_ai.identify(
                    self.topic_ai, lookup, [w.text for w in lines], last_said, table.speaker_book
                ),
                TOPIC_CALL_TIMEOUT_S,
            )
            table.speaker_calls += 1
            table.speaker_tokens[0] += reply.input_tokens
            table.speaker_tokens[1] += reply.output_tokens
            table.speaker_failures = 0
        except Exception as exc:
            table.speaker_failures += 1
            if table.speaker_failures >= TOPIC_FAILURES_BEFORE_REST:
                table.speaker_paused_until = time.monotonic() + TOPIC_REST_S
                table.speaker_failures = 0
                log.warning("Speaker labels: resting after failures (%s)", type(exc).__name__)
            else:
                log.info("Speaker labels: couldn't ask (%s); no labels", type(exc).__name__)
            return
        now = await self._current_names(table)  # the DM may have made a name secret meanwhile
        if now is None:
            return
        for waiting, guess in zip(lines, guesses, strict=True):
            entity = speaker_ai.check_entity(now, guess.entity) if guess.sure else ""
            if entity:
                await self._save_entity(table, waiting, entity)
            elif not guess.sure:  # no label in the file; the DM may say who it was
                self._offer_speaker_question(table, waiting, guess, now, last_said)

    def _offer_speaker_question(
        self,
        table: Table,
        waiting: Waiting,
        guess: speaker_ai.Guess,
        lookup: CampaignLookup,
        last_said: dict[str, float],
    ) -> None:
        """Ask the DM "Who was speaking just now?" about a line the AI wasn't sure of (#53),
        in the DM screen only: see `SpeakerBook` for the limits. The guess, if any, is the
        first button, then the NPCs said most lately. Not asked when the DM screen is quiet,
        or the session is finishing."""
        if (
            self.tables.get(table.guild_id) is not table
            or not screen_levels.allows(table.screen_level, screen_levels.QUESTION)
            or not self.consent.has_consent(table.guild_id, waiting.speaker)
        ):
            return
        now = time.monotonic()
        if table.speaker_questions.expire(now) is not None:
            self._track(
                self._close_speaker_question(table, speaker_q.not_answered_text()), "speaker-q"
            )
        guessed = speaker_ai.check_entity(lookup, guess.entity)
        said = speaker_ai.offered(lookup, last_said, table.speaker_book)
        options = list(dict.fromkeys(n for n in [guessed, *said] if n and n != NARRATING))
        asked = table.speaker_questions.offer(
            waiting.speaker, waiting.started_ms, waiting.text, options, now
        )
        if asked is not None:
            self._track(self._ask_speaker(table, asked), "speaker-question")

    async def _ask_speaker(self, table: Table, asked: speaker_q.Asked) -> None:
        """Post the question to the DM screen. Consent is checked again first: this runs
        after the line was delivered."""
        book = table.speaker_questions
        if not self.consent.has_consent(table.guild_id, asked.speaker):
            if book.is_open(asked.id):
                book.close(speaker_q.STOPPED, time.monotonic())
            return
        text = speaker_q.question_text(discord.utils.escape_markdown(asked.context))
        message = await self.post_message(
            table.screen_channel_id, text, speaker_question_view(table.guild_id, asked)
        )
        if message is None:
            if book.is_open(asked.id):  # not posted: free the slot, ask again later
                book.close(speaker_q.NOT_POSTED, time.monotonic())
            return
        if book.is_open(asked.id):
            table.speaker_message = message
            return
        closed = (  # closed while posting: say how
            speaker_q.GONE
            if book.why_closed(asked.id) == speaker_q.STOPPED
            else speaker_q.not_answered_text()
        )
        with contextlib.suppress(discord.HTTPException):
            await message.edit(content=closed, view=None)

    async def _close_speaker_question(self, table: Table, text: str) -> None:
        """Take the question's buttons down, saying why."""
        message, table.speaker_message = table.speaker_message, None
        if message is not None:
            with contextlib.suppress(discord.HTTPException):
                await message.edit(content=text, view=None)

    def _speaker_table(self, guild_id: int, question_id: str) -> Table | None:
        for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
            if table is not None and (
                table.speaker_questions.is_open(question_id)
                or table.speaker_questions.why_closed(question_id) is not None
            ):
                return table
        return None

    async def answer_speaker_question(
        self, guild_id: int, question_id: str, pick: str, user_id: int, typed: str | None = None
    ) -> tuple[str, bool]:
        """The DM answered "Who was speaking just now?" (#53): what to tell them, and
        whether the question is now closed (its message then shows the answer). The name
        is checked against the names a label may take, as they are now, so a secret name
        is never written; consent is checked before and after every wait."""
        table = self._speaker_table(guild_id, question_id)
        book = table.speaker_questions if table is not None else None
        asked = book.open if book is not None else None
        if table is None or book is None or asked is None or asked.id != question_id:
            gone = book is not None and book.why_closed(question_id) == speaker_q.STOPPED
            return (speaker_q.GONE if gone else speaker_q.EXPIRED), True
        if not table.is_dm(user_id):
            return speaker_q.ONLY_DM, False
        if not self.consent.has_consent(guild_id, asked.speaker):
            book.close(speaker_q.STOPPED, time.monotonic())
            return speaker_q.GONE, True
        began = book.begin(question_id)
        if began is speaker_q.Begin.BUSY:
            return speaker_q.BUSY, False
        if began is speaker_q.Begin.GONE:
            return speaker_q.EXPIRED, True
        try:
            if pick == speaker_q.SKIP:
                book.close(speaker_q.ANSWERED, time.monotonic())
                return speaker_q.SKIPPED, True
            wanted = typed if pick == speaker_q.TYPE else speaker_q.pick_to_name(asked, pick)
            # As they are now (a name may have been made secret since), and never waited on
            # for long: the question can't be answered meanwhile.
            lookup = await self._current_names(table)
            if lookup is None:
                book.failed(question_id)
                return speaker_q.CANT_CHECK, False
            name = speaker_ai.check_entity(lookup, wanted or "")
            if not name:
                book.failed(question_id)
                return speaker_q.NOT_ALLOWED, False
            if not self.consent.has_consent(guild_id, asked.speaker) or not book.is_open(
                question_id
            ):
                book.close(speaker_q.STOPPED, time.monotonic())
                return speaker_q.GONE, True
            await self._save_entity(
                table, Waiting(asked.speaker, asked.started_ms, asked.context, 0.0), name
            )
        except BaseException:
            book.failed(question_id)
            raise
        if book.is_open(question_id):
            book.close(speaker_q.ANSWERED, time.monotonic())
        return speaker_q.answered_text(discord.utils.escape_markdown(name)), True

    async def _save_entity(self, table: Table, waiting: Waiting, entity: str) -> None:
        """Save who a line was said as, waiting or saved. Only while its speaker is recorded."""
        guild_id, speaker, started = table.guild_id, waiting.speaker, waiting.started_ms
        self._label_known(table, speaker, started, entity)  # (a look-up waiting for it, #1624)
        async with table.save_lock:
            if self.consent.has_consent(guild_id, speaker):
                found = table.unsaved.set_entity(speaker, started, entity)
                session_id = table.transcript_session_id
                if not found and self.transcripts is not None and session_id is not None:
                    try:
                        await self.transcripts.set_entity(
                            guild_id, session_id, speaker, started, entity
                        )
                    except Exception:
                        log.exception("Couldn't save who a line was said as")
        if self.settings.live_heads_up and entity and entity != NARRATING:
            self._track(self._live_heads_up(table, speaker, entity), "live-heads-up")

    async def _live_heads_up(self, table: Table, speaker: int, label: str) -> None:
        """The first live heads-up (#2068, docs/STORY_MEMORY.md section 4, level 1): a line was
        just labelled as a character the DM confirmed dead. Code only, no AI; a post on the DM
        screen alone, and nothing is written without the DM's press. Runs only while that
        speaker is recorded (asked again after every wait), while the session runs, on a plan that
        includes story memory (closed if that can't be checked), and never on Quiet. A failure
        costs the heads-up, never the line."""
        gid, cid = table.guild_id, table.campaign_id
        lookup = table.name_lookup
        if (
            self.memory is None
            or cid is None
            or lookup is None
            or self.tables.get(gid) is not table
            or not screen_levels.allows(table.screen_level, screen_levels.WARNING)
            or not self.consent.has_consent(gid, speaker)
        ):
            return
        entity_id = speaker_ai.entity_for(lookup, label)  # exactly one entry, by the label's rule
        entity = lookup.entities.get(entity_id) if entity_id is not None else None
        if entity is None:
            return
        live = table.live_heads_up
        with log_context(guild_id=gid, campaign_id=cid):
            try:
                campaign = await self.campaigns.get(gid, cid)
                if campaign is None or not await self.plan_allows(
                    "story", gid, campaign, fail_open=False
                ):
                    return
                if live.ready is None:  # read once a session
                    live.ready = await ContinuityStore(self.memory).is_ready(
                        gid, cid, self.settings.continuity
                    )
                if not live.ready or not self.consent.has_consent(gid, speaker):
                    return
                state = (await self.memory.states(gid, cid, [entity.id])).get(entity.id)
                if not self.consent.has_consent(gid, speaker) or self.tables.get(gid) is not table:
                    return
                before = live.last_at
                found = live_heads_up_for(
                    entity,
                    state,
                    ready=live.ready,
                    session=live,
                    now=time.monotonic(),
                    cooldown_s=self.settings.live_heads_up_cooldown_s,
                )
                if found is None:
                    return
                posted = False
                try:
                    number = None
                    if self.transcripts is not None and found.since is not None:
                        sessions = await self.transcripts.sessions(gid, cid, limit=1000)
                        number = {s.started_at: s.number for s in sessions}.get(found.since)
                    if (
                        not self.consent.has_consent(gid, speaker)
                        or self.tables.get(gid) is not table
                        or table.campaign_id != cid
                        or not screen_levels.allows(table.screen_level, screen_levels.WARNING)
                    ):
                        return
                    posted = bool(
                        await self.post(
                            table.screen_channel_id,
                            heads_up_text(found, number),
                            view=heads_up_view(cid, found.entity_id),
                        )
                    )
                finally:
                    if not posted:
                        live.last_at = before  # nothing was said, so the cooldown is not used
                if not posted:
                    return
                log.info("Live heads-up posted")  # no names, no words
            except Exception:
                log.exception("The live heads-up failed")

    async def _save_topic(self, table: Table, waiting: Waiting, topic: str) -> None:
        """Save a line's topic, waiting or saved. Only while its speaker is recorded."""
        guild_id, speaker, started = table.guild_id, waiting.speaker, waiting.started_ms
        async with table.save_lock:
            if self.consent.has_consent(guild_id, speaker):
                found = table.unsaved.set_topic(speaker, started, topic, typed=waiting.typed)
                session_id = table.transcript_session_id
                if not found and self.transcripts is not None and session_id is not None:
                    try:
                        await self.transcripts.set_topic(
                            guild_id, session_id, speaker, started, topic, typed=waiting.typed
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
                if waiting.typed:  # typed chat is never in the live channel (a spoken line may
                    continue  # share its key): only the saved transcript hides it
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

    def _clean(self, table: Table, heard: str, *, unsure: bool, count: bool = False) -> Cleaned:
        """The line with misheard names fixed (#127), from the campaign's names as last
        loaded; as heard if cleaning fails. `unsure`: also fixes from names DMbot only
        suggested and close look-alikes (shown with Undo). `count`: table speech, whose close calls
        are counted for the session (#1577). No await: the consent check just made still holds."""
        assert table.name_lookup is not None
        try:
            result = clean(
                table.name_lookup,
                heard,
                vocabulary=table.vocabulary,
                people=table.people,
                scene=table.scene.scene(time.monotonic()).keys(),
                unsure=unsure,
                close_calls=table.close_calls if count else None,
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

    def _look_back(
        self, table: Table, utterance: Utterance, text: str, result: Cleaned
    ) -> lookback.Seam | None:
        """A name split across this piece and the same person's one before (#127): a question
        for the DM, never a change (`lookback`). Only while this session runs, the DM screen
        allows questions, and nothing is being asked already."""
        prev = table.last_piece.get(utterance.user_id)
        if (
            prev is None
            or result.questions
            or table.name_lookup is None
            or table.questions.open is not None
            or self.tables.get(table.guild_id) is not table
            or not screen_levels.allows(table.screen_level, screen_levels.QUESTION)
        ):
            return None
        this = lookback.Piece(utterance.start_ms, utterance.end_ms, text, result)
        try:
            return lookback.find(
                table.name_lookup,
                prev,
                this,
                people=table.people,
                vocabulary=table.vocabulary,
                scene=table.scene.scene(time.monotonic()).keys(),
            )
        except Exception:
            log.exception("Couldn't look back across two pieces of speech")
            return None

    def _offer_question(
        self,
        table: Table,
        speaker: int,
        started_ms: int,
        heard: str,
        result: Cleaned,
        seam: lookback.Seam | None = None,
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
            self._keep_unanswered(table, expired)
            self._track(self._close_question(table, self._not_answered(table, expired)), "q")
        if not screen_levels.allows(table.screen_level, screen_levels.QUESTION):
            return  # quiet: the words stay as heard (after expiring any still open)
        # A name split across two pieces is asked about only when this piece has no question
        # of its own (the look-back pass, #127).
        questions = result.questions or ((seam.question,) if seam is not None else ())
        if not questions:
            return
        lookup, scene = table.name_lookup, table.scene.scene(now)
        matters = {
            entity_id
            for question in questions
            for entity_id, _ in question.options
            if entity_id in scene
            or getattr(lookup.entities.get(entity_id), "is_player_character", False)
        }
        across = seam if seam is not None and not result.questions else None
        asked = table.questions.offer(
            speaker,
            questions,
            now,
            matters,
            started_ms=started_ms,
            line=heard,
            fixes=result.fixes,
            prev_started_ms=across.prev.started_ms if across else 0,
            prev_line=across.prev.heard if across else "",
            prev_fixes=across.prev.result.fixes if across else (),
            prev_start=across.prev_start if across else 0,
            prev_end=across.prev_end if across else 0,
        )
        if asked is not None:
            table.names_asked += 1
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
        asked = await self._rank_question(table, asked)
        if not self.consent.has_consent(table.guild_id, asked.speaker):  # (after the wait)
            if book.is_open(asked.id):
                book.close(name_questions.STOPPED, time.monotonic())
            return
        if not book.is_open(asked.id):
            return  # closed or replaced during the wait: nothing to post
        name = self.name_of(table.guild_id, asked.speaker)
        speaker = "Someone" if name.startswith("<@") else discord.utils.escape_markdown(name)
        md = discord.utils.escape_markdown
        if asked.prev_line:  # a name split across two pieces: show both (#127)
            text = name_questions.seam_text(
                speaker,
                md(name_questions.last_words(asked.prev_line[: asked.prev_end], 4)),
                md(name_questions.first_words(asked.line, 4)),
            )
        else:
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

    async def _rank_question(
        self, table: Table, asked: name_questions.Asked
    ) -> name_questions.Asked:
        """Ask the fast AI which option is most likely and put it first, marked "(likely)"
        (#2048). It only orders the buttons: the DM still answers, and any failure, an
        answer of "none", the per-session cap or a secret name near the line leaves the
        question as it was. Counts only are logged. Never held up longer than `TIMEOUT_S`."""
        ai = self.topic_ai
        names = [name for _, name in asked.options]
        if (
            ai is None
            or table.rank_calls >= self.settings.cleaner_rank_max
            or len(asked.options) < 2
            or len(set(names)) < len(names)  # two of one name: a pick by name would be unsure
        ):
            return asked
        lookup = await self._names_now(table)  # fresh: a name may have just been made secret
        if lookup is None:
            return asked
        # A clue only some people know, or an entry the players haven't been told of (#2321), is as
        # secret as a secret name.
        secret = {
            entry.key
            for entry in lookup.names
            if entry.secret
            or getattr(lookup.entities.get(entry.entity_id), "unseen_by_players", False)
        }
        agreed = [
            t for _, who, t in table.recent.spoken if self.consent.has_consent(table.guild_id, who)
        ]
        scene = agreed[-rank_options.SCENE_LINES :]
        line = f"{asked.prev_line} {asked.line}" if asked.prev_line else asked.line
        if not rank_options.safe_to_send([*names, asked.heard, line, *scene], secret):
            return asked  # a secret name is in it, or is an option: nothing is sent
        table.rank_calls += 1
        try:
            reply = await asyncio.wait_for(
                ai.complete(
                    rank_options.SYSTEM,
                    rank_options.prompt(asked.heard, line, scene, names),
                    max_tokens=rank_options.MAX_TOKENS,
                    schema=rank_options.schema(names),
                ),
                rank_options.TIMEOUT_S,
            )
        except Exception:
            table.rank_errors += 1
            return asked
        choice = None if reply.refused or reply.cut else rank_options.read(reply.text, names)
        if choice is None:
            table.rank_none += 1
            return asked
        table.rank_likely += 1
        return table.questions.rank(asked.id, choice) or asked

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
        # A picture's words need the speaker's yes to pictures, as writing them did.
        agreed = (
            self.consent.has_image_consent
            if note.fix.how == IMAGE_EDIT
            else self.consent.has_consent
        )
        if not agreed(guild_id, note.speaker):
            self._redraw_fix_notes(table)  # their line shouldn't be listed any more
            return fix_notes.STOPPED, None
        undone = table.fix_notes.undo(note_id)  # before any await: undone once
        if not undone:
            return fix_notes.ALREADY, None
        allow: tuple[str, int] | None = None
        if note.fix.how in (DM_EDIT, IMAGE_EDIT):
            pass  # a line rewritten by hand (#1499): no name, so no "keep as heard" rule
        else:
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
        if not agreed(guild_id, note.speaker):
            return fix_notes.STOPPED, allow  # their words aren't put back anywhere
        changed = await self._rewrite_line(
            table,
            note.speaker,
            note.started_ms,
            table.fix_notes.line_text(note),
            image=note.fix.how == IMAGE_EDIT,
        )
        if not agreed(guild_id, note.speaker):
            return fix_notes.STOPPED, allow  # stopped meanwhile: nothing about their line
        if note.fix.how in (DM_EDIT, IMAGE_EDIT):
            return fix_notes.edit_done_text(line_kept=not changed), None
        md = discord.utils.escape_markdown
        text = fix_notes.done_text(md(note.fix.heard), md(note.fix.written), line_kept=not changed)
        return text, allow

    async def tell_user(self, user_id: int, text: str) -> bool:
        """One private message to a person; False if Discord won't deliver it (private messages
        off). Never raises."""
        try:
            user = self.get_user(user_id) or await self.fetch_user(user_id)
            if user.bot:
                return False
            await user.send(text, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            return False
        return True

    async def apply_line_fix(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        user_id: int,
        started_ms: int,
        text: str,
        *,
        noted: bool = False,
        image: bool = False,
        by: int = 0,
        by_dm: bool = False,
    ) -> bool:
        """Write a fix the DM accepted into one line's cleaned words (#1491); what was heard
        never changes. `noted` (a DM's own edit, #1499): in a running session the change is listed
        in the "✏️ Name fixes to check" message with Undo, as a whole-line fix; a line that
        already has a fix or an answer on it is not edited then (an Undo of the older one would
        bring the old words back over this edit). A running session's line goes through the same
        writer as every fix (the saved line, the one waiting to be saved, and the channel message
        while it is recent); a finished session's saved line is changed in the database. Only
        while the speaker is still recorded. True if a line changed. `image` (#2450): the line
        describes a picture the speaker posted, which is kept apart from their speech; a correction
        to it is always listed with Undo, and needs the speaker's yes to pictures. `by` and
        `by_dm`: who made the fix and whether they are a DM: a second correction replaces the
        earlier one only if it was theirs, or they are a DM."""
        if image:
            if not self.consent.has_image_consent(guild_id, user_id) or self.transcripts is None:
                return False
            noted = True
        elif not self.consent.has_consent(guild_id, user_id) or self.suggestions is None:
            return False
        for table in [self.tables.get(guild_id), *self._ending.get(guild_id, [])]:
            if (
                table is not None
                and table.campaign_id == campaign_id
                and table.transcript_session_id == session_id
            ):
                if image:
                    assert self.transcripts is not None  # (checked above)
                    read = await self.transcripts.image_line(
                        guild_id, session_id, user_id, started_ms
                    )
                    own = (
                        None
                        if read is None
                        else line_suggestions.Own(started_ms, read[0], read[1], user_id)
                    )
                else:
                    assert self.suggestions is not None  # (checked above)
                    own = await self.suggestions.line(guild_id, session_id, user_id, started_ms)
                if noted:
                    asked = table.questions.open
                    # A picture's words corrected before are replaced by the new correction (the
                    # Undo of the new one puts back the words as first read), not refused: only
                    # a DM's Undo could ever clear the old note.
                    earlier = (
                        table.fix_notes.picture_edits(user_id, started_ms, by, by_dm)
                        if image
                        else []
                    )
                    if (
                        own is None
                        or earlier is None  # not theirs to replace
                        or (table.fix_notes.on_line(user_id, started_ms) and not earlier)
                        # An open "Did they mean…?" about this line would write over the edit
                        # from the words as heard when it is answered.
                        or (
                            asked is not None
                            and asked.speaker == user_id
                            and started_ms in (asked.started_ms, asked.prev_started_ms)
                        )
                    ):
                        return False
                    # The note is over the line's words now (not as heard), so an Undo puts
                    # those back and keeps any name already fixed in it.
                    base = earlier[0].fix.heard if earlier else own.text
                    edit = Fix(
                        0, len(base), base, text, "",
                        IMAGE_EDIT if image else DM_EDIT, sure=False,
                    )  # fmt: skip
                    before = list(table.fix_notes.notes)
                    table.fix_notes.notes = [n for n in before if n not in earlier]
                    table.fix_notes.add(user_id, started_ms, base, (edit,), by=by)
                    words = table.fix_notes.words_now(user_id, started_ms, base, (edit,))
                    done = await self._rewrite_line(table, user_id, started_ms, words, image=image)
                    if done:
                        self._redraw_fix_notes(table)
                    else:  # nothing changed: nothing to undo; the earlier notes stay as they were
                        table.fix_notes.notes = before
                    return done
                if own is not None:
                    # Like an answer to "Did they mean…?": the notes know the line's words
                    # now, so a later Undo of a name fix keeps this.
                    table.fix_notes.answered(
                        fix_notes.Answer(
                            None, user_id, started_ms, own.heard, (), 0, len(own.heard), text
                        )
                    )
                return await self._rewrite_line(table, user_id, started_ms, text)
        if self.transcripts is None:
            return False
        return bool(
            await self.transcripts.relabel_line(
                guild_id,
                session_id,
                user_id,
                started_ms,
                text,
                **({"image": True} if image else {}),
            )
        )

    async def _rewrite_line(
        self, table: Table, speaker: int, started_ms: int, text: str, *, image: bool = False
    ) -> bool:
        """Change a line's words (#296, #503): saved, waiting to be saved, and in the
        transcript channel if it was posted in the last ~30 s. Only while the speaker is
        still recorded, checked again after every wait. True if the line changed: saved
        or waiting, or the channel message. All or nothing: if the saved line can't be
        changed (a database error, logged), the channel isn't either, so "That line stays
        as heard" is true everywhere."""
        guild_id, found, edited = table.guild_id, False, False
        agreed = self.consent.has_image_consent if image else self.consent.has_consent
        # The saved line: hold the save lock, so a batch being saved can't miss this.
        async with table.save_lock:
            if agreed(guild_id, speaker):
                found = table.unsaved.relabel(speaker, started_ms, text, image=image)
                session_id = table.transcript_session_id
                if not found and self.transcripts is not None and session_id is not None:
                    try:
                        found = bool(
                            await self.transcripts.relabel_line(
                                guild_id,
                                session_id,
                                speaker,
                                started_ms,
                                text,
                                **({"image": True} if image else {}),
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
            # (a typed line, a picture's too, is never in the channel)
            if agreed(guild_id, speaker) and not hidden and not image:
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
        changed = found or edited
        if changed and not image and agreed(guild_id, speaker):
            self._line_changed(table, speaker, started_ms, text)
        return changed

    def _line_changed(self, table: Table, speaker: int, started_ms: int, new: str) -> None:
        """A piece's words changed after the helpers read it (#127): bring the helpers' COPIES
        of it up to date (the names scan's lines, the sidebar's recent scene, the off-topic
        filter's waiting copy, and which names count as heard and in the scene). Helpers that
        act on a piece (rules cards, house rules, the clock, sheet re-reads, the sidebar trigger)
        are never run again: acting twice is worse than missing a name. A piece the helpers no
        longer hold (too old, trimmed) is skipped quietly."""
        key = (speaker, started_ms)
        kept = table.piece_words.get(key)
        if kept is None or kept[0] == new:
            return
        old, said_at = kept
        table.piece_words[key] = (new, said_at)
        # A copy is changed only when it is certainly this piece's: the names scan's line only
        # if the piece isn't held back or hidden (then it isn't in that list) and the words
        # match exactly one entry (two identical lines from one person can't be told apart, and
        # a missed update is safer than a wrong one).
        if key not in table.held and key not in table.hidden:
            matches = [i for i, h in enumerate(table.heard) if h == (speaker, old)]
            if len(matches) == 1:
                table.heard[matches[0]] = (speaker, new)
        table.recent.revise(speaker, old, new)
        if key in table.held:
            table.held[key] = new
        table.topics.revise(speaker, started_ms, new)
        lookup = table.name_lookup
        if lookup is None:
            return
        try:
            before, after = set(mentions(lookup, old)), set(mentions(lookup, new))
        except Exception:
            log.exception("Couldn't update which names a changed line mentions")
            return
        now = time.monotonic()
        if after - before and now - said_at <= SCENE_FRESH_S:
            # Only a piece said lately says its name "now": an old one must not push a name
            # up the hints. Taking a name off after an Undo leaves the scene alone (it fades).
            table.scene.note(after - before, speaker, now)
        for entity_id in after - before:
            table.heard_counts[entity_id, speaker] += 1
        for entity_id in before - after:
            if table.heard_counts.get((entity_id, speaker), 0) > 1:
                table.heard_counts[entity_id, speaker] -= 1
            else:
                table.heard_counts.pop((entity_id, speaker), None)

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
            if line.typed:  # its words reached the scan and the word list when it was typed
                continue
            now_said = self._words_now(table, line)
            if len(table.heard) < HEARD_MAX:
                table.heard.append((line.speaker, now_said))
            table.vocabulary.note(line.speaker, now_said)  # and its words count now (#1748)
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
                line
                for line in lines
                if not table.unsaved.set_topic(speaker, line.started_ms, GAME, typed=line.typed)
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
                changed = 0
                for typed in (False, True):  # (typed chat and speech can share a millisecond)
                    started = [line.started_ms for line in saved if line.typed == typed]
                    if started:
                        changed += await asyncio.wait_for(
                            self.transcripts.set_topics(
                                guild_id, session_id, speaker, started, GAME, typed=typed
                            ),
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
        # typed lines never had a marker in the live channel and can share a spoken line's key
        markers = [x for x in lines if not x.typed and (x.speaker, x.started_ms) in table.hidden]
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
        if asked.likely:  # did the DM take the AI's first option? (#2048: a count, nothing more)
            table.rank_answered += 1
            if pick == "0":
                table.rank_picked += 1
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
        earlier = await self._fix_earlier_lines(table, asked, name, undo)
        md = discord.utils.escape_markdown
        answer = name_questions.fixed_text(
            heard,
            md(name),
            line_fixed=line is True,
            line_kept=line is False,
            new=chosen[0] is None,
            earlier=earlier,
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
        if asked.prev_line:
            return await self._fix_seam(table, asked, name, undo)
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

    async def _fix_seam(
        self, table: Table, asked: name_questions.Asked, name: str, undo: tuple[str, int] | None
    ) -> bool | None:
        """Write the answer to a name split across two pieces (#127, the look-back pass): the
        name goes where the tail words were in the earlier piece, and the head words are taken
        out of this one. Both or neither: if the second can't be written the first is put back,
        so the transcript never says the name twice. Never if it would put a secret name in
        either piece (checked with the names as they are now), if another answer or a hidden
        line is in the way, or if either piece is no longer there to change; Undo of the answer
        puts both back (`answer_undone`). True if both changed, False if they stay as heard."""
        lookup = await self._names_now(table)
        notes, speaker = table.fix_notes, asked.speaker
        written = with_ending(asked.heard, name)
        before = notes.still_fixed(speaker, asked.prev_started_ms, asked.prev_fixes)
        now_fixes = notes.still_fixed(speaker, asked.started_ms, asked.fixes)
        stop = lookback.head_edit(asked.line, asked.end)[1]
        prev_key, this_key = (speaker, asked.prev_started_ms), (speaker, asked.started_ms)
        if (
            lookup is None
            or prev_key in table.hidden
            or this_key in table.hidden
            or _answer_in_the_way(notes, prev_key, asked.prev_start, asked.prev_end)
            or _answer_in_the_way(notes, this_key, 0, stop)
            # the name beside the words before it (earlier piece) and the words after the head
            or not safe_answer(
                lookup, asked.prev_line, before, asked.prev_start, asked.prev_end, written
            )
            or not safe_answer(lookup, asked.line, now_fixes, 0, asked.end, written)
        ):
            return False  # the rule is saved; both pieces stay as heard
        batch = undo[1] if undo is not None else None
        tail = fix_notes.Answer(
            batch, speaker, asked.prev_started_ms, asked.prev_line, before,
            asked.prev_start, asked.prev_end, written, True,
        )  # fmt: skip
        head = fix_notes.Answer(
            batch, speaker, asked.started_ms, asked.line, asked.fixes, 0, stop, ""
        )
        notes.answered(tail)
        notes.answered(head)

        def words(answer: fix_notes.Answer) -> str:
            return notes.words_now(answer.speaker, answer.started_ms, answer.heard, answer.fixes)

        touched, first, second = False, False, False
        try:
            touched = True  # (a failure part-way through may still have changed it)
            first = await self._rewrite_line(table, speaker, tail.started_ms, words(tail))
            touched = first
            if first:
                second = await self._rewrite_line(table, speaker, head.started_ms, words(head))
        except Exception:
            log.exception("Couldn't write an answer across two pieces of speech")
            second = False
        if first and second:
            return True
        for answer in (tail, head):
            with contextlib.suppress(ValueError):
                notes.answers.remove(answer)
        if touched:  # the earlier piece may have changed: put it back as it was
            try:
                if not await self._rewrite_line(table, speaker, tail.started_ms, words(tail)):
                    log.warning("Couldn't put the earlier piece back after a failed answer")
            except Exception:
                log.exception("Couldn't put the earlier piece back after a failed answer")
        return False

    async def _fix_earlier_lines(
        self, table: Table, asked: name_questions.Asked, name: str, undo: tuple[str, int] | None
    ) -> int:
        """Write the DM's answer into the earlier lines of this session that said the same
        words (#127, "Learning is immediate"): the saved ones and those still waiting to be
        saved, not the line the question was about (`_fix_asked_line` did that). Only lines
        that still say exactly what was heard, only with the DM's spelling of exactly those
        words, and never where it would put a secret name (`refix.plan`). Each change is
        logged by count, never by its words. Consent is checked again before every line. Undo
        of the answer puts them back with it (`answer_undone`). How many lines were changed;
        a failure here never loses the answer, which is saved already."""
        try:
            lookup = await self._names_now(table)
            if lookup is None or not self.consent.has_consent(table.guild_id, asked.speaker):
                return 0
            saved: list[Line] = []
            session_id = table.transcript_session_id
            if self.transcripts is not None and session_id is not None:
                saved = await self.transcripts.lines(table.guild_id, session_id)
            # Saved and waiting lines, once each (a failed save can leave a line in both). Lines
            # spoken after the asked one, while the question was open, count too: the Cleaner
            # hadn't fixed them either.
            seen: set[tuple[int, int]] = set()
            candidates = []
            for line in [*saved, *table.unsaved.lines()]:
                key = (line.user_id, line.started_ms)
                if (
                    key == (asked.speaker, asked.started_ms)
                    or key in seen
                    or table.fix_notes.on_line(*key)  # already has a fix or an answer
                    or not self.consent.has_consent(table.guild_id, line.user_id)
                ):
                    continue
                seen.add(key)
                candidates.append(line)
            # Cleaning is the slow part and runs off the event loop; the lookup is only read.
            todo = await asyncio.to_thread(
                refix.plan, lookup, candidates, asked.heard, people=list(table.people)
            )
            batch = undo[1] if undo is not None else None
            changed = 0
            for item in todo:
                if not self.consent.has_consent(table.guild_id, item.speaker):
                    continue
                if table.fix_notes.on_line(item.speaker, item.started_ms):
                    continue  # another answer got there first, during the awaits
                answers = [
                    fix_notes.Answer(
                        batch, item.speaker, item.started_ms, item.heard, (), s, e, w, True
                    )
                    for s, e, w in item.edits
                ]
                for answer in answers:
                    table.fix_notes.answered(answer)
                # The words as the notes have them now (like `_fix_asked_line`), not a
                # snapshot from before the awaits.
                words = table.fix_notes.words_now(item.speaker, item.started_ms, item.heard, ())
                try:
                    done = await self._rewrite_line(table, item.speaker, item.started_ms, words)
                except Exception:
                    log.exception("Couldn't write an answer into an earlier line")
                    done = False
                if done:
                    changed += 1
                else:  # a later Undo of the answer must not bring this line's words back
                    for answer in answers:
                        with contextlib.suppress(ValueError):
                            table.fix_notes.answers.remove(answer)
            if changed:
                log.info("An answer to a name question fixed %d earlier line(s)", changed)
            return changed
        except Exception:
            log.exception("Couldn't re-check earlier lines after an answer")
            return 0

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
            taken = []
            while (more := table.fix_notes.take_back(batch)) is not None:
                taken.append(more)
            if not taken:
                continue
            # One answer can have fixed earlier lines too (#127): they go back with it, best
            # effort. What is reported is the line the question was about, if it was fixed.
            asked_line = next((a for a in taken if not a.earlier), None)
            changed: bool | None = None
            # A name split across two pieces (the head's answer has no words) is put back tail
            # first, and the head only if the tail went back: never leave the name twice.
            seam = any(a.written == "" for a in taken)
            ok = True
            for answer in sorted(taken, key=lambda a: not a.earlier):
                if seam and not answer.earlier and not ok:
                    changed = False  # the earlier piece still has the name: leave this one
                    continue
                text = table.fix_notes.words_now(
                    answer.speaker, answer.started_ms, answer.heard, answer.fixes
                )
                try:
                    done = await self._rewrite_line(table, answer.speaker, answer.started_ms, text)
                except Exception:
                    log.exception("Couldn't put a line back after undoing an answer")
                    done = False
                if answer.earlier:
                    ok = ok and done
                if answer is asked_line:
                    changed = done
            if seam and not ok:
                changed = False
            if asked_line is None or not self.consent.has_consent(guild_id, asked_line.speaker):
                return None  # nothing to say about the asked line
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
            spelled=table.spelled_hints,
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

    def _feed_name_answers(self, table: Table, user_id: int, at_ms: int, text: str) -> None:
        """One consented line to the who-answers-to-what learner, which also needs to know who
        the campaign's DMs are and what each is called at the table (#1547). Consent was just
        checked by the caller."""
        dms = {
            uid: self.name_of(table.guild_id, uid)
            for uid in {table.dm_user_id, *table.dm_user_ids}
            if uid
        }
        # (an unknown member comes back as a mention, which is not a name: the DM still counts)
        table.name_answers.set_dms({u: "" if n.startswith("<@") else n for u, n in dms.items()})
        table.name_answers.feed(user_id, at_ms, text, consented=True)

    async def log_close_calls(self, table: Table) -> None:
        """One INFO line at the session's end for dev1 (#1577): how many lines landed near the
        Cleaner's likeness thresholds. Counts only (no words, names or speakers); nothing goes to
        the DM screen or to players. Said once: the counter is emptied as it is read."""
        calls, table.close_calls = table.close_calls, CloseCalls()
        if calls.lines:
            with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
                log.info("%s", calls.report())

    def _keep_unanswered(self, table: Table, asked: name_questions.Asked) -> None:
        """A "Did they mean…?" nobody answered goes to the after-session check (#1572), in memory
        until the session ends. Not one about a name split across two pieces of speech (no one
        line to fix), nor one whose speaker has stopped being recorded."""
        if (
            not asked.line
            or asked.prev_line
            or not self.consent.has_consent(table.guild_id, asked.speaker)
        ):
            return
        table.unanswered.append(asked)
        del table.unanswered[:-MAX_PER_SESSION]  # the oldest go first

    async def save_unanswered(self, table: Table) -> None:
        """After a session: keep the questions that went unanswered for the after-session check,
        and say how many wait (#1572). Only for speakers who still agree, checked again after the
        write; a person who stopped meanwhile has theirs removed. DM screen only."""
        items, table.unanswered = table.unanswered, []  # (lost if the save fails: only a reminder)
        session_id = table.transcript_session_id
        if (
            self.heard_questions is None
            or table.campaign_id is None
            or session_id is None
            or not items
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            agreed = await self.consent.consenting(gid)
            keep = [
                Unanswered(a.speaker, a.started_ms, a.heard, tuple(e for e, _ in a.options))
                for a in items
                if a.speaker in agreed
            ]
            if not keep:
                return
            await self.heard_questions.save(gid, cid, session_id, keep)
            agreed = await self.consent.consenting(gid)  # someone may have stopped meanwhile
            for gone in {k.speaker_id for k in keep if k.speaker_id not in agreed}:
                await self.heard_questions.forget_speaker(gid, gone)
            waiting = await self.heard_questions.count(gid, cid)
            log.info("Did they mean: %d question(s) wait for the DM", waiting)
            if waiting:
                await self.post(
                    table.screen_channel_id,
                    heard_waiting_text(waiting),
                    view=heard_review_view(cid),
                )

    async def answer_waiting_question(
        self, guild_id: int, question_id: int, pick: str, user_id: int, typed: str | None = None
    ) -> tuple[str, bool]:
        """The DM answered a "Did they mean…?" that waited for the after-session check (#1572):
        what to tell them, and whether the question is now closed. A name picked (or typed) is
        saved as the campaign's correction, so later sessions write it that way, and is written
        into that one line of the cleaned transcript (never what was heard); **Keep as heard**
        saves the "don't change this" rule. DMs only, checked here on every press; the speaker
        must still be recorded, checked again after every wait."""
        store, memory = self.heard_questions, self.memory
        if store is None or memory is None or self.lookup is None:
            return name_questions.TYPED_CANT_CHECK, False
        waiting = await store.get(guild_id, question_id)
        if waiting is None:
            return name_questions.EXPIRED, True
        campaign = await self.campaigns.get(guild_id, waiting.campaign_id)
        if campaign is None or user_id not in campaign.dm_user_ids:
            return name_questions.ONLY_DM, False
        if not self.consent.has_consent(guild_id, waiting.speaker_id):
            await store.forget_speaker(guild_id, waiting.speaker_id)
            return name_questions.GONE, True
        heard = waiting.heard
        keep = pick == name_questions.KEEP
        chosen: tuple[str | None, str] | None = None
        if pick == name_questions.TYPE:
            typed = " ".join((typed or "").split())
            if name_key(typed) == name_key(heard):
                keep = True  # typed just as heard
            else:
                why = name_questions.typed_problem(typed)
                if why is not None:
                    return why, False
                found = self._known_typed(await self.lookup.get(guild_id, campaign.id), typed)
                if isinstance(found, str):
                    return found, False
                chosen = found
        elif not keep:
            if not (pick.isdigit() and int(pick) < len(waiting.option_ids)):
                return name_questions.EXPIRED, False  # a stale button: the question stays
            lookup = await self.lookup.get(guild_id, campaign.id)
            entity_id = waiting.option_ids[int(pick)]
            written = own_name(lookup, entity_id)
            if written is None:  # removed, or made secret, since
                await store.decide(guild_id, question_id, KEPT, user_id)
                return name_questions.NAME_GONE, True
            chosen = (entity_id, written)
        if not await store.decide(guild_id, question_id, KEPT if keep else ANSWERED, user_id):
            return name_questions.EXPIRED, True  # answered on another screen meanwhile
        name = ""
        try:
            if chosen is None:
                await memory.add_correction(guild_id, campaign.id, heard, action=KEEP, source=DM)
            elif chosen[0] is None:  # a new name, to check in 📝 Check new names
                name = chosen[1]
                await memory.add_typed_name(guild_id, campaign.id, heard, name)
            else:
                name = chosen[1]
                await memory.add_correction(
                    guild_id, campaign.id, heard, action=FIX, source=DM, entity_id=chosen[0]
                )
        except MemoryRuleError:
            await store.reopen(guild_id, question_id)
            if chosen is None:  # Keep as heard: nothing about a name
                return ANSWER_NOT_SAVED, False
            if chosen[0] is None:
                return name_questions.TYPED_REFUSED, False
            return name_questions.NAME_GONE, True
        except Exception:
            await store.reopen(guild_id, question_id)
            raise
        self.lookup.mark_stale(guild_id, campaign.id)  # the next line uses the answer
        shown = discord.utils.escape_markdown(heard)
        if chosen is None:
            return name_questions.kept_text(shown), True
        fixed = await self._fix_waiting_line(guild_id, campaign.id, waiting, name)
        md = discord.utils.escape_markdown
        return (
            name_questions.fixed_text(
                shown, md(name), line_fixed=fixed, line_kept=not fixed, new=chosen[0] is None
            ),
            True,
        )

    async def _fix_waiting_line(
        self, guild_id: int, campaign_id: str, waiting: WaitingQuestion, name: str
    ) -> bool:
        """Write the answer into the one stored line the question was about (cleaned words only).
        Not if the words are not in the line exactly once now, if the line is gone, or if the name
        would put a secret one in the line (checked with the names as they are now): the line
        then stays as heard. True if the line changed."""
        if self.lookup is None:
            return False
        text = fix_words(waiting.text, waiting.heard, name)
        span = find_once(waiting.text, waiting.heard)
        if (
            text is None
            or span is None
            or not self.consent.has_consent(guild_id, waiting.speaker_id)
        ):
            return False
        try:  # the answer is saved already: a failure here only leaves the line as heard
            lookup = await self.lookup.get(guild_id, campaign_id)
            if not safe_answer(lookup, waiting.text, (), span[0], span[1], name):
                return False
            return await self.apply_line_fix(
                guild_id,
                campaign_id,
                waiting.session_id,
                waiting.speaker_id,
                waiting.started_ms,
                text,
            )
        except Exception:
            log.exception("Couldn't write an answer into its stored line")
            return False

    async def link_the_party(self, table: Table) -> None:
        """After a live session: the player characters whose players took part know each other and
        are members of The party (#3036). By rule, from who had a line in the stored session; only
        people who still agree count. Counts only in the log."""
        if self.memory is None or table.campaign_id is None or table.transcript_session_id is None:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            await self._link_the_party(gid, cid, table.transcript_session_id)

    async def _link_the_party(self, gid: int, cid: str, session_id: str) -> None:
        """Link the party for one stored session's speakers. Shared by live sessions and imports, so
        the rule and the consent check are the same for both."""
        assert self.memory is not None and self.transcripts is not None
        speakers = await session_speakers(self.transcripts.db, gid, cid, session_id)
        # Agreed, not "recorded now": a pause is temporary, and who took part is a fact (#3036).
        agreed = await self.consent.agreed(gid)
        linked = await self.memory.link_party(gid, cid, {u for u in speakers if u in agreed})
        log.info(
            "The party: %d characters, %d pairs, %d members",
            linked.characters,
            linked.pairs,
            linked.members,
        )

    async def learn_people(self, table: Table) -> None:
        """After a session: turn the names people answered to at least twice into waiting
        questions for the DM (#1188). Only people who still agree; the questions are saved
        as waiting, and the DM screen only says how many."""
        answers = table.name_answers.answered()
        table.name_answers = NameThenAnswer()  # the session is over; free it
        if self.memory is None or table.campaign_id is None or not answers:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            agreed = await self.consent.consenting(gid)
            rows = [(a.name, a.speaker, a.times) for a in answers if a.speaker in agreed]
            if not rows:
                return
            written = await self.memory.learn_people(gid, cid, rows, limit=MAX_SUGGESTIONS)
            # Again: someone may have stopped recording while this was written.
            agreed = await self.consent.consenting(gid)
            for gone in {s.user_id for s in written.value if s.user_id not in agreed}:
                await self.memory.forget_learned(gid, gone)
            kept = [s for s in written.value if s.user_id in agreed]
            log.info("Who is who: %d question(s) from this session", len(kept))
            if kept:
                await self.post(
                    table.screen_channel_id, waiting_text(len(kept)), view=people_review_view(cid)
                )

    def start_plan_read(
        self, guild_id: int, campaign_id: str, document_id: str, start: int
    ) -> None:
        """Begin reading an adventure into the story plan in the background (#1714). The store has
        already checked the DM, the switch, the confirmation and the caps (`PlanStore.begin`)."""
        key = (guild_id, document_id)
        if key in self.plan_reads:
            return
        task = asyncio.create_task(self._read_plan(guild_id, campaign_id, document_id, start))
        self.plan_reads[key] = task
        task.add_done_callback(lambda _: self.plan_reads.pop(key, None))

    async def _read_plan(
        self, guild_id: int, campaign_id: str, document_id: str, start: int
    ) -> None:
        """One read, start to finish. It ends the document as done, or as stopped with the reason
        (a stopped read carries on from where it got to). The log gets counts only."""
        if self.plan is None or self.plan_ai is None:
            return
        plan, ai = self.plan, self.plan_ai

        async def plan_allowed() -> bool:
            campaign = await self.campaigns.get(guild_id, campaign_id)
            # Closed, not open: a plan that can't be checked is not run (as for the extractor).
            return campaign is not None and await self.plan_allows(
                "adventure", guild_id, campaign, fail_open=False
            )

        key = (guild_id, document_id)
        now = time.monotonic()
        clock = self.plan_clocks[key] = plan_reader.ReadClock(now, now, now)

        async def moved(_position: int) -> None:
            clock.moved = time.monotonic()  # a call came back (stored, or cut and read in halves)

        with log_context(guild_id=guild_id, campaign_id=campaign_id):
            try:
                self.plan_waiting.add(key)
                async with self._plan_slots:
                    self.plan_waiting.discard(key)
                    clock.began = clock.moved = time.monotonic()  # the turn has come
                    report = plan_reader.Report()  # the caller's: a cut-off run keeps its counts
                    # The whole read ends within MAX_RUN_S, a call in progress included (the
                    # reader checks the time only between calls), so the card's "stops by itself
                    # after" is true (#2564).
                    limit = asyncio.timeout(plan_reader.MAX_RUN_S)
                    try:
                        async with limit:
                            report = await plan_reader.run(
                                ai,
                                plan,
                                guild_id=guild_id,
                                campaign_id=campaign_id,
                                document_id=document_id,
                                start_chunk=start,
                                model=lambda: ai.model,
                                allowed=plan_allowed,
                                progress=moved,
                                report=report,
                            )
                    except TimeoutError:
                        if not limit.expired():
                            raise  # a store's or a call's own timeout: the generic handler
                        report.stopped = plan_reader.STOP_TIME
                log.info("%s", report.line())
                if report.stopped:
                    await plan.finish(guild_id, campaign_id, document_id, "stopped", report.stopped)
                else:
                    await plan.finish(guild_id, campaign_id, document_id, "done")
                await self._plan_read_notice(guild_id, campaign_id, document_id)
            except asyncio.CancelledError:
                raise  # a restart: the state stays 'reading' and a press carries on
            except Exception:
                log.exception("Story-plan reader failed")
                with contextlib.suppress(Exception):
                    await plan.finish(
                        guild_id, campaign_id, document_id, "stopped", plan_reader.STOP_ERROR
                    )
                    await self._plan_read_notice(guild_id, campaign_id, document_id)
            finally:
                self.plan_waiting.discard(key)
                self.plan_clocks.pop(key, None)

    async def _plan_read_notice(self, guild_id: int, campaign_id: str, document_id: str) -> None:
        """One line on the DM screen when a read ends, done or stopped (#1723): the DM is not
        left pressing Refresh. Never raises: a missing channel or a failed post is only logged."""
        try:
            if self.plan is None or self.adventure is None:
                return
            campaign = await self.campaigns.get(guild_id, campaign_id)
            if campaign is None or campaign.dm_screen_channel_id is None:
                return
            state = (await self.plan.states(guild_id, campaign_id)).get(document_id)
            docs = await self.adventure.documents(guild_id, campaign_id)
            doc = next((d for d in docs if d.id == document_id), None)
            if state is None or doc is None:
                return
            counts = (await self.plan.counts(guild_id, campaign_id)).get(document_id)
            await self.post(
                campaign.dm_screen_channel_id, finished_text(doc.file_name, state, counts)
            )
        except Exception as exc:
            log.warning(
                "Story-plan reader: couldn't post the finish notice (%s)", type(exc).__name__
            )

    async def extract_claims(self, table: Table, ended_at: int) -> None:
        """After a session, only with DMBOT_STORY_EXTRACTOR on (it is off until #1282 passes): read
        the stored transcript in short batches and store what the AI heard as proposals for the DM
        (#1363). Off means off: no AI client, no stored lines read, no prompt built. Only a campaign
        whose plan includes it runs (#1367), within its session and daily call limits, and a
        restart carries on after the lines already read. Only lines from people who still agree
        are sent, and the claims are checked against consent again before they are stored. The
        log gets counts only. Nothing here is a fact."""
        if (
            not self.settings.story_extractor_on
            or self.extractor_ai is None
            or self.memory is None
            or self.transcripts is None
            or table.campaign_id is None
            or table.transcript_session_id is None
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            session = await self.transcripts.session(gid, table.transcript_session_id)
            if session is None or session.lines == 0:
                return
            campaign = await self.campaigns.get(gid, cid)
            # Closed, not open: a plan that can't be checked is not run, and nothing is recorded.
            if campaign is None or not await self.plan_allows(
                "story", gid, campaign, fail_open=False
            ):
                log.info(
                    "Story extractor: not run, the plan does not include it or can't be checked"
                )
                return
            sid = table.transcript_session_id
            after_ms, used = await self.transcripts.extraction(gid, sid)
            now = int(time.time())
            day_used = await self.transcripts.extractor_calls_since(gid, cid, now - 86400)
            allowed = extractor.calls_left(
                self.settings.story_extractor_max_calls,
                used,
                self.settings.story_extractor_max_calls_day,
                day_used,
            )
            if allowed == 0:
                log.info("Story extractor: not run, its call limit is used (%d today)", day_used)
                return
            transcripts = self.transcripts
            lines = await transcripts.lines(gid, sid)
            # The people at the table are not story names (as in the name scan).
            people = {table.dm_user_id, *table.dm_user_ids, *(await self.consent.consenting(gid))}
            skip: set[str] = set()
            for uid in people:
                name = self.name_of(gid, uid)
                if not name.startswith("<@"):
                    skip.add(name_key(name))
                    skip.update(name_key(word) for word in name.split())
            report = await extractor.run(
                self.extractor_ai,
                ClaimStore(self.memory),
                lines,
                guild_id=gid,
                campaign_id=cid,
                dm_ids={table.dm_user_id, *table.dm_user_ids},
                consenting=lambda: self.consent.consenting(gid),
                model=lambda: self.extractor_ai.model if self.extractor_ai else "",
                session_started_at=session.started_at,
                tally=table.steward_tally,
                max_calls=allowed,
                skip=skip,
                after_ms=after_ms,
                readings=self.settings.story_readings_on,
                family_only=self.settings.story_family_only,
                imported=await transcripts.is_import(gid, sid),
                count_call=lambda: transcripts.record_extraction(
                    gid, sid, through_ms=0, calls=1, now=int(time.time())
                ),
            )
            log.info(report.line())
            try:  # what a restart resumes from, and what the day's limit counts
                await transcripts.record_extraction(
                    gid, sid, through_ms=report.through_ms, calls=0, now=now
                )  # (the calls were counted as they were made)
            except Exception:
                log.exception("Story extractor: couldn't note how far it got")
            if report.named:  # the DM checks them where the name scan's are checked
                await self.post(
                    table.screen_channel_id,
                    held_back_text(report.new_names),
                    view=review_view(cid),
                )

    async def read_put_back_lines(self, guild_id: int) -> None:
        """Start the read of the lines DMs put back (#2587) as a tracked background task, at most
        one a server: the extractor's AI calls take minutes, and the poll loop that asks for this
        (about every ten minutes) must not wait for them."""
        task = self._put_back_tasks.get(guild_id)
        if task is not None and not task.done():
            return
        if self.transcripts is None or self.memory is None:
            return
        started = self._track(self._read_put_back_all(guild_id), "put-back-read")
        if started is None:  # shutting down
            return
        self._put_back_tasks[guild_id] = started

        def forget(done: asyncio.Task[None], gid: int = guild_id) -> None:
            if self._put_back_tasks.get(gid) is done:
                del self._put_back_tasks[gid]

        started.add_done_callback(forget)

    async def _read_put_back_all(self, guild_id: int) -> int:
        """Lines a DM put back after their session (#2587): the names scan and the story extractor
        read just those lines. Only the lines of people who agree now are read (a speaker who
        stopped being recorded is skipped, and their line is marked done so it is not asked about
        again); the words go through the same checks as at the end of a session.

        A line is marked read once the story extractor has had its one try (a batch may have been
        sent, so a retry would duplicate claims), or the extractor is not on here. When nothing
        could be sent yet (its call limit is used, or no session row), the line waits for a later
        sweep: the name scan then runs again, which proposes nothing twice, because a name it
        proposed is a known name by the next scan. The log gets counts only. Returns how many
        lines were read."""
        if self.transcripts is None or self.memory is None:
            return 0
        waiting = await self.transcripts.sessions_with_put_back(guild_id)
        total = 0
        for cid, sid in waiting:
            with log_context(guild_id=guild_id, campaign_id=cid):
                try:
                    total += await self._read_put_back_session(guild_id, cid, sid)
                except Exception as exc:  # the type only: a database error can quote a row
                    log.warning("Put-back lines: a session's read failed (%s)", type(exc).__name__)
        return total

    async def _read_put_back_session(self, guild_id: int, cid: str, sid: str) -> int:
        assert self.transcripts is not None and self.memory is not None
        gid = guild_id
        marks = await self.transcripts.put_back_in(gid, sid)
        if not marks:
            return 0
        if any(
            t.campaign_id == cid for t in (self.tables.get(gid), *self._ending.get(gid, [])) if t
        ):
            return 0  # the session's own end-of-session reads are still to run: later, not twice
        campaign = await self.campaigns.get(gid, cid)
        agreed = await self.consent.consenting(gid)
        mine = {(uid, at) for uid, at in marks if uid in agreed}
        every = await self.transcripts.lines(gid, sid)
        chosen = [x for x in every if (x.user_id, x.started_ms) in mine and x.text.strip()]
        if chosen and campaign is not None:
            dms = {*campaign.dm_user_ids}
            users = {x.user_id for x in every} | agreed | dms
            added = await self._propose_new_names(
                gid, cid, [x.text for x in chosen], users, sid, agreed=agreed
            )
            log.info("Put-back lines: %d line(s) scanned, %d new name(s)", len(chosen), len(added))
            # One try once a batch may have been sent: a retry would send the batches that worked
            # again (duplicate claims). Nothing sent yet: the lines wait for the next sweep.
            if not await self._extract_put_back(gid, cid, sid, campaign, every, mine, users):
                return 0
        by_person: dict[int, list[int]] = {}
        for uid, at in marks:  # the skipped speakers' lines too: not asked about again
            by_person.setdefault(uid, []).append(at)
        for uid, started in by_person.items():
            await self.transcripts.mark_read(gid, cid, uid, started)
        return len(chosen)

    async def _extract_put_back(
        self,
        gid: int,
        cid: str,
        sid: str,
        campaign: Campaign,
        every: list[Line],
        mine: set[tuple[int, int]],
        users: Collection[int],
    ) -> bool:
        """The story extractor on just the put-back lines (numbered by their place in the whole
        session, the others marked off-topic so `readable` skips them), once. True when the lines
        are done: it had its try (it ran, stopped early or failed after a batch may have been
        sent) or it is not on here. False when nothing could be sent yet, so the lines wait."""
        assert self.transcripts is not None and self.memory is not None
        if (
            not self.settings.story_extractor_on
            or self.extractor_ai is None
            or not await self.plan_allows("story", gid, campaign, fail_open=False)
        ):
            return True  # nothing to wait for: the extractor is off here
        transcripts = self.transcripts
        session = await transcripts.session(gid, sid)
        if session is None:
            return False
        _, used = await transcripts.extraction(gid, sid)
        now = int(time.time())
        day_used = await transcripts.extractor_calls_since(gid, cid, now - 86400)
        allowed = extractor.calls_left(
            self.settings.story_extractor_max_calls,
            used,
            self.settings.story_extractor_max_calls_day,
            day_used,
        )
        if allowed == 0:
            log.info("Put-back lines: the extractor's call limit is used; the lines wait")
            return False
        only = [
            x if (x.user_id, x.started_ms) in mine else replace(x, topic=OFF_TOPIC) for x in every
        ]
        skip: set[str] = set()
        for uid in users:
            name = self.name_of(gid, uid)
            if not name.startswith("<@"):
                skip.add(name_key(name))
                skip.update(name_key(word) for word in name.split())
        try:
            report = await extractor.run(
                self.extractor_ai,
                ClaimStore(self.memory),
                only,
                guild_id=gid,
                campaign_id=cid,
                dm_ids={*campaign.dm_user_ids},
                consenting=lambda: self.consent.consenting(gid),
                model=lambda: self.extractor_ai.model if self.extractor_ai else "",
                session_started_at=session.started_at,
                max_calls=allowed,
                skip=skip,
                after_ms=0,
                readings=self.settings.story_readings_on,
                family_only=self.settings.story_family_only,
                imported=await transcripts.is_import(gid, sid),
                count_call=lambda: transcripts.record_extraction(
                    gid, sid, through_ms=0, calls=1, now=int(time.time())
                ),
            )
        except Exception as exc:  # a batch may already be stored: done, never run twice
            log.warning("Put-back lines: the extractor failed (%s)", type(exc).__name__)
            return True
        log.info(report.line())
        return report.calls > 0  # no call made: nothing was sent, so the lines wait

    async def grade_contradictions(self, table: Table) -> None:
        """After a session, only with DMBOT_STORY_CONTRADICTIONS on (and the extractor): PlotBot
        grades the clashes one point of view holds, major or minor (#2660), and the grades are kept
        for the DM. It reads no transcript line: the AI is shown only the campaign's own names and
        relationship words. Inside the extractor's plan check and call limits (its calls are
        counted there), and at most `CONTRADICTION_CALLS` a session. Nothing here changes a belief
        or tells the DM; the log gets counts only.

        No consent re-check, on purpose: no audio or transcript line is read or sent here, only the
        DM-confirmed names and relationships the campaign already stores, so there is no
        speaker's consent for a step to depend on."""
        if (
            not self.settings.story_contradictions_on
            or not self.settings.story_extractor_on
            or self.extractor_ai is None
            or self.memory is None
            or self.transcripts is None
            or table.campaign_id is None
            or table.transcript_session_id is None
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None or not await self.plan_allows(
                "story", gid, campaign, fail_open=False
            ):
                return  # closed: a plan that can't be checked is not run
            sid = table.transcript_session_id
            session = await self.transcripts.session(gid, sid)
            _, used = await self.transcripts.extraction(gid, sid)
            day_used = await self.transcripts.extractor_calls_since(
                gid, cid, int(time.time()) - 86400
            )
            allowed = min(
                CONTRADICTION_CALLS,
                extractor.calls_left(
                    self.settings.story_extractor_max_calls,
                    used,
                    self.settings.story_extractor_max_calls_day,
                    day_used,
                ),
            )
            if allowed == 0:
                log.info("Contradictions: not run, the extractor's call limit is used")
                return
            transcripts = self.transcripts
            report = await contradiction_run.run(
                self.memory,
                GradeStore.of(self.memory),
                self.extractor_ai,
                gid,
                cid,
                session_started_at=session.started_at if session is not None else None,
                max_calls=allowed,
                count_call=lambda: transcripts.record_extraction(
                    gid, sid, through_ms=0, calls=1, now=int(time.time())
                ),
            )
            log.info(report.line())

    async def make_report(self, table: Table) -> None:
        """After a session, only with DMBOT_STORY_REPORT on (and the extractor): make the report
        the recap page shows (#2661): grade the clashes not graded yet, find what the party has
        wrong and have PlotBot sort it. It reads no transcript line: the AI is shown only the
        campaign's own names and relationship words. Inside the extractor's plan check and call
        limits (its calls are counted there), and at most `REPORT_CALLS` a session; what the limit
        leaves is made next time and the report says so. The run shows its stage and counts to the
        page, and the log gets counts only. Nothing here changes a belief or tells a player.

        No consent re-check, on purpose: no audio or transcript line is read or sent here, only the
        DM-confirmed names and relationships the campaign already stores."""
        if table.campaign_id is None or table.transcript_session_id is None:
            return
        await self._make_report_for(table.guild_id, table.campaign_id, table.transcript_session_id)

    async def report_for_session_at(self, guild_id: int, campaign_id: str, started_at: int) -> str:
        """The same report for a session the website named by when it began (#2805), when a DM
        asks for it again. "" when it was made; else why not, as one short word (the website's
        row carries it)."""
        if self.transcripts is None:
            return web_report_requests.UNAVAILABLE
        session = await self.transcripts.session_at(guild_id, campaign_id, started_at)
        if session is None:
            return web_report_requests.FAILED
        return await self._make_report_for(guild_id, campaign_id, session.id)

    async def _make_report_for(self, gid: int, cid: str, sid: str) -> str:
        """Make one session's report (see `make_report`). "" when it was made; else why it was
        not: `unavailable` (switched off, or no AI), `plan`, `limit` (the extractor's call limit is
        used) or `failed` (the campaign or session is gone)."""
        if (
            not self.settings.story_report_on
            or not self.settings.story_extractor_on
            or self.extractor_ai is None
            or self.memory is None
            or self.transcripts is None
        ):
            return web_report_requests.UNAVAILABLE
        with log_context(guild_id=gid, campaign_id=cid):
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None:
                return web_report_requests.FAILED
            if not await self.plan_allows("story", gid, campaign, fail_open=False):
                return web_report_requests.PLAN  # closed: a plan that can't be checked is not run
            session = await self.transcripts.session(gid, sid)
            if session is None:
                return web_report_requests.FAILED
            _, used = await self.transcripts.extraction(gid, sid)
            day_used = await self.transcripts.extractor_calls_since(
                gid, cid, int(time.time()) - 86400
            )
            allowed = min(
                REPORT_CALLS,
                extractor.calls_left(
                    self.settings.story_extractor_max_calls,
                    used,
                    self.settings.story_extractor_max_calls_day,
                    day_used,
                ),
            )
            if allowed == 0:
                log.info("Report: not made, the extractor's call limit is used")
                return web_report_requests.LIMIT
            transcripts = self.transcripts
            made = await report_make.make(
                self.memory,
                GradeStore.of(self.memory),
                ReportStore.of(self.memory),
                self.extractor_ai,
                gid,
                cid,
                session.started_at,
                session_started_at=session.started_at,
                max_calls=allowed,
                count_call=lambda: transcripts.record_extraction(
                    gid, sid, through_ms=0, calls=1, now=int(time.time())
                ),
            )
            log.info(made.line())
            # What the website's row says (the after-session step ignores it): a report that never
            # started (a run was going) is not "done", and neither is one that failed inside.
            if not made.ran:
                return web_report_requests.BUSY
            if made.failed == report_make.OUT_OF_FUNDS:
                return web_report_requests.UNAVAILABLE
            if made.failed:
                return web_report_requests.FAILED
            return ""

    async def meaning_leads(self, campaign: Campaign, question: str, asker_id: int) -> list[str]:
        """More starting points for the sidebar's recall by what the question means (#2664).
        "Players": the reply can reach the raw transcript, so an entry the players may not know yet
        is never a lead. Optional: it has its own wait, and a slow or failing search is just no
        leads, said in a line and not a traceback."""
        if self.meaning_seeds is None:
            return []
        # The question is a person's words, typed or spoken, so it goes to Voyage AI only if that
        # person agrees to be recorded (the same rule as a line of the transcript).
        if asker_id not in await self.consent.consenting(campaign.guild_id):
            return []
        try:
            async with asyncio.timeout(MEANING_WAIT_S):
                return await self.meaning_seeds.find(
                    campaign.guild_id, campaign.id, question, for_players=True
                )
        except TimeoutError:
            log.info("Meaning search: no answer in time; answering from the names alone")
            return []

    async def close_meaning(self) -> None:
        """End the connection to Voyage AI, if there is one. (getattr: a bot built without the usual
        start-up has no such attribute, and closing must never be what fails.)"""
        http = getattr(self, "_voyage_http", None)
        if http is not None:
            with contextlib.suppress(Exception):
                await http.close()

    async def meaning_allowed(self) -> bool:
        """Whether text may go to Voyage AI right now: the switch is on and the key is set. Asked
        before every call, so turning the switch off stops a run at its next batch."""
        return self.settings.meaning_search_on

    async def embed_campaign(self, table: Table) -> None:
        """After a session, only with DMBOT_MEANING_SEARCH on and a Voyage key (#2664): bring the
        campaign's numbers for meaning search up to date. What is sent is the campaign's own
        entries and the game talk of people who agree to be recorded (checked before every batch
        and again after the call); nothing else. Inside the story plan, at most
        `MEANING_RUN_CALLS` calls. The log gets counts only."""
        if (
            not self.settings.meaning_search_on
            or self.voyage is None
            or self.memory is None
            or table.campaign_id is None
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None or not await self.plan_allows(
                "story", gid, campaign, fail_open=False
            ):
                return  # closed: a plan that can't be checked is not run
            db = self.memory._db
            await embedding.run(
                VectorStore.of(self.memory),
                self.voyage,
                [embed_sources.LineSource(db), embed_sources.EntitySource(db)],
                self.consent.consenting,
                gid,
                cid,
                self.settings.voyage_embed_model,
                allowed=self.meaning_allowed,
                max_calls=MEANING_RUN_CALLS,
                count_call=self.meaning_calls.count,
            )

    async def tell_contradictions(self, table: Table) -> None:
        """After the grading, with the same switches (#2660): the major clashes PlotBot graded that
        were not told yet, at most `contradiction_notes.SAY_MAX` each time this step runs, as one
        private message. The minor ones are left for the after-session report. No AI call, no
        transcript line read; nothing changes a belief. The line can name an entry, so with a
        private DM screen it goes there (pulled back if the screen opens up), and otherwise to each
        DM by private message with only a number on the screen. A clash is marked told only once a
        message reached the DM, so one that did not is tried again next session."""
        if (
            not self.settings.story_contradictions_on
            or not self.settings.story_extractor_on
            or self.memory is None
            or table.campaign_id is None
            or table.screen_channel_id is None
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None or not await self.plan_allows(
                "story", gid, campaign, fail_open=False
            ):
                return
            grades = GradeStore.of(self.memory)
            waiting = await grades.rows(gid, cid, grade=MAJOR, unsaid=True)
            told = waiting[: contradiction_notes.SAY_MAX]
            log.info("Contradictions: %d major waiting, %d told", len(waiting), len(told))
            if not told:
                return
            texts = contradiction_notes.told_parts(told)  # (split at line ends, never cut short)
            private = campaign.dm_screen_visibility == "private"
            if private:
                sent = True
                for text in texts:
                    sent = sent and await self.post(
                        table.screen_channel_id, text, secret_of=(gid, cid)
                    )
            else:
                # Players may peek at the screen, and the line can name an entry: the DMs' own
                # private messages instead, and nothing named on the screen (as the secret
                # questions do). Told only if a DM got every part.
                results = []
                for dm in campaign.dm_user_ids:
                    got = [await self.sidebar_send_dm(dm, text) for text in texts]
                    results.append("sent" if all(g == "sent" for g in got) else got[-1])
                sent = "sent" in results
                if not sent and "forbidden" in results:
                    await self.post(
                        table.screen_channel_id,
                        contradiction_notes.waiting_text(len(told), private=False),
                    )  # (no name, no number; they stay untold, so a later session tries again)
            if sent:
                await grades.mark_said(
                    gid,
                    cid,
                    [
                        (t.perspective_id, t.kind, t.first_relation_id, t.second_relation_id)
                        for t in told
                    ],
                )

    async def log_steward_tally(self, table: Table) -> None:
        """After the session's other work: one line in the server log of what the ontology
        steward decided (#1303), counts only. Never shown to a player or a DM. Nothing is
        written when it decided nothing, which is every session while the extractor is off.
        The extractor's landing code passes `table.steward_tally` to `ClaimStore.propose(tally=)`
        (not wired yet: `propose` has no caller until the extractor runs)."""
        tally, table.steward_tally = table.steward_tally, Tally()
        if tally.refused or tally.reused or tally.accepted:
            tally.write_to_log()

    async def log_memory_health(self, table: Table, ended_at: int) -> None:
        """After the session's other work: one line in the server log with the story graph's
        health numbers for this campaign (#1699), counts only. Never shown to a player or a DM.
        A failed read is a warning and nothing else: it never stops the steps after it."""
        if self.memory is None or table.campaign_id is None:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            try:
                numbers = await memory_health.read(
                    self.memory,
                    gid,
                    cid,
                    since=table.started_at or ended_at,
                    names_asked=len(table.questions.closed_why),
                )
            except Exception:
                log.warning("Couldn't read the memory health numbers", exc_info=True)
                return
            log.info(numbers.line())

    async def reveal_names_said(self, table: Table) -> None:
        """After a session: a name the story was holding back from the players (a backstory name,
        or one the DM added by hand) that someone at the table said or typed is shown to them now
        (#2404). Code only, whole names, only recorded people's table lines (the DM's included,
        never the sidebar). The DM screen says which, with an Undo. A count in the log."""
        if (
            self.memory is None
            or self.transcripts is None
            or table.campaign_id is None
            or table.transcript_session_id is None
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            lines = await self.transcripts.lines(gid, table.transcript_session_id)
            sessions = await self.transcripts.sessions(gid, cid, limit=1000)
            number = sum(1 for s in sessions if s.started_at <= (table.started_at or 0)) or 1

            async def spoken() -> list[str]:
                # Asked last, right before the names are matched: a person can stop at any moment
                agreed = await self.consent.consenting(gid)
                recorded = {line.user_id for line in lines} & set(agreed)
                return table_lines(lines, recorded=recorded)

            await self._reveal_said(gid, cid, spoken, f"Session {number}")

    async def _reveal_said(
        self, gid: int, cid: str, texts: Callable[[], Awaitable[list[str]]], where: str
    ) -> None:
        """Show the held-back names the table said (`texts` reads the words, after everything
        else is loaded), and say so on the DM screen (see above)."""
        if self.memory is None:
            return
        held = await unrevealed_names(self.memory, gid, cid)
        found = said(held, await texts())
        if not found:
            return
        written = await self.memory.reveal_said(gid, cid, [f.entity_id for f in found])
        if not written.value or written.batch is None:
            return
        if self.lookup is not None:
            self.lookup.drop([cid])
        log.info("Names said at the table and shown: %d", len(written.value))  # a count only
        campaign = await self.campaigns.get(gid, cid)
        if campaign is None or campaign.dm_screen_channel_id is None:
            return
        shown = [f.name for f in found if f.entity_id in set(written.value)]
        waiting = await self.memory.waiting_connections(gid, cid, written.value)
        if not await self.post(
            campaign.dm_screen_channel_id,
            report_text(shown, where=where, waiting=waiting),
            view=revealed_view(cid, written.batch),
        ):
            # The names are shown and the DM could not be told: say so in the log (a count only)
            log.warning("Names shown at the table, but the DM screen could not say so")

    async def record_exposures(self, table: Table) -> None:
        """After a session: remember the secret names and secret connections said or typed at the
        table (#2410), the DM's own words included. Nothing is applied and nobody is told here:
        the players see no change until the summary goes out. Only the words of people recorded
        now count, never the DM sidebar. The log gets a count, never a name."""
        if (
            self.memory is None
            or self.exposures is None
            or self.transcripts is None
            or table.campaign_id is None
            or table.transcript_session_id is None
        ):
            return
        if not table.started_at:  # the session's key: without it a re-run would double the rows
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None:
                return
            names = await secret_names(self.memory, gid, cid)
            connections = await secret_connections(self.memory, gid, cid)
            if not names and not connections:
                return
            lines = await self.transcripts.lines(gid, table.transcript_session_id)
            agreed = await self.consent.consenting(gid)  # asked after the lines were read
            # The DM's own table words count (a slip of the DM's tongue is the first case); the
            # sidebar is left out by said_lines, and so is anyone not recorded now
            players = {line.user_id for line in lines} & set(agreed)
            found = find_exposures(names, connections, said_lines(lines, people=players))
            if not found:
                return
            waiting = await self.exposures.record(gid, cid, table.started_at, found)
            log.info("Secrets said at the table: %d found, %d waiting", len(found), waiting)

    async def send_exposure_summary(self, table: Table) -> None:
        """After a session (#2410, slice 2): tell the DMs what the table said that was secret, then
        show it to the players. One private message (the DM screen while it is private, a private
        message to each DM when players may look at it), each item with a ↩️ Recant button.
        Nothing is shown to the players unless the message went out, and each item is shown in a
        change of its own, so Recant undoes exactly that one. At most `MAX_LISTED` items; the rest
        wait for the next summary and are not shown until then. The log has counts, no names."""
        if (
            self.memory is None
            or self.exposures is None
            or table.campaign_id is None
            or table.screen_channel_id is None
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None:
                return
            rows, stands_for = dedupe(await self.exposures.waiting(gid, cid))
            described = await describe(self.memory, gid, cid, rows) if rows else []
            kept = {row.id for row, _ in described}
            gone = [row.id for row in rows if row.id not in kept]
            if gone:  # no longer secret (the DM changed it meanwhile): nothing to tell
                await self.exposures.mark(
                    gid, cid, [*gone, *(i for g in gone for i in stands_for[g])], "settled"
                )
            # With no secret to tell, the message still goes when there are new names or things to
            # check (#2410): one message after a session, and the Recant part is simply absent.
            if not described and not (table.new_names or table.claims_waiting):
                return
            listed = described[:MAX_LISTED]
            ids = [row.id for row, _ in listed]

            def message(private: bool) -> str:
                return summary_text(
                    [words for _, words in listed],
                    len(described) - len(listed),
                    campaign_name=campaign.name,
                    private=private,
                    new_names=table.new_names,
                    claims_waiting=table.claims_waiting,
                    claim_lines=table.claim_preview,  # (every copy of this message is DM-only)
                    can_check=not private,
                )

            async def by_private_message(dm_ids: Iterable[int]) -> tuple[int, int]:
                reached = closed = 0
                for dm_id in sorted(dm_ids):
                    sent = await self.sidebar_send_dm(
                        dm_id,
                        message(True),
                        summary_view(ids, cid, gid, new_names=bool(table.new_names), private=True),
                    )
                    reached += sent == "sent"
                    closed += sent == "forbidden"
                return reached, closed

            reached = closed = 0
            on_screen = 0  # the message stayed on the DM screen (the count of checks is in it)
            if campaign.dm_screen_visibility != "private":  # players may look at the screen
                reached, closed = await by_private_message(campaign.dm_user_ids)
            else:
                sent = await self.post_message(
                    table.screen_channel_id,
                    message(False),
                    summary_view(
                        ids,
                        cid,
                        gid,
                        new_names=bool(table.new_names),
                        claims=bool(table.claims_waiting),
                    ),
                )
                if sent is not None:
                    # Asked again after the send: if the screen opened up meanwhile the message is
                    # pulled back, so it does not count as told; it goes privately instead.
                    now = await self.campaigns.get(gid, cid)
                    if now is None or now.dm_screen_visibility != "private":
                        await secret_posts.hide_one(sent)
                        reached, closed = await by_private_message((now or campaign).dm_user_ids)
                    else:
                        secret_posts.remember(gid, cid, sent)
                        reached = on_screen = 1
            if not reached:
                log.warning("The secrets summary could not be sent: %d waiting", len(described))
                if closed and described:  # nobody could be told: their private messages are closed
                    await self.post(table.screen_channel_id, SUMMARY_WAITING)
                return  # nothing is shown to the players
            if on_screen or reached >= len(campaign.dm_user_ids):
                # Told: the screen has it, or every DM got it. A co-DM whose private messages are
                # closed has not heard, so then the "new names" step still posts them.
                table.new_names = []
            if on_screen:
                table.claims_waiting = 0  # the screen message carried the count
                table.claim_preview = []
            shown = failed = 0
            for row, _ in listed:  # told: now show them, one change each
                # Claimed first: a Recant pressed since the message went out has already taken the
                # row (the claim then matches nothing and it stays secret), and one pressed now
                # is told to wait, so it can never fall between the showing and the record of it.
                if not await self.exposures.mark(gid, cid, [row.id], "applying"):
                    # taken back (or settled) already: the same secret said in earlier sessions
                    # must not come back in the next summary
                    await self.exposures.mark(gid, cid, stands_for[row.id], "settled")
                    continue
                spelling = [row.spelling] if row.kind == "name" and row.spelling else []
                relation = [row.relation_id] if row.kind == "connection" and row.relation_id else []
                written = None
                for _try in range(2):  # once more if it fails
                    try:
                        written = await self.memory.reveal_exposed(
                            gid, cid, spellings=spelling, relation_ids=relation
                        )
                        break
                    except Exception as exc:  # (the text can carry a name: only the type is logged)
                        log.warning(
                            "Couldn't show a secret said at the table: %s", type(exc).__name__
                        )
                if written is None:
                    await self.exposures.mark(gid, cid, [row.id], "pending")  # the next summary
                    failed += 1
                    continue
                if written.batch is None:
                    await self.exposures.mark(gid, cid, [row.id], "settled")
                else:
                    await self.exposures.mark(gid, cid, [row.id], "applied", written.batch)
                    shown += 1
                if stands_for[row.id]:  # the same secret said in earlier sessions: handled with it
                    await self.exposures.mark(gid, cid, stands_for[row.id], "settled")
            if shown and self.lookup is not None:
                self.lookup.drop([cid])
            if failed:  # the message said "now shown" for these: say it was not, with no name
                for dm_id in sorted(campaign.dm_user_ids):
                    await self.sidebar_send_dm(dm_id, SUMMARY_PARTIAL)
            log.info(
                "Secrets said at the table: %d told, %d shown, %d not", len(listed), shown, failed
            )

    async def ask_about_secrets(self, table: Table) -> None:
        """After a session: a name the DM keeps secret was said by a player, so it is now in the
        transcript everyone may read (#1407). The DM screen asks, privately, once for each such
        name (at most five, most said first), with two buttons. Only players who are recorded
        now count: not the DM, not DMbot, not someone who stopped. No AI call. The log gets a
        count, never a name."""
        if (
            self.memory is None
            or self.transcripts is None
            or table.campaign_id is None
            or table.transcript_session_id is None
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None:
                return
            secrets = await secret_names(self.memory, gid, cid)
            if not secrets:
                return
            lines = await self.transcripts.lines(gid, table.transcript_session_id)
            agreed = await self.consent.consenting(gid)  # asked after the lines were read
            players = {line.user_id for line in lines} & set(agreed) - set(campaign.dm_user_ids)
            every = spoken(secrets, said_lines(lines, people=players))
            found = every[:MAX_ASKED]
            alone = campaign.dm_screen_visibility != "private"
            reached = 0  # DMs who got the question privately
            closed_dms = 0  # DMs whose private messages are closed
            for said in found:
                if alone:  # players may look at the screen: the name goes to the DMs only
                    for dm_id in sorted(campaign.dm_user_ids):
                        sent = await self.sidebar_send_dm(
                            dm_id,
                            question_text(
                                said.text,
                                said.entries,
                                private=True,
                                campaign_name=campaign.name,
                            ),
                            secret_aloud_view(cid, said.alias_id, gid),
                        )
                        reached += sent == "sent"
                        closed_dms += sent == "forbidden"
                    continue
                await self.post(
                    table.screen_channel_id,
                    question_text(said.text, said.entries),
                    view=secret_aloud_view(cid, said.alias_id, gid),
                    secret_of=(gid, cid),  # pulled back if the screen opens up (#2097)
                )
            if alone and found:
                log.info("Secret questions sent privately: %d DM(s) reached", reached)  # a count
                if not reached and closed_dms:
                    # Nobody could be asked because their private messages are closed (a send that
                    # merely failed is not that): say so, with no name.
                    await self.post(table.screen_channel_id, SECRET_QUESTION_WAITING)
            if len(every) > len(found):  # the DM should know the rest exist
                await self.post(table.screen_channel_id, more_text(len(every) - len(found)))
            if found:
                log.info("Secret name(s) said by players: %d asked", len(found))  # a count only

    async def note_claims(self, table: Table) -> None:
        """After a session (#1297): count the proposals the story extractor left waiting. With the
        extractor off (the default) nothing is ever waiting; this step makes no AI call. The
        summary tells the DM when it goes to the DM screen (#2410); `post_claims` otherwise."""
        if self.memory is None or table.campaign_id is None:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            agreed = await self.consent.consenting(gid)
            counts = await ClaimStore(self.memory).counts(gid, cid, opted_in=agreed)
            table.claims_waiting = counts.get("proposed", 0)
            if table.claims_waiting:
                waiting = await ClaimStore(self.memory).waiting(
                    gid, cid, opted_in=agreed, limit=CLAIMS_PREVIEW
                )
                table.claim_preview = [claims_question(claim) for claim in waiting[:CLAIMS_PREVIEW]]

    async def post_claims(self, table: Table) -> None:
        """The DM screen says how many things wait for the check, never what, with a button only
        the DM can use, when the summary has not already said so."""
        waiting, table.claims_waiting = table.claims_waiting, 0
        table.claim_preview = []
        if waiting and table.campaign_id is not None:
            await self.ask_dm(
                table,
                Ask(
                    CLAIMS,
                    claims_waiting_text(waiting, private=True),
                    f"{waiting} {'thing' if waiting == 1 else 'things'} said at the table to check",
                    lambda c=table.campaign_id: ClaimsReviewButton(c),
                    claims_waiting_text(waiting),
                ),
            )

    async def ask_about_claims(self, table: Table) -> None:
        """Count and tell in one go: the two steps above, for a caller that wants both."""
        await self.note_claims(table)
        await self.post_claims(table)

    async def warn_about_family(self, table: Table) -> None:
        """After a session (#3066): if family links worked out from the DM's own (#2960) wait for a
        yes, the DM screen says how many, never which, with the 📜 button only a DM can use. Nothing
        is posted with none waiting. Counts only in the log and on the screen. No AI call."""
        if self.memory is None or table.campaign_id is None:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            await self.memory.rework_family(
                gid, cid
            )  # (so the count follows the links as they are)
            waiting = len(await self.memory.worked_out_waiting(gid, cid))
            log.info("Worked-out family links: %d waiting", waiting)
            if waiting:
                await self.ask_dm(
                    table,
                    Ask(
                        FAMILY,
                        links_ready.family_notice(waiting),
                        f"{waiting} family {'link' if waiting == 1 else 'links'} to keep or skip",
                        lambda c=cid: links_ready.ReadyButton(c),
                        links_ready.family_notice(waiting),
                    ),
                )

    async def warn_about_continuity(self, table: Table) -> None:
        """After a session: if what was said clashes with the story the DM already confirmed
        (#1320), the DM screen says how many, never what, with a button only the DM can use.
        Nothing is posted below the critical mass (except a clash with a connection the DM pinned
        as 100% truth, #2447) or with nothing waiting (always the case while the extractor
        is off); this step makes no AI call."""
        if self.memory is None or table.campaign_id is None:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            agreed = await self.consent.consenting(gid)
            report = await ContinuityStore(self.memory).check(
                gid, cid, opted_in=agreed, levels=self.settings.continuity
            )
            log.info(
                "Continuity: %d claim(s) looked at, %d heads-up(s), %d confirmed fact(s) in %d "
                "session(s), %s",
                report.claims, len(report.conflicts), report.facts, report.sessions,
                "on" if report.ready else "not enough story yet",
            )  # fmt: skip
            if report.conflicts:
                await self.post(
                    table.screen_channel_id,
                    continuity_waiting_text(
                        len(report.conflicts), sum(1 for c in report.conflicts if c.pinned)
                    ),
                    view=continuity_view(cid),
                )

    async def watch_story(
        self,
        guild_id: int,
        campaign_id: str | None,
        channel_id: int | None,
        *,
        entity_id: str | None = None,
        state_id: str | None = None,
    ) -> None:
        """After a session, or when the DM sets a condition (#1729): if the table has left what
        the DM's adventure needs (a person it needs alive is dead), the DM screen says so, once.
        Only while Shared story is on with a DM's confirmation (the store asks) and the owner's
        plan allows it (asked here, closed). No AI call; DMbot points and never says what to do."""
        if self.watch is None or campaign_id is None or channel_id is None:
            return
        with log_context(guild_id=guild_id, campaign_id=campaign_id):
            campaign = await self.campaigns.get(guild_id, campaign_id)
            if campaign is None or not await self.plan_allows(
                "adventure", guild_id, campaign, fail_open=False
            ):
                return
            found = await self.watch.check(
                guild_id, campaign_id, entity_id=entity_id, state_id=state_id
            )
            log.info("Story watch: %d told, %d waiting", len(found.shown), found.more)
            if not found.shown:
                return
            numbers: dict[int, int] = {}
            if self.transcripts is not None:
                sessions = await self.transcripts.sessions(guild_id, campaign_id, limit=100)
                numbers = {x.started_at: x.number for x in sessions}  # (the recent ones)
            posted = await self.post(
                channel_id, message_text(found, numbers), view=watch_view(campaign_id, found)
            )
            if not posted:  # Discord was down: let the next check tell them rather than lose them
                await self.watch.retract(guild_id, campaign_id, [c.id for c in found.shown])

    async def warn_about_threads(self, table: Table) -> None:
        """After a session (#1662): if story threads the DM keeps haven't moved for a few
        sessions, the DM screen says how many and for how long, never which, with a button only
        the DM can use. Nothing is posted with none stalled. No AI call."""
        if self.memory is None or table.campaign_id is None:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            found = await self.memory.threads(
                gid, cid, stalled_after=self.settings.thread_stalled_sessions
            )
            quiet = [t for t in found if t.stalled]
            log.info("Threads: %d, %d stalled", len(found), len(quiet))  # counts, never names
            if quiet:
                await self.post(
                    table.screen_channel_id,
                    threads_waiting_text(
                        len(quiet),
                        min(t.quiet_sessions for t in quiet),
                        sum(1 for t in quiet if t.thread.type == "promise"),
                    ),
                    view=threads_view(cid),
                )

    async def recheck_definitions(self, table: Table) -> None:
        """After a session: facts written under an older meaning of a relationship are looked
        at again; ones that no longer fit become flags for the DM (#1189)."""
        if self.memory is None or table.campaign_id is None:
            return
        with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
            await self.memory.recheck_definitions(table.guild_id, table.campaign_id)

    async def check_people(self, table: Table) -> None:
        """After a session: every player has their entry and link to a character, and a
        person who plays nothing and isn't a DM is marked "needs a look" (#1188)."""
        if self.memory is None or table.campaign_id is None:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            dms = {table.dm_user_id, *table.dm_user_ids}
            await self.memory.check_people(gid, cid, dms)

    def _note_spelling(
        self,
        table: Table,
        user_id: int,
        line: str,
        *,
        held_key: tuple[int, int] | None = None,
    ) -> None:
        """A name spelled out at the table (#2168): the word becomes a speech hint at once (a hint
        only, nothing says it is a name) and is kept for the end-of-session suggestion. The line is
        from someone who agreed (the pipeline has just re-checked) and is not kept: only the word,
        who said it and a nearby word that sounds like it. A secret name is neither hinted nor
        suggested. The log gets counts only."""
        found = table.spelling.note(user_id, line, time.monotonic())
        if not found:
            return
        if held_key is not None:
            # The off-topic filter has this line: its finds count only if it is labelled game
            # talk (`_label_topics`), never for talk about something else (#1748).
            table.held_spelled[held_key] = found
            return
        self._record_spelled(table, user_id, found)

    def _record_spelled(
        self, table: Table, user_id: int, found: list[spelled_out.SpelledOut]
    ) -> None:
        secret = table.hint_parts.secret if table.hint_parts is not None else frozenset()
        for hit in found:
            key = name_key(hit.word)
            if key in secret or any(name_key(w) == key for w, _, _ in table.spelled):
                continue
            if len(table.spelled) >= SPELLED_MAX:
                break
            like = set(sound_codes(hit.word))
            near = next(
                (w for w in hit.near if name_key(w) != key and like & set(sound_codes(w))), ""
            )
            table.spelled.append((hit.word, user_id, near))
            table.spelled_hints = (*table.spelled_hints, hit.word)[-SPELLED_MAX:]
            log.info("Spelled out at the table: a name of %d letters", len(hit.word))

    async def suggest_spelled(self, table: Table) -> None:
        """After a session: the names spelled out at the table become suggestions for the DM to
        check (#2168), marked "spelled out at the table". Only from people who still agree
        (checked now, after the session), only a spelling DMbot does not already have, and
        nothing changes without the DM. A spelling that sounds like a known name with a different
        spelling is suggested all the same: the review shows which known name it sounds like."""
        spelled = table.spelled
        table.spelled = []
        # (the watcher's recent lines are not kept past the session)
        table.spelling = spelled_out.SpellingWatcher()
        if self.memory is None or table.campaign_id is None or not spelled:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            agreed = await self.consent.consenting(gid)
            known = set(await self.memory.known_keys(gid, cid))
            added = 0
            # (a person the story never named has no name to be spelled: #2790)
            waiting = [
                e
                for e in await self.memory.entities(gid, cid, statuses=[PROPOSED])
                if not e.unnamed
            ]
            given: set[str] = set()  # suggestions that already got a spelling in this run
            for word, user_id, near in spelled:
                key = name_key(word)
                if user_id not in agreed or key in known:
                    continue
                # Said next to a suggestion that sounds like it: that suggestion is what was being
                # spelled, so it gets the spelling and the DM is offered to fix it (#2168).
                target = next(
                    (e for e in waiting if near and name_key(e.name) == name_key(near)), None
                )
                if target is not None and name_key(target.name) != key:
                    if target.id in given or target.description.startswith(SPELLED_OUT):
                        continue  # the first spelling stays; a second never silently wins
                    given.add(target.id)
                    try:
                        await self.memory.set_note(
                            gid, cid, target.id, f"{SPELLED_OUT}: {word}", source="scan"
                        )
                        added += 1
                    except MemoryRuleError as exc:
                        log.info("Skipped a spelling for a suggestion: %s", exc)
                    continue
                try:
                    await self.memory.add_entity(
                        gid,
                        cid,
                        type="concept",
                        name=word,
                        source="scan",
                        description=SPELLED_OUT,
                    )
                except MemoryRuleError as exc:
                    log.info("Skipped a spelled-out name: %s", exc)
                    continue
                added += 1
            log.info("Spelled-out names: %d of %d suggested", added, len(spelled))

    def _count_heard_forms(self, table: Table, user_id: int, fixes: Sequence[Fix]) -> None:
        """Count the other spellings the cleaner turned into a confirmed name (#2171): the words
        only, per person, so a person who stops being recorded can be forgotten. Only a sure fix
        by sound is a hearing: an unsure one may be undone, a spelling fix is dropped later, and
        a fix the DM made by hand is no hearing at all. The name written is kept too, because a
        fix to an alias says nothing about the main name (the store checks it)."""
        for fix in fixes:
            if fix.how != SOUND or not fix.sure:
                continue
            heard = " ".join(fix.heard.split())
            if not heard or len(heard) > SAID_AS_HEARD_MAX:
                continue
            written = lookup_key(fix.written.removesuffix("'s").removesuffix("’s"))
            key = (fix.entity_id, heard.casefold(), written, user_id)
            if key not in table.heard_forms and len(table.heard_forms) >= HEARD_FORMS_MAX:
                continue
            table.heard_forms[key] = table.heard_forms.get(key, 0) + 1

    def _note_pronounced(
        self,
        table: Table,
        user_id: int,
        line: str,
        found: Sequence[Found],
        raw: str,
        *,
        held_key: tuple[int, int] | None = None,
    ) -> None:
        """A name said with how it is pronounced (#2171): "it's pronounced Kay-el". `found` are the
        names already looked up in the cleaned `line`; the sound is read from `raw`, the line as
        heard, because the cleaner turns a misheard "Kale" into the name. The line is from someone
        who agreed (the pipeline has just re-checked) and is not kept: only the sound, whom it was
        said by and the name it belongs to. Only a name said in its main spelling counts. A line
        the off-topic filter holds keeps its find until it is labelled game talk
        (`_label_topics`), never for talk about something else (#1748). The log gets counts."""
        lookup = table.name_lookup
        if lookup is None:
            return
        names: list[tuple[str, int]] = []
        for hit in found:
            entity = lookup.entities.get(hit.entity_id)
            said = line[hit.span[0] : hit.span[1]]
            if (
                hit.method == "exact"
                and entity is not None
                and name_key(said) == name_key(entity.name)
            ):
                names.append((hit.entity_id, hit.span[0]))
        sounds = (
            table.pronouncing.parse(line, raw)
            if pronounced_aloud.mentions_pronunciation(line)
            else []
        )
        now = time.monotonic()
        if held_key is not None:
            # Held with the line, its name settled now (not when it is labelled, by which time
            # other names will have been said); a held line does not count as a name just said.
            resolved = table.pronouncing.resolve(sounds, names, now) if sounds else []
            if resolved:
                table.held_pronounced[held_key] = resolved
            return
        attached = table.pronouncing.attach(user_id, sounds, names, now)
        self._count_pronounced(table, user_id, attached)

    def _count_pronounced(
        self,
        table: Table,
        user_id: int,
        found: Sequence[tuple[pronounced_aloud.Sound, str]],
    ) -> None:
        """Keep the count of each sound found. One that is a secret name, or has a word of one, or
        is another entry's name ("No, the other one, it's pronounced Kael"), or has no sound's
        shape and is not close to the name ("was pronounced Tuesday"), is no pronunciation of this
        name. Nothing here may break a line's delivery."""
        try:
            for sound, entity_id in found:
                if not self._sound_allowed(table, entity_id, sound):
                    continue
                key = (entity_id, pronounced_aloud.sound_key(sound.phrase), user_id)
                if key not in table.pronounced and len(table.pronounced) >= PRONOUNCED_MAX:
                    continue
                spelled = table.pronounced.setdefault(key, {})
                spelled[sound.phrase] = spelled.get(sound.phrase, 0) + 1
                log.info("Said aloud: a pronunciation of %d letters", len(sound.phrase))
        except Exception:
            log.exception("Said aloud: couldn't keep a pronunciation")

    @staticmethod
    def _sound_allowed(table: Table, entity_id: str, sound: pronounced_aloud.Sound) -> bool:
        lookup = table.name_lookup
        entity = lookup.entities.get(entity_id) if lookup is not None else None
        if lookup is None or entity is None:
            return False
        phrase = sound.phrase
        words = {name_key(w) for w in re.split(r"[\s-]+", phrase) if w}
        hidden: set[str] = set()
        for key, entries in lookup.by_key.items():
            if any(e.secret for e in entries):
                hidden.add(key)
                hidden.update(key.split())
        if name_key(phrase) in hidden or words & hidden:
            return False  # a secret name, or a word of one, is never what a name sounds like
        if any(
            e.entity_id != entity_id
            for key in {name_key(phrase), *words}
            for e in lookup.by_key.get(key, ())
        ):
            return False
        if sound.shaped:
            return True
        # Not shaped like a sound ("Tuesday", "Fireball"): only when it sounds like the name.
        wanted = {c for w in re.split(r"[\s-]+", entity.name) if w for c in sound_codes(w)}
        if any(c in wanted for w in re.split(r"[\s-]+", phrase) if w for c in sound_codes(w)):
            return True
        # (the codes miss "Kerrick" for "Cerric": a soft c); close in its letters is close enough
        said, written = pronounced_aloud.sound_key(phrase), pronounced_aloud.sound_key(entity.name)
        # A short name sits inside a longer word ("Aron" in "Baron", "Fin" in "Final"), which
        # scores high without sounding alike, so the lengths must be near and the score over.
        if abs(len(said) - len(written)) > SOUND_LENGTH_GAP:
            return False
        return SequenceMatcher(None, said, written).ratio() > SOUND_CLOSE

    async def suggest_pronounced(self, table: Table) -> None:
        """After a session: a name someone said with its pronunciation is offered for the
        narrator's "said as" (#2171), for the DM to answer in 🔊 Check how to say names. The same
        rules as the counted offers (`suggest_said_as`): only where spoken recaps are on, only
        from people who still agree (checked now), only a public name, never a change. If the
        table said it more than one way, the one said most often is offered. The log gets
        counts."""
        said = table.pronounced
        table.pronounced = {}
        table.pronouncing = pronounced_aloud.PronunciationWatcher()  # (not kept past the session)
        if (
            self.memory is None
            or table.campaign_id is None
            or not said
            or not self.settings.recap_audio_on
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None or not await self.plan_allows(
                "recap_audio", gid, campaign, fail_open=False
            ):
                return
            agreed = await self.consent.consenting(gid)  # after the waits: who still agrees
            names = {e.id: e.name for e in await self.memory.entities(gid, cid)}
            totals: dict[tuple[str, str], int] = {}
            spelled: dict[tuple[str, str], dict[str, int]] = {}
            for (entity_id, key, speaker), writings in said.items():
                if speaker not in agreed or entity_id not in names:
                    continue
                for writing, times in writings.items():
                    totals[(entity_id, key)] = totals.get((entity_id, key), 0) + times
                    seen = spelled.setdefault((entity_id, key), {})
                    seen[writing] = seen.get(writing, 0) + times
            best: dict[str, tuple[str, int]] = {}
            for (entity_id, key), times in totals.items():
                if times > best.get(entity_id, ("", 0))[1]:  # (a tie keeps the first said)
                    writings = spelled[(entity_id, key)]
                    best[entity_id] = (max(writings, key=lambda w: writings[w]), times)
            offers = [(e, phrase, names[e], times) for e, (phrase, times) in best.items()]
            kept = await self.memory.add_said_as_suggestions(gid, cid, offers)
            log.info("Said aloud: %d of %d offered", kept, len(best))

    async def suggest_said_as(self, table: Table) -> None:
        """After a session: a name the cleaner fixed at least `SAID_AS_MIN_FIXES` times from the
        same other spelling is offered for the narrator's "said as" (#2171), for the DM to answer
        in 🔊 Check how to say names. Only where spoken recaps are on for the campaign, only
        hearings from people who still agree (checked now), only a public name. Offers only,
        never a change. The log gets counts."""
        counts = table.heard_forms
        table.heard_forms = {}
        if (
            self.memory is None
            or table.campaign_id is None
            or not counts
            or not self.settings.recap_audio_on
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None or not await self.plan_allows(
                "recap_audio", gid, campaign, fail_open=False
            ):
                return
            agreed = await self.consent.consenting(gid)  # after the waits: who still agrees
            totals: dict[tuple[str, str, str], int] = {}
            for (entity_id, heard, written, speaker), times in counts.items():
                if speaker in agreed:
                    key = (entity_id, heard, written)
                    totals[key] = totals.get(key, 0) + times
            best: dict[str, tuple[str, str, int]] = {}
            for (entity_id, heard, written), times in totals.items():
                if times >= SAID_AS_MIN_FIXES and times > best.get(entity_id, ("", "", 0))[2]:
                    best[entity_id] = (heard, written, times)
            kept = await self.memory.add_said_as_suggestions(
                gid, cid, [(e, h, w, t) for e, (h, w, t) in best.items()]
            )
            log.info("Say it this way: %d offered", kept)

    async def repair_names(
        self,
        gid: int,
        cid: str,
        session_id: str,
        users: Collection[int] = (),
        agreed: Collection[int] | None = None,
        hold: list[name_pass_logic.Report] | None = None,
        game_only: bool = False,
    ) -> int:
        """PlotBot's name pass (#2628): once per session, when its transcript is complete and before
        its recap, a word the cleaner left as heard that PlotBot is sure means a name on the list
        is written as the list has it, in the cleaned text only, with Undo on the DM screen.
        Returns how many lines were fixed. It never raises, and the session is marked done whatever
        happened (a pass that could not run must not hold the recap back); the DM can still fix a
        spelling by hand. `hold`: an import's pass (#2649) puts its report there instead of posting
        it, and the import posts one report for all its sessions. `game_only`: read only the lines
        the off-topic filter kept as game talk, as the import's name scan does."""
        fixed = 0
        try:
            fixed = await self._repair_names(gid, cid, session_id, users, agreed, hold, game_only)
        except Exception:
            log.exception("The name pass failed")
        finally:
            if self.transcripts is not None:
                try:
                    await self.transcripts.mark_names_repaired(gid, session_id, int(time.time()))
                except Exception:
                    log.exception("The name pass couldn't be marked done")
        return fixed

    async def _repair_names(
        self,
        gid: int,
        cid: str,
        session_id: str,
        users: Collection[int],
        agreed: Collection[int] | None,
        hold: list[name_pass_logic.Report] | None = None,
        game_only: bool = False,
    ) -> int:
        if (
            self.transcripts is None
            or self.memory is None
            or self.lookup is None
            or self.name_pass_ai is None
        ):
            return 0
        campaign = await self.campaigns.get(gid, cid)
        if campaign is None or not await self.plan_allows("ai", gid, campaign, fail_open=False):
            return 0
        lookup = await self.lookup.get(gid, cid)
        people: set[str] = set()
        for uid in users:
            name = self.name_of(gid, uid)
            if not name.startswith("<@"):
                people.add(name_key(name))
                people.update(name_key(word) for word in name.split())
        ai = self.name_pass_ai

        async def ask(system: str, question: str) -> Any:
            return await ai.complete(
                system, question, max_tokens=2000, schema=name_pass_logic.SCHEMA
            )  # (the reply itself: its `cut` flag is logged, #2649)

        now = int(time.time())
        report = await name_pass_run.run(
            self.transcripts, lookup, ask, gid, cid, session_id,
            people=people, known=await self.memory.known_keys(gid, cid), agreed=agreed, now=now,
            forget_before=now - int(self.memory.keep_days * 86400), game_only=game_only,
            consenting=lambda: self.consent.consenting(gid),
        )  # fmt: skip
        # The words PlotBot was not sure of, and the ones it says are new names, are name questions
        # for the DM, whatever the scan's own "heard twice" rule says; the recap is held until they
        # are answered (#2169). The report counts only what will really be offered: `run` leaves out
        # a word the campaign knows already, or a person at the table (the scan's own rule).
        if report.unsure or report.new:
            self._name_pass_asks[session_id] = [
                (c.heard, c.times) for c in (*report.unsure, *report.new)
            ]
        try:  # kept for the website's recap page (#2684); never stops the repair
            await NamePassLog.of(self.transcripts).record(gid, cid, session_id, report, now=now)
        except Exception:
            log.exception("The name pass report couldn't be kept")
        if hold is not None:
            if not report.quiet:
                hold.append(report)
        elif not report.quiet and campaign.dm_screen_channel_id is not None:
            await self.post(
                campaign.dm_screen_channel_id,
                name_pass_logic.report_text(report),
                name_pass_undo.undo_view(cid, report.fixed),
            )
        log.info("Name pass report: %s", name_pass_logic.counts_line(report))  # counts only
        return sum(f.times for f in report.fixed)

    async def _propose_new_names(
        self,
        gid: int,
        cid: str,
        lines: Sequence[str],
        users: Collection[int],
        session_id: str | None = None,
        *,
        agreed: Collection[int] | None = None,
        game_only: bool = False,
    ) -> list[str]:
        """Suggest the names DMbot heard in `lines` but doesn't know, for the DM to check (the name
        scan of a live session's end and of an imported one, #2106). `users`: the people whose
        names are not story names. `session_id`: the stored transcript the lines came from, so
        each suggestion keeps where it was said (#2329). Returns the names proposed; nothing is
        confirmed."""
        assert self.memory is not None
        heard_in: list[Line] = []
        if session_id is not None and self.transcripts is not None:
            try:
                # only the lines the scan itself read: people who still agree and, for an imported
                # session, game talk (a place must never quote what the scan left out)
                heard_in = [
                    x
                    for x in await self.transcripts.lines(gid, session_id)
                    if (agreed is None or x.user_id in agreed)
                    and (not game_only or x.topic == GAME)
                ]
            except Exception:  # the places are a nicety: the suggestion is still made
                log.exception("Couldn't read the session to find where names were said")
        # People at the table aren't story names: skip their whole display names and
        # each word of them ("Mia Stone" → "Mia", "Stone").
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
        # Words PlotBot's name pass was not sure of (#2628) are offered too, even heard once.
        asked = self._name_pass_asks.pop(session_id, []) if session_id is not None else []
        have = {name_key(f.name) for f in found}
        for name, times in asked:
            if name_key(name) not in skip and name_key(name) not in have:
                found.append(Suggestion(name, times))
        added: list[str] = []
        kinds = await self._scan_kinds(gid, cid, [f.name for f in found], lines)
        log.info(  # counts only
            "Name scan: %d name(s) suggested, %d with a kind, %d without",
            len(found), len(kinds), len(found) - len(kinds),
        )  # fmt: skip
        for suggestion in found:
            written = None
            # (a kind the campaign's ontology does not allow falls back to none: the card asks)
            for kind in dict.fromkeys((kinds.get(suggestion.name, "concept"), "concept")):
                try:
                    written = await self.memory.add_entity(
                        gid,
                        cid,
                        type=kind,
                        name=suggestion.name,
                        source="scan",
                        description=f"Heard {suggestion.times} times",
                    )
                    break
                except MemoryRuleError as exc:
                    log.info("Skipped a suggested name: %s", exc)
            if written is None:
                continue
            added.append(suggestion.name)
            if heard_in and session_id is not None:
                await self._keep_places(
                    gid, cid, session_id, written.value.id, heard_in,
                    [suggestion.name, *suggestion.also],
                )  # fmt: skip
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
        return added

    async def _scan_kinds(
        self, gid: int, cid: str, names: Sequence[str], lines: Sequence[str]
    ) -> dict[str, str]:
        """What each suggested name is (npc, place, faction, item, event), asked of the AI from the
        sentence it was said in, so the review card can say "Yes, a place" (#2937). Never raises:
        no AI, a plan without it, or a failed call mean no kind for those names, and the card
        shows its own picker. Counts only in the log."""
        ai = self.name_pass_ai
        if ai is None or not names:
            return {}
        try:
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None or not await self.plan_allows("ai", gid, campaign, fail_open=False):
                return {}
            kinds: dict[str, str] = {}

            async def ask_batch(batch: Sequence[str]) -> None:
                ask = [(n, scan_kinds.context_for(n, lines)) for n in batch]
                reply = await ai.complete(
                    scan_kinds.SYSTEM,
                    scan_kinds.request(ask),
                    max_tokens=OUTPUT_FLOOR,  # (background calls never send less, #2596)
                    schema=scan_kinds.SCHEMA,
                )
                if reply.cut and len(batch) > 1:  # cut off: ask again in smaller parts
                    half = len(batch) // 2
                    await ask_batch(batch[:half])
                    await ask_batch(batch[half:])
                    return
                kinds.update(scan_kinds.read_answer(reply.text, batch))

            for batch in scan_kinds.batches(names):
                await ask_batch(batch)
            return kinds
        except Exception as exc:  # the kinds are a nicety: the suggestions are made without them
            log.warning("Couldn't ask for the kinds of suggested names: %s", type(exc).__name__)
            return {}

    async def _keep_places(
        self,
        gid: int,
        cid: str,
        session_id: str,
        entity_id: str,
        lines: Sequence[Line],
        spellings: Sequence[str],
    ) -> None:
        """Keep where a suggested name was said, spread across the session: pointers only (the
        line's reference and the place in it), never the words (#2329). Never raises."""
        assert self.memory is not None
        try:
            session = await self._session_of(gid, session_id)
            session_at = session.started_at if session is not None else None
            for spot in spread(find_spots(lines, spellings)):
                await self.memory.add_mention(
                    gid,
                    cid,
                    entity_id,
                    line_ref=line_ref(session_id, spot.line.started_ms, spot.line.user_id),
                    span=(spot.start, spot.end),
                    confidence=1.0,
                    method="exact",
                    source="scan",
                    session_started_at=session_at,
                )
        except Exception:
            log.exception("Couldn't keep where a suggested name was said")

    async def _session_of(self, gid: int, session_id: str) -> TranscriptSession | None:
        assert self.transcripts is not None
        return await self.transcripts.session(gid, session_id)

    async def suggest_names(self, table: Table) -> None:
        """After a session: suggest names DMbot heard but doesn't know, for the DM to
        check. Only lines from people who still agree; never anything DMbot already has
        an answer for (including names the DM said aren't names). Whatever happens, the session's
        name pass is marked done at the end (#2628): an early return, or a failure, must not leave
        the recap waiting for a pass that will never run."""
        session_id = table.transcript_session_id
        gid = table.guild_id
        try:
            await self._suggest_names(table)
        finally:
            if session_id is not None and self.transcripts is not None:
                try:
                    await self.transcripts.mark_names_repaired(gid, session_id, int(time.time()))
                except Exception:
                    log.exception("The name pass couldn't be marked done")

    async def _suggest_names(self, table: Table) -> None:
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
            users = speakers | agreed | {table.dm_user_id}
            users |= table.dm_user_ids
            repaired = False
            if table.transcript_session_id is not None:
                await self.save_transcript(table)  # so every line is stored to point at
                # PlotBot's name pass (#2628), before the scan and the recap, so the names they
                # use are the list's: a repaired spelling is read again from the stored lines.
                fixed = await self.repair_names(
                    gid, cid, table.transcript_session_id, users, agreed
                )
                if fixed and self.transcripts is not None:
                    try:
                        lines = [
                            x.text
                            for x in await self.transcripts.lines(gid, table.transcript_session_id)
                            if x.user_id in agreed and not x.sidebar and not x.typed
                        ] or lines
                        repaired = True  # (every stored line is in it, the turned-back ones too)
                    except Exception:
                        log.exception("Name pass: couldn't read the repaired lines")
                if not repaired and self.transcripts is not None:
                    # The lines the end-of-session pass turned back into game talk were labelled
                    # off-topic while the session ran, so the held words left them out (#2587).
                    try:
                        lines += [
                            x.text
                            for x in await self.transcripts.leaned_lines(
                                gid, table.transcript_session_id
                            )
                            if x.user_id in agreed and x.text.strip()
                        ]
                    except Exception:
                        log.exception("Name pass: couldn't read the lines turned back")
            added = await self._propose_new_names(
                gid, cid, lines, users, table.transcript_session_id, agreed=agreed
            )
            log.info("After-session scan: %d line(s), %d new name(s)", len(lines), len(added))
            if self.lookup is not None and not any(
                t.campaign_id == cid for t in self.tables.values()
            ):
                self.lookup.drop([cid])  # the session's copy isn't needed any more
            table.new_names = list(added)  # told by the summary, or by the "new names" step

    async def post_new_names(self, table: Table) -> None:
        """After a session (#2410): the new names nobody has been told about yet (the secrets
        summary tells them when it is sent, and then empties the list)."""
        names, table.new_names = table.new_names, []
        if names and table.campaign_id is not None:
            await self.ask_dm(
                table,
                Ask(
                    NAMES,
                    private_after_session_text(names),
                    f"{len(names)} new {'name' if len(names) == 1 else 'names'} to check",
                    lambda g=table.guild_id, c=table.campaign_id: RecapNamesButton(g, c),
                    after_session_text(names),
                ),
            )

    async def ask_dm(self, table: Table, ask: Ask) -> None:
        """A question for the DM after a session (#3210). While the after-session steps run
        (`table.asks` is a list) it is collected, so the DM gets one message; otherwise it goes
        out at once, on its own."""
        if table.asks is not None:
            table.asks.append(ask)
        else:
            await self.send_asks(table, [ask])

    async def send_asks(self, table: Table, asks: list[Ask]) -> None:
        """Send the collected questions as one private message with a button for each. If no DM
        can be reached they stay where they were: each on the DM screen with its own words and
        a line saying why. Nothing is lost."""
        combined = combine(asks)
        if combined is None or table.campaign_id is None:
            return
        text, view = combined
        if await self.ask_privately(table.guild_id, table.campaign_id, text, view):
            return
        for ask in sorted(asks, key=lambda a: ORDER.index(a.kind) if a.kind in ORDER else 99):
            await self.post(
                table.screen_channel_id,
                ask.screen + NO_PRIVATE_MESSAGE,
                view=view_of(ask),
            )

    async def send_collected_asks(self, table: Table) -> None:
        """The after-session step that sends what the steps before it collected (#3210)."""
        asks, table.asks = table.asks or [], []
        await self.send_asks(table, asks)

    async def ask_privately(
        self, guild_id: int, campaign_id: str, text: str, view: discord.ui.View
    ) -> bool:
        """Send a question to each of the campaign's DMs in their private chat (#3210). True when
        at least one got it; False (DMs closed, campaign gone) means the caller keeps its old
        place for the question. Never raises."""
        try:
            campaign = await self.campaigns.get(guild_id, campaign_id)
            if campaign is None:
                return False
            sent = False
            # Marked as from DMbot, with the campaign, so a DM with several campaigns knows which
            # one is asking (owner, 2026-11-01, #3210).
            who = discord.utils.escape_markdown(campaign.name)
            text = f"❓ **DMbot has a question** · {who}\n{text}"
            for user_id in sorted(campaign.dm_user_ids):
                # Each DM in its own try: one that fails must not stop the others, nor discard a
                # send that worked (the caller would then also post on the DM screen, asking
                # twice). "At least one DM got it" is the contract.
                try:
                    if await self.sidebar_send_dm(user_id, text, view) == "sent":
                        sent = True
                except Exception:
                    log.exception("Couldn't ask a DM privately")
            return sent
        except Exception:
            log.exception("Couldn't ask the DM privately")
            return False

    async def _dm_trial_user(self, user_id: int, text: str) -> trial_jobs.DmResult:
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

    async def after_account_move(self, old_user_id: int, new_user_id: int) -> None:
        """After an account has moved (#1230): tell the old account privately, if it still
        exists. The payment company's records are brought up to date when its next event
        arrives (the website's webhook finds a moved person through the marker). Never
        raises."""
        from dmbot.account_move import long_date, old_told

        self._moved_checked.discard(old_user_id)  # its next command is checked again
        try:
            await self._dm_trial_user(old_user_id, old_told(long_date(int(time.time()))))
        except Exception:
            log.exception("Couldn't tell the old account about a move")

    def tell_if_moved_later(self, user_id: int) -> None:
        """Start the "your account moved" check without making the command wait for it."""
        if self.account_moves is None or user_id in self._moved_checked:
            return
        # Marked before the task starts, so two commands at once start one check.
        if len(self._moved_checked) >= MOVED_CHECKED_MAX:
            self._moved_checked.clear()
        self._moved_checked.add(user_id)
        task = asyncio.create_task(self.tell_if_moved(user_id))
        self._moved_tasks.add(task)  # a reference, or the task could be dropped mid-way
        task.add_done_callback(self._moved_tasks.discard)

    async def tell_if_moved(self, user_id: int) -> None:
        """An old account whose DMbot account moved (#1230) hears so, privately, the first time
        it uses any command (marked told as it is read; the line is lost if the message can't
        be sent, which beats telling twice). Never raises and never holds a command up."""
        if self.account_moves is None:
            return
        try:
            notice = await self.account_moves.take_moved_notice(user_id)
            if notice:
                await self._dm_trial_user(user_id, notice)
        except Exception:
            self._moved_checked.discard(user_id)  # try again at their next command
            log.exception("Couldn't tell an old account that its DMbot account moved")

    async def _discord_name(self, user_id: int) -> str:
        try:
            user = await self.fetch_user(user_id)
        except discord.HTTPException:
            return "there"
        return user.display_name

    async def _import_loop(self) -> None:
        """Every 30 seconds: the sessions a DM imported that wait to be cleaned (#2105). A restart
        puts one it was in the middle of back in the queue."""
        assert self.import_job is not None
        await asyncio.sleep(45)
        for guild in list(self.guilds):
            try:
                await self.import_job.store.resume(guild.id, older_than_s=0)
            except Exception:
                log.exception("Import: couldn't put the unfinished ones back")
        while True:
            for guild in list(self.guilds):
                try:
                    await self.import_job.run_round(guild.id)
                    await self.run_import_followups(guild.id)
                except Exception:
                    log.exception("Import: a round failed for a server")
            await asyncio.sleep(IMPORT_ROUND_S)

    async def run_import_followups(self, gid: int) -> int:
        """After an imported session is cleaned (#2106): in date order, one per campaign, its name
        scan (once), then, for the sessions the DM chose Write recaps for, the recap through the
        same hold as a live session, so it waits for the name answers. Returns how many scans were
        done. The scan is claimed before it runs, so two bots never both propose names (a claim
        that never finished is retaken after a while); a scan that fails is given back and tried
        again, and let go (empty window, no hold) after `IMPORT_SCAN_TRIES` so it never blocks the
        sessions after it. The DM hears of new names once per import, when its last session is
        scanned, never once per session. Counts only in the log."""
        assert self.import_job is not None
        store = self.import_job.store
        # First: the DM's "plays which character" choices, so a session scanned below is linked to
        # the characters those choices make the players' own (#3036).
        await self._apply_import_plays(gid, store)
        done = 0
        for ready in await store.ready_for_scan(gid):
            with log_context(guild_id=gid, campaign_id=ready.campaign_id):
                began = int(time.time())
                if not await store.claim_scan(gid, ready.session_id, began):
                    continue  # another round or bot has it
                try:
                    # Well inside the stale claim. A scan cancelled here may have proposed names
                    # already: they are on the review list, just not in the "new names" post.
                    async with asyncio.timeout(STALE_SCAN_S // 3):
                        added = await self._scan_import(gid, ready)
                    await store.finish_scan(gid, ready.session_id, int(time.time()))
                    await self._link_import_party(gid, ready)
                    await self._offer_import_time(gid, ready)
                    done += 1
                    self._import_names.setdefault(ready.campaign_id, []).extend(added)
                except Exception:
                    log.exception("Import: a session's name scan failed")
                    if await store.release_scan(gid, ready.session_id) >= IMPORT_SCAN_TRIES:
                        log.warning("Import: giving up on a session's name scan")
                        await store.give_up_scan(gid, ready.session_id)
                        self._import_unchecked[ready.campaign_id] = (
                            self._import_unchecked.get(ready.campaign_id, 0) + 1
                        )
        for ready in await store.ready_for_recap(gid):
            with log_context(guild_id=gid, campaign_id=ready.campaign_id):
                await self._start_import_recap(gid, store, ready)
        await self._apply_import_plays(gid, store)
        await self._tell_import_news(gid, store)
        return done

    async def _offer_import_time(self, gid: int, ready: Ready) -> None:
        """After an imported session's scan is done (#2979): work out when in game time it ended
        and offer the DM to set the clock there, with one button on the DM screen. Never moves
        the clock. Only when the clock is set, the result is later than it, this is the campaign's
        newest session (an older one imported later never moves it back) and the party is not
        split (then the DM is told why nothing is offered). The DM's lines of the game talk only,
        from speakers who still agree; the strict phrases in code first, and one AI question over
        the cue lines only when none of them moved it. Its own `try`: a failure here is logged and
        never reaches the scan's claim. Counts only in the log."""
        try:
            await self._offer_import_time_inner(gid, ready)
        except Exception:
            log.exception("Import: couldn't work out the game time a session ended at")

    async def _offer_import_time_inner(self, gid: int, ready: Ready) -> None:
        assert self.import_job is not None
        if self.clocks is None or self.transcripts is None:
            return
        campaign = await self.campaigns.get(gid, ready.campaign_id)
        if campaign is None or campaign.dm_screen_channel_id is None:
            return
        stored = await self.clocks.get(gid, campaign.id)
        if stored is None:
            return
        if await self.transcripts.next_session_start(gid, campaign.id, ready.started_at):
            return  # a newer session exists: this one never moves the clock
        rows = await self.import_job.store.scan_lines(gid, ready.session_id)
        agreed = await self.consent.consenting(gid)
        lines = [t for uid, t in rows if uid in campaign.dm_user_ids and uid in agreed]
        if not lines:
            return
        walked = import_time.walk(lines, stored.clock, clock_screen.apply_action)
        target, calls = walked.clock.minute, 0
        if (
            not walked.moved
            and walked.cues
            and self.settings.time_suggestions
            and self.import_time_ai is not None
            and await self.plan_allows("ai", gid, campaign, fail_open=False)
        ):
            calls = 1
            total = await self._import_time_total(walked.cues, stored.clock.minute)
            if total is not None:
                target = clock_game.advance(stored.clock, total).minute
        log.info(  # counts, never words
            "Import time: %d lines, %d moved by phrases, %d cue lines, %d call(s), %s",
            len(lines), walked.moved, len(walked.cues), calls,
            "an offer" if target > stored.clock.minute else "no offer",
        )  # fmt: skip
        if target <= stored.clock.minute:
            return
        if stored.split:
            await self.post(campaign.dm_screen_channel_id, import_time_screen.SPLIT_WHY)
            return
        await self.post(
            campaign.dm_screen_channel_id,
            import_time.offer_text(target),
            view=import_time_screen.offer_view(campaign.id, target),
        )

    async def _import_time_total(self, cues: tuple[str, ...], start: int) -> int | None:
        """The one question over an imported session's cue lines: the minutes they pass in all,
        or None (the AI failed, said null or answered in the wrong shape)."""
        assert self.import_time_ai is not None
        try:
            # A background call: its own feature, not a live one, so the client sends it with the
            # output floor whatever small estimate is given here (#2596).
            reply = await asyncio.wait_for(
                self.import_time_ai.complete(
                    import_time.SYSTEM,
                    import_time.prompt(cues, clock_game.label(start)),
                    max_tokens=import_time.ANSWER_TOKENS,
                ),
                import_time.TIMEOUT_S,
            )
        except Exception as exc:
            log.info("Import time: couldn't ask (%s)", type(exc).__name__)
            return None
        if reply.cut or reply.refused:
            # A cut answer is never read as a smaller one: nothing is offered (#2596).
            log.info("Import time: the answer stopped early")
            return None
        return import_time.read_answer(reply.text)

    async def _link_import_party(self, gid: int, ready: Ready) -> None:
        """After an imported session's scan is done: link its party (#3036). Its own `try`: a
        failure here is logged and never reaches the scan's claim or counts, which are settled."""
        if self.memory is None:
            return
        try:
            await self._link_the_party(gid, ready.campaign_id, ready.session_id)
        except Exception:
            log.exception("Import: couldn't link the party for a session")

    async def _apply_import_plays(self, gid: int, store: ImportStore) -> None:
        """The DM's "plays which character" choices from the import page become links, through a
        store call that judges each again (the DM may have changed things since). One that no
        longer holds, or whose member is no longer recorded, is dropped, never forced; one that
        fails for another reason stays for the next round. Counts only in the log."""
        waiting = await store.waiting_plays(gid)
        if self.memory is None or not waiting:
            return
        recorded = await self.consent.agreed(gid)  # (off the record is not a refusal)
        made = dropped = 0
        for play in waiting:
            with log_context(guild_id=gid, campaign_id=play.campaign_id):
                try:
                    if play.user_id in recorded:
                        applied = await self.memory.apply_import_plays(
                            gid, play.campaign_id, play.character_id, play.user_id
                        )
                        made += bool(applied.value)
                        dropped += not applied.value
                    else:
                        dropped += 1
                except Exception:
                    log.exception("Import: a 'plays' choice could not be applied")
                    continue
                await store.done_plays(gid, play)
        log.info("Import: %d 'plays' choice(s) made, %d dropped", made, dropped)

    async def _tell_import_news(self, gid: int, store: ImportStore) -> None:
        """Say once, on the DM screen, what an import found: the new names and the sessions whose
        name check was given up, in one 📝 line when the import's last session has been scanned
        (the given-up note stands alone only when no names were found), and the recaps that were
        asked for and could not be written."""
        for key in [k for k in self._import_reports if k[0] == gid]:
            cid = key[1]
            if await store.pending_scans(gid, cid):
                continue  # the import is not over: one report at the end
            reports = self._import_reports.pop(key, [])
            campaign = await self.campaigns.get(gid, cid)
            if reports and campaign is not None and campaign.dm_screen_channel_id is not None:
                views = name_pass_undo.undo_views_import(cid, [r.fixed for r in reports])
                screen = campaign.dm_screen_channel_id
                await self.post(
                    screen, name_pass_logic.import_text(reports), views[0] if views else None
                )
                for extra in views[1:]:  # Discord holds 25 buttons a message: no Undo is lost
                    await self.post(screen, "↩️ More Undo buttons for the report above:", extra)
        for cid in list(self._import_names.keys() | self._import_unchecked.keys()):
            if await store.pending_scans(gid, cid):
                continue  # the import is not over: say it once at the end
            names = self._import_names.pop(cid, [])
            skipped = self._import_unchecked.pop(cid, 0)
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None or campaign.dm_screen_channel_id is None:
                continue
            text = after_session_text(names, where="your imported sessions") if names else ""
            if skipped:
                one = skipped == 1
                more = " more" if names else ""
                what = (
                    f"one{more} imported session" if one else f"{skipped}{more} imported sessions"
                )
                note = (
                    f"I couldn't check {what} for new names, so names from "
                    f"{'it' if one else 'them'} may be missing. Add any with ➕ Add a name "
                    "on the names panel."
                )
                text = f"{text}\n{note}" if text else f"📝 {note}"
            if text:
                await self.post(
                    campaign.dm_screen_channel_id, text, view=review_view(cid) if names else None
                )
        for cid in list(self._import_queued.keys() | self._import_unhonoured.keys()):
            if await store.pending_scans(gid, cid):
                continue  # once, at the end of the import
            queued = self._import_queued.pop(cid, [])
            reasons = self._import_unhonoured.pop(cid, [])
            campaign = await self.campaigns.get(gid, cid)
            if campaign is None or campaign.dm_screen_channel_id is None:
                continue
            channel = campaign.dm_screen_channel_id
            missed = ""
            if reasons:
                head = (
                    "1 recap you asked for from your imported sessions wasn't written"
                    if len(reasons) == 1
                    else f"{len(reasons)} recaps you asked for from your imported sessions "
                    "weren't written"
                )
                missed = f"{head}: {'; '.join(sorted(set(reasons)))}."
            if queued:  # one line for what is being written and what is not
                await self._say_group_writing(
                    campaign,
                    channel,
                    len(queued),
                    session_at=queued[-1],
                    also=missed,
                )
            elif reasons:
                await self.post(channel, f"📜 {missed}")

    async def _scan_import(self, gid: int, ready: Ready) -> list[str]:
        """The name scan of one imported session: the lines of people who still agree (asked after
        the lines are read, since a person can stop at any moment) and nobody else's. Returns the
        names proposed."""
        assert self.import_job is not None
        campaign = await self.campaigns.get(gid, ready.campaign_id)
        if self.memory is None or campaign is None:
            log.info("Import: no name scan (names are off, or the campaign is gone)")
            return []
        rows = await self.import_job.store.scan_lines(gid, ready.session_id)
        agreed = await self.consent.consenting(gid)
        lines = [(uid, text) for uid, text in rows if uid in agreed]
        if not lines:
            await self.repair_names(
                gid, ready.campaign_id, ready.session_id, (), agreed,
                self._import_reports.setdefault((gid, ready.campaign_id), []),
                game_only=True,
            )  # (marks it)  # fmt: skip
            return []
        users = {uid for uid, _ in lines} | set(agreed) | set(campaign.dm_user_ids)
        # PlotBot's name pass (#2628) for an imported session too, before the scan and the recap;
        # the lines are read again when it fixed something.
        held = self._import_reports.setdefault((gid, ready.campaign_id), [])
        if await self.repair_names(
            gid, ready.campaign_id, ready.session_id, users, agreed, held, game_only=True
        ):
            rows = await self.import_job.store.scan_lines(gid, ready.session_id)
            lines = [(uid, text) for uid, text in rows if uid in agreed] or lines
        # What an imported session says is what was said at the table: held-back names it says
        # are shown, as after a live session (#2404).
        try:
            await self._reveal_said(
                gid, ready.campaign_id, _fixed([t for _, t in lines]), "an imported session"
            )
        except Exception:
            log.exception(
                "Import: showing the names the session said failed"
            )  # never stops the scan
        added = await self._propose_new_names(
            gid, ready.campaign_id, [t for _, t in lines], users, ready.session_id,
            agreed=set(agreed), game_only=True,
        )  # fmt: skip
        log.info("Import scan: %d lines, %d new names", len(lines), len(added))
        return added

    async def _finish_web_delete(self, notice: Notice) -> None:
        # (the whole body is guarded: a note is taken once, so one failing must not lose the rest
        # of its batch)
        with log_context(guild_id=notice.guild_id, campaign_id=notice.id):
            try:
                try:
                    await self._retire_campaign_channels(notice, int(time.time()), by_hand=True)
                except Exception:
                    log.exception("Couldn't tidy a website-deleted campaign's channels")
                name = discord.utils.escape_markdown(notice.name)
                who = discord.utils.escape_markdown(
                    self.name_of(notice.guild_id, notice.by_user_id)
                )
                text = delete_campaign.TOLD.format(who=who, name=name)
                told = [await self._dm_user(other, text) for other in notice.other_dms]
                log.info(
                    "A campaign deleted on the website was finished: %d of %d game masters told",
                    sum(told),
                    len(told),
                )
            except Exception:
                log.exception("Couldn't finish a website-deleted campaign")

    async def export_campaign(self, guild_id: int, campaign_id: str) -> tuple[Any, list[bytes]]:
        """A campaign's complete copy, as Discord's Download makes it (#2554): the main file's
        data and the transcripts files (#2552, #2772), both from one moment."""
        return await self.campaigns.export_set(guild_id, campaign_id)

    async def email_hand_over_owner(
        self, owner_id: int, kind: str, subject: str, body: str
    ) -> None:
        """An owner's email about their hand-over offer (#2532, slice D): to the address DMbot has
        for their account, if any. Once for each `kind` (the mail log keeps a row, so a repeat is
        refused); an email that could not go (the day's cap, the mail server) is not retried, as for
        other account emails. Never logs the address or the text. Raises only if the account
        lookup does; the caller (`SiteOffers._email_owner`) catches that."""
        if self.mailer is None or self.meter is None:
            return
        to = await accounts.account_email(self.meter.db, owner_id)
        if not to:
            return
        result = await self.mailer.send(
            user_id=owner_id, kind=kind, to=to, subject=subject, body=body
        )
        log.info("Hand-over email to an owner: %s", result)

    async def run_web_backups(self) -> None:
        """Build the copies the website asked for (#2554, part 1b): every server that ever held a
        campaign, the ones the bot has left included, because a copy needs only the database. A
        few per server per round; the file is never logged."""
        store = self.web_backups
        if store is None:
            return
        for guild_id in await self.campaigns.guilds_with_campaigns():
            for _ in range(web_backup.PER_ROUND):
                # Shutting down: the row is still waiting, so the next start takes it.
                if self._closing:
                    return
                item = await store.claim_next(guild_id)
                if item is None:
                    break
                with log_context(guild_id=guild_id, campaign_id=item.campaign_id):
                    why = await web_backup.build(self, guild_id, item, store)
                    log.info("Website backup: %s", why or "done")  # the word only

    async def run_web_line_deletions(self) -> None:
        """Carry out the "Delete my transcripts" asked for on the website (#2780): the same call
        as Discord's 🗑 button, for the person who asked and nobody else. Only servers the bot is
        in now, so a request for a server it has left waits (the page says it may still happen).
        Counts only are logged."""
        store = self.web_line_deletions
        if store is None or self.transcripts is None:
            return
        for guild in list(self.guilds):
            try:  # one server's trouble must not starve the others
                for _ in range(web_line_delete.PER_ROUND):
                    item = await store.claim_next(guild.id)
                    if item is None:
                        break
                    with log_context(guild_id=guild.id):
                        why = await web_line_delete.carry_out(self, guild.id, item, store)
                        log.info("Website line deletion: %s", why or "done")  # the word only
            except Exception as exc:
                log.warning("The website line deletions of a server failed: %s", type(exc).__name__)

    async def run_web_restores(self) -> None:
        """Apply the copies DMs uploaded on the website (#2746), one at a time per server, with
        the same code and the same checks as Discord's `/dmbot restore`. Only the servers the bot
        is in: the person must be in the server for it to count. The copy is never logged."""
        store = self.web_restores
        if store is None:
            return
        store.beat()  # a round began: the worker is alive
        for guild in list(self.guilds):
            for _ in range(web_restore.PER_ROUND):
                # Shutting down: the row is still waiting, so the next start takes it.
                if self._closing:
                    return
                item = await store.claim_next(guild.id)
                if item is None:
                    break
                with log_context(guild_id=guild.id):
                    if not await web_restore.run_one(self, store, guild.id, item):
                        break

    async def tidy_web_restores(self) -> None:
        """The upkeep of the website's restore rows (#2746), about once a minute: end what a
        stopped bot left, end the rows of a server DMbot has left (they hold whole campaigns;
        decided from the install record, never from the cache), delete what is a day old. Every
        server that has rows, not only the ones the bot is in."""
        if self.web_restores is not None:
            await web_restore.tidy(self.web_restores)

    async def keep_web_restores_alive(self) -> None:
        """Show the website's queued restores as alive (#2746), from a task of its own so a long
        restore ahead of them does not make them look stalled."""
        if self.web_restores is not None:
            await self.web_restores.keep_waiting_alive()

    async def restorer_in_server(self, guild_id: int, user_id: int) -> bool:
        """Whether this person is still in the server (a website restore is judged at the moment
        it is applied, not when it was asked for). The cache first, then one lookup. Only "no such
        member" is False: a Discord error is not an answer, so it rises."""
        guild = self.get_guild(guild_id)
        if guild is None:
            return False
        if guild.get_member(user_id) is not None:
            return True
        try:
            await guild.fetch_member(user_id)
        except discord.NotFound:
            return False
        return True  # (any other Discord error rises: the restore fails and can be tried again)

    async def restore_copy(
        self, guild_id: int, data: object, importer_id: int, **kw: Any
    ) -> Campaign:
        """Restore a copy the way Discord's `/dmbot restore` does (for a website restore)."""
        return await self.campaigns.import_backup(guild_id, data, importer_id, **kw)

    async def after_restore(self, guild_id: int, campaign_id: str, replaced: bool) -> None:
        """What Discord's restore does once the copy is in: refresh the planner's statistics (a
        big restore adds thousands of rows at once, #2376) and, for a replaced campaign, the NPC
        pages' view."""
        memory = getattr(self, "memory", None)
        if memory is not None:
            await memory.analyze(guild_id)
        if replaced:
            self._npcs_changed(campaign_id)

    async def run_web_delete_notices(self) -> None:
        """Finish the campaigns the website deleted (#2554): tidy their Discord channels and tell
        the other game masters, the same as Discord's own delete. Every server that ever held a
        campaign is looked at, not only the ones the bot is in now: a server the bot has left
        gets its game masters told and its channels skipped. Nothing the note holds is logged."""
        store = self.web_delete_notices
        if store is None:
            return
        for guild_id in await self.campaigns.guilds_with_campaigns():
            for notice in await store.take(guild_id):
                await self._finish_web_delete(notice)
            await self.campaigns.forget_guild_if_empty(guild_id)

    async def run_peek_notices(self) -> None:
        """Tell the DM, on the DM screen, that a player turned the peek on (#2404): one line for
        each visit, taken from the website's peek door's rows. Nothing but the person's display
        name is in it."""
        store = self.peek_notices
        if store is None:
            return
        for guild in list(self.guilds):
            for peek in await store.take(guild.id):
                with log_context(guild_id=guild.id, campaign_id=peek.campaign_id):
                    campaign = await self.campaigns.get(guild.id, peek.campaign_id)
                    if campaign is None or not campaign.dm_screen_channel_id:
                        continue
                    await self.post(campaign.dm_screen_channel_id, peek_notice(peek.name))

    def _waiting_answers(self, guild_id: int) -> web_actions.Waiting | None:
        """What the website's answers to waiting connections need (#2635): the claims, the
        backstory's queue, and who may be recorded now (read when the answer is applied)."""
        if self.memory is None or self.backstory_links is None:
            return None
        actions = self.name_actions
        transcripts = self.transcripts  # (bound here so the lambda below keeps the narrowed type)
        return web_actions.Waiting(
            ClaimStore(self.memory),
            self.backstory_links,
            lambda: self.consent.consenting(guild_id),
            (lambda cid, uid, answer: actions.kept_reading(guild_id, cid, uid, answer))
            if actions is not None
            else None,
            (lambda cid, answer: actions.spent(guild_id, cid, answer))
            if actions is not None
            else None,
            name_pass_undo=(
                (
                    lambda cid, batch: name_pass_undo_for_web(
                        transcripts, self.memory, guild_id, cid, batch, now=int(time.time())
                    )
                )
                if transcripts is not None
                else None
            ),
            progress=(
                (lambda action, done, total: actions.progress(guild_id, action, done, total))
                if actions is not None
                else None
            ),
        )

    def _named_batch_for(
        self, guild_id: int
    ) -> Callable[[str, int, str], Awaitable[int | None]] | None:
        """For the website's Undo of a name it gave (#2808): the change batch of the DM's own done
        action, read when the Undo is applied."""
        actions = self.name_actions
        if actions is None:
            return None

        async def batch_of(campaign_id: str, user_id: int, action_id: str) -> int | None:
            return await actions.named_batch(guild_id, campaign_id, user_id, action_id)

        return batch_of

    def _player_lookup(self, guild_id: int) -> web_actions.PlayerLookupFn:
        """Who a Discord ID is, for the website's "Please confirm: <name>" (#3004): the lookup the
        Discord side uses too (`ui.player_id.look_up`: Discord's user lookup, so someone who has not
        joined can be found). None when Discord does not know the ID; a failed lookup raises, so it
        is never read as "unknown" and the action fails and can be sent again."""

        async def look_up(user_id: int) -> web_actions.PlayerLookup | None:
            found = await player_id.look_up(self, guild_id, str(user_id))
            if found.kind == player_id.FOUND:
                return web_actions.PlayerLookup(found.name, False, found.in_server)
            if found.kind == player_id.BOT:
                return web_actions.PlayerLookup("", True, True)
            if found.kind == player_id.FAILED:
                raise RuntimeError("Discord could not be asked")
            return None  # Discord does not know that ID (or it is not one)

        return look_up

    async def run_name_actions(self) -> None:
        """What DMs did on the website's All names page (#2294), applied through the same memory
        paths as Discord's name card, so a change on either shows on both and Undo works. Each
        sender must still be a DM of the campaign; only the outcome word is logged, never the
        spelling or the other name."""
        store = self.name_actions
        if store is None or self.memory is None:
            return
        for guild in list(self.guilds):
            for _ in range(NAME_ANSWERS_PER_ROUND):
                # Shutting down: the row is still waiting, so the next start takes it.
                if self._closing:
                    return
                action = await store.claim_next(guild.id)
                if action is None:
                    break
                with log_context(guild_id=guild.id, campaign_id=action.campaign_id):
                    try:
                        campaign = await self.campaigns.get(guild.id, action.campaign_id)
                        done = (
                            web_actions.Outcome(error=web_actions.NOT_ALLOWED)
                            if campaign is None
                            else await web_actions.apply(
                                self.memory,
                                guild.id,
                                campaign.dm_user_ids,
                                action,
                                self._waiting_answers(guild.id),
                                named_batch=self._named_batch_for(guild.id),
                                look_up=self._player_lookup(guild.id),
                            )
                        )
                    except Exception as exc:  # the row must never stay "working" for a bug
                        log.warning("A name action failed: %s", type(exc).__name__)
                        done = web_actions.Outcome(error=web_actions.FAILED)
                    try:
                        await store.finish(
                            guild.id,
                            action,
                            error=done.error,
                            result_id=done.result_id,
                            result_other=done.result_other,
                        )
                    except Exception as exc:  # taken again later: apply() sees it already done
                        log.warning("A name action could not be marked: %s", type(exc).__name__)
                        break
                    log.info("Name action %s: %s", action.action, done.error or "done")
                    if not done.error and self.lookup is not None:
                        self.lookup.mark_stale(guild.id, action.campaign_id)

    async def run_web_recap_style(self) -> None:
        """The Recap style choices DMs made on the website (#3060): for each server, sweep its
        queue (the DM's pasted words never wait on a DM who did not come back), then apply the
        oldest waiting choices in the order made. Each is a quick store call; a typed note is one
        fast AI sort. The word of the outcome only in the log, never the words or who asked."""
        queue = self.web_recap_style
        if queue is None:
            return
        for guild in list(self.guilds):
            if self._closing:
                return
            now = time.monotonic()
            if now - self._web_style_swept.get(guild.id, -LORE_SWEEP_EVERY_S) >= LORE_SWEEP_EVERY_S:
                self._web_style_swept[guild.id] = now
                await queue.sweep(guild.id)
            for _ in range(WEB_STYLE_PER_ROUND):
                claim = await queue.claim_next(guild.id)
                if claim is None:
                    break
                with log_context(guild_id=guild.id, campaign_id=claim.campaign_id):
                    try:
                        error = await web_style.apply(self, queue, guild.id, claim)
                    except Exception as exc:  # the row must never stay "working" for a bug
                        log.warning("A recap style choice failed: %s", type(exc).__name__)
                        error = web_style.FAILED
                    await queue.finish(guild.id, claim, error)
                    log.info("Recap style %s: %s", claim.action, error or "done")

    async def run_lore_uploads(self) -> None:
        """The lore DMs uploaded on the website (#3022): for each server, sweep its queue (words
        never stay just because the DM did not come back), and when no read is running there, take
        the oldest waiting upload and read it in its own task. A read takes minutes, so it must not
        hold this loop, which every other website action shares. One read at a time per server."""
        queue = self.lore_uploads
        if queue is None or self.memory is None:
            return
        for guild in list(self.guilds):
            if self._closing:
                return
            running = self._lore_reads.get(guild.id)
            if running is not None and running.done():
                del self._lore_reads[guild.id]
                running = None
            now = time.monotonic()
            if now - self._lore_swept.get(guild.id, -LORE_SWEEP_EVERY_S) >= LORE_SWEEP_EVERY_S:
                self._lore_swept[guild.id] = now
                await queue.sweep(guild.id)
            if running is not None:
                continue
            claim = await queue.claim_next(guild.id)
            if claim is None:
                continue
            with log_context(guild_id=guild.id, campaign_id=claim.campaign_id):
                self._lore_reads[guild.id] = asyncio.ensure_future(
                    lore_upload.run_upload(self, queue, claim, guild.id)
                )

    async def run_house_file_requests(self) -> None:
        """The House-rules file requests DMs made on the website (#3087): for each server, sweep
        its queue (the link and file's words never stay just because the DM did not come back),
        and when none is running there, take the oldest waiting one and do it in its own task. A
        read of a file can take minutes, so it must not hold this loop, which every other website
        action shares. One at a time per server."""
        queue = self.house_file_queue
        if queue is None:
            return
        for guild in list(self.guilds):
            if self._closing:
                return
            running = self._house_file_runs.get(guild.id)
            if running is not None and running.done():
                del self._house_file_runs[guild.id]
                running = None
            now = time.monotonic()
            if (
                now - self._house_file_swept.get(guild.id, -LORE_SWEEP_EVERY_S)
                >= LORE_SWEEP_EVERY_S
            ):
                self._house_file_swept[guild.id] = now
                await queue.sweep(guild.id)
            if running is not None:
                continue
            claim = await queue.claim_next(guild.id)
            if claim is None:
                continue
            with log_context(guild_id=guild.id, campaign_id=claim.campaign_id):
                self._house_file_runs[guild.id] = asyncio.ensure_future(
                    house_file_web.run_request(self, queue, claim, guild.id)
                )

    # ---- the website's Settings page (#2463): the Host of dm_screen.web_settings ----

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None:
        return await self.campaigns.get(guild_id, campaign_id)

    async def apply_visibility(self, campaign: Campaign, visibility: str) -> str:
        """Who can see the DM screen, as the Discord button does it: saved and applied together
        under the campaign's lock, the same warning posted in the screen, a live session caught
        up. The DM's notice is Discord's own words."""
        guild = self.get_guild(campaign.guild_id)
        if guild is None:
            raise DMScreenError(screen_messages.CAMPAIGN_GONE)
        result = await setup_dm_screen(guild, campaign.id, self.campaigns, visibility=visibility)
        note = screen_messages.visibility_changed(visibility, was=campaign.dm_screen_visibility)
        if result.warning:
            note += "\n" + result.warning
            with contextlib.suppress(discord.HTTPException):
                await result.channel.send(result.warning, allowed_mentions=NO_PINGS)
        await self.after_screen_change(result.campaign, result.channel)
        return note

    async def apply_level(self, campaign: Campaign, level: str) -> Campaign:
        return await self.set_screen_level(
            campaign.guild_id, campaign.id, level, was=campaign.dm_screen_level
        )

    async def apply_retention(self, campaign: Campaign, days: int | None) -> Campaign:
        return await self.campaigns.set_transcript_keep(campaign.guild_id, campaign.id, days)

    async def apply_story_mode(
        self, campaign: Campaign, mode: str, confirmed_by: int | None
    ) -> Campaign:
        return await self.campaigns.set_story_mode(
            campaign.guild_id, campaign.id, mode, confirmed_by=confirmed_by
        )

    async def apply_invite(self, campaign: Campaign, invite: str | None) -> Campaign:
        """The D&D Beyond invite chosen on the website (#3095), as Discord's form saves it: the
        store call, then the running session and the transcript card follow."""
        saved = await self.campaigns.set_dndbeyond_invite(campaign.guild_id, campaign.id, invite)
        await self.after_invite_change(saved)
        return saved

    async def apply_pause(self, campaign: Campaign, paused: bool, user_id: int) -> Campaign:
        """Pause or unpause chosen on the website (#3062): `set_paused` itself, under the session
        lock like Discord's button, so a running session is never paused under its feet."""
        return await self.set_paused(campaign.guild_id, campaign.id, user_id, paused)

    async def apply_rulesets(self, campaign: Campaign, main: str, fallback: str) -> bool:
        """The main rules chosen on the website (#3112): saved, the new pair's names built ahead
        of use (in its own try: a failure there costs only the first lookup's wait), and whether
        a session of this campaign is live. A live table keeps the pair it captured at its start
        (`rules_rulesets`), so the change reaches it from the next session."""
        await self.campaigns.set_rulesets(campaign.guild_id, campaign.id, main, fallback)
        try:
            self._warm_rules((main, fallback))
        except Exception as exc:  # the type only
            log.warning("Warming the new rules failed: %s", type(exc).__name__)
        table = self.tables.get(campaign.guild_id)
        return table is not None and table.campaign_id == campaign.id

    async def optional_overrides(self, campaign: Campaign) -> dict[str, bool]:
        return await self.campaigns.optional_rule_overrides(campaign.guild_id, campaign.id)

    async def apply_optional_rule(
        self, campaign: Campaign, rule: OptionalRule, turn_on: bool, sender_id: int
    ) -> bool:
        """An optional rule chosen on the website (#2755), as `/dmbot optionalrules` does it:
        saved, then noted in the DM screen if the campaign has one. A line that could not be
        posted is not the choice's failure (the rule is saved): False says so, for the page to
        mention. The note never pings."""
        await self.campaigns.set_optional_rule(campaign.guild_id, campaign.id, rule.id, turn_on)
        if campaign.dm_screen_channel_id is None:
            return True
        try:
            return await self.post(
                campaign.dm_screen_channel_id,
                optional.screen_line(rule, turn_on, f"<@{sender_id}>"),
            )
        except Exception as exc:  # the type only
            log.warning("The DM screen line for an optional rule failed: %s", type(exc).__name__)
            return False

    async def find_member(self, guild_id: int, user_id: int) -> web_settings.Member | None:
        """A person of the server for the website's game master change (#2776): the cache first
        (DMbot has no members intent), then Discord. None only when Discord says they are not in
        the server; any other failure raises, so a lookup that failed is never read as "left"."""
        guild = self.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("the server is not reachable")
        found = guild.get_member(user_id)
        if found is None:
            try:
                found = await asyncio.wait_for(
                    guild.fetch_member(user_id), game_masters.LOOKUP_S * 4
                )  # (one slow call must not hold every server's queue)
            except discord.NotFound:
                return None
        return web_settings.Member(found.display_name, found.bot)

    async def apply_co_dm(
        self, campaign: Campaign, user_id: int, add: bool, asker_id: int
    ) -> web_settings.CoDmDone:
        """A game master added or removed on the website (#2776), exactly as Discord's 👑 Game
        masters does it: the store's rules, then access follows and a new game master gets the
        one private message. The caller has checked an added person is in the server and not a
        bot. The change stays saved if either follow-up fails. Ids in the log only."""
        guild = self.get_guild(campaign.guild_id)
        if guild is None:
            raise CampaignError(screen_messages.CAMPAIGN_GONE)
        if add:
            changed = await self.campaigns.add_dm(campaign.guild_id, campaign.id, user_id)
        else:
            changed = await self.campaigns.remove_dm(campaign.guild_id, campaign.id, user_id)
        with log_context(guild_id=campaign.guild_id, campaign_id=campaign.id):
            log.info(
                "Game master %s %s by %s on the website",
                user_id,
                "added" if add else "removed",
                asker_id,
            )
        late = await game_masters.follow_access_on(self, guild, changed)
        if not add:
            return web_settings.CoDmDone(late=late)
        try:  # the cache is often empty (no members intent), so ask Discord too
            asker = await self.find_member(campaign.guild_id, asker_id)
        except Exception:
            asker = None
        by = asker.name if asker is not None else "A game master"
        told = await game_masters.tell_new(guild, changed, user_id, by)
        return web_settings.CoDmDone(told=told, late=late)

    async def run_web_settings(self) -> None:
        """What DMs chose on the website's Settings page (#2463), applied the way Discord's
        ⚙️ Manage DMbot buttons apply it. Each sender must still be a DM of the campaign; only the
        outcome word is logged, never the choice."""
        store = self.web_settings
        if store is None:
            return
        for guild in list(self.guilds):
            for _ in range(web_settings.SETTINGS_PER_ROUND):
                # Shutting down: the row is still waiting, so the next start takes it.
                if self._closing:
                    return
                item = await store.claim_next(guild.id)
                if item is None:
                    break
                with log_context(guild_id=guild.id, campaign_id=item.campaign_id):
                    if not await web_settings.run_one(self, store, guild.id, item):
                        break

    async def run_web_report_requests(self) -> None:
        """The reports DMs asked for again on the website (#2805): each asker must still be a DM of
        the campaign, and the report is made the way it is after a session, inside the same plan
        check and call limits. Each runs as a tracked background task, at most one a server, so
        this returns at once; only the outcome word is logged."""
        store = self.report_requests
        if store is None:
            return
        running = self._report_tasks
        for guild in list(self.guilds):
            task = running.get(guild.id)
            if task is not None and not task.done():
                continue  # one report at a time for a server; the loop does not wait for it
            # Shutting down: the row is still waiting, so the next start takes it.
            if self._closing:
                return
            req = await store.claim_next(guild.id)
            if req is None:
                continue
            gid = guild.id
            task = self._track(self._run_report_request(store, gid, req), "web-report-request")
            if task is None:
                # Shutting down: the row stays working and is retaken after RETAKE_AFTER_S
                # (core/src/dmbot/memory/web_report_requests.py).
                continue
            running[gid] = task

            def forget(done: asyncio.Task[None], gid: int = gid) -> None:
                if running.get(gid) is done:
                    del running[gid]

            task.add_done_callback(forget)

    async def _run_report_request(
        self,
        store: web_report_requests.RequestStore,
        guild_id: int,
        req: web_report_requests.Request,
    ) -> None:
        """One website report request, as a background task so the poll loop (and every other
        website action in it) does not wait for the many AI calls of a report."""
        with log_context(guild_id=guild_id, campaign_id=req.campaign_id):
            await web_report_requests.run_one(self, store, guild_id, req)

    # ---- the website's rule sharing (#2793): the Host of rules.web_sharing ----

    async def share_refusal(self, campaign: Campaign, user_id: int) -> str | None:
        return await self.plan_gate("share", campaign.guild_id, campaign, user_id)

    async def own_rules(self, campaign: Campaign) -> tuple[list[HouseRule], str | None]:
        """The campaign's house rules and its rules document link, as Discord's Share reads
        them; rules from a followed set or a rulebook are not among them."""
        if self.house_rules is None:
            return [], None
        rules = await self.house_rules.list(campaign.guild_id, campaign.id)
        links = self.house_file_links
        link = await links.get(campaign.guild_id, campaign.id) if links is not None else None
        return rules, getattr(link, "link", None)

    async def settle(self, campaign: Campaign, *, following: bool) -> str:
        """After a follow or an unfollow: the relationship definitions borrowed from the set
        match it again (#1456), and the sentence Discord adds to its answer. A failure here
        never undoes the follow."""
        if self.memory is None or self.rule_sets is None:
            return ""
        done = await set_sync.safely(
            set_sync.reconcile(self.memory, self.rule_sets, campaign.guild_id, campaign.id)
        )
        if following:
            return shared_rules_defs.followed_note(done)
        return shared_rules_defs.stopped_note(done)

    async def run_web_rule_sharing(self) -> None:
        """What DMs chose on the website's rule sharing (#2793), applied the way Discord's
        📚 Shared rules buttons apply it. Each asker must still be a DM of the campaign. Each
        runs as a tracked background task, at most one a server, so this returns at once and the
        poll loop's other website actions do not wait; only outcome words are logged."""
        store = self.web_rule_actions
        if store is None or self.rule_sets is None:
            return
        running = self._rule_tasks
        for guild in list(self.guilds):
            task = running.get(guild.id)
            if task is not None and not task.done():
                continue  # one at a time for a server; the loop does not wait for it
            # Shutting down: the row is still waiting, so the next start takes it.
            if self._closing:
                return
            item = await store.claim_next(guild.id)
            if item is None:
                continue
            gid = guild.id
            task = self._track(self._run_rule_choice(store, gid, item), "web-rule-choice")
            if task is None:
                continue  # shutting down: the row is retaken after the door's ten minutes
            running[gid] = task

            def forget(done: asyncio.Task[None], gid: int = gid) -> None:
                if running.get(gid) is done:
                    del running[gid]

            task.add_done_callback(forget)

    async def _run_rule_choice(
        self, store: web_sharing.ActionStore, guild_id: int, item: web_sharing.Action
    ) -> None:
        with log_context(guild_id=guild_id, campaign_id=item.campaign_id):
            await web_sharing.run_one(self, store, guild_id, item)

    # ---- the website's character sheets page (#2817): the Host of memory.web_sheets ----

    async def in_server(self, guild_id: int, user_id: int) -> bool:
        return await self.find_member(guild_id, user_id) is not None

    async def fetch_sheet(self, character: int) -> dict[str, Any]:
        return await sheets_memory.fetch(character)

    async def read_all(
        self, guild_id: int, campaign_id: str, progress: Callable[[], Awaitable[None]]
    ) -> None:
        """Refresh sheets, as the names panel does it: every linked sheet read again."""
        store = self.sheets
        if store is None:
            return
        now = int(time.time())
        await refresh_sheets(store, guild_id, campaign_id, now, progress=progress)
        self.sheets_changed(guild_id, campaign_id)

    def link_too_soon(self, guild_id: int, user_id: int) -> bool:
        return sheets_ui.too_soon(
            sheets_ui.last_link, (guild_id, user_id), sheets_ui.LINK_COOLDOWN_S
        )

    def document_too_soon(self, guild_id: int, user_id: int, campaign_id: str) -> bool:
        """The website's document reads: 60 s a person for a campaign (the door's own limit, so
        this is only a second check). Each one costs an AI read, so it has its own count."""
        return sheets_ui.too_soon(
            self._web_document_last, (guild_id, user_id, campaign_id), WEB_DOCUMENT_COOLDOWN_S
        )

    def read_too_soon(self, guild_id: int, campaign_id: str) -> bool:
        return sheets_ui.too_soon(
            sheets_ui.last_refresh, (guild_id, campaign_id), sheets_ui.REFRESH_COOLDOWN_S
        )

    async def run_web_sheets(self) -> None:
        """What players and DMs chose on the website's character sheets page (#2817), applied the
        way Discord's 📜 buttons apply it. Each asker must still play the character (or be a DM).
        A link is a 10-second read of D&D Beyond and a read-again is one per sheet, so each runs as
        a tracked background task, at most one a server: this returns at once and the poll loop's
        other website actions do not wait. Only outcome words are logged, never a link."""
        store = self.web_sheets
        if store is None or self.sheets is None:
            return
        running = self._sheet_tasks
        for guild in list(self.guilds):
            task = running.get(guild.id)
            if task is not None and not task.done():
                continue  # one at a time for a server; the loop does not wait for it
            # Shutting down: the row is still waiting, so the next start takes it.
            if self._closing:
                return
            item = await store.claim_next(guild.id)
            if item is None:
                continue
            gid = guild.id
            task = self._track(self._run_sheet_choice(store, gid, item), "web-sheet-choice")
            if task is None:
                # Shutting down: the row stays working and is retaken after RETAKE_AFTER_S
                # (core/src/dmbot/memory/web_sheets.py).
                continue
            running[gid] = task

            def forget(done: asyncio.Task[None], gid: int = gid) -> None:
                if running.get(gid) is done:
                    del running[gid]

            task.add_done_callback(forget)

    async def _run_sheet_choice(
        self, store: web_sheets.ActionStore, guild_id: int, item: web_sheets.Action
    ) -> None:
        with log_context(guild_id=guild_id, campaign_id=item.campaign_id):
            await web_sheets.run_one(self, store, guild_id, item)

    # ---- a line fixed from the website (#2883): the Host of transcript.web_line_fixes ----

    def fix_has_consent(self, guild_id: int, user_id: int) -> bool:
        return self.consent.has_consent(guild_id, user_id)

    async def fix_names_a_secret(self, guild_id: int, campaign_id: str, text: str) -> bool:
        lookup = await self.lookup.get(guild_id, campaign_id) if self.lookup else None
        return lookup is None or text_names_a_secret(lookup, text)

    def fix_session_running(self, guild_id: int, session_id: str) -> bool:
        table = self.tables.get(guild_id)
        return table is not None and table.transcript_session_id == session_id

    async def fix_post_suggestion(
        self,
        guild_id: int,
        channel_id: int,
        made: line_suggestions.Suggestion,
        line: line_suggestions.Own,
        text: str,
    ) -> bool:
        """The DM-screen question, worded and buttoned exactly as `/dmbot fix` posts it."""
        try:
            member = await self.find_member(guild_id, made.user_id)
        except Exception:  # the name is only for the DM's line: don't lose the question for it
            member = None
        who = member.name if member is not None else "Someone"
        posted = await self.post_message(
            channel_id,
            dm_text(who, line.heard, line.text, text),
            suggestion_view(made.id),
            remember=("fix", made.id),
        )
        return posted is not None

    async def run_web_line_fixes(self) -> None:
        """Fixes asked for on the Transcripts page. Each runs as a tracked background task, at
        most one a server, so the poll loop's other website actions do not wait for it (a
        suggestion posts to Discord, a rewrite touches the helpers' copies). Only outcome words
        are logged, never the person's words."""
        store = self.web_line_fixes
        if store is None:
            return
        for guild in list(self.guilds):
            task = self._fix_tasks.get(guild.id)
            if task is not None and not task.done():
                continue
            item = await store.claim_next(guild.id)
            if item is None:
                continue
            gid = guild.id
            started = self._track(self._run_line_fix(store, gid, item), f"line-fix-{gid}")
            if started is None:  # shutting down: the row is taken again after a restart
                return
            self._fix_tasks[gid] = started

            def forget(done: asyncio.Task[None], gid: int = gid) -> None:
                if self._fix_tasks.get(gid) is done:
                    del self._fix_tasks[gid]

            started.add_done_callback(forget)

    async def _run_line_fix(
        self, store: web_line_fixes.ActionStore, guild_id: int, item: web_line_fixes.Fix
    ) -> None:
        with log_context(guild_id=guild_id, campaign_id=item.campaign_id):
            await web_line_fixes.run_one(self, store, guild_id, item)

    async def _trial_loop(self) -> None:
        """Every hour: the last-day messages and the automatic extensions (#1229). Waits a
        few minutes first so a restart loop can't send anything twice in a hurry (each message
        is also recorded, so none goes out twice anyway)."""
        await asyncio.sleep(300)
        while True:
            try:
                if self.trial_jobs is not None:
                    await self.trial_jobs.run()
                    if self.meter is not None:  # old sign-in and move-code records (#1229)
                        await mail.prune_account_emails(self.meter.db, int(time.time()))
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

    async def _idle_loop(self) -> None:
        """Once a minute, look at each running session for the automatic stop (#2034). One table
        at a time, and a failure for one never stops the others."""
        while True:
            await asyncio.sleep(IDLE_CHECK_S)
            for table in list(self.tables.values()):
                try:
                    await self._idle_check(table)
                except Exception:
                    log.exception("The idle check failed for a session")

    def keep_listening(self, guild_id: int, campaign_id: str, session: int) -> bool:
        """The DM pressed Keep listening: both clocks start again. False when that session is
        not the one running (the button is on an old warning)."""
        table = self.tables.get(guild_id)
        if table is None or table.campaign_id != campaign_id or table.segmenter.session != session:
            return False
        table.idle.keep(time.monotonic())
        table.idle_message = None
        return True

    async def _quiet_minutes(self, table: Table) -> int | None:
        """The campaign's quiet limit, read fresh so a change applies at once; None if it can't
        be read: that minute's check is skipped, rather than guessing a limit the DM may have
        changed (a campaign set to Off must not be warned by a database blip)."""
        if table.campaign_id is None:
            return None
        try:
            campaign = await self.campaigns.get(table.guild_id, table.campaign_id)
        except Exception:
            log.exception("Couldn't read the quiet-stop setting")
            return None
        return campaign.quiet_stop_minutes if campaign is not None else None

    async def _idle_check(self, table: Table) -> None:
        if not table.listening or table.campaign_id is None:
            return
        if self.tables.get(table.guild_id) is not table:
            return
        voice = self.get_channel(table.voice_channel_id)
        if not isinstance(voice, discord.VoiceChannel | discord.StageChannel):
            return  # the channel isn't known right now (a reconnect): not the same as empty
        minutes = await self._quiet_minutes(table)
        if minutes is None or self.tables.get(table.guild_id) is not table:
            return  # unreadable, or the session ended or was replaced while we looked
        now = time.monotonic()
        table.idle.saw_speech(now, sum(t.seconds for t in table.totals.speakers.values()))
        verdict = table.idle.poll(
            now, people=bool(self._people_in_voice(table)), quiet_minutes=minutes
        )
        with log_context(guild_id=table.guild_id, campaign_id=table.campaign_id):
            if verdict.action == "warn" and verdict.reason is not None:
                log.info("Idle: warning that the session will stop (%s)", verdict.reason)
                try:
                    table.idle_message = await self.post_message(
                        table.screen_channel_id,
                        idle_warning_text(verdict.reason, minutes),
                        keep_view(table.campaign_id, table.segmenter.session),
                    )
                except Exception:
                    log.exception("Couldn't post the idle warning")
                    table.idle_message = None
                if table.idle_message is None:
                    table.idle.unwarn()  # never stop someone who was not warned
            elif verdict.action == "stop" and verdict.reason is not None:
                log.info("Idle: stopping the session (%s)", verdict.reason)
                await self._remove_idle_warning(table)
                # Who was there when it stopped, and who runs the table: read before the stop
                # clears the table (#2288).
                in_voice = self._people_in_voice(table)
                dms = {table.dm_user_id, *table.dm_user_ids}
                name = table.campaign_name
                result = await self.stop_session(
                    table.guild_id,
                    table.dm_user_id,
                    True,
                    campaign_id=table.campaign_id,
                    session=table.segmenter.session,
                    auto=verdict.reason,
                )
                # Only this call's own stop is announced: a Stop pressed first, or a newer
                # session, answers with something else and nothing is said about it.
                if result.startswith("Stopped listening"):
                    await self.post(table.screen_channel_id, stopped_text(verdict.reason, minutes))
                    # Then private messages, in a task of their own: they never delay the stop
                    # and a closed inbox never breaks it (#2288). Not sent for a DM's own Stop.
                    self._track(
                        self._tell_auto_stop(
                            table.guild_id, table.campaign_id, name, verdict.reason, minutes,
                            dms, in_voice,
                        ),
                        "auto-stop messages",
                    )  # fmt: skip
            elif table.idle_message is not None and table.idle.warned_at is None:
                await self._remove_idle_warning(table, CANCELLED)  # people are back, or spoke

    async def _tell_auto_stop(
        self,
        guild_id: int,
        campaign_id: str,
        name: str,
        reason: str,
        minutes: int,
        dms: set[int],
        in_voice: list[int],
    ) -> None:
        """Say privately that DMbot stopped by itself (#2288): the campaign's DM with where to
        change it, and each other person who was in the voice channel. A closed inbox is skipped
        and only counted in the log (counts, never names or IDs)."""
        where = "with `/dmbot settings`"
        try:
            campaign = await self.campaigns.get(guild_id, campaign_id)
        except Exception:
            campaign = None
            log.exception("Couldn't read the campaign for the auto-stop message")
        if campaign is not None and campaign.dm_bot_channel_id:
            where = f"in <#{campaign.dm_bot_channel_id}>"
        sent = skipped = 0
        for user_id in sorted(dms):
            text = stopped_dm_text(name, reason, minutes, where)
            if await self._dm_user(user_id, text):
                sent += 1
            else:
                skipped += 1
        for user_id in dict.fromkeys(u for u in in_voice if u not in dms):
            if await self._dm_user(user_id, stopped_player_text(name, reason, minutes)):
                sent += 1
            else:
                skipped += 1
        log.info("Auto-stop messages: %d sent, %d skipped (closed inbox)", sent, skipped)

    @staticmethod
    async def _remove_idle_warning(table: Table, text: str | None = None) -> None:
        message, table.idle_message = table.idle_message, None
        if message is not None:
            with contextlib.suppress(discord.HTTPException):
                await message.edit(content=text if text is not None else message.content, view=None)

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

    async def upgrade_dm_bot_channel(self, campaign: Campaign) -> int | None:
        """A campaign made before the DM Bot channel (#1666) gets one when its ⚙️ Manage DMbot is
        pressed, not only at the next session start. Returns the new channel's id, or None
        (it already had one, or it couldn't be made: the start-time note says why). The
        campaign is read again first, so two quick presses make one channel."""
        guild = self.get_guild(campaign.guild_id)
        if guild is None:
            return None
        current = await self.campaigns.get(guild.id, campaign.id)
        if current is None or current.dm_bot_channel_id is not None:
            return None
        screen = (
            guild.get_channel(current.dm_screen_channel_id)
            if current.dm_screen_channel_id
            else None
        )
        await self._dm_bot_channel(guild, current, screen)  # (a failed make leaves no id)
        fresh = await self.campaigns.get(guild.id, campaign.id)
        return fresh.dm_bot_channel_id if fresh is not None else None

    async def _dm_bot_channel(
        self, guild: discord.Guild, campaign: Campaign, screen: object
    ) -> str | None:
        """The DM Bot channel (#1666), made or repaired at every start. A note for the DM if it
        couldn't be set up; a failure here never stops a session."""
        category = getattr(screen, "category", None)
        try:
            await setup_dm_bot_channel(
                guild,
                campaign.id,
                self.campaigns,
                category=category if isinstance(category, discord.CategoryChannel) else None,
            )
            fresh = await self.campaigns.get(guild.id, campaign.id)
            if fresh is not None:
                await dm_bot_buttons.ensure_panel(self, fresh)  # (a failure there is logged)
                if fresh.dm_bot_channel_id != campaign.dm_bot_channel_id and isinstance(
                    screen, discord.TextChannel
                ):  # a new channel: the DM screen's help card points to it now, not next start
                    try:
                        await update_help_card(screen, fresh, guild.me)
                    except Exception:  # the channel exists: never report it as failed
                        log.warning("Couldn't refresh the help card for the DM Bot channel",
                                    exc_info=True)  # fmt: skip
        except DmBotChannelError as exc:
            return screen_messages.dm_bot_failed(str(exc))
        except Exception:
            log.exception("Couldn't set up the DM Bot channel")
            return screen_messages.dm_bot_failed(screen_messages.TRANSCRIPT_NO_ANSWER)
        return None

    async def _tell_players_the_clock(self, guild_id: int, campaign_id: str) -> None:
        """At a start: if the game clock is set and the players' view of it differs from DMbot's
        last clock line in the chat channel, post it (#2877). Best effort, never blocks a start."""
        try:
            stored = await self.clocks.get(guild_id, campaign_id) if self.clocks else None
            fresh = await self.campaigns.get(guild_id, campaign_id)
            if stored is not None and fresh is not None:
                await clock_screen.show_players(self, fresh, stored)
                # Redraws the DM's pinned clock too, so an older one loses its rest buttons.
                await clock_screen.show(self, fresh, stored)
        except Exception as exc:
            log.warning("Couldn't show the clock at the start: %s", type(exc).__name__)

    async def _retire_old_channels(self, guild: discord.Guild, campaign: Campaign) -> None:
        """Delete the rules and NPC channels DMbot made before #2874 (only its own, by id and
        name) and forget their ids. A failure never stops a start; counts only are logged."""
        if campaign.rules_channel_id is None and campaign.npcs_channel_id is None:
            return
        try:
            await remove_old_channels(guild, campaign, self.campaigns)
        except Exception as exc:  # the type only
            log.warning("Couldn't remove the old rules and NPC channels: %s", type(exc).__name__)

    async def _start_up_campaign_sweep(self) -> None:
        """At bot start, once for every campaign, a few at a time (Discord's rate limits): delete
        the old rules and NPC channels that are left, and bring the NPC pages' view up to date, so
        a restart or a missed change never leaves it stale (people's notes depend on it)."""
        alive: set[str] = set()
        for guild in list(self.guilds):
            try:
                found = await self.campaigns.list_campaigns(guild.id)
            except Exception as exc:
                log.warning(
                    "Couldn't list campaigns for the start-up sweep: %s", type(exc).__name__
                )
                continue
            alive.update(c.id for c in found)
            for gone in [
                c for c, g in self._npcs_guilds.items() if g == guild.id and c not in alive
            ]:
                self._forget_campaign(gone)
            for campaign in found:
                self._npcs_guilds[campaign.id] = guild.id
                had_channels = (
                    campaign.rules_channel_id is not None or campaign.npcs_channel_id is not None
                )
                await self._retire_old_channels(guild, campaign)
                try:
                    if time.monotonic() - self._npcs_last.get(campaign.id, -1e9) >= 60:
                        await self._refresh_npc_pages(
                            guild.id, campaign.id
                        )  # (skipped if a start just did it)
                except Exception as exc:
                    log.warning("Couldn't update the NPC pages' view: %s", type(exc).__name__)
                    self._npcs_changed(campaign.id)  # try again soon
                if had_channels:
                    await asyncio.sleep(SWEEP_PACE_S)  # (deleting a channel is rate limited)

    def _npcs_service(self) -> RecallService:
        if self._npcs_recall is None:
            assert self.memory is not None
            self._npcs_recall = RecallService(self.memory)
        return self._npcs_recall

    async def _refresh_npc_pages(self, guild_id: int, campaign_id: str) -> None:
        """Write the campaign's players' view of its NPCs for the website's pages. Raises on a
        failure: the caller logs the type and tries again."""
        if self.memory is None or self._npcs_roster is None:
            return
        self._npcs_last[campaign_id] = time.monotonic()
        await refresh_npc_pages(
            self._npcs_service(), self._npcs_roster, guild_id, campaign_id, int(time.time())
        )

    def _npcs_changed(self, campaign_id: str) -> None:
        """A campaign's memory changed: bring its NPC pages' view up to date soon, at most once a
        minute. Quick and never raises (it only schedules)."""
        running = self._npcs_sync.get(campaign_id)
        if running is not None and not running.done():
            self._npcs_dirty.add(campaign_id)  # it will look again when it has finished
            return
        task = self._track(self._npcs_run(campaign_id), "npc-pages")
        if task is not None:
            self._npcs_sync[campaign_id] = task

    async def _npcs_run(self, campaign_id: str) -> None:
        try:
            for _ in range(NPCS_PASSES):
                await asyncio.sleep(pass_delay(self._npcs_last.get(campaign_id), time.monotonic()))
                self._npcs_dirty.discard(campaign_id)
                try:
                    await self._sync_npcs(campaign_id)
                    more = False
                except Exception as exc:  # the type only: a database error can quote a row
                    log.warning("Couldn't update the NPC pages' view: %s", type(exc).__name__)
                    more = True
                if campaign_id not in self._npcs_dirty and not more:
                    return
        finally:
            self._npcs_sync.pop(campaign_id, None)
            late = campaign_id in self._npcs_dirty
            self._npcs_dirty.discard(campaign_id)
            if late:
                self._npcs_changed(campaign_id)  # a change came in during the last pass

    async def _sync_npcs(self, campaign_id: str) -> None:
        guild_id = self._npcs_guilds.get(campaign_id)
        campaign = await self.campaigns.get(guild_id, campaign_id) if guild_id is not None else None
        if campaign is None:  # moved, gone, or never seen: look in every server once
            self._npcs_guilds.pop(campaign_id, None)
            for guild in list(self.guilds):
                if await self.campaigns.get(guild.id, campaign_id) is not None:
                    self._npcs_guilds[campaign_id] = guild_id = guild.id
                    break
            else:
                self._npcs_last.pop(campaign_id, None)
                return
        assert guild_id is not None
        await self._refresh_npc_pages(guild_id, campaign_id)

    async def _follow_npcs(self) -> None:
        """Hear every change to a campaign's memory, until cancelled, and refresh that
        campaign's NPC pages' view. Missed changes (the connection was down) are caught by the
        sweep at the next start; a reconnect just carries on."""
        assert self.memory is not None
        failures = 0
        while True:
            try:
                async with contextlib.aclosing(self.memory.listen(memory_notify.CHANNEL)) as stream:
                    failures = 0
                    async for raw in stream:
                        event = memory_notify.parse(raw)
                        if event is not None and (event.names_changed or event.kinds_changed):
                            # (entries, names, facts, and the labels of kinds show on the pages)
                            self._npcs_changed(event.campaign_id)
                        if event is not None and event.names_changed:
                            self._links_ready_changed(event.campaign_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Lost campaign memory updates for the NPC pages (%s)", exc)
            failures += 1
            await asyncio.sleep(min(5.0 * failures, 60.0))

    def _links_ready_changed(self, campaign_id: str) -> None:
        """A campaign's names changed: look soon at whether waiting backstory connections became
        ready (#2650). Quick and never raises; a burst of answers is one look."""
        if self.backstory_links is None or campaign_id in self._links_ready_pending:
            return
        self._links_ready_pending.add(campaign_id)
        if self._track(self._links_ready_look(campaign_id), "links-ready") is None:
            self._links_ready_pending.discard(campaign_id)

    async def _links_ready_look(self, campaign_id: str) -> None:
        """Say once, on the DM screen, how many waiting connections are ready to check (counts
        and the upload's name only). The wait lets a burst of answers (Add all) settle first."""
        try:
            await asyncio.sleep(LINKS_READY_SETTLE_S)
        finally:
            self._links_ready_pending.discard(campaign_id)
        store, memory = self.backstory_links, self.memory
        if store is None or memory is None:
            return
        found = None
        known = self._links_guilds.get(campaign_id)
        if known is not None:  # the server it was last found in: most looks are one lookup
            campaign = await self.campaigns.get(known, campaign_id)
            if campaign is not None:
                found = (known, campaign)
            else:  # moved to another server (or gone): forget it and look everywhere
                self._links_guilds.pop(campaign_id, None)
        if found is None:
            for guild in list(self.guilds):
                campaign = await self.campaigns.get(guild.id, campaign_id)
                if campaign is not None:
                    self._links_guilds[campaign_id] = guild.id
                    found = (guild.id, campaign)
                    break
        if found is None:
            return
        guild_id, campaign = found
        with log_context(guild_id=guild_id, campaign_id=campaign_id):
            # (nothing is marked told while there is nowhere to say it)
            if not campaign.dm_screen_channel_id or not await store.count(guild_id, campaign_id):
                return  # most campaigns have none waiting: the common case costs one count
            onto = await memory.ontology(guild_id, campaign_id)
            symmetric = {k for k, p in onto.predicates.items() if p.symmetric}
            taken = await store.take_ready_notices(guild_id, campaign_id, symmetric=symmetric)
            text = links_ready.notice(taken.counts)
            if text and not await self.post(
                campaign.dm_screen_channel_id, text, links_ready.view(campaign_id)
            ):
                await store.untell(guild_id, campaign_id, taken.ids)  # said next time instead

    def _winding_down(self, guild_id: int, campaign_id: str) -> bool:
        """Is a session of this campaign still finishing its after-session work (#2532)?"""
        return any(t.campaign_id == campaign_id for t in self._ending.get(guild_id, []))

    def _forget_moved(self, campaign_id: str) -> None:
        """A campaign moved to another server: drop what is kept by its id alone, and its loaded
        names. The caches keyed by (server, campaign) need nothing: the campaign no longer exists
        under the old server, so nothing asks for those keys again (as for a deleted campaign),
        and under the new server they start empty."""
        self._forget_campaign(campaign_id)
        if self.lookup is not None:
            self.lookup.drop([campaign_id])

    def _forget_campaign(self, campaign_id: str) -> None:
        """A campaign is gone: drop what the NPC pages' pass kept for it in memory (#1792). A pass
        that is running is left to finish and clean up after itself."""
        self._npcs_guilds.pop(campaign_id, None)
        self._npcs_last.pop(campaign_id, None)
        running = self._npcs_sync.get(campaign_id)
        if running is None or running.done():
            self._npcs_sync.pop(campaign_id, None)
            self._npcs_dirty.discard(campaign_id)

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
            fresh = {x.user_id for x in batch if x.user_id in still}
            fresh -= table.named_speakers
            if fresh:
                try:
                    table.named_speakers |= await self._keep_speaker_names(gid, session_id, fresh)
                except Exception:  # the names are a nicety: never hold up the lines
                    log.exception("Couldn't keep the speakers' names")

    async def _keep_speaker_names(self, gid: int, session_id: str, user_ids: set[int]) -> set[int]:
        """Keep these people's display names, as they are now, with the session's transcript so the
        website can show them (#2337). Returns the people settled: found, or not in the server
        (kept as not found). Anyone Discord could not answer for just now, and everyone when the
        server isn't there to ask, is left out, so they are tried again. Counts in the log, never
        names."""
        assert self.transcripts is not None
        guild = self.get_guild(gid)
        if guild is None:
            return set()
        settled: set[int] = set()
        wanted = sorted(user_ids)
        for start in range(0, len(wanted), MAX_SPEAKER_NAMES):
            ids = tuple(wanted[start : start + MAX_SPEAKER_NAMES])
            found, gone = await lookup_members(guild, ids)
            names: dict[int, str | None] = {u: n for u, n in found.items()}
            names.update({u: None for u in gone})
            await self.transcripts.record_names(gid, session_id, names)
            settled |= set(names)
        log.info("Kept %d speaker name(s) of %d asked", len(settled), len(wanted))
        return settled

    async def _name_old_speakers(self) -> None:
        """Once at start-up: keep the names of speakers in sessions saved before names were kept,
        or cut short by a restart, where DMbot can still find them (#2337). The name is the one they
        have now, and it includes people who stopped being recorded later but kept their past
        lines (as the save path does). A person who left the server is kept as not found, so this
        finds nothing the next time."""
        for guild in list(self.guilds):
            with log_context(guild_id=guild.id):
                try:
                    if self.transcripts is None:
                        return
                    for session_id, ids in await self.transcripts.unnamed(guild.id):
                        await self._keep_speaker_names(guild.id, session_id, set(ids))
                except Exception:
                    log.exception("Couldn't keep the names of an older session's speakers")
                await asyncio.sleep(1)  # a few servers a second, not all at once

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

    async def _retire_campaign_channels(
        self, campaign: HasChannels, now: int, *, by_hand: bool = False
    ) -> ChannelCounts:
        """Retention (or the plan holder, `by_hand`, #1608) deleted this campaign: delete its DM
        screen and game-time channels and close its transcript and dmb-chat channels (#1598).
        Only the channels recorded on it."""
        self._forget_campaign(campaign.id)
        me = self.user
        guild = self.get_guild(campaign.guild_id)
        if me is None:
            return await retire_channels(None, campaign, 0, now, by_hand=by_hand)
        return await retire_channels(guild, campaign, me.id, now, by_hand=by_hand)

    async def retire_campaign_channels(
        self, campaign: Campaign, now: int, *, by_hand: bool = False
    ) -> ChannelCounts:
        """The same, for the screens that delete a campaign (the settings card, #1608)."""
        return await self._retire_campaign_channels(campaign, now, by_hand=by_hand)

    async def dm_user(self, user_id: int, text: str) -> bool:
        """A private message that may fail; False if it did (for the screens, #1608)."""
        return await self._dm_user(user_id, text)

    async def _drop_old_suggestions(self, table: Table, session_id: str) -> None:
        """A suggested line fix that waited past the end of the next session is dropped, with no
        message (#1491): when a session ends, only suggestions for this session stay."""
        if self.suggestions is None or table.campaign_id is None:
            return
        try:
            await self.suggestions.drop_old(table.guild_id, table.campaign_id, [session_id])
        except Exception:
            log.exception("Couldn't drop old line-fix suggestions")

    async def _settle_topics(self, table: Table, session_id: str) -> None:
        """The off-topic filter leans to keeping (#2567): after the session, what it was not sure
        about is kept. Counts go in the log (no words), so it can be tuned on real sessions. Never
        stops the end of the session."""
        if self.transcripts is None:
            return
        try:
            done = await self.transcripts.settle_topics(
                table.guild_id, session_id, {table.dm_user_id, *table.dm_user_ids}
            )
        except Exception:
            log.exception("Couldn't settle the off-topic labels")
            return
        log.info(
            "Off-topic filter: kept %d, turned back %d, left out %d, put back by a DM %d",
            done.kept,
            done.turned_back,
            done.left_out,
            done.put_back,
        )

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
            await self._drop_old_suggestions(table, session_id)
            await self._settle_topics(table, session_id)
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


def _answer_in_the_way(
    notes: fix_notes.FixNotes, line: tuple[int, int], start: int, end: int
) -> bool:
    """Whether an answer already written into this line (speaker, started_ms) touches the
    words from `start` to `end` (as heard): writing over it would garble the line."""
    return any(a.line == line and a.start < end and start < a.end for a in notes.answers)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _free_bytes(root: Path) -> int:
    test_files.ensure_root(root)
    return shutil.disk_usage(root).free


def _bot(interaction: discord.Interaction) -> DMBot:
    return cast(DMBot, interaction.client)


def register_commands(tree: app_commands.CommandTree[Any]) -> None:
    """Add DMbot's slash commands to the tree. The one place both the bot's start-up and the
    check against Discord's limits (tests/test_command_limits.py) build the tree from."""
    tree.add_command(dmbot_group)
    tree.add_command(consent_group)
    tree.add_command(transcript_command)


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
    # One warning first, as from the 🗂️ Menu (#807): nothing stops until Yes, which stops
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


def register_backup_sections(
    campaigns: CampaignStore,
    *,
    forget_audio: Callable[[int, str], None],
    consenting: Callable[[int], Awaitable[frozenset[int]]],
) -> None:
    """Everything a campaign's backup holds (and so what a backup test must export): one list, used
    by the running bot and by the tests, so a test exports exactly what production does."""
    campaigns.register_section(MemorySection())  # campaign memory goes in backups
    campaigns.register_section(HouseRulesSection())  # and so do house rules (#865)
    campaigns.register_section(WinsSection())  # and which disagreeing rule wins (#2284)
    campaigns.register_section(HomebrewSection())  # and its own homebrew (#1520)
    campaigns.register_section(AdventureSection())  # and its adventure text (#1698)
    campaigns.register_section(SpendSection())  # and what reading it cost, amounts only (#1723)
    campaigns.register_section(SiteApprovalsSection())  # the sites it approved (#1633)
    campaigns.register_section(MapNotesSection())  # and the maps posted at its table (#2450)
    campaigns.register_section(StyleSection())  # and how it likes its recaps to sound (#2075)
    campaigns.register_section(NotesSection())  # and its standing notes on that (#2076)
    campaigns.register_section(ImportSpeakersSection())  # and who its import labels were (#2105)
    campaigns.register_section(FollowedSetsSection())  # and the sets it follows (#1260)
    campaigns.register_section(RuleSetsSection())  # and the sets it publishes (#2552)
    campaigns.register_section(ClockSection())  # and the game clock (#965)
    campaigns.register_section(EffectsSection())  # and its timed effects (#998)
    # and its session recaps (#1382), whose audio files go with the campaign (#1383)
    campaigns.register_section(RecapSection(forget_audio=forget_audio))
    campaigns.register_section(RecapRightsSection())  # who may use recaps written by the table
    campaigns.register_section(AcceptedSection())  # the recaps the DM approved (#2122)
    campaigns.register_section(SessionNotesSection())  # each session's notes, kept for good (#3026)
    campaigns.register_section(PlotIssuesSection())  # PlotBot's issues and the DM's answers
    campaigns.register_section(PlotFineSection())  # ... and what the DM said was not a problem
    campaigns.register_section(ReportAnswersSection())  # the DM's report answers (#2661)
    # And how often names were said, kept only for who is recorded now (#1747).
    campaigns.register_section(HeardSection(consenting))
    campaigns.register_section(ImportanceSection())  # after memory: needs its entries (#3172)


async def run(settings: Settings) -> None:
    entitlements.configure_free_users(settings.free_users)  # #771
    alerter = alerts.Alerter(settings.mail)
    alerts.install(alerter)
    alerter.say_if_off()
    try:
        db = await Database.open(settings.database_url, max_size=settings.database_pool_max)
    except DatabaseError:
        await alerter.alert(alerts.Problem.DATABASE_DOWN)  # before the exit, so it is sent
        raise
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
        homebrew_store, sources = HomebrewStore(db), SourceStore(db)
        # DMBot sets this too; passing it here means the store never starts out wrong.
        consent = ConsentStore(db, outside=settings.transcription.outside_engine)
        register_backup_sections(
            campaigns,
            forget_audio=AudioFiles(settings.data_dir / "recap-audio").forget_campaign,
            consenting=consent.consenting,
        )
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
            PackSuggestionStore(db),  # shared sets and homebrew packs (#1190, #1527, #1537)
            RecapStore(db),
            SuggestionStore(db),
            homebrew_store,
            WaitingQuestionStore(db),
            OptOutStore(db),
            sources,
            HomebrewImporter(homebrew_store, sources),
            AdventureStore(db),
            PlanStore(db),
            npcs_roster=NpcRosterStore(db),
            recap_styles=StyleStore(db),
            recap_notes=NoteStore(db),
            installs=InstallStore(db),
            recap_holds=recap_hold.HoldStore(db),
            backstory_links=BackstoryLinks(db),
        )
        bot.house_wins = WinsStore(db)
        bot.map_notes = MapNoteStore(db)  # maps put into words, for MapBot (#2450)
        if bot.image_desk is not None:  # the day's count lives in the database (#2450)
            bot.image_desk.counter = ImageUseStore(db)
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
