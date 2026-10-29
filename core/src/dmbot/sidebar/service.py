"""The DM sidebar's ways in (docs/PLAN.md, "DM sidebar"; #935).

Three roads lead to the same place, a short answer sent privately to the DM:
a voice message in the DM's chat with DMbot, a typed message there, and the DM saying
"hold on, I need to find …" at the table. The answer itself comes from #934
(`Answerer`); this module hears the question, checks the rules and writes the transcript
lines.

Rules kept here:
- only a campaign's DM, only while a session of theirs is running, and only a DM who agreed
  to be recorded. Consent is checked again after every step that waits.
- a voice message's audio is never kept: it is read into memory and dropped.
- the answer is never posted anywhere but the DM's own chat.
- the question and DMbot's in-game answer are saved as sidebar lines (raw transcript only;
  see dmbot.sidebar.access for who may read them).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import discord

from dmbot.ai import AIError
from dmbot.audio.segmenter import Utterance
from dmbot.discord_limits import MESSAGE_MAX
from dmbot.mentions import NO_PINGS
from dmbot.recaps import feedback as recap_feedback
from dmbot.rules import house_voice
from dmbot.rules.wins import RuleRef
from dmbot.sidebar import guide_phrase, looked, memo, rule_clash, table_links
from dmbot.sidebar.ask import AskLimiter, Request, request_in, voiced_by_npc
from dmbot.transcript.models import (
    SIDEBAR_ANSWER,
    SIDEBAR_QUESTION,
    SIDEBAR_TABLE_QUESTION,
    VIA_CHAT,
    VIA_TABLE,
    VIA_TABLE_CHAT,
    VIA_TYPED,
    VIA_VOICE,
    Line,
    Lineage,
)
from dmbot.transcription.base import TranscriptionProblem

if TYPE_CHECKING:
    from dmbot.campaigns import Campaign
    from dmbot.table import Table

log = logging.getLogger(__name__)

MAX_TYPED_CHARS = 500
ANSWER_TIMEOUT_S = 25.0
SCENE_SECONDS = 180.0
SCENE_CHARS = 1_200
RECENT_LINES = 40
MIN_WORDS = 2  # "ok" or "thanks" is not a question
READ_TIMEOUT_S = 30.0
NOTE_KEEP = 1_000
UNSTORED_KEEP = 200  # questions asked off the record and not saved yet (they finish in seconds)
DECODE_AT_ONCE = 2
MESSAGE_LIMIT = MESSAGE_MAX
NOTE_EVERY_S = 30.0  # the same kind of note to the same person, at most this often
ASK_PER_PERSON_PER_MINUTE = 2  # so one player can't use up the table's questions (#1187)
ASK_PER_PERSON_PER_SESSION = 30  # (and not the whole session's 120 either)
Q_CHARS = 100  # the question as shown in the chat: one short line

NO_SESSION = "No game is running with you as DM. Start one with /dmbot start, then ask again."
NOT_YET = "Quick answers aren't switched on yet."
NOT_SOON_AGAIN = "Still on your last question. One moment."
FAILED = "I couldn't answer that. Try again, or type it here."
NO_WORDS = "I couldn't make out any words. Try again."
NOT_AGREED = (
    "First, agree to be recorded with the button below. Then ask again. "
    "I didn't keep your question."
)
CANT_DM = (
    "I couldn't message you the answer privately. Allow direct messages from this server "
    "(server name, then Privacy Settings), then ask again."
)
HOUSE_SENT = "It's on your DM screen now. Press **Save** to keep it. Nothing is saved until you do."
HOUSE_SOON = (
    "One house rule a minute, and your last one is on your DM screen. Try again in a minute."
)
HOUSE_REPEAT = "I already offered that house rule in this game. Look on your DM screen."
HOUSE_NOT_NOW = (
    "I can't offer that right now. Check that the game is still running, "
    "then type your house rule again."
)
HOUSE_NO_SESSION = (
    "No game is running with you as DM. Start one with /dmbot start, "
    "then type your house rule again."
)
HOUSE_NOT_AGREED = (
    "First, agree to be recorded with the button below. Then type your house rule again. "
    "I didn't keep what you typed."
)
HOUSE_WHICH = "Which game is this house rule for?"
WHICH = "Which game is this for?"
SENT_PRIVATELY = "Sent to the DM privately."
PRIVATE_NOTE = "(answered privately: Guide me)"  # all the transcript keeps of a Guide me reply
COULDNT_SEND = "I couldn't send that to the DM. Ask again."
TOO_MANY = "That's a lot of questions at once. Ask again in a minute."
TYPE_INSTEAD = "I can only read typed questions and voice messages."
NOT_YOURS = "This isn't your question."
CLASH_SAVED = "(asked the DM which house rule applies)"  # all the transcript keeps of it
CLASH_NOTE = rule_clash.NOTE
TOO_LONG = memo.TOO_LONG
ECHO_CHARS = 120


class Answer(Protocol):
    """What the answer engine (#934) gives back."""

    @property
    def text(self) -> str: ...
    @property
    def in_game(self) -> bool: ...  # about the campaign or the game: goes in the transcript
    @property
    def refused(self) -> bool: ...  # the plan said no: `text` has the plain words
    # Where the reply came from, kept with it in the transcript (owner, #933).
    @property
    def model(self) -> str: ...
    @property
    def prompt_version(self) -> str: ...
    @property
    def sources(self) -> Sequence[str]: ...  # rules entries, house rule numbers, facts, span
    @property
    def parts(self) -> Sequence[str]: ...  # a long answer as messages; else (text,)
    @property
    def one_for_dm(self) -> bool: ...  # a table question that needs the DM (#1187)
    @property
    def question(self) -> str: ...  # the question restated in a few words, or ""
    @property
    def clash(
        self,
    ) -> tuple[rule_clash.Side, rule_clash.Side] | None: ...  # two rules disagree (#2284)

    # (the entries it was built from, #1957: read with getattr, an engine without it has none)


class Answerer(Protocol):
    async def answer(
        self, campaign: Campaign, question: str, *, asker_id: int, scene: str, table: bool = False
    ) -> Answer: ...


class Host(Protocol):
    """What the bot gives the sidebar (dmbot.bot.DMBot)."""

    @property
    def tables(self) -> Mapping[int, Table]: ...
    def sidebar_has_consent(self, guild_id: int, user_id: int) -> bool: ...
    def sidebar_paused(self, guild_id: int, user_id: int) -> bool: ...
    def name_of(self, guild_id: int, user_id: int) -> str: ...
    async def sidebar_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...
    def sidebar_server_name(self, guild_id: int) -> str: ...
    def consent_request(self, table: Table) -> tuple[str, discord.ui.View]: ...
    async def sidebar_transcribe(self, table: Table, utterance: Utterance) -> str | None: ...
    def sidebar_clean(self, table: Table, text: str) -> str: ...
    # sent, forbidden or failed; `view`, under the text, only for a rules answer (#1957)
    async def sidebar_send_dm(
        self, user_id: int, text: str, view: discord.ui.View | None = None
    ) -> str: ...
    def sidebar_save(self, table: Table, line: Line) -> None: ...
    def sidebar_stt(self) -> str: ...  # the speech-to-text in use: "engine model host"
    async def sidebar_tell_screen(self, table: Table, text: str) -> None: ...
    # The DM chose which of two disagreeing house rules applies (#2284); False if it was not saved.
    async def sidebar_record_win(
        self, campaign: Campaign, winner: RuleRef, loser: RuleRef, by: int
    ) -> bool: ...
    # The Discord screens under an answer, built by the bot so no sidebar module imports `ui`
    # (#2827): the two buttons of "which house rule applies?" (#2284), and "📖 What I looked at"
    # for the list kept under `key` (#1957).
    def sidebar_clash_view(
        self,
        campaign: Campaign,
        clash: tuple[rule_clash.Side, rule_clash.Side],
        retry: Callable[[], Awaitable[None]],
    ) -> discord.ui.View: ...
    def sidebar_looked_view(self, key: str) -> discord.ui.View: ...
    # The DM's spoken feedback on a waiting recap (#2076); False when there is none to talk about.
    async def sidebar_recap_feedback(self, table: Table, user_id: int, text: str) -> bool: ...
    def sidebar_house_rule(self, table: Table, user_id: int, text: str) -> str: ...
    # The table's DMbot chat (#1187): post there; how to name someone (their character's name
    # if one is linked, else their nickname); the links this campaign (and only it) may show;
    # and the one reply with the agree button for someone who typed there before agreeing.
    async def sidebar_post_chat(self, table: Table, text: str) -> bool: ...
    async def sidebar_table_name(self, table: Table, user_id: int) -> str: ...
    async def sidebar_links(self, table: Table) -> tuple[str | None, tuple[str, ...]]: ...
    async def sidebar_nudge_chat(self, table: Table, user_id: int) -> None: ...
    # Guide me's answer (#1739), or None when this campaign's three conditions don't hold.
    async def sidebar_guide(self, table: Table, user_id: int) -> str | None: ...


# The text, and (for the DM's private answer only) the view under it: "📖 What I looked at" (#1957).
Reply = Callable[..., Awaitable[bool]]


@dataclass(slots=True)
class Recent:
    """What was said lately, to give the answer engine the scene. In memory, bounded."""

    lines: deque[tuple[float, int, str]]
    # The same, but only what was *spoken* (cleaned by the Transcript Cleaner): typed chat
    # is raw text anyone can write anything in, so FightBot (#1129) reads only this.
    spoken: deque[tuple[float, int, str]] = field(
        default_factory=lambda: deque(maxlen=RECENT_LINES)
    )

    @classmethod
    def new(cls) -> Recent:
        return cls(deque(maxlen=RECENT_LINES))

    def add(self, user_id: int, text: str, now: float, *, typed: bool = False) -> None:
        if text.strip():
            self.lines.append((now, user_id, text))
            if not typed:
                self.spoken.append((now, user_id, text))

    def revise(self, user_id: int, old: str, new: str) -> bool:
        """A line's words changed after the fact (a name fixed, #127): the copy of `old` by this
        person says `new` now, in each list that holds exactly one such copy (two identical
        lines from one person can't be told apart, and a missed update is safer than a wrong
        one). True if anything was changed."""
        changed = False
        for lines in (self.lines, self.spoken):
            hits = [i for i, (_, who, text) in enumerate(lines) if who == user_id and text == old]
            if len(hits) == 1:
                at, who, _ = lines[hits[0]]
                lines[hits[0]] = (at, who, new)
                changed = True
        return changed

    def drop_speaker(self, user_id: int) -> None:
        """Someone stopped being recorded: nothing they said is used again."""
        for lines in (self.lines, self.spoken):
            kept = [item for item in lines if item[1] != user_id]
            lines.clear()
            lines.extend(kept)

    def scene(
        self,
        name_of: Callable[[int], str],
        now: float,
        agreed: Callable[[int], bool] = lambda user_id: True,
    ) -> str:
        """The last few minutes, newest last, cut to a size the engine can take. Only
        people who still agree to be recorded."""
        kept = [(u, t) for at, u, t in self.lines if now - at <= SCENE_SECONDS and agreed(u)]
        out: list[str] = []
        size = 0
        for user_id, text in reversed(kept):
            piece = f"{name_of(user_id)}: {' '.join(text.split())}"
            if size + len(piece) > SCENE_CHARS and out:
                break
            out.append(piece[:SCENE_CHARS])
            size += len(piece) + 1
        return "\n".join(reversed(out))


class SidebarService:
    def __init__(
        self,
        host: Host,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.host = host
        self._monotonic = monotonic  # what Table.recent is timed with
        self.answerer: Answerer | None = None  # set when the answer engine (#934) exists
        self._clock = clock
        self._busy: set[tuple[int, str]] = set()  # campaigns with a question being answered
        self._noted: dict[tuple[int, str], float] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._decoding = asyncio.Semaphore(DECODE_AT_ONCE)
        self._known_dms: set[int] = set()  # seen as a campaign's DM since DMbot started
        self._undelivered: dict[int, str] = {}  # why the last private message failed
        # (who, when) of questions asked while off the record: answered, never stored (#2466)
        self._unstored: set[tuple[int, int]] = set()
        self.looked = looked.Registry()  # the lists behind "What I looked at" (#1957)

    # ---- the DM's chat with DMbot ---------------------------------------------------

    async def on_dm_message(self, message: discord.Message) -> None:
        """A message sent to DMbot in a private chat. Anything but a DM of a running game
        asking something gets at most a short note."""
        if message.author.bot or message.guild is not None:
            return
        user_id = message.author.id
        voice = message.flags.voice and bool(message.attachments)
        typed = message.content.strip() if not message.attachments else ""
        if not voice and not typed:
            if message.attachments and self._note_ok(user_id, "kind"):
                with contextlib.suppress(discord.HTTPException):
                    await message.channel.send(TYPE_INSTEAD, allowed_mentions=NO_PINGS)
            return
        # (a rule of fewer than two words isn't one: it is read as any other message)
        typed = typed[:MESSAGE_LIMIT]
        if typed and not typed.endswith("?") and house_voice.find(typed) is not None:
            await self._house_rule_message(message, typed)  # no AI, so no answer engine needed
            return
        if typed and not _is_question(typed):
            return  # "ok", "thanks", "👍": not a question, no AI call, nothing written down
        tables = self.tables_of(user_id)

        async def reply(text: str, view: discord.ui.View | None = None) -> bool:
            try:
                if view is None:
                    await message.channel.send(text, allowed_mentions=NO_PINGS)
                else:
                    sent = await message.channel.send(text, view=view, allowed_mentions=NO_PINGS)
                    keep = getattr(view, "keep_message", None)  # a view that edits itself (#2284)
                    if keep is not None:
                        keep(sent)
            except discord.HTTPException:
                return False
            return True

        if not tables:
            # Only someone known to be a campaign's DM is told; anyone else gets nothing.
            if user_id in self._known_dms and self._note_ok(user_id, "nosession"):
                await reply(NO_SESSION)
            return
        # Guide me needs no AI (#1745), so a typed "what's next?" works without an answer engine.
        if self.answerer is None and not (typed and guide_phrase.is_whats_next(typed)):
            # (checked before any download or speech-to-text is paid for)
            if self._note_ok(user_id, "notyet"):
                await reply(NOT_YET)
            return
        if len(tables) == 1:
            await self._from_message(tables[0], message, reply)
            return
        await message.channel.send(
            WHICH, view=_PickView(self, tables, message, reply), allowed_mentions=NO_PINGS
        )

    async def _house_rule_message(self, message: discord.Message, typed: str) -> None:
        """A typed "house rule: …" from a DM (#960): the same proposal as one said aloud, on
        the DM screen. Not a question: no AI, no sidebar line, nothing written down here."""
        user_id = message.author.id

        async def reply(text: str) -> bool:
            try:
                await message.channel.send(text, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                return False
            return True

        tables = self.tables_of(user_id)
        if not tables:
            if user_id in self._known_dms and self._note_ok(user_id, "nosession"):
                await reply(HOUSE_NO_SESSION)
            return

        async def propose(table: Table) -> None:
            await self._house_rule_for(table, message, typed, reply)

        if len(tables) == 1:
            await propose(tables[0])
            return
        if not self._note_ok(user_id, "which"):  # (each picker holds its message for 5 minutes)
            return
        view = _PickView(self, tables, message, reply, propose, HOUSE_NO_SESSION)
        await message.channel.send(HOUSE_WHICH, view=view, allowed_mentions=NO_PINGS)

    async def _house_rule_for(
        self, table: Table, message: discord.Message, typed: str, reply: Reply
    ) -> None:
        user_id = message.author.id
        if not await self._agreed(table, message, reply, HOUSE_NOT_AGREED):
            return
        if not self._still(table, user_id):
            await reply(HOUSE_NO_SESSION)
            return
        result = self.host.sidebar_house_rule(table, user_id, typed)
        if result != house_voice.STARTED and not self._note_ok(user_id, "house-no"):
            return  # the refusals are said once in a while, not for every message
        await reply(
            {
                house_voice.STARTED: HOUSE_SENT,
                house_voice.TOO_SOON: HOUSE_SOON,
                house_voice.REPEAT: HOUSE_REPEAT,
            }.get(result, HOUSE_NOT_NOW)
        )

    async def _agreed(
        self, table: Table, message: discord.Message, reply: Reply, ask: str = NOT_AGREED
    ) -> bool:
        """Has the DM agreed to be recorded? If not, say so (and ask) once in a while."""
        user_id = message.author.id
        if self.host.sidebar_has_consent(table.guild_id, user_id):
            return True
        if self.host.sidebar_paused(table.guild_id, user_id):
            return False  # off the record, not unagreed: no nudge to agree
        if self._note_ok(user_id, "agree"):
            text, view = self.host.consent_request(table)
            await reply(ask)
            try:
                await message.channel.send(text, view=view, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                log.info("Couldn't send the consent question to user %s", user_id)
        return False

    def tables_of(self, user_id: int) -> list[Table]:
        """The running sessions this person is a DM of."""
        mine = [
            t for t in self.host.tables.values() if t.campaign_id is not None and _is_dm(t, user_id)
        ]
        if mine:
            self._known_dms.add(user_id)
        return mine

    def _note_ok(self, user_id: int, kind: str) -> bool:
        now = self._clock()
        if len(self._noted) > NOTE_KEEP:  # strangers can message DMbot: keep this small
            self._noted = {k: at for k, at in self._noted.items() if now - at < NOTE_EVERY_S}
        if now - self._noted.get((user_id, kind), -NOTE_EVERY_S) < NOTE_EVERY_S:
            return False
        self._noted[(user_id, kind)] = now
        return True

    async def _from_message(self, table: Table, message: discord.Message, reply: Reply) -> None:
        user_id = message.author.id
        if not await self._agreed(table, message, reply):
            return
        if _campaign_key(table) in self._busy:
            if self._note_ok(user_id, "busy"):
                await reply(NOT_SOON_AGAIN)
            return
        if not table.sidebar_limiter.allow(self._clock()):
            if self._note_ok(user_id, "limit"):
                await reply(TOO_MANY)
            return
        self._busy.add(_campaign_key(table))
        try:
            heard: str | None
            if message.flags.voice and message.attachments:
                heard = await self._hear(table, user_id, message.attachments[0], reply)
                if heard is None:
                    return
                if not _is_question(heard):
                    await reply(NO_WORDS)
                    return
                show = True
            else:
                heard = message.content.strip()[:MAX_TYPED_CHARS]
                show = False
            await self._ask(table, user_id, heard, reply, via=VIA_VOICE if show else VIA_TYPED)
        finally:
            self._busy.discard(_campaign_key(table))

    async def _hear(
        self, table: Table, user_id: int, attachment: discord.Attachment, reply: Reply
    ) -> str | None:
        """The voice message as text, or None after telling the DM why not."""
        guild_id = table.guild_id
        length = getattr(attachment, "duration", None)  # Discord says how long it is
        if attachment.size > memo.MAX_MEMO_BYTES or (length and length > memo.MAX_MEMO_S):
            await reply(TOO_LONG)  # before downloading anything
            return None
        try:
            data = await asyncio.wait_for(attachment.read(), READ_TIMEOUT_S)
            async with self._decoding:  # a few at a time: it is CPU work in a shared pool
                pcm = await asyncio.to_thread(memo.decode, data)
        except memo.MemoError as exc:
            await reply(str(exc))
            return None
        except (discord.HTTPException, OSError, TimeoutError):
            await reply(FAILED)
            return None
        except Exception:
            log.exception("Couldn't read a voice message")
            await reply(FAILED)
            return None
        if not self._still(table, user_id):
            return None
        now_ms = int(self._clock() * 1000)
        duration_ms = int(memo.seconds(pcm) * 1000)
        utterance = Utterance(
            guild_id, user_id, now_ms - duration_ms, now_ms, pcm, table.segmenter.session
        )
        try:
            text = await self.host.sidebar_transcribe(table, utterance)
        except (TranscriptionProblem, TimeoutError):
            await reply(FAILED)
            return None
        except Exception:
            log.exception("Couldn't write down a voice message")
            await reply(FAILED)
            return None
        finally:
            del pcm, utterance  # the audio goes as soon as it is written down
        if not self._still(table, user_id):
            return None  # they stopped being recorded meanwhile: nothing is kept
        if not text or not text.strip():
            await reply(NO_WORDS)
            return None
        return str(text)

    # ---- said at the table ----------------------------------------------------------

    def ask_at_table(
        self,
        table: Table,
        user_id: int,
        text: str,
        *,
        in_character: bool = False,
        typed: bool = False,
        entity: str = "",
    ) -> bool:
        """A line at the table: if it clearly asks for a look-up, answer it in the background.
        "Hey DMbot, …" works from anyone who agreed to be recorded and is answered in the
        table's DMbot chat, where everyone reads it (#1187; with no such channel only the DM's
        is answered, privately, as before). "Hold on, I need to find …" is the DM's alone and
        stays private. True if a question was started, within the safety limits. `typed`: it
        was typed in the table's chat, and its lineage says so (no speech-to-text; #1118).
        `entity` (#1624): who the DM was voicing in this line, if known (a transcript line's
        `entity`): a line said as a named NPC never asks DMbot anything; "" (not known) and
        narrating do."""
        if table.campaign_id is None:
            return False
        is_dm = _is_dm(table, user_id)
        request = request_in(text, in_character=in_character or voiced_by_npc(entity))
        if request is None or (request.verb and not is_dm):
            return False  # (a player saying "I need to find my sheet" is not asking DMbot)
        # No engine: quietly nothing, and the minute is not used up. Guide me alone needs none
        # (the DM's "what's next?" is handled before the engine is used).
        if self.answerer is None and not (is_dm and guide_phrase.is_whats_next(request.question)):
            return False
        public = not request.verb and table.dmbchat_channel_id is not None
        if not public and not is_dm:
            return False  # nowhere everyone can read the answer
        return self._start(
            table, user_id, request, public=public, via=VIA_TABLE_CHAT if typed else VIA_TABLE
        )

    def ask_in_chat(self, table: Table, user_id: int, text: str) -> bool:
        """A message typed in the table's DMbot chat (#1187): every one is a question, no wake
        phrase needed. Only from someone who agreed to be recorded; anyone else is shown the
        agree button once a session, and what they typed goes nowhere."""
        if self.answerer is None or table.campaign_id is None or table.dmbchat_channel_id is None:
            return False
        if not self._may_ask(table.guild_id, user_id):
            if user_id not in table.dmbchat_nudged:
                table.dmbchat_nudged.add(user_id)
                self._background(self.host.sidebar_nudge_chat(table, user_id), "sidebar-nudge")
            return False
        question = " ".join(text.split())
        if len(question.split()) < MIN_WORDS:
            return False
        return self._start(table, user_id, Request("", question), public=True, via=VIA_CHAT)

    def _start(
        self, table: Table, user_id: int, request: Request, *, public: bool, via: str
    ) -> bool:
        """The limits, then the question in the background. A refused question is not counted
        against any limit, and the person is told (privately, or in the chat for the table)."""
        if _campaign_key(table) in self._busy:
            self._note(table, user_id, "busy", NOT_SOON_AGAIN, public)
            return False
        # Per person only for the table's questions: one player can't use up the table's
        # questions (the DM's private ones have only the table-wide limit, as before).
        now = self._clock()
        person = (
            table.person_limits.setdefault(
                user_id, AskLimiter(ASK_PER_PERSON_PER_MINUTE, ASK_PER_PERSON_PER_SESSION)
            )
            if public
            else None
        )
        # Both are asked before either counts, so a question the table refuses doesn't use up
        # the person's allowance (and the other way round).
        if not (
            table.sidebar_limiter.would_allow(now) and (person is None or person.would_allow(now))
        ):
            self._note(table, user_id, "limit", TOO_MANY, public)
            return False
        table.sidebar_limiter.allow(now)
        if person is not None:
            person.allow(now)
        self._busy.add(_campaign_key(table))
        self._background(
            self._table_question(table, user_id, request, via=via, public=public), "sidebar"
        )
        return True

    def _background(self, work: Coroutine[object, object, object], name: str) -> None:
        async def run() -> None:
            await work

        task = asyncio.create_task(run(), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        """A background job ended: forget it, and log (never raise) how it failed."""
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            log.warning("A sidebar background job failed: %s", type(error).__name__)

    def _note(self, table: Table, user_id: int, kind: str, text: str, public: bool) -> None:
        """A short note about a question that wasn't answered, at most one of its kind every
        NOTE_EVERY_S: in the chat for the table's questions, else to the DM privately."""
        if not public:
            self._note_later(user_id, kind, text)
            return
        if not self._note_ok(user_id, kind):
            return
        self._background(self.host.sidebar_post_chat(table, text), "sidebar-note")

    def _note_later(self, user_id: int, kind: str, text: str) -> None:
        """A short private note about a spoken question that wasn't answered, at most one of
        its kind every NOTE_EVERY_S: the DM is waiting and should know why."""
        if not self._note_ok(user_id, kind):
            return

        async def send() -> None:
            await self.host.sidebar_send_dm(user_id, text)

        task = asyncio.create_task(send(), name="sidebar-note")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _table_question(
        self, table: Table, user_id: int, request: Request, *, via: str, public: bool
    ) -> None:
        async def reply(text: str, view: discord.ui.View | None = None) -> bool:
            if public:
                return await self.host.sidebar_post_chat(table, text)
            if view is None:
                status = await self.host.sidebar_send_dm(user_id, text)
            else:
                status = await self.host.sidebar_send_dm(user_id, text, view)
            self._undelivered[user_id] = status
            return status == "sent"

        try:
            await self._ask(table, user_id, request.question, reply, via=via, public=public)
        except Exception:
            log.exception("A question said at the table failed")
        finally:
            self._busy.discard(_campaign_key(table))

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---- the question, the answer, the lines ------------------------------------------

    def _still(self, table: Table, user_id: int, *, dm: bool = True) -> bool:
        """The session is still the one running, the asker is still its DM (unless anyone at the
        table may ask, #1187), and still agrees to be recorded."""
        return (
            self.host.tables.get(table.guild_id) is table
            and table.campaign_id is not None
            and (_is_dm(table, user_id) or not dm)
            and self._may_ask(table.guild_id, user_id)
        )

    def _may_ask(self, guild_id: int, user_id: int) -> bool:
        """Agreed to be recorded, which includes being off the record for a while (#2466): a
        paused person's question is answered, only nothing of it is stored (`_save_lines`)."""
        return self.host.sidebar_has_consent(guild_id, user_id) or self.host.sidebar_paused(
            guild_id, user_id
        )

    async def _ask(
        self,
        table: Table,
        user_id: int,
        heard: str,
        reply: Reply,
        *,
        via: str,
        public: bool = False,
    ) -> None:
        """`via`: how the question came in (VIA_VOICE, VIA_TYPED, VIA_TABLE, VIA_TABLE_CHAT or
        VIA_CHAT). `public`: it is the table's question, answered in the DMbot chat for everyone
        (#1187). Nothing is written to the transcript unless an answer reached its reader: no
        orphan questions."""
        guild_id, campaign_id = table.guild_id, table.campaign_id
        dm_only = not public
        if campaign_id is None or not self._still(table, user_id, dm=dm_only):
            return
        question = self.host.sidebar_clean(table, heard)
        asked_ms = int(self._clock() * 1000)
        if self.host.sidebar_paused(guild_id, user_id):
            # Off the record when it was asked: it is answered but never stored, even if the
            # answer comes after they resume (#2466). Decided now, not when the lines are saved.
            if len(self._unstored) > UNSTORED_KEEP:
                self._unstored.clear()
            self._unstored.add((user_id, asked_ms))
        campaign = await self.host.sidebar_campaign(guild_id, campaign_id)
        if campaign is None or not self._still(table, user_id, dm=dm_only):
            return
        if (
            # Not VIA_CHAT (typed in the DMbot chat) or VIA_TABLE_CHAT (typed in the table's text
            # chat): those are the table's questions, read by everyone.
            via in (VIA_VOICE, VIA_TYPED, VIA_TABLE)
            and _is_dm(table, user_id)
            and guide_phrase.is_whats_next(question)
            and await self._guide_answer(table, user_id, heard, question, asked_ms, via)
        ):
            return  # (else the usual sidebar answer: Guide me is off, or can't be given)
        if (
            via in (VIA_VOICE, VIA_TABLE)  # spoken only: typed feedback is the button's (#2076)
            and _is_dm(table, user_id)
            and recap_feedback.is_recap_feedback(question)
            and await self._recap_feedback(table, user_id, heard, question, asked_ms, via)
        ):
            return  # (else the usual sidebar answer: no recap is waiting, or it can't be sorted)
        answerer = self.answerer
        if answerer is None:
            # No engine: only Guide me, above, could be answered. A typed message is told so
            # (the same note as before this change), never left in silence.
            if via == VIA_TYPED:
                await reply(NOT_YET)
            return
        now = self._monotonic()
        scene = table.recent.scene(
            lambda u: self.host.name_of(guild_id, u),
            now,
            lambda u: self.host.sidebar_has_consent(guild_id, u),
        )
        try:
            answer = await asyncio.wait_for(
                answerer.answer(campaign, question, asker_id=user_id, scene=scene, table=public),
                ANSWER_TIMEOUT_S,
            )
        except AIError as exc:  # plain words from the engine: show them, keep nothing
            await reply(self._framed(table, user_id, question, str(exc), public))
            return
        except Exception:
            log.exception("The sidebar couldn't answer")
            await reply(self._framed(table, user_id, question, FAILED, public))
            return
        if not self._still(table, user_id, dm=dm_only):
            return
        if public and _is_dm(table, user_id) and answer.one_for_dm:
            # The DM's question needs the DM's side of the game (secrets, notes): the table
            # sees only that it was sent, and the answer, with everything, goes to the DM.
            # (a clash the table answer already found is put to the DM as it is: a second call
            # could skip it and answer from one rule)
            if not await (
                self._send_clash(table, user_id, campaign, answer.clash, question, scene, answerer)
                if answer.clash is not None
                else self._answer_privately(table, user_id, campaign, question, scene, answerer)
            ):
                # The table is told only that it failed, never what was asked privately.
                await reply(self._framed(table, user_id, question, COULDNT_SEND, public))
                return
            name = await self.host.sidebar_table_name(table, user_id)
            if not self._still(table, user_id, dm=dm_only):
                return  # (consent can be withdrawn while the name is looked up)
            if not await reply(self._chat_line(name, question, SENT_PRIVATELY, answer.question)):
                return
            self._save_lines(
                table, user_id, heard, question, asked_ms, answer, SENT_PRIVATELY, via, public
            )
            return
        said = answer.text.strip()
        shown = [x.strip() for x in answer.parts if x.strip()] or [said]
        if not said:
            await reply(self._framed(table, user_id, question, FAILED, public))
            return
        name = ""
        if public:
            # The character's name and the campaign's links are read together, not one after the
            # other, and the links only when the answer could show one.
            name, (house, others) = await asyncio.gather(
                self.host.sidebar_table_name(table, user_id),
                self._links_for(table, answer, " ".join(shown)),
            )
            shown = self._for_the_table(house, others, answer, shown)
            said = shown[0]
            if not self._still(table, user_id, dm=dm_only):
                return  # consent can be withdrawn while those are read: nothing is posted
        elif via == VIA_VOICE:
            echo = question if len(question) <= ECHO_CHARS else question[:ECHO_CHARS].rstrip() + "…"
            shown[0] = f"🎙️ “{discord.utils.escape_markdown(echo)}”\n{shown[0]}"
        self._undelivered.pop(user_id, None)
        pieces = [piece for text in shown for piece in _parts(text)]
        if public:
            # One message in the chat (no reply chain). One over Discord's limit goes in parts and
            # is never cut: the answer's length is a target in its instructions (#2597).
            pieces = _parts(self._chat_line(name, question, " ".join(shown), answer.question))
        clash = answer.clash
        under: discord.ui.View | None
        if clash is not None and not public:
            under = self._clash_view(
                campaign,
                clash,
                lambda: self._ask(table, user_id, heard, reply, via=via, public=False),
            )
            said = CLASH_SAVED  # the transcript keeps that it was asked, not the rules' words
        else:
            under = None if public else self._looked_view(table, user_id, campaign, answer)
        for n, part in enumerate(pieces):
            if public and not self._still(table, user_id, dm=dm_only):
                return  # (right before every post)
            last = n == len(pieces) - 1
            if not await (reply(part, under) if last and under is not None else reply(part)):
                # Only the DM's private path, and only when Discord says their messages are
                # closed: the DM screen is the one place left to say so.
                if (
                    not public
                    and via in (VIA_TABLE, VIA_TABLE_CHAT)
                    and (self._undelivered.pop(user_id, "") == "forbidden")
                ):
                    await self._cant_dm(table, user_id)
                return
        if answer.refused or not self._still(table, user_id, dm=dm_only):
            return
        if public and clash is not None and self._note_ok(user_id, "clash"):
            # A player's table question met two rules that disagree (#2284): the table got "one
            # for your DM"; the DM is told on the DM screen which two, and nothing else.
            await self.host.sidebar_tell_screen(
                table, CLASH_NOTE.format(a=clash[0].label, b=clash[1].label)
            )
        self._save_lines(table, user_id, heard, question, asked_ms, answer, said, via, public)

    def _clash_view(
        self,
        campaign: Campaign,
        clash: tuple[rule_clash.Side, rule_clash.Side],
        retry: Callable[[], Awaitable[None]],
    ) -> discord.ui.View:
        """The two buttons under the DM's "which one applies?" (#2284)."""
        return self.host.sidebar_clash_view(campaign, clash, retry)

    def _looked_view(
        self, table: Table, user_id: int, campaign: Campaign, answer: Answer
    ) -> discord.ui.View | None:
        """The DM's "📖 What I looked at" under a rules answer, with the list the answer was built
        from kept for it (#1957). Never for a table question (the caller passes none), and not
        for a refusal or an off-topic reply."""
        if answer.refused or not answer.in_game:
            return None
        kept = looked.Kept(
            user_id, table.guild_id, campaign.id, tuple(getattr(answer, "looked_at", ()))
        )
        return self.host.sidebar_looked_view(self.looked.remember(kept))

    async def _guide_answer(
        self,
        table: Table,
        user_id: int,
        heard: str,
        question: str,
        asked_ms: int,
        via: str,
    ) -> bool:
        """ "Hey DMbot, what's next?" from the DM (#1739): Guide me's answer, privately, the same
        text as the 🧭 button. True when it was handled. False (nothing sent) when Guide me is not
        on for this campaign, so the question goes the usual way.

        The answer is the DM's story, DM-screen content (CLAUDE.md), and the sidebar's replies
        normally go into the raw transcript, which anyone in the server may read. This one never
        does: the question is logged as usual, and the reply as "(answered privately: Guide me)"
        and nothing more."""
        try:
            text = await asyncio.wait_for(self.host.sidebar_guide(table, user_id), ANSWER_TIMEOUT_S)
        except Exception:  # (a timeout too: the usual answer follows, the campaign is not held up)
            log.exception("The sidebar couldn't give Guide me's answer")
            return False
        if text is None:
            return False
        if not self._still(table, user_id, dm=True):
            return True  # (they stopped being recorded meanwhile: nothing is sent or kept)
        for part in _parts(text):
            if not self._still(table, user_id, dm=True):
                return True  # (consent can go while parts are sent: nothing more is sent)
            # Always the DM's own chat with DMbot, even when the DM said it at a table that has a
            # DMbot chat: nobody else may read the DM's story.
            status = await self.host.sidebar_send_dm(user_id, part)
            if status != "sent":
                if status == "forbidden":
                    await self._cant_dm(table, user_id)
                else:
                    self._note_later(user_id, "guide", FAILED)  # (never the text)
                return True
        if self._still(table, user_id, dm=True):
            self._save_lines(
                table, user_id, heard, question, asked_ms, _Private(), PRIVATE_NOTE, via, False
            )
        return True

    async def _recap_feedback(
        self,
        table: Table,
        user_id: int,
        heard: str,
        question: str,
        asked_ms: int,
        via: str,
    ) -> bool:
        """The DM's spoken feedback on the recap waiting for them (#2076): sorted and answered in
        their private messages with the same Save / Try again buttons as the typed form. True when
        it was handled; False (nothing sent) when no recap is waiting, so it goes the usual way.

        What is said is the DM's own opinion of a draft nobody else has seen, so, like Guide me, it
        is logged as the question and "(answered privately)" and nothing more."""
        try:
            handled = await self.host.sidebar_recap_feedback(table, user_id, question)
        except Exception:
            log.exception("The sidebar couldn't handle spoken recap feedback")
            return False
        if not handled:
            return False
        if self._still(table, user_id, dm=True):
            self._save_lines(
                table, user_id, heard, question, asked_ms, _Private(), PRIVATE_NOTE, via, False
            )
        return True

    def _save_lines(
        self,
        table: Table,
        user_id: int,
        heard: str,
        question: str,
        asked_ms: int,
        answer: Answer,
        said: str,
        via: str,
        public: bool,
    ) -> None:
        """The question and DMbot's in-game answer, in the raw transcript only. A player's
        question is a Table Q line, the DM's a DM Sidebar line (#1187)."""
        if (user_id, asked_ms) in self._unstored or self.host.sidebar_paused(
            table.guild_id, user_id
        ):
            self._unstored.discard((user_id, asked_ms))
            return  # off the record: the question is answered, but nothing of it is stored (#2466)
        ref = uuid.uuid4().hex[:6]  # the reply says which question it answers
        stt = self.host.sidebar_stt() if via not in (VIA_TYPED, VIA_TABLE_CHAT, VIA_CHAT) else ""
        kind = SIDEBAR_TABLE_QUESTION if not _is_dm(table, user_id) else SIDEBAR_QUESTION
        self.host.sidebar_save(
            table,
            Line(
                asked_ms,
                user_id,
                heard,
                question,
                sidebar=kind,
                lineage=Lineage(ref=ref, via=via, stt=stt),
            ),
        )
        if answer.in_game or (public and not _is_dm(table, user_id)):
            self.host.sidebar_save(
                table,
                Line(
                    int(self._clock() * 1000),
                    user_id,
                    said,
                    said,
                    sidebar=SIDEBAR_ANSWER,
                    lineage=Lineage(
                        reply_to=ref,
                        model=answer.model,
                        prompt=answer.prompt_version,
                        sources=tuple(answer.sources),
                    ),
                ),
            )

    def _chat_line(self, name: str, question: str, answer: str, restated: str = "") -> str:
        """One message for the DMbot chat, in the owner's form (#1187):

            Q (Mira): Does fireball need line of sight?
            Answer: No. You pick a point within 150 feet. (PHB 2024, sure)

        The name is the linked character's, else the nickname. What people said is escaped, so
        it can't format the message or ping anyone."""
        # The AI's few-word restatement when it gave one, else what was heard, shortened.
        short = " ".join(table_links.strip_links(restated or question).split())
        name = table_links.strip_links(name) or "Someone"
        if len(short) > Q_CHARS:
            short = short[:Q_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        elif short and short[-1] not in "?.!…":
            short += "?"  # (the wake phrase's parsing drops it; the chat shows a question)
        short = short[:1].upper() + short[1:]
        return discord.utils.escape_mentions(
            f"Q ({_plain(name)}): {_plain(short)}\nAnswer: {answer}"
        )

    def _framed(self, table: Table, user_id: int, question: str, text: str, public: bool) -> str:
        """A short problem line: plain for the DM's private chat, in the chat's form (with the
        question it belongs to) for the table. Synchronous: the name is the nickname."""
        if not public:
            return text
        name = table_links.strip_links(self.host.name_of(table.guild_id, user_id)) or "Someone"
        short = " ".join(table_links.strip_links(question).split())[:Q_CHARS]
        return f"Q ({_plain(name)}): {_plain(short)}\nAnswer: {text}"

    async def _links_for(
        self, table: Table, answer: Answer, first: str
    ) -> tuple[str | None, tuple[str, ...]]:
        """This campaign's shareable links, read only if the answer could show one: it has a
        link in it, or it comes from a house rule."""
        if table_links.has_link(first) or _from_a_house_rule(answer):  # (`first`: all parts)
            return await self.host.sidebar_links(table)
        return None, ()

    @staticmethod
    def _for_the_table(
        house: str | None, others: tuple[str, ...], answer: Answer, shown: list[str]
    ) -> list[str]:
        """The answer as the whole table may read it: at most one link, and only an allowed
        one (dndbeyond.com, or a public link this campaign shared). The house-rules link
        comes first when the answer comes from a house rule."""
        house = house if house and table_links.is_plain(house) else None
        allowed = (*([house] if house else []), *others)
        kept = table_links.keep_allowed_in(shown, allowed)  # one link in all the parts
        if house and not any(table_links.has_link(x) for x in kept) and _from_a_house_rule(answer):
            kept[0] = f"{kept[0]} {house}"
        return kept

    async def _answer_privately(
        self,
        table: Table,
        user_id: int,
        campaign: Campaign,
        question: str,
        scene: str,
        answerer: Answerer,
    ) -> bool:
        """The DM's question that needs their side of the game: answered with everything, in
        their own chat with DMbot. False if it couldn't be sent (they are told on the DM
        screen, and the table is told nothing)."""
        try:
            answer = await asyncio.wait_for(
                answerer.answer(campaign, question, asker_id=user_id, scene=scene, table=False),
                ANSWER_TIMEOUT_S,
            )
        except AIError as exc:
            await self.host.sidebar_send_dm(user_id, str(exc))
            return False
        except Exception:
            log.exception("The sidebar couldn't answer privately")
            await self.host.sidebar_send_dm(user_id, FAILED)
            return False
        if not self._still(table, user_id, dm=True):
            return False
        status = "failed"
        clash = answer.clash
        view = (
            self._clash_view(
                campaign,
                clash,
                lambda: self._retry_privately(table, user_id, campaign, question, scene, answerer),
            )
            if clash is not None
            else None
        )
        parts = [p for text in (answer.parts or (answer.text,)) for p in _parts(text)]
        for n, part in enumerate(parts):
            last = n == len(parts) - 1
            status = await self.host.sidebar_send_dm(
                user_id, part, view if last and view is not None else None
            )
            if status != "sent":
                if status == "forbidden":
                    await self._cant_dm(table, user_id)
                return False
        return status == "sent"

    async def _send_clash(
        self,
        table: Table,
        user_id: int,
        campaign: Campaign,
        clash: tuple[rule_clash.Side, rule_clash.Side],
        question: str,
        scene: str,
        answerer: Answerer,
    ) -> bool:
        """ "Which one applies?" to the DM alone, with a button for each rule (#2284)."""
        view = self._clash_view(
            campaign,
            clash,
            lambda: self._retry_privately(table, user_id, campaign, question, scene, answerer),
        )
        status = await self.host.sidebar_send_dm(user_id, rule_clash.question_text(*clash), view)
        if status == "forbidden":
            await self._cant_dm(table, user_id)
        return status == "sent"

    async def _retry_privately(
        self,
        table: Table,
        user_id: int,
        campaign: Campaign,
        question: str,
        scene: str,
        answerer: Answerer,
    ) -> None:
        """After the DM chose which rule applies: the same private question again."""
        if not await self._answer_privately(table, user_id, campaign, question, scene, answerer):
            raise RuntimeError("the private answer after a rule choice could not be sent")

    async def _cant_dm(self, table: Table, user_id: int) -> None:
        """The answer couldn't be sent. DMs are closed, so the DM screen is the only place
        to say so: a line that never holds the question or the answer."""
        log.info("Couldn't send a sidebar answer to the DM")
        if self._note_ok(user_id, "cantdm"):
            await self.host.sidebar_tell_screen(table, CANT_DM)


@dataclass(frozen=True, slots=True)
class _Private:
    """The sidebar "answer" saved for a private Guide me reply: only that it was given."""

    text: str = PRIVATE_NOTE
    in_game: bool = True
    refused: bool = False
    model: str = ""
    prompt_version: str = ""
    sources: Sequence[str] = ()
    parts: Sequence[str] = (PRIVATE_NOTE,)
    one_for_dm: bool = False
    question: str = ""
    clash: tuple[rule_clash.Side, rule_clash.Side] | None = None


def _campaign_key(table: Table) -> tuple[int, str]:
    return (table.guild_id, table.campaign_id or "")


def _plain(text: str) -> str:
    """What a person said or is called, shown as plain text: no formatting, no pings."""
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))


def _from_a_house_rule(answer: Answer) -> bool:
    return any(str(source).lower().startswith("house rule") for source in answer.sources)


def _is_dm(table: Table, user_id: int) -> bool:
    """One of the campaign's DMs (not just whoever pressed Start)."""
    return user_id in table.dm_user_ids


def _is_question(text: str) -> bool:
    return len(text.split()) >= MIN_WORDS or "?" in text


def _parts(text: str) -> list[str]:
    """The text in pieces Discord will take, cut at line ends or spaces."""
    out: list[str] = []
    while len(text) > MESSAGE_LIMIT:
        cut = text.rfind("\n", 0, MESSAGE_LIMIT)
        if cut < MESSAGE_LIMIT // 2:
            cut = text.rfind(" ", 0, MESSAGE_LIMIT)
        if cut < MESSAGE_LIMIT // 2:
            cut = MESSAGE_LIMIT
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return [*out, text] if text else out


class _PickView(discord.ui.View):
    """Which of the DM's running games a message is for, when there are several."""

    def __init__(
        self,
        service: SidebarService,
        tables: list[Table],
        message: discord.Message,
        reply: Reply,
        after: Callable[[Table], Awaitable[None]] | None = None,
        no_session: str = NO_SESSION,
    ) -> None:
        super().__init__(timeout=300)
        self._service = service
        self._message = message
        self._reply = reply
        self._no_session = no_session
        # What happens for the game picked: a question by default (a house rule, #960)
        self._after = after or (lambda table: service._from_message(table, message, reply))
        self._by_id = {t.campaign_id: t for t in tables if t.campaign_id}
        names = [t.campaign_name for t in self._by_id.values()]
        for table in list(self._by_id.values())[:5]:
            label = table.campaign_name or "This game"
            if names.count(table.campaign_name) > 1:  # two games, one name: say where
                label = f"{label} ({service.host.sidebar_server_name(table.guild_id)})"
            self.add_item(_PickButton(table, label[:80]))


class _PickButton(discord.ui.Button["_PickView"]):
    def __init__(self, table: Table, label: str) -> None:
        super().__init__(label=label)
        self._campaign_id = table.campaign_id or ""
        self._name = table.campaign_name

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert view is not None
        if interaction.user.id != view._message.author.id:
            await interaction.response.send_message(NOT_YOURS, ephemeral=True)
            return
        table = view._by_id.get(self._campaign_id)
        await interaction.response.edit_message(
            content=f"For {self._name or 'this game'}.", view=None
        )
        view.stop()
        live = table is not None and view._service.host.tables.get(table.guild_id) is table
        if table is None or not live or not _is_dm(table, interaction.user.id):
            await view._reply(view._no_session)
            return
        await view._after(table)
