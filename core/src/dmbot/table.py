"""The state of one running session at a table (the `Table`), moved out of `bot.py` (#2827) so
features can name it in type hints without importing the bot. Plain data: the bot owns it and
the features read it through small methods. Nothing here talks to Discord by itself.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field

import discord

from dmbot import (
    table_chat,
)
from dmbot.audio.segmenter import Segmenter
from dmbot.audio_check import AudioChecker
from dmbot.campaigns.models import (
    DEFAULT_DM_SCREEN_LEVEL,
)
from dmbot.capture_log import CaptureLog, SessionTotals
from dmbot.dm_questions import Ask
from dmbot.dm_screen import (
    rules_cards,
)
from dmbot.fightbot import FightState
from dmbot.idle_stop import IdleWatch
from dmbot.memory.live_heads_up import LiveSession
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.ontology import Tally
from dmbot.memory.scene import HintParts, SceneTracker
from dmbot.memory.secret_reveal import RevealSession
from dmbot.memory.sheet_questions import Questions as SheetQuestions
from dmbot.memory.sheet_store import CharacterSheet
from dmbot.memory.sheet_watch import SheetWatch
from dmbot.memory.table_names import NameThenAnswer
from dmbot.rules import house_voice
from dmbot.rules.homebrew_spotter import HomebrewSpotter
from dmbot.sidebar.ask import AskLimiter
from dmbot.sidebar.service import Recent
from dmbot.test_recording.session import TestSession
from dmbot.timebot import suggest as time_suggest
from dmbot.transcript import (
    fix_notes,
    left_out,
    lookback,
    speaker_ai,
)
from dmbot.transcript import pronounced as pronounced_aloud
from dmbot.transcript import questions as name_questions
from dmbot.transcript import speaker_question as speaker_q
from dmbot.transcript import spelled as spelled_out
from dmbot.transcript.cleaner import (
    Vocabulary,
)
from dmbot.transcript.close_calls import CloseCalls
from dmbot.transcript.models import (
    TranscriptBuffer,
)
from dmbot.transcript.stream import TranscriptStream
from dmbot.transcript.topics import TopicWindow

HintPeople = tuple[tuple[str, ...], tuple[str, ...]]  # (at the table, agreed but not there)


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
    # What the ontology steward decided this session (#1303), for one line in the server log.
    steward_tally: Tally = field(default_factory=Tally)
    # Spell, feature and item names from the players' D&D Beyond sheets (#723).
    sheet_hints: tuple[str, ...] = ()
    # Names someone spelled out at the table this session (#2168): watched for in every cleaned
    # line, kept as (spelling, whom it was said by, a word nearby that sounds like it) for the
    # after-session suggestions, and as speech hints at once. Words only, never a line.
    spelling: spelled_out.SpellingWatcher = field(default_factory=spelled_out.SpellingWatcher)
    spelled: list[tuple[str, int, str]] = field(default_factory=list)
    spelled_hints: tuple[str, ...] = ()
    held_spelled: dict[tuple[int, int], list[spelled_out.SpelledOut]] = field(
        default_factory=dict
    )  # spellings found in lines the off-topic filter holds, until they are labelled
    # A name said with how it is pronounced (#2171): watched for in every game-talk line, kept as
    # the counts below for the after-session offers. Words only.
    pronouncing: pronounced_aloud.PronunciationWatcher = field(
        default_factory=pronounced_aloud.PronunciationWatcher
    )
    # (name ID, the sound's key, whom it was said by) → how it was written → how often, so the
    # sound said most is offered ("Kay el" and "Kay-el" count together).
    pronounced: dict[tuple[str, str, int], dict[str, int]] = field(default_factory=dict)
    # What a line the off-topic filter holds said about a name, until it is labelled: each sound
    # with the name it was heard to belong to (settled when it was heard), words only.
    held_pronounced: dict[tuple[int, int], list[tuple[pronounced_aloud.Sound, str]]] = field(
        default_factory=dict
    )
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
    where_task: asyncio.Task[None] | None = None  # the quiet "where are we" note (#1738)
    # When the game clock was last shown or moved (monotonic seconds), starting at the session's
    # start; kept in memory only (a restart just starts the wait again).
    clock_shown_at: float = field(default_factory=time.monotonic)
    sheet_loads: int = 0  # each load of the names counts up; only the newest one is used
    # For the Transcript Cleaner (#127): what this session's lines say about words, and
    # the display names of people who agreed (never "fixed" into a name).
    vocabulary: Vocabulary = field(default_factory=Vocabulary)
    # "Did they mean…?" for the DM (#296): one open at a time, each word once a session.
    questions: name_questions.QuestionBook = field(default_factory=name_questions.QuestionBook)
    # Where the open question was asked (#3210): one copy in each reachable DM's private chat.
    # When one DM answers it, or it runs out, every copy is edited. `closed_copies` keeps the copies
    # of a question just answered until the answering DM's own message has been dealt with.
    question_messages: list[discord.Message] = field(default_factory=list)
    # (question id -> the id of the message that answered it, and the copies to edit afterwards)
    closed_copies: dict[str, tuple[int | None, list[discord.Message]]] = field(default_factory=dict)
    # How often a line landed near one of the Cleaner's likeness thresholds (#1577): counts only.
    close_calls: CloseCalls = field(default_factory=CloseCalls)
    # Questions that closed with no answer (expired, or the session ended), for the after-session
    # check (#1572): kept in memory, at most MAX_PER_SESSION, oldest dropped first.
    unanswered: list[name_questions.Asked] = field(default_factory=list)
    # The words each piece has in the helpers' copies (heard, recent, held, counts), by
    # (speaker, started_ms), so a later change can bring those copies up to date (#127). Bounded.
    piece_words: dict[tuple[int, int], tuple[str, float]] = field(default_factory=dict)
    # Each person's last piece of speech as the Cleaner read it, for the look-back pass (#127).
    last_piece: dict[int, lookback.Piece] = field(default_factory=dict)
    # Fixes from names DMbot only suggested, each with Undo, in one DM-screen message.
    fix_notes: fix_notes.FixNotes = field(default_factory=fix_notes.FixNotes)
    fix_message: discord.Message | None = None
    fix_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    fix_queued: bool = False  # a redraw is waiting: more changes just ride along
    fix_ended: bool = False  # the session is over: the lists stay, their buttons go
    # What the Cleaner did this session, for the one line of counts in the end-of-session message
    # (#1510): name fixes made, "Did they mean…?" questions asked. Counts only; no words.
    names_fixed: int = 0
    # How often the cleaner fixed a name from the same other spelling, per person (#2171): the
    # words only, kept for the session. Dropped for anyone who stops being recorded.
    heard_forms: dict[tuple[str, str, str, int], int] = field(default_factory=dict)
    names_asked: int = 0
    # The AI ranking of "Did they mean…?" options (#2048), counts only, for the session's end log:
    # calls, "likely" chosen, "none", errors; and of the questions answered with a likely first
    # option, how many the DM picked it for.
    rank_calls: int = 0
    rank_likely: int = 0
    rank_none: int = 0
    rank_errors: int = 0
    rank_answered: int = 0
    rank_picked: int = 0
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
    # The campaign's own homebrew names, for the rules cards (#1524); None until read.
    homebrew_spotter: HomebrewSpotter | None = None
    homebrew_read: int = 0  # which read of the names is the newest (an older one is dropped)
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
    # DMs told once this session that a story statement belongs in the private chat (#3209).
    dmbchat_hinted: set[int] = field(default_factory=set)
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
    # The new names the after-session scan found (#2410): they ride in the secrets summary when
    # there is one, and are posted on their own by the "new names" step when nothing carried them.
    new_names: list[str] = field(default_factory=list)
    # How many things said at the table wait for the DM's check (#2410): counted by the "what was
    # said" step, told by the summary when it goes to the DM screen, else posted on its own.
    claims_waiting: int = 0
    # The questions the after-session steps have for the DM (#3210), collected so they go out as
    # one message. None means "not collecting": a question is then sent at once, on its own.
    asks: list[Ask] | None = None
    # The first few of them in words ("**Bram** carries **Heartseeker**? (a player said)"), for the
    # DM-only summary. Never posted where a player can read: the screen post says the count only.
    claim_preview: list[str] = field(default_factory=list)
    # Who answered to which name this session ("a name, then an answer", #1188); in memory
    # only, and nothing a speaker said stays once they stop being recorded.
    name_answers: NameThenAnswer = field(default_factory=NameThenAnswer)
    # How many lines of each speaker named each known name, kept at the end of the
    # session so hints can rank names by how often and when they come up. Bounded by
    # names times speakers.
    heard_counts: Counter[tuple[str, int]] = field(default_factory=Counter)
    # The stored transcript (#41, #125): this session's row, and lines not saved yet.
    started_at: int = 0  # Unix seconds; the same after a restart
    live_heads_up: LiveSession = field(default_factory=LiveSession)  # #2068, for this session
    reveal: RevealSession = field(default_factory=RevealSession)  # #2088, for this session
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
    # Speakers whose display names are kept with this session's transcript (#2337), so a name is
    # looked up once for each person, not on every save.
    named_speakers: set[int] = field(default_factory=set)
    transcript_warned: bool = False  # told the DM saving isn't working
    listening_message: discord.Message | None = None  # carries the Stop button (#108)
    # Is anyone here? (#2034) The watch for the automatic stop, and the warning's message. A
    # new table (a start or a resume) starts a new watch: a restart is not silence.
    idle: IdleWatch = field(default_factory=lambda: IdleWatch(time.monotonic()))
    idle_message: discord.Message | None = None
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
    # A time jump suggested from vague narration (#1758): what this session remembers about it.
    time_suggest: time_suggest.State = field(default_factory=time_suggest.State)
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
    # A DM's spoken look-up waits here for its line's speaker label (#1624): (speaker, started_ms)
    # -> the label (the NPC's name, or "" for none). Set when the label is saved or the labelling
    # call ends, so a line already flushed to the database still answers.
    label_waits: dict[tuple[int, int], asyncio.Future[str]] = field(default_factory=dict)
    early_close_at: float = -1e9  # monotonic seconds of the last early close of the window
    # The DM sidebar (#935): what was said lately (the scene for an answer), and the
    # safety limit on questions (6 a minute, 120 a session; #1040), spoken and typed.
    recent: Recent = field(default_factory=Recent.new)
    # Test recordings (#1019): the saved session, only in a test server.
    test_session: TestSession | None = None
    sidebar_limiter: AskLimiter = field(default_factory=AskLimiter)

    def is_dm(self, user_id: int) -> bool:
        return user_id == self.dm_user_id or user_id in self.dm_user_ids
