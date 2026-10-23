"""Typed chat at the table (#1118): which messages DMbot reads (only the table's chat, only
while listening, only from people who agreed, never a bot or a command), that a typed
"we take a long rest" does what the spoken one does, that a typed line is tagged (typed) in
the as-heard transcript and absent from the cleaned one, the Settings panel, and the consent
version bump."""

from __future__ import annotations

import asyncio
import datetime as dt
import itertools
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot import bot as bot_module
from dmbot import table_chat
from dmbot.audio.segmenter import Segmenter
from dmbot.bot import Table
from dmbot.consent import TERMS_VERSION
from dmbot.dm_screen import table_chat_panel as panel
from dmbot.sidebar.service import Recent
from dmbot.transcript import export
from dmbot.transcript.models import Line, TranscriptSession
from tests.test_timebot import (
    DM,
    GUILD_A,
    GUILD_B,
    NOON4,
    OTHER_DM,
    PLAYER,
    AtTheTable,
)

VOICE, EXTRA, OTHER = 700, 701, 702
_IDS = itertools.count(5000)


class Rules(unittest.TestCase):
    def test_only_the_voice_channel_and_the_one_extra_channel(self) -> None:
        self.assertTrue(table_chat.is_table_chat(VOICE, VOICE, None))
        self.assertTrue(table_chat.is_table_chat(EXTRA, VOICE, EXTRA))
        self.assertFalse(table_chat.is_table_chat(OTHER, VOICE, EXTRA))
        self.assertFalse(table_chat.is_table_chat(OTHER, VOICE, None))

    def test_an_empty_or_enormous_message_is_nothing_to_read(self) -> None:
        self.assertIsNone(table_chat.clean(""))
        self.assertIsNone(table_chat.clean("   \n "))
        self.assertIsNone(table_chat.clean("x" * (table_chat.MAX_CHARS + 1)))
        self.assertEqual(table_chat.clean("we  take\na long rest"), "we take a long rest")

    def test_consent_terms_went_up(self) -> None:
        self.assertGreaterEqual(TERMS_VERSION, 5)


def message(
    text: str,
    *,
    author: int = DM,
    channel: int = VOICE,
    bot: bool = False,
    webhook: int | None = None,
    kind: discord.MessageType = discord.MessageType.default,
    guild: int | None = GUILD_A,
    interaction: object | None = None,
) -> Any:
    return SimpleNamespace(
        id=next(_IDS),
        content=text,
        clean_content=text,  # Discord's version with mentions turned into names
        author=SimpleNamespace(id=author, bot=bot),
        channel=SimpleNamespace(id=channel),
        guild=None if guild is None else SimpleNamespace(id=guild),
        webhook_id=webhook,
        type=kind,
        interaction_metadata=interaction,
        created_at=dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.UTC),
    )


class AtTheTableTyped(AtTheTable):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # A real table, so the typed path touches what a live one has.
        self.live = Table(
            guild_id=GUILD_A,
            voice_channel_id=VOICE,
            screen_channel_id=555,
            dm_user_id=DM,
            segmenter=Segmenter(GUILD_A),
            campaign_id=self.a.id,
            campaign_name="Alpha",
            dm_user_ids=frozenset({DM, OTHER_DM}),
            listening=True,
        )
        self.live.chat_channel_id = EXTRA
        self.table = cast(Any, self.live)  # the base tests' table is this one
        self.bot.tables[GUILD_A] = self.live
        self.bot.transcripts = cast(Any, object())  # lines wait in the buffer to be saved

    async def hear(self, msg: Any) -> None:
        self.bot._hear_typed(msg)
        await asyncio.gather(*list(self.bot._asking))

    async def minute(self) -> int:
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        return stored.clock.minute

    async def test_a_typed_dm_line_reaches_combat(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.hear(message("roll initiative"))
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual(stored.clock.combat_round, 1)
        await self.hear(message("roll initiative", author=PLAYER))  # a player's words: never
        self.live.clock_said.clear()
        await self.hear(message("next round", author=PLAYER))
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual(stored.clock.combat_round, 1)

    async def test_a_flood_from_one_person_skips_the_listeners_but_stays_in_the_transcript(
        self,
    ) -> None:
        asked: list[str] = []
        cast(Any, self.bot.sidebar).ask_at_table = lambda t, u, text, typed=False: asked.append(
            text
        )
        for i in range(table_chat.TYPED_PER_MINUTE + 5):
            await self.hear(message(f"line {i}", author=PLAYER))
        self.assertEqual(len(asked), table_chat.TYPED_PER_MINUTE)  # the rest never reached them
        self.assertEqual(len(self.live.unsaved), table_chat.TYPED_PER_MINUTE + 5)  # all kept raw

    async def test_lines_over_the_limit_stay_out_of_recent_and_the_rest_are_typed(self) -> None:
        added: list[tuple[str, bool]] = []
        original = Recent.add

        def spy(this: Recent, user_id: int, text: str, now: float, *, typed: bool = False) -> None:
            added.append((text, typed))
            original(this, user_id, text, now, typed=typed)

        with patch.object(Recent, "add", spy):
            for i in range(table_chat.TYPED_PER_MINUTE + 5):
                await self.hear(message(f"line {i}", author=PLAYER))
        self.assertEqual(len(added), table_chat.TYPED_PER_MINUTE)  # the rest never got in
        self.assertTrue(all(typed for _, typed in added))  # and FightBot only reads spoken lines
        self.assertNotIn(f"line {table_chat.TYPED_PER_MINUTE}", [text for text, _ in added])
        self.assertEqual(len(self.live.unsaved), table_chat.TYPED_PER_MINUTE + 5)  # all kept raw

    async def test_one_persons_flood_does_not_silence_another(self) -> None:
        await self.press("set", set_to=NOON4)
        for i in range(table_chat.TYPED_PER_MINUTE + 5):
            await self.hear(message(f"chatter {i}", author=PLAYER))
        await self.hear(message("we take a long rest"))  # the DM, still heard
        self.assertEqual(await self.minute(), NOON4 + 480)

    async def test_a_flood_is_one_log_line_with_no_text(self) -> None:
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            for i in range(table_chat.TYPED_PER_MINUTE + 30):
                await self.hear(message(f"secret text {i}", author=PLAYER))
        floods = [r for r in logs.records if "flood" in r.getMessage()]
        self.assertEqual(len(floods), 1)
        self.assertNotIn("secret text", floods[0].getMessage())

    async def test_the_cap_holds_real_waiting_checks_and_gives_places_back(self) -> None:
        release = asyncio.Event()
        started = 0

        async def slow(table: Any, user_id: int, line: str, *, held: bool = False) -> None:
            nonlocal started
            started += 1
            await release.wait()

        cast(Any, self.bot)._note_clock_phrase = slow
        for i in range(table_chat.CLOCK_WAITING_MAX + 3):
            self.bot._hear_typed(message(f"we wait {i}", author=PLAYER + i))
        await asyncio.sleep(0)
        self.assertEqual(started, table_chat.CLOCK_WAITING_MAX)
        self.assertEqual(self.live.clock_waiting, table_chat.CLOCK_WAITING_MAX)
        release.set()
        await asyncio.gather(*list(self.bot._asking))
        self.assertEqual(self.live.clock_waiting, 0)

    async def test_speech_is_never_skipped_by_the_typed_cap(self) -> None:
        await self.press("set", set_to=NOON4)
        self.live.clock_waiting = table_chat.CLOCK_WAITING_MAX
        self.bot._act_on_line(self.live, DM, "we take a long rest")  # spoken
        await asyncio.gather(*list(self.bot._asking))
        self.assertEqual(await self.minute(), NOON4 + 480)

    async def test_clock_phrase_checks_waiting_at_once_are_capped(self) -> None:
        self.live.clock_waiting = table_chat.CLOCK_WAITING_MAX
        await self.press("set", set_to=NOON4)
        await self.hear(message("we take a long rest"))
        self.assertEqual(await self.minute(), NOON4)  # skipped while the cap is full
        self.live.clock_waiting = 0
        await self.hear(message("we take a long rest"))
        self.assertEqual(await self.minute(), NOON4 + 480)
        self.assertEqual(self.live.clock_waiting, 0)  # each finished check gives its place back

    async def test_a_typed_rest_from_the_dm_moves_the_clock_like_speech(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.hear(message("we take a long rest"))
        self.assertEqual(await self.minute(), NOON4 + 480)
        self.assertTrue(any("Long rest" in text for text in self.posted))

    async def test_the_extra_channel_is_read_too(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.hear(message("we take a short rest", channel=EXTRA))
        self.assertEqual(await self.minute(), NOON4 + 60)

    async def test_nothing_else_is_ever_read(self) -> None:
        await self.press("set", set_to=NOON4)
        for msg in (
            message("we take a long rest", channel=OTHER),  # another channel
            message("we take a long rest", author=PLAYER + 99, bot=True),  # a bot
            message("we take a long rest", webhook=5),  # a webhook
            message("we take a long rest", kind=discord.MessageType.chat_input_command),
            message("we take a long rest", guild=GUILD_B),  # a server with no session
            message("   "),  # nothing typed (a file only)
            message("we take a long rest", interaction=object()),  # an app command's message
        ):
            await self.hear(msg)
        self.assertEqual(await self.minute(), NOON4)
        self.assertEqual(len(self.live.unsaved), 0)

    async def test_nobody_who_did_not_agree_is_read(self) -> None:
        await self.press("set", set_to=NOON4)
        self.bot.consent.has_consent = lambda guild_id, user_id: user_id != DM  # type: ignore[method-assign]
        await self.hear(message("we take a long rest"))
        self.assertEqual(await self.minute(), NOON4)
        self.assertEqual(len(self.live.unsaved), 0)

    async def test_not_while_not_listening_and_not_when_the_dm_turned_it_off(self) -> None:
        await self.press("set", set_to=NOON4)
        self.live.listening = False
        await self.hear(message("we take a long rest"))
        self.live.listening = True
        self.live.chat_on = False
        await self.hear(message("we take a long rest"))
        self.assertEqual(await self.minute(), NOON4)
        self.assertEqual(len(self.live.unsaved), 0)

    async def test_a_player_typing_a_suggestion_only_asks_the_dm(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.hear(message("let's take a short rest", author=PLAYER))
        self.assertEqual(await self.minute(), NOON4)
        self.assertTrue(any("suggests a short rest" in text for text in self.posted))

    async def test_a_typed_line_is_saved_as_typed_with_what_was_typed(self) -> None:
        await self.hear(message("The door is locked", author=PLAYER))
        [line] = self.live.unsaved.take(lambda _user: True)
        self.assertTrue(line.typed)
        self.assertEqual(
            (line.user_id, line.heard, line.text), (PLAYER,) + ("The door is locked",) * 2
        )
        self.assertEqual(line.started_ms, 1_791_892_800_000)

    async def test_stopping_the_recording_of_someone_forgets_what_they_typed(self) -> None:
        await self.hear(message("The door is locked", author=PLAYER))
        self.assertEqual(len(self.live.unsaved), 1)
        self.bot.stop_recording(GUILD_A, PLAYER)
        self.assertEqual(len(self.live.unsaved), 0)
        self.bot.consent.has_consent = lambda guild_id, user_id: user_id != PLAYER  # type: ignore[method-assign]
        await self.hear(message("And again", author=PLAYER))
        self.assertEqual(len(self.live.unsaved), 0)

    async def test_more_message_shapes(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.hear(message("we take a short rest", kind=discord.MessageType.reply))
        self.assertEqual(await self.minute(), NOON4 + 60)  # a reply is read
        self.bot._connection.user = cast(Any, SimpleNamespace(id=4242))
        before = await self.minute()
        for msg in (
            message("we take a long rest", author=4242),  # DMbot's own words
            message("we take a long rest", channel=VOICE + 5000),  # a thread of it, say
            message("x" * (table_chat.MAX_CHARS + 1)),
        ):
            await self.hear(msg)
        self.live.campaign_id = None  # no campaign yet: nothing to attach it to
        await self.hear(message("we take a long rest"))
        self.assertEqual(await self.minute(), before)
        edge = "y" * table_chat.MAX_CHARS
        self.live.campaign_id = self.a.id
        await self.hear(message(edge, author=PLAYER))
        self.assertEqual(len(self.live.unsaved), 2)  # the reply read at the start, and this

    async def test_on_message_routes_server_messages_here_and_private_ones_to_the_sidebar(
        self,
    ) -> None:
        heard = MagicMock()
        self.bot._hear_typed = heard  # type: ignore[method-assign]
        self.bot.sidebar = SimpleNamespace(on_dm_message=AsyncMock())  # type: ignore[assignment]
        await self.bot.on_message(message("hello"))
        heard.assert_called_once()
        await self.bot.on_message(message("hello", guild=None))
        self.bot.sidebar.on_dm_message.assert_awaited_once()  # type: ignore[attr-defined]
        heard.side_effect = RuntimeError("boom")
        await self.bot.on_message(message("hello"))  # swallowed and logged

    def test_the_intents_widen_only_as_far_as_typed_chat_needs(self) -> None:
        intents = self.bot.intents
        self.assertTrue(intents.guild_messages and intents.message_content)
        self.assertFalse(intents.members or intents.presences or intents.guild_reactions)
        self.assertIsNone(self.bot._connection._messages)  # no message cache

    async def test_this_campaigns_screen_and_transcript_channels_are_never_read(self) -> None:
        await self.press("set", set_to=NOON4)
        for blocked in (self.live.screen_channel_id, 9001):
            self.live.transcript_channel_id = 9001
            self.live.chat_channel_id = blocked
            await self.hear(message("we take a long rest", channel=blocked))
        self.assertEqual(await self.minute(), NOON4)

    async def test_another_campaigns_screen_cannot_be_the_chat_channel(self) -> None:
        # Campaign B in the same server has a DM screen; A's DM points Table chat at it.
        other = await self.campaigns.create(GUILD_A, "Beta", DM)
        await self.campaigns.set_dm_screen(GUILD_A, other.id, EXTRA + 40)
        self.live.chat_channel_id = EXTRA + 40
        self.bot._chat_blocked.clear()
        self.assertFalse(await self.bot._chat_channel_allowed(message("hi", channel=EXTRA + 40)))
        self.assertTrue(await self.bot._chat_channel_allowed(message("hi", channel=VOICE)))
        self.live.chat_channel_id = EXTRA
        self.assertTrue(await self.bot._chat_channel_allowed(message("hi", channel=EXTRA)))

    async def test_a_typed_hey_dmbot_says_it_was_typed(self) -> None:
        calls: list[dict[str, Any]] = []
        self.bot.sidebar = SimpleNamespace(  # type: ignore[assignment]
            ask_at_table=lambda *a, **kw: calls.append(kw)
        )
        await self.hear(message("Hey DMbot, what is flanking?"))
        self.assertEqual(calls, [{"typed": True}])

    async def test_mentions_are_read_as_names(self) -> None:
        msg = message("<@123> take a short rest <#700>")
        msg.clean_content = "@Mira take a short rest #table"
        await self.hear(msg)
        [line] = self.live.unsaved.take(lambda _u: True)
        self.assertEqual(line.heard, "@Mira take a short rest #table")

    async def test_a_deleted_message_is_taken_back_while_unsaved(self) -> None:
        keep, gone = message("stays", author=PLAYER), message("goes", author=PLAYER)
        keep.id, gone.id = 11, 12
        await self.hear(keep)
        await self.hear(gone)
        await self.bot.on_raw_message_delete(
            SimpleNamespace(guild_id=GUILD_A, message_id=12, channel_id=VOICE)  # type: ignore[arg-type]
        )
        lines = self.live.unsaved.take(lambda _u: True)
        self.assertEqual([x.heard for x in lines], ["stays"])
        await self.bot.on_raw_message_delete(
            SimpleNamespace(guild_id=None, message_id=1, channel_id=1)  # type: ignore[arg-type]
        )  # a private message: nothing to do

    async def test_the_chat_channel_hears_about_it_once(self) -> None:
        posts: list[tuple[int, str]] = []

        async def post(
            channel_id: int,
            text: str,
            view: Any = None,
            *,
            remember: Any = None,
            secret_of: Any = None,
        ) -> bool:
            posts.append((channel_id, text))
            return True

        self.bot.post = post  # type: ignore[method-assign]
        await self.bot._tell_chat_channel(self.live)
        await self.bot._tell_chat_channel(self.live)
        self.assertEqual([c for c, _ in posts], [EXTRA])
        self.assertIn("Only people who agreed are read", posts[0][1])
        self.live.chat_notice_posted, self.live.chat_on = False, False
        await self.bot._tell_chat_channel(self.live)
        self.assertEqual(len(posts), 1)  # off: nothing to say

    async def test_a_chat_channel_set_mid_session_is_told_once(self) -> None:
        posts: list[tuple[int, str]] = []

        async def post(
            channel_id: int,
            text: str,
            view: Any = None,
            *,
            remember: Any = None,
            secret_of: Any = None,
        ) -> bool:
            posts.append((channel_id, text))
            return True

        self.bot.post = post  # type: ignore[method-assign]
        self.live.chat_channel_id, self.live.chat_notice_posted = None, False
        saved = await self.campaigns.set_table_chat(GUILD_A, self.a.id, on=True, channel_id=EXTRA)
        self.bot.after_chat_change(saved)
        await asyncio.gather(*list(self.bot._asking))
        self.assertEqual([c for c, _ in posts], [EXTRA])
        self.bot.after_chat_change(saved)  # nothing changed: nothing said again
        await asyncio.gather(*list(self.bot._asking))
        self.assertEqual(len(posts), 1)
        other = await self.campaigns.set_table_chat(GUILD_A, self.a.id, on=True, channel_id=OTHER)
        self.bot.after_chat_change(other)  # a different channel hasn't been told
        await asyncio.gather(*list(self.bot._asking))
        self.assertEqual([c for c, _ in posts], [EXTRA, OTHER])
        off = await self.campaigns.set_table_chat(GUILD_A, self.a.id, on=False, channel_id=OTHER)
        self.bot.after_chat_change(off)  # turning it off says nothing
        self.live.listening = False
        back = await self.campaigns.set_table_chat(GUILD_A, self.a.id, on=True, channel_id=OTHER)
        self.bot.after_chat_change(back)  # not listening: told when listening starts instead
        await asyncio.gather(*list(self.bot._asking))
        self.assertEqual(len(posts), 2)

    async def test_typed_chat_goes_through_the_off_topic_filter_but_its_words_are_not_held(
        self,
    ) -> None:
        """#2447, item 6: a typed line not plainly about the game waits for a label like talk; the
        label is saved on the line, and nothing is held back from the names scan or the words."""
        self.bot.topic_ai = cast(Any, object())
        reply = SimpleNamespace(input_tokens=0, output_tokens=0)
        with patch.object(bot_module, "classify", AsyncMock(return_value=(["off_topic"], reply))):
            await self.hear(message("my cat knocked a mug off the shelf yesterday", author=PLAYER))
            self.assertEqual(self.live.held, {})  # (its words already reached the scan, once)
            await asyncio.gather(*list(self.live.topic_timers), *list(self.live.topic_tasks))
        (saved,) = [x for x in self.live.unsaved.lines() if x.typed]
        self.assertEqual((saved.topic, saved.user_id), ("off_topic", PLAYER))
        cleaned = export.render(
            "Alpha", self.session(), [saved], {PLAYER: "Mia"}, version=export.CLEANED
        )
        self.assertNotIn("my cat", cleaned)

    async def test_putting_back_typed_chat_does_not_count_its_words_twice(self) -> None:
        """#2447: a typed line's words reached the names scan and the word list when it was
        typed; "Put it back" gives it its place in the cleaned transcript and nothing more."""
        self.bot.topic_ai = cast(Any, object())
        reply = SimpleNamespace(input_tokens=0, output_tokens=0)
        text = "my cat knocked a mug off the shelf yesterday"
        with patch.object(bot_module, "classify", AsyncMock(return_value=(["off_topic"], reply))):
            await self.hear(message(text, author=PLAYER))
            await asyncio.gather(*list(self.live.topic_timers), *list(self.live.topic_tasks))
        (run,) = self.live.left_out.runs
        heard_before = len(self.live.heard)
        self.live.dm_user_ids = frozenset({DM, OTHER_DM})
        await self.bot.put_back(GUILD_A, run.id, DM)
        self.assertEqual(len(self.live.heard), heard_before)  # not fed to the names scan again
        (saved,) = [x for x in self.live.unsaved.lines() if x.typed]
        self.assertEqual(saved.topic, "game")  # but it is in the cleaned transcript again

    async def test_a_typed_and_a_spoken_line_in_one_millisecond_are_told_apart(self) -> None:
        from dmbot.transcript.models import Line

        spoken = Line(5_000, PLAYER, "hello there", "hello there", 1000)
        typed = Line(5_000, PLAYER, "my cat", "my cat", typed=True)
        self.live.unsaved.add(spoken)
        self.live.unsaved.add(typed)
        self.assertTrue(self.live.unsaved.set_topic(PLAYER, 5_000, "off_topic", typed=True))
        by = {x.typed: x.topic for x in self.live.unsaved.lines()}
        self.assertEqual(by, {False: "game", True: "off_topic"})

    async def test_typed_chat_plainly_about_the_game_is_never_held_for_a_label(self) -> None:
        self.bot.topic_ai = cast(Any, object())
        await self.hear(message("I roll a d20 for initiative", author=PLAYER))
        self.assertEqual((list(self.live.topics.lines), len(self.live.topic_timers)), ([], 0))

    def session(self) -> Any:
        session = MagicMock(spec=TranscriptSession)
        session.started_at = 1_000
        session.ended_at = None
        session.number = 1
        session.engines = []
        return session

    async def test_a_purge_takes_back_the_unsaved_lines_it_deletes(self) -> None:
        msgs = [message(f"line {i}", author=PLAYER) for i in range(4)]
        for m in msgs:
            await self.hear(m)
        await self.bot.on_raw_bulk_message_delete(
            cast(
                Any,
                SimpleNamespace(
                    guild_id=GUILD_A, message_ids={msgs[0].id, msgs[2].id}, channel_id=VOICE
                ),
            )
        )
        left = self.live.unsaved.take(lambda _u: True)
        self.assertEqual([x.heard for x in left], ["line 1", "line 3"])

    async def test_consent_taken_back_while_the_clock_waits_stops_the_move(self) -> None:
        await self.press("set", set_to=NOON4)
        original = self.bot._less_what_a_rest_covers

        async def revoke_then_answer(table: Any, campaign: Any, said: Any, now: float) -> Any:
            self.bot.consent.has_consent = lambda guild_id, user_id: False  # type: ignore[method-assign]
            return await original(table, campaign, said, now)

        self.bot._less_what_a_rest_covers = revoke_then_answer  # type: ignore[method-assign]
        await self.hear(message("we take a long rest"))
        self.assertEqual(await self.minute(), NOON4)

    async def test_the_dm_screen_changes_reach_a_running_session(self) -> None:
        saved = await self.campaigns.set_table_chat(GUILD_A, self.a.id, on=False, channel_id=OTHER)
        self.bot.after_chat_change(saved)
        self.assertEqual((self.live.chat_on, self.live.chat_channel_id), (False, OTHER))


class Transcript(unittest.TestCase):
    def test_typed_lines_are_tagged_in_both_transcripts(self) -> None:
        session = MagicMock(spec=TranscriptSession)
        session.started_at = 1_000
        session.ended_at = None
        session.number = 1
        session.engines = []
        lines = [
            Line(1_000_000 + 5_000, 1, "we take a long rest", "we take a long rest", 0, typed=True),
            Line(1_000_000 + 9_000, 2, "Hello there", "Hello there", 2000),
        ]
        names = {1: "Dee", 2: "Mia"}
        raw = export.render("Alpha", session, lines, names, version=export.AS_HEARD)
        cleaned = export.render("Alpha", session, lines, names, version=export.CLEANED)
        self.assertIn("(Dee) (typed): we take a long rest", raw)
        self.assertIn("Hello there", raw)
        self.assertIn("(Dee) (typed): we take a long rest", cleaned)  # #2447, item 6
        self.assertIn("Hello there", cleaned)


class Panel(unittest.IsolatedAsyncioTestCase):
    def interaction(self, store: Any, user: int = DM) -> Any:
        guild = MagicMock()
        guild.id = GUILD_A
        guild.get_channel = MagicMock(return_value=MagicMock(spec=discord.TextChannel, id=EXTRA))
        it = MagicMock()
        it.guild, it.guild_id = guild, GUILD_A
        it.user = SimpleNamespace(id=user, guild_permissions=SimpleNamespace(manage_guild=False))
        it.client = SimpleNamespace(campaigns=store, after_chat_change=MagicMock())
        it.response.edit_message = AsyncMock()
        it.response.send_message = AsyncMock()
        return it

    def campaign(self, **kw: Any) -> Any:
        base = dict(
            id="c" * 32,
            guild_id=GUILD_A,
            dm_user_ids=frozenset({DM}),
            table_chat=True,
            table_chat_channel_id=None,
            dm_screen_channel_id=None,
            transcript_channel_id=None,
            gametime_channel_id=None,
        )
        return SimpleNamespace(**{**base, **kw})

    def test_the_panel_says_exactly_which_channels_are_read(self) -> None:
        self.assertIn("voice channel's chat. No other channel", panel.panel_text(self.campaign()))
        self.assertIn(f"<#{EXTRA}>", panel.panel_text(self.campaign(table_chat_channel_id=EXTRA)))
        self.assertIn("off", panel.panel_text(self.campaign(table_chat=False)).lower())
        self.assertIn("Off", panel.line(self.campaign(table_chat=False)))

    async def test_a_dm_turns_it_off_and_a_player_cannot(self) -> None:
        saved = self.campaign(table_chat=False)
        store = SimpleNamespace(
            get=AsyncMock(return_value=self.campaign()),
            set_table_chat=AsyncMock(return_value=saved),
        )
        view = panel.TableChatPanel(self.campaign())
        it = self.interaction(store)
        await view.save(it, turn_on=False)
        store.set_table_chat.assert_awaited_once()
        self.assertEqual(store.set_table_chat.await_args.kwargs["on"], False)
        it.client.after_chat_change.assert_called_once_with(saved)
        stranger = self.interaction(store, user=PLAYER)
        store.set_table_chat.reset_mock()
        await view.save(stranger, turn_on=False)
        store.set_table_chat.assert_not_awaited()
        self.assertIn("Only this campaign's DM", stranger.response.send_message.await_args.args[0])

    async def test_another_campaigns_channel_cannot_be_picked(self) -> None:
        campaign = self.campaign()
        store = SimpleNamespace(
            get=AsyncMock(return_value=campaign),
            special_channel_ids=AsyncMock(return_value=frozenset({EXTRA})),
            set_table_chat=AsyncMock(),
        )
        it = self.interaction(store)
        await panel.TableChatPanel(campaign).save(it, channel_id=EXTRA)
        self.assertIn("won't read the DM screen", it.response.send_message.await_args.args[0])
        store.set_table_chat.assert_not_awaited()

    async def test_a_store_that_fails_is_told_not_crashed(self) -> None:
        store = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("down")))
        it = self.interaction(store)
        await panel.TableChatPanel(self.campaign()).save(it, turn_on=False)
        self.assertEqual(it.response.send_message.await_args.args[0], panel.FAILED)

    async def test_the_extra_channel_is_saved_and_can_be_cleared(self) -> None:
        store = SimpleNamespace(
            get=AsyncMock(return_value=self.campaign(table_chat_channel_id=EXTRA)),
            special_channel_ids=AsyncMock(return_value=frozenset()),
            set_table_chat=AsyncMock(return_value=self.campaign()),
        )
        view = panel.TableChatPanel(self.campaign(table_chat_channel_id=EXTRA))
        await view.save(self.interaction(store), channel_id=EXTRA)
        self.assertEqual(store.set_table_chat.await_args.kwargs["channel_id"], EXTRA)
        await view.save(self.interaction(store), clear_channel=True)
        self.assertIsNone(store.set_table_chat.await_args.kwargs["channel_id"])


class TypedRateRules(unittest.TestCase):
    def test_twenty_a_minute_then_none_until_the_minute_is_up(self) -> None:
        rate = table_chat.TypedRate()
        self.assertTrue(all(rate.allow(1, 100.0 + i * 0.1) for i in range(20)))
        self.assertFalse(rate.allow(1, 103.0))
        self.assertTrue(rate.allow(1, 160.5))  # the first lines have aged out

    def test_each_person_has_their_own_count(self) -> None:
        rate = table_chat.TypedRate()
        for _ in range(20):
            rate.allow(1, 10.0)
        self.assertFalse(rate.allow(1, 10.0))
        self.assertTrue(rate.allow(2, 10.0))

    def test_the_log_is_once_a_minute(self) -> None:
        rate = table_chat.TypedRate()
        self.assertTrue(rate.should_log(5.0))
        self.assertFalse(rate.should_log(30.0))
        self.assertTrue(rate.should_log(66.0))
