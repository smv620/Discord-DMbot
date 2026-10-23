"""Pictures posted in the table chat (#2450, part 2): only people who agreed are ever read, the
AI's description is a record and never an order, the day's limit, and the plan check. The AI and
the fetch are stand-ins; invented names only."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from dmbot import images, table_chat
from dmbot.ai import Image
from dmbot.consent import IMAGE_TERMS_VERSION
from dmbot.image_desk import DAY_S, ImageDesk, ImageUseStore
from dmbot.transcript.models import Line
from tests import test_table_chat as chat
from tests.test_table_chat import EXTRA, VOICE, message
from tests.test_timebot import DM, GUILD_A, GUILD_B, NOON4, PLAYER, Clocks

PNG = Image("image/png", b"\x89PNG\r\n\x1a\nxx")
BOT_ID = 9001
LINK = "https://cdn.discordapp.com/attachments/1/2/map.png"


class Vision:
    """Answers with the same description, and counts what it was sent."""

    def __init__(self, alt: str = "A torn letter with a wax seal.", kind: str = "picture") -> None:
        self.alt, self.kind, self.calls = alt, kind, 0
        self.sent: list[str] = []

    async def complete(self, system: str, text: str, **kw: Any) -> Any:
        self.calls += 1
        self.sent.append(system + text)
        body = {"kind": self.kind, "alt": self.alt, "text": "The key is under the stone."}
        return SimpleNamespace(text=json.dumps(body), refused=False, cut=False)


def picture(text: str = "", **kw: Any) -> Any:
    msg = message(text, **kw)
    msg.attachments = [SimpleNamespace(url=LINK, content_type="image/png", size=1000)]
    return msg


class TableImages(chat.AtTheTableTyped):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.fetched: list[str] = []

        async def fetcher(link: str) -> Image:
            self.fetched.append(link)
            return PNG

        self.vision = Vision()
        self.fetcher = fetcher
        # Pictures are read only for a yes given under wording that mentions them (a later change
        # brings that wording): these tests act as if it were live.
        patcher = patch("dmbot.consent.TERMS_VERSION", IMAGE_TERMS_VERSION)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bot.image_desk = self.make_desk()

    def make_desk(self, per_day: int = 40, vision: Vision | None = None) -> ImageDesk:
        ai = vision or self.vision
        return ImageDesk(
            ai, ai, per_day=per_day, counter=ImageUseStore(self.db), fetcher=self.fetcher
        )

    def lines(self) -> list[Any]:
        return self.live.unsaved.take(lambda _user: True)

    async def test_a_picture_from_someone_who_agreed_becomes_an_image_line(self) -> None:
        await self.hear(picture())
        [line] = self.lines()
        self.assertTrue(line.image and line.typed)
        self.assertEqual(line.user_id, DM)
        self.assertIn("A torn letter with a wax seal.", line.text)
        self.assertEqual(self.fetched, [LINK])

    async def test_past_the_limit_nothing_is_downloaded(self) -> None:
        self.bot.image_desk = self.make_desk(per_day=0)
        await self.hear(picture())
        self.assertEqual((self.fetched, self.vision.calls), ([], 0))
        self.assertEqual([line.text for line in self.lines()], [images.NOT_DESCRIBED])

    async def test_a_picture_with_words_keeps_both_in_the_same_message(self) -> None:
        await self.hear(picture("look at this"))
        texts = sorted(line.text for line in self.lines())
        self.assertEqual(len(texts), 2)
        self.assertIn("look at this", texts)

    async def test_nothing_is_fetched_or_sent_for_someone_who_did_not_agree(self) -> None:
        self.bot.consent.has_consent = lambda guild_id, user_id: user_id != PLAYER  # type: ignore[method-assign]
        await self.hear(picture(author=PLAYER))
        self.assertEqual((self.fetched, self.vision.calls, self.lines()), ([], 0, []))

    async def test_a_yes_under_wording_that_does_not_mention_pictures_reads_none(self) -> None:
        with patch("dmbot.consent.TERMS_VERSION", IMAGE_TERMS_VERSION - 1):
            await self.hear(picture("hello"))
        self.assertEqual((self.fetched, self.vision.calls), ([], 0))
        [line] = self.lines()  # the words they typed are read as before; only the picture is not
        self.assertEqual(line.text, "hello")

    async def test_a_stop_after_the_first_picture_is_read_stops_the_rest(self) -> None:
        msg = picture()
        msg.attachments = msg.attachments * 3
        agreed = {"yes": True}
        vision = self.vision

        async def read_then_stop(system: str, text: str, **kw: Any) -> Any:
            got = await Vision.complete(vision, system, text, **kw)
            agreed["yes"] = False  # they pressed Stop while the first picture was being read
            return got

        vision.complete = read_then_stop  # type: ignore[method-assign]
        self.bot.consent.has_image_consent = lambda guild_id, user_id: agreed["yes"]  # type: ignore[method-assign]
        await self.hear(msg)
        self.assertEqual(self.fetched, [LINK])  # the other two were never even downloaded
        self.assertEqual(vision.calls, 1)
        self.assertEqual(self.lines(), [])  # and a stopped message leaves no line at all

    async def test_a_stop_before_the_download_reads_nothing(self) -> None:
        self.bot.consent.has_image_consent = lambda guild_id, user_id: False  # type: ignore[method-assign]
        await self.hear(picture())
        self.assertEqual((self.fetched, self.vision.calls, self.lines()), ([], 0, []))

    async def test_a_bot_and_a_webhook_are_never_read(self) -> None:
        await self.hear(picture(author=PLAYER, bot=True))
        await self.hear(picture(webhook=99))
        self.assertEqual((self.fetched, self.vision.calls, self.lines()), ([], 0, []))

    async def test_a_picture_outside_the_table_chat_is_never_read(self) -> None:
        await self.hear(picture(channel=9999))
        self.assertEqual((self.fetched, self.lines()), ([], []))

    async def test_someone_who_stops_before_the_description_comes_back_gets_no_line(self) -> None:
        said = {"yes": True}
        self.bot.consent.has_consent = lambda guild_id, user_id: said["yes"]  # type: ignore[method-assign]
        vision = self.vision

        async def stopping(system: str, text: str, **kw: Any) -> Any:
            said["yes"] = False  # they pressed Stop while the AI was reading
            return await Vision.complete(vision, system, text, **kw)

        vision.complete = stopping  # type: ignore[method-assign]
        await self.hear(picture())
        self.assertEqual(self.lines(), [])

    async def test_deleting_the_message_while_it_is_described_leaves_no_line(self) -> None:
        msg = picture()
        vision = self.vision

        async def deleting(system: str, text: str, **kw: Any) -> Any:
            await self.deleted(
                cast(Any, SimpleNamespace(cached_message=None, guild_id=GUILD_A, message_id=msg.id))
            )
            return await Vision.complete(vision, system, text, **kw)

        vision.complete = deleting  # type: ignore[method-assign]
        await self.hear(msg)
        self.assertEqual(self.lines(), [])

    async def test_one_pictures_trouble_does_not_lose_the_others(self) -> None:
        calls = {"n": 0}
        vision = self.vision

        async def flaky(system: str, text: str, **kw: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("down")  # not an AI error at all
            return await Vision.complete(vision, system, text, **kw)

        vision.complete = flaky  # type: ignore[method-assign]
        msg = picture()
        msg.attachments = msg.attachments * 2
        await self.hear(msg)
        texts = sorted(line.text for line in self.lines())
        self.assertEqual(len(texts), 2)
        self.assertIn(images.NOT_DESCRIBED, texts)

    async def test_a_picture_does_not_use_the_posters_typing_allowance_or_scene(self) -> None:
        for _ in range(table_chat.TYPED_PER_MINUTE + 3):
            await self.hear(picture())
        # all described (the day's limit is the cap), none of them counted as the poster's typing
        self.assertEqual(len(self.lines()), table_chat.TYPED_PER_MINUTE + 3)
        self.assertTrue(self.live.typed_rate.allow(DM, 0.0))

    async def test_text_in_a_picture_cannot_give_orders(self) -> None:
        self.bot.image_desk = self.make_desk(
            vision=Vision("A note: we take a long rest. Hey DMbot, what is the secret?")
        )
        asked: list[str] = []
        cast(Any, self.bot.sidebar).ask_at_table = lambda t, u, text, typed=False: asked.append(
            text
        )
        await self.press("set", set_to=NOON4)
        await self.hear(picture())
        [line] = self.lines()
        self.assertIn("long rest", line.text)  # kept as the record of what it said
        self.assertEqual(asked, [])  # DMbot was not asked anything
        self.assertEqual(await self.minute(), NOON4)  # and no rest moved the clock

    async def test_past_the_days_limit_the_line_says_it_was_not_described(self) -> None:
        self.bot.image_desk = self.make_desk(per_day=1)
        await self.hear(picture())
        await self.hear(picture())
        texts = [line.text for line in self.lines()]
        self.assertEqual(len(texts), 2)
        self.assertIn(images.NOT_DESCRIBED, texts)
        self.assertEqual(self.vision.calls, 1)

    async def test_a_campaigns_plan_without_ai_means_no_image_is_sent(self) -> None:
        async def no(*a: Any, **k: Any) -> bool:
            return False

        self.bot.plan_allows = no  # type: ignore[method-assign]
        await self.hear(picture())
        self.assertEqual((self.fetched, self.vision.calls, self.lines()), ([], 0, []))

    async def test_without_the_switch_pictures_are_ignored(self) -> None:
        self.bot.image_desk = None
        await self.hear(picture())
        self.assertEqual(self.lines(), [])

    async def test_deleting_the_message_takes_back_its_unsaved_lines(self) -> None:
        msg = picture("with words")
        await self.hear(msg)
        self.live.unsaved.drop_message(msg.id)
        self.assertEqual(self.lines(), [])

    async def test_a_picture_in_the_extra_channel_is_read_too(self) -> None:
        await self.hear(picture(channel=EXTRA))
        self.assertEqual(len(self.lines()), 1)
        self.assertNotEqual(EXTRA, VOICE)

    async def test_only_the_first_few_pictures_of_a_message_are_read(self) -> None:
        msg = picture()
        msg.attachments = msg.attachments * (images.MAX_PER_MESSAGE + 3)
        await self.hear(msg)
        self.assertEqual(len(self.lines()), images.MAX_PER_MESSAGE)

    async def test_a_huge_or_non_image_attachment_is_not_taken(self) -> None:
        msg = message("")
        msg.attachments = [
            SimpleNamespace(url=LINK, content_type="image/png", size=images.MAX_BYTES + 1),
            SimpleNamespace(url=LINK, content_type="application/pdf", size=10),
            SimpleNamespace(url=LINK, content_type=None, size=10),
        ]
        await self.hear(msg)
        self.assertEqual((self.fetched, self.lines()), ([], []))

    # ---- a picture of writing is answered in the chat (#2450, part 4) ----

    async def settle(self) -> None:
        """Let the background work the bot started finish (the reply look-ups are tracked)."""
        await asyncio.gather(*list(self.bot._asking))

    async def deleted(self, payload: Any) -> None:
        await self.bot.on_raw_message_delete(payload)
        await self.settle()

    async def purged(self, payload: Any) -> None:
        await self.bot.on_raw_bulk_message_delete(payload)
        await self.settle()

    def say_into(self) -> AsyncMock:
        """The chat's replies are caught here instead of sent."""
        self.live.transcript_session_id = "a" * 32
        said = AsyncMock(return_value=object())
        self.bot._post_reply = said  # type: ignore[method-assign]
        return said

    def reads(self, kind: str) -> None:
        self.bot.image_desk = self.make_desk(vision=Vision(kind=kind))

    async def test_a_picture_of_text_is_answered_in_the_chat_it_was_posted_in(self) -> None:
        said = self.say_into()
        self.reads("text")
        msg = picture(channel=EXTRA)
        await self.hear(msg)
        said.assert_awaited_once()
        assert said.await_args is not None
        channel, to_message, text, view = said.await_args.args
        self.assertEqual((channel, to_message), (EXTRA, msg.id))  # a reply to the picture
        self.assertEqual(text, "Text in this image:\n> The key is under the stone.")
        [button] = view.children
        [line] = self.lines()
        # the key of the line it fixes: the session, the poster and the moment
        self.assertEqual(button.custom_id, f"dmbot:imgfix:{'a' * 32}:{DM}:{line.started_ms}")

    async def test_deleting_the_picture_takes_the_reply_back(self) -> None:
        said = self.say_into()
        reply = SimpleNamespace(delete=AsyncMock())
        said.return_value = reply
        self.reads("text")
        msg = picture(channel=EXTRA)
        await self.hear(msg)
        payload = SimpleNamespace(
            cached_message=None, message_id=msg.id, guild_id=GUILD_A, channel_id=EXTRA
        )
        await self.deleted(cast(Any, payload))
        reply.delete.assert_awaited_once()
        await self.deleted(cast(Any, payload))  # (a second delete event finds nothing)
        reply.delete.assert_awaited_once()

    async def test_a_purge_takes_the_replies_back_too(self) -> None:
        said = self.say_into()
        reply = SimpleNamespace(delete=AsyncMock())
        said.return_value = reply
        self.reads("text")
        first, second = picture(channel=EXTRA), picture(channel=EXTRA)
        await self.hear(first)
        await self.hear(second)
        await self.purged(
            cast(
                Any,
                SimpleNamespace(
                    cached_messages=[],
                    message_ids={first.id, second.id},
                    guild_id=GUILD_A,
                    channel_id=EXTRA,
                ),
            )
        )
        self.assertEqual(reply.delete.await_count, 2)

    async def test_a_picture_deleted_while_the_reply_is_sent_loses_the_reply(self) -> None:
        said = self.say_into()
        reply = SimpleNamespace(delete=AsyncMock())

        async def post_then_delete(*args: Any, **kw: Any) -> Any:
            self.bot._note_deleted(77)  # the picture was deleted meanwhile
            return reply

        said.side_effect = post_then_delete
        await self.bot._say_image_text(
            self.live, DM, 5, EXTRA, images.Read("text", "x", "words"), message_id=77
        )
        reply.delete.assert_awaited_once()

    async def test_a_reply_that_cannot_be_deleted_is_let_go(self) -> None:
        import discord

        said = self.say_into()
        reply = SimpleNamespace(
            delete=AsyncMock(
                side_effect=discord.HTTPException(
                    cast(Any, SimpleNamespace(status=404, reason="gone")), "gone"
                )
            )
        )
        said.return_value = reply
        self.reads("text")
        msg = picture(channel=EXTRA)
        await self.hear(msg)
        await self.deleted(
            cast(
                Any,
                SimpleNamespace(
                    cached_message=None, message_id=msg.id, guild_id=GUILD_A, channel_id=EXTRA
                ),
            )
        )
        reply.delete.assert_awaited_once()  # (and nothing raised)

    async def test_a_picture_that_is_not_writing_gets_no_reply(self) -> None:
        said = self.say_into()
        for kind in ("picture", "map"):
            self.reads(kind)
            await self.hear(picture())
        said.assert_not_awaited()

    async def test_a_picture_that_was_not_described_gets_no_reply(self) -> None:
        said = self.say_into()
        self.bot.image_desk = self.make_desk(per_day=0)
        await self.hear(picture())
        said.assert_not_awaited()

    async def test_nobody_who_stopped_meanwhile_is_answered(self) -> None:
        said = self.say_into()
        self.bot.consent.has_consent = lambda guild_id, user_id: False  # type: ignore[method-assign]
        await self.bot._say_image_text(self.live, DM, 5, VOICE, images.Read("text", "x", "words"))
        said.assert_not_awaited()

    async def test_the_reply_is_not_sent_for_someone_who_did_not_agree(self) -> None:
        said = self.say_into()
        self.reads("text")
        self.bot.consent.has_consent = lambda guild_id, user_id: user_id != PLAYER  # type: ignore[method-assign]
        await self.hear(picture(author=PLAYER))
        said.assert_not_awaited()

    # ---- a deleted picture takes its reply back even after a restart (#2522) ----

    def chat_of(self, found: list[Any], *, voice: bool = True, channel_id: int = VOICE) -> Any:
        """A channel whose messages after the picture are `found`; the reads are counted."""
        import discord

        scans: list[dict[str, Any]] = []

        def history(this: Any, **kw: Any) -> Any:
            scans.append(kw)

            async def walk() -> Any:
                for item in found:
                    yield item

            return walk()

        base: type = discord.VoiceChannel if voice else discord.abc.Messageable
        chat: Any = object.__new__(type("Chat", (base,), {"history": history}))
        chat.id = channel_id
        chat.scans = scans
        return chat

    def in_channel(self, chat: Any) -> None:
        """DMbot sees this channel, and has a user of its own (the test bot never logged in)."""
        cast(Any, self.bot).get_channel = lambda _id: chat
        patcher = patch.object(type(self.bot), "user", SimpleNamespace(id=BOT_ID))
        patcher.start()
        self.addCleanup(patcher.stop)

    def mine(self, reference: int | None, *, author: int | None = None) -> Any:
        ref = None if reference is None else SimpleNamespace(message_id=reference)
        me = BOT_ID if author is None else author
        return SimpleNamespace(author=SimpleNamespace(id=me), reference=ref, delete=AsyncMock())

    async def delete_after_restart(self, chat: Any, picture_id: int = 500) -> None:
        self.bot._image_replies.clear()  # (a restart: nothing is remembered)
        self.in_channel(chat)
        await self.deleted(
            cast(
                Any,
                SimpleNamespace(
                    cached_message=None, message_id=picture_id, guild_id=GUILD_A, channel_id=chat.id
                ),
            )
        )

    async def test_after_a_restart_a_delete_still_removes_the_reply_that_references_it(
        self,
    ) -> None:
        ours = self.mine(500)
        theirs = self.mine(500, author=999)  # a person's reply to the same picture
        other_bot = self.mine(500, author=998)
        elsewhere = self.mine(501)  # ours, but to another message
        plain = self.mine(None)  # ours, not a reply
        chat = self.chat_of([theirs, other_bot, elsewhere, plain, ours])
        await self.delete_after_restart(chat)
        ours.delete.assert_awaited_once()
        for untouched in (theirs, other_bot, elsewhere, plain):
            untouched.delete.assert_not_awaited()  # only DMbot's own reply to it is touched
        self.assertEqual(chat.scans[0]["limit"], 50)  # a short stretch after the picture

    async def test_past_the_cap_a_delete_still_removes_the_reply(self) -> None:
        from dmbot import bot as bot_module

        for n in range(bot_module.IMAGE_REPLIES_KEPT + 1):
            self.bot._image_replies[1000 + n] = [self.mine(1000 + n)]
        # (the newest are kept, so the very first picture's reply is no longer remembered)
        self.bot._image_replies.pop(1000, None)
        ours = self.mine(1000)
        chat = self.chat_of([ours])
        self.in_channel(chat)
        await self.deleted(
            cast(
                Any,
                SimpleNamespace(
                    cached_message=None, message_id=1000, guild_id=GUILD_A, channel_id=chat.id
                ),
            )
        )
        ours.delete.assert_awaited_once()

    async def test_a_purge_reads_the_chat_once_for_all_of_them(self) -> None:
        a, b, c = self.mine(600), self.mine(601), self.mine(777)
        chat = self.chat_of([a, c, b])
        self.bot._image_replies.clear()
        self.in_channel(chat)
        await self.purged(
            cast(
                Any,
                SimpleNamespace(
                    cached_messages=[], message_ids={600, 601}, guild_id=GUILD_A, channel_id=chat.id
                ),
            )
        )
        self.assertEqual(len(chat.scans), 1)
        a.delete.assert_awaited_once()
        b.delete.assert_awaited_once()
        c.delete.assert_not_awaited()

    async def test_a_line_is_dropped_even_when_looking_for_the_reply_fails(self) -> None:
        """The deleted message's unsaved line goes first, whatever the look-up does (#1118)."""
        self.live.unsaved.add(Line(1, DM, "words", "words", typed=True, message_id=555))
        chat = self.chat_of([], voice=False, channel_id=EXTRA)
        self.in_channel(chat)
        self.bot._image_replies.clear()
        self.bot.campaigns.table_chat_channel_ids = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        payload = SimpleNamespace(
            cached_message=None, message_id=555, guild_id=GUILD_A, channel_id=EXTRA
        )
        await self.deleted(cast(Any, payload))
        self.assertEqual(self.live.unsaved.lines(), [])

    async def test_a_chat_that_cannot_be_read_is_let_go_quietly(self) -> None:
        import discord

        chat = self.chat_of([], channel_id=VOICE)

        def refuse(this: Any, **kw: Any) -> Any:
            raise discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="no")), "no")

        type(chat).history = refuse
        with self.assertLogs("dmbot.bot", "INFO") as logged:
            await self.delete_after_restart(chat)
        self.assertTrue(any("could not be read" in line for line in logged.output))
        self.assertFalse(any("500" in line for line in logged.output))  # nothing of the message

    async def test_one_servers_limit_leaves_another_server_alone(self) -> None:
        from dmbot import bot as bot_module

        chat = self.chat_of([])
        for n in range(bot_module.REPLY_SCANS_PER_MINUTE):
            await self.delete_after_restart(chat, picture_id=800 + n)
        other = SimpleNamespace(
            cached_message=None, message_id=900, guild_id=GUILD_B, channel_id=chat.id
        )
        await self.deleted(cast(Any, other))  # another server: its own count
        self.assertEqual(len(chat.scans), bot_module.REPLY_SCANS_PER_MINUTE + 1)

    async def test_a_message_the_cache_knows_had_no_picture_costs_no_read(self) -> None:
        chat = self.chat_of([])
        self.in_channel(chat)
        plain = SimpleNamespace(attachments=[])
        gone = SimpleNamespace(cached_message=plain, message_id=1, guild_id=GUILD_A, channel_id=1)
        await self.deleted(cast(Any, gone))
        bulk = SimpleNamespace(
            cached_messages=[SimpleNamespace(id=2, attachments=[])],
            message_ids={2},
            guild_id=GUILD_A,
            channel_id=chat.id,
        )
        await self.purged(cast(Any, bulk))
        self.assertEqual(chat.scans, [])

    async def test_a_remembered_reply_needs_no_reading_of_the_chat(self) -> None:
        ours = self.mine(500)
        self.bot._image_replies[500] = [ours]
        chat = self.chat_of([])
        self.in_channel(chat)
        await self.deleted(
            cast(
                Any,
                SimpleNamespace(
                    cached_message=None, message_id=500, guild_id=GUILD_A, channel_id=chat.id
                ),
            )
        )
        ours.delete.assert_awaited_once()
        self.assertEqual(chat.scans, [])

    async def test_a_channel_that_is_not_read_as_table_chat_is_never_read(self) -> None:
        chat = self.chat_of([self.mine(500)], voice=False, channel_id=424242)
        await self.delete_after_restart(chat)
        self.assertEqual(chat.scans, [])  # (not a voice channel, nor any campaign's extra chat)

    async def test_the_extra_table_chat_channel_is_read(self) -> None:
        ours = self.mine(500)
        chat = self.chat_of([ours], voice=False, channel_id=EXTRA)
        await self.a_campaign_reads(EXTRA)
        await self.delete_after_restart(chat)
        ours.delete.assert_awaited_once()

    async def a_campaign_reads(self, channel_id: int) -> None:
        self.bot.campaigns.table_chat_channel_ids = AsyncMock(  # type: ignore[method-assign]
            return_value=frozenset({channel_id})
        )

    async def test_reading_the_chat_for_replies_is_limited_per_minute(self) -> None:
        from dmbot import bot as bot_module

        chat = self.chat_of([])
        for n in range(bot_module.REPLY_SCANS_PER_MINUTE + 5):
            await self.delete_after_restart(chat, picture_id=700 + n)
        self.assertEqual(len(chat.scans), bot_module.REPLY_SCANS_PER_MINUTE)

    async def test_the_reply_is_sent_as_a_reply_that_still_posts_if_the_picture_is_gone(
        self,
    ) -> None:
        import discord

        sent: list[dict[str, Any]] = []

        class Chat(discord.abc.Messageable):
            async def _get_channel(self) -> Any:
                return self

            async def send(self, text: str, **kw: Any) -> Any:  # type: ignore[override]
                sent.append(kw)
                return SimpleNamespace()

        cast(Any, self.bot).get_channel = lambda _id: Chat()
        await self.bot._post_reply(EXTRA, 500, "words", cast(Any, SimpleNamespace()))
        ref = sent[0]["reference"]
        self.assertEqual((ref.message_id, ref.fail_if_not_exists), (500, False))


class TheDay(Clocks):
    """The day's count is in the database: it outlives a desk, and each campaign has its own."""

    def desk(self, now: list[float], per_day: int = 2) -> ImageDesk:
        async def fetcher(link: str) -> Image:
            return PNG

        vision = Vision()
        return ImageDesk(
            vision,
            vision,
            per_day=per_day,
            counter=ImageUseStore(self.db),
            clock=lambda: now[0],
            fetcher=fetcher,
        )

    async def test_the_count_survives_a_new_desk_and_the_day_starts_again(self) -> None:
        now = [10 * DAY_S + 5.0]
        store = ImageUseStore(self.db)
        for _ in range(3):
            await self.desk(now).describe(GUILD_A, self.a.id, [LINK])  # a new desk each time
        self.assertEqual(await store.used(GUILD_A, self.a.id, 10), 2)
        now[0] += DAY_S
        self.assertIsNotNone((await self.desk(now).describe(GUILD_A, self.a.id, [LINK]))[0])
        self.assertEqual(await store.used(GUILD_A, self.a.id, 11), 1)
        self.assertEqual(await store.used(GUILD_A, self.a.id, 10), 0)  # the old day is cleared

    async def test_each_campaign_and_server_has_its_own_count(self) -> None:
        now = [10 * DAY_S + 5.0]
        desk = self.desk(now, per_day=1)
        self.assertIsNotNone((await desk.describe(GUILD_A, self.a.id, [LINK]))[0])
        self.assertIsNone((await desk.describe(GUILD_A, self.a.id, [LINK]))[0])  # used up
        other = (await self.campaigns.create(GUILD_A, "Gamma", DM)).id
        self.assertIsNotNone((await desk.describe(GUILD_A, other, [LINK]))[0])  # another campaign
        self.assertIsNotNone((await desk.describe(GUILD_B, self.b.id, [LINK]))[0])  # another server

    async def test_two_pictures_at_once_cannot_both_take_the_last_place(self) -> None:
        now = [10 * DAY_S + 5.0]
        desk = self.desk(now, per_day=1)
        got = await asyncio.gather(
            desk.describe(GUILD_A, self.a.id, [LINK]), desk.describe(GUILD_A, self.a.id, [LINK])
        )
        self.assertEqual(sorted(r[0] is not None for r in got), [False, True])

    async def test_a_limit_of_nothing_reads_nothing(self) -> None:
        self.assertEqual(await self.desk([DAY_S * 1.0], per_day=0).describe(1, "a", [LINK]), [None])

    async def test_without_a_counter_nothing_is_read(self) -> None:
        desk = ImageDesk(Vision(), Vision(), per_day=5)
        self.assertEqual(await desk.describe(1, "a", [LINK]), [])

    async def test_a_link_that_is_not_discords_is_never_opened(self) -> None:
        desk = ImageDesk(Vision(), Vision(), per_day=5, counter=ImageUseStore(self.db))
        got = await desk.describe(GUILD_A, self.a.id, ["https://example.com/x.png"])
        self.assertEqual(got, [None])

    async def test_the_count_goes_with_its_campaign(self) -> None:
        now = [10 * DAY_S + 5.0]
        await self.desk(now).describe(GUILD_A, self.a.id, [LINK])
        await self.campaigns.delete(GUILD_A, self.a.id)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM image_day_use")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)
