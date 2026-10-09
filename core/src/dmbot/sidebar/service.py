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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import discord

from dmbot.ai import AIError
from dmbot.audio.segmenter import Utterance
from dmbot.sidebar import memo
from dmbot.sidebar.ask import Request, request_in
from dmbot.transcript.models import (
    SIDEBAR_ANSWER,
    SIDEBAR_QUESTION,
    VIA_TABLE,
    VIA_TYPED,
    VIA_VOICE,
    Line,
    Lineage,
)
from dmbot.transcription.base import TranscriptionProblem

if TYPE_CHECKING:
    from dmbot.bot import Table
    from dmbot.campaigns import Campaign

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
MAX_TYPED_CHARS = 500
ANSWER_TIMEOUT_S = 25.0
SCENE_SECONDS = 180.0
SCENE_CHARS = 1_200
RECENT_LINES = 40
MIN_WORDS = 2  # "ok" or "thanks" is not a question
READ_TIMEOUT_S = 30.0
CHAT_PER_MINUTE = 6  # questions typed or spoken in the DM chat, per DM
NOTE_KEEP = 1_000
DECODE_AT_ONCE = 2
MESSAGE_LIMIT = 2_000  # Discord's
NOTE_EVERY_S = 30.0  # the same kind of note to the same person, at most this often

NO_SESSION = "No game is running with you as DM. Start one with /dmbot start, then ask again."
NOT_YET = "Quick answers aren't switched on yet."
NOT_SOON_AGAIN = "Still on your last question. One moment."
ONE_A_MINUTE = "One spoken question a minute. Message me here to ask now."
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
WHICH = "Which game is this for?"
SLOW_DOWN = "That's a lot of questions. Try again in a minute."
TYPE_INSTEAD = "I can only read typed questions and voice messages."
NOT_YOURS = "This isn't your question."
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


class Answerer(Protocol):
    async def answer(
        self, campaign: Campaign, question: str, *, asker_id: int, scene: str
    ) -> Answer: ...


class Host(Protocol):
    """What the bot gives the sidebar (dmbot.bot.DMBot)."""

    @property
    def tables(self) -> Mapping[int, Table]: ...
    def sidebar_has_consent(self, guild_id: int, user_id: int) -> bool: ...
    def name_of(self, guild_id: int, user_id: int) -> str: ...
    async def sidebar_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...
    def sidebar_server_name(self, guild_id: int) -> str: ...
    def consent_request(self, table: Table) -> tuple[str, discord.ui.View]: ...
    async def sidebar_transcribe(self, table: Table, utterance: Utterance) -> str | None: ...
    def sidebar_clean(self, table: Table, text: str) -> str: ...
    async def sidebar_send_dm(self, user_id: int, text: str) -> str: ...  # sent, forbidden, failed
    def sidebar_save(self, table: Table, line: Line) -> None: ...
    def sidebar_stt(self) -> str: ...  # the speech-to-text in use: "engine model host"
    async def sidebar_tell_screen(self, table: Table, text: str) -> None: ...


Reply = Callable[[str], Awaitable[bool]]


@dataclass(slots=True)
class Recent:
    """What was said lately, to give the answer engine the scene. In memory, bounded."""

    lines: deque[tuple[float, int, str]]

    @classmethod
    def new(cls) -> Recent:
        return cls(deque(maxlen=RECENT_LINES))

    def add(self, user_id: int, text: str, now: float) -> None:
        if text.strip():
            self.lines.append((now, user_id, text))

    def drop_speaker(self, user_id: int) -> None:
        """Someone stopped being recorded: nothing they said is used again."""
        kept = [item for item in self.lines if item[1] != user_id]
        self.lines.clear()
        self.lines.extend(kept)

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
        self._asked_at: dict[int, deque[float]] = {}  # per DM: when lately (chat path)
        self._tasks: set[asyncio.Task[None]] = set()
        self._decoding = asyncio.Semaphore(DECODE_AT_ONCE)
        self._known_dms: set[int] = set()  # seen as a campaign's DM since DMbot started
        self._undelivered: dict[int, str] = {}  # why the last private message failed

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
        if typed and not _is_question(typed):
            return  # "ok", "thanks", "👍": not a question, no AI call, nothing written down
        tables = self.tables_of(user_id)

        async def reply(text: str) -> bool:
            try:
                await message.channel.send(text, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                return False
            return True

        if not tables:
            # Only someone known to be a campaign's DM is told; anyone else gets nothing.
            if user_id in self._known_dms and self._note_ok(user_id, "nosession"):
                await reply(NO_SESSION)
            return
        if self.answerer is None:  # before any download or speech-to-text is paid for
            if self._note_ok(user_id, "notyet"):
                await reply(NOT_YET)
            return
        if len(tables) == 1:
            await self._from_message(tables[0], message, reply)
            return
        await message.channel.send(
            WHICH, view=_PickView(self, tables, message, reply), allowed_mentions=NO_PINGS
        )

    def tables_of(self, user_id: int) -> list[Table]:
        """The running sessions this person is a DM of."""
        mine = [
            t for t in self.host.tables.values() if t.campaign_id is not None and _is_dm(t, user_id)
        ]
        if mine:
            self._known_dms.add(user_id)
        return mine

    def _chat_ok(self, user_id: int) -> bool:
        """A few questions a minute in the DM chat: each one can spend speech-to-text and
        AI money (the spoken trigger has its own one-a-minute limit)."""
        now = self._clock()
        recent = self._asked_at.setdefault(user_id, deque(maxlen=CHAT_PER_MINUTE))
        if len(recent) == CHAT_PER_MINUTE and now - recent[0] < 60.0:
            return False
        recent.append(now)
        return True

    def _note_ok(self, user_id: int, kind: str) -> bool:
        now = self._clock()
        if len(self._noted) > NOTE_KEEP:  # strangers can message DMbot: keep this small
            self._noted = {k: at for k, at in self._noted.items() if now - at < NOTE_EVERY_S}
            self._asked_at = {u: d for u, d in self._asked_at.items() if now - d[-1] < 60.0}
        if now - self._noted.get((user_id, kind), -NOTE_EVERY_S) < NOTE_EVERY_S:
            return False
        self._noted[(user_id, kind)] = now
        return True

    async def _from_message(self, table: Table, message: discord.Message, reply: Reply) -> None:
        user_id = message.author.id
        guild_id = table.guild_id
        if not self.host.sidebar_has_consent(guild_id, user_id):
            if not self._note_ok(user_id, "agree"):
                return
            text, view = self.host.consent_request(table)
            await reply(NOT_AGREED)
            try:
                await message.channel.send(text, view=view, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                log.info("Couldn't send the consent question to user %s", user_id)
            return
        if _campaign_key(table) in self._busy:
            if self._note_ok(user_id, "busy"):
                await reply(NOT_SOON_AGAIN)
            return
        if not self._chat_ok(user_id):
            if self._note_ok(user_id, "slow"):
                await reply(SLOW_DOWN)
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
        self, table: Table, user_id: int, text: str, *, in_character: bool = False
    ) -> bool:
        """The DM's line at the table: if it clearly asks for a look-up, answer it
        privately, in the background. Only the campaign's own DM's lines count, at most one
        a minute. True if a question was started."""
        if self.answerer is None or table.campaign_id is None or not _is_dm(table, user_id):
            return False  # no engine: quietly nothing, and the minute is not used up
        request = request_in(text, in_character=in_character)
        if request is None:
            return False
        if _campaign_key(table) in self._busy:
            self._note_later(user_id, "busy", NOT_SOON_AGAIN)
            return False
        if not table.sidebar_limiter.allow(self._clock()):  # last: it uses up the minute
            self._note_later(user_id, "limit", ONE_A_MINUTE)
            return False
        self._busy.add(_campaign_key(table))
        task = asyncio.create_task(self._table_question(table, user_id, request), name="sidebar")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

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

    async def _table_question(self, table: Table, user_id: int, request: Request) -> None:
        async def reply(text: str) -> bool:
            status = await self.host.sidebar_send_dm(user_id, text)
            self._undelivered[user_id] = status
            return status == "sent"

        try:
            await self._ask(table, user_id, request.question, reply, via=VIA_TABLE)
        except Exception:
            log.exception("A question said at the table failed")
        finally:
            self._busy.discard(_campaign_key(table))

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---- the question, the answer, the lines ------------------------------------------

    def _still(self, table: Table, user_id: int) -> bool:
        """The session is still the one running, the asker is still its DM, and still agrees to
        be recorded."""
        return (
            self.host.tables.get(table.guild_id) is table
            and table.campaign_id is not None
            and _is_dm(table, user_id)
            and self.host.sidebar_has_consent(table.guild_id, user_id)
        )

    async def _ask(self, table: Table, user_id: int, heard: str, reply: Reply, *, via: str) -> None:
        """`via`: how the question came in (VIA_VOICE, VIA_TYPED or VIA_TABLE). Nothing is
        written to the transcript unless an answer reached the DM: no orphan questions."""
        guild_id, campaign_id = table.guild_id, table.campaign_id
        if campaign_id is None or not self._still(table, user_id):
            return
        answerer = self.answerer
        if answerer is None:
            return
        question = self.host.sidebar_clean(table, heard)
        asked_ms = int(self._clock() * 1000)
        campaign = await self.host.sidebar_campaign(guild_id, campaign_id)
        if campaign is None or not self._still(table, user_id):
            return
        now = self._monotonic()
        scene = table.recent.scene(
            lambda u: self.host.name_of(guild_id, u),
            now,
            lambda u: self.host.sidebar_has_consent(guild_id, u),
        )
        try:
            answer = await asyncio.wait_for(
                answerer.answer(campaign, question, asker_id=user_id, scene=scene),
                ANSWER_TIMEOUT_S,
            )
        except AIError as exc:  # plain words from the engine: show them, keep nothing
            await reply(str(exc))
            return
        except Exception:
            log.exception("The sidebar couldn't answer")
            await reply(FAILED)
            return
        if not self._still(table, user_id):
            return
        said = answer.text.strip()
        shown = [x.strip() for x in answer.parts if x.strip()] or [said]
        if not said:
            await reply(FAILED)
            return
        if via == VIA_VOICE:
            echo = question if len(question) <= ECHO_CHARS else question[:ECHO_CHARS].rstrip() + "…"
            shown[0] = f"🎙️ “{discord.utils.escape_markdown(echo)}”\n{shown[0]}"
        self._undelivered.pop(user_id, None)
        for part in (piece for text in shown for piece in _parts(text)):
            if not await reply(part):
                # Only the table path, and only when Discord says their messages are closed:
                # the DM screen is the one place left to say so.
                if via == VIA_TABLE and self._undelivered.pop(user_id, "") == "forbidden":
                    await self._cant_dm(table, user_id)
                return
        if answer.refused or not self._still(table, user_id):
            return
        ref = uuid.uuid4().hex[:6]  # the reply says which question it answers
        stt = self.host.sidebar_stt() if via != VIA_TYPED else ""
        self.host.sidebar_save(
            table,
            Line(
                asked_ms,
                user_id,
                heard,
                question,
                sidebar=SIDEBAR_QUESTION,
                lineage=Lineage(ref=ref, via=via, stt=stt),
            ),
        )
        if answer.in_game:
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

    async def _cant_dm(self, table: Table, user_id: int) -> None:
        """The answer couldn't be sent. DMs are closed, so the DM screen is the only place
        to say so: a line that never holds the question or the answer."""
        log.info("Couldn't send a sidebar answer to the DM")
        if self._note_ok(user_id, "cantdm"):
            await self.host.sidebar_tell_screen(table, CANT_DM)


def _campaign_key(table: Table) -> tuple[int, str]:
    return (table.guild_id, table.campaign_id or "")


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
    ) -> None:
        super().__init__(timeout=300)
        self._service = service
        self._message = message
        self._reply = reply
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
            await view._reply(NO_SESSION)
            return
        await view._service._from_message(table, view._message, view._reply)
