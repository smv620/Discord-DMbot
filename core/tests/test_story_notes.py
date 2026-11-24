"""The story read's counts-only notes go through the outbox with no Discord (#4904 3b-4a).
Invented fixtures only."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.bot import DMBot, _story_note
from dmbot.outbox import (
    STORY_HELD_BACK,
    STORY_JOINED,
    STORY_MET_LIST,
    STORY_RECAP_LINES,
    STORY_SECRET_COUNT,
    STORY_THREADS,
    NotAnId,
    clean_payload,
)
from dmbot.ui import met_list as met_list_ui
from dmbot.ui import names as names_ui
from dmbot.ui import recap_lines as recap_lines_ui

GUILD = 7001
CID = "c" * 32


def bot(*, headless: bool, channel: int | None = 55, posted: bool = True) -> Any:
    out: Any = SimpleNamespace(
        headless=headless,
        outbox=SimpleNamespace(add_now=AsyncMock()),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id=CID))),
        _upkeep_channel=MagicMock(return_value=(channel, None)),
        post=AsyncMock(return_value=posted),
        transcripts=SimpleNamespace(
            session=AsyncMock(return_value=SimpleNamespace(started_at=1_700_000_000)),
            claim_recap_note=AsyncMock(return_value=True),
            release_recap_note=AsyncMock(),
        ),
    )
    out._say_story_note = DMBot._say_story_note.__get__(out)
    out._post_recap_note = DMBot._post_recap_note.__get__(out)
    return out


class TheQueue(IsolatedAsyncioTestCase):
    async def test_headless_a_note_is_an_outbox_row_with_a_dedupe_key(self) -> None:
        b = bot(headless=True)
        queued = await _story_note(b, GUILD, CID, STORY_MET_LIST, {"n": 3})
        self.assertTrue(queued)
        args, kwargs = b.outbox.add_now.await_args
        self.assertEqual(args, (GUILD, CID, STORY_MET_LIST, {"n": 3}))
        self.assertTrue(kwargs["unique"])

    async def test_the_bot_posts_it_itself(self) -> None:
        b = bot(headless=False)
        self.assertFalse(await _story_note(b, GUILD, CID, STORY_MET_LIST, {"n": 3}))
        b.outbox.add_now.assert_not_awaited()

    async def test_with_no_outbox_the_read_posts_it_itself(self) -> None:
        b = bot(headless=True)
        b.outbox = None
        self.assertFalse(await _story_note(b, GUILD, CID, STORY_MET_LIST, {"n": 3}))

    def test_a_payload_is_ids_and_counts_only(self) -> None:
        self.assertEqual(clean_payload({"sid": "a" * 32, "n": 2}), {"sid": "a" * 32, "n": 2})
        with self.assertRaises(NotAnId):
            clean_payload({"sid": "the dragon of the north", "n": 2})


class TheDelivery(IsolatedAsyncioTestCase):
    async def test_the_met_list_line_is_the_one_the_read_posts(self) -> None:
        b = bot(headless=False)
        await b._say_story_note(GUILD, CID, STORY_MET_LIST, {"n": 2})
        channel, text = b.post.await_args.args
        self.assertEqual((channel, text), (55, met_list_ui.notice(2)))

    async def test_the_recap_lines_note_carries_its_undo_button(self) -> None:
        b = bot(headless=False)
        await b._say_story_note(GUILD, CID, STORY_RECAP_LINES, {"sid": "a" * 32, "n": 4})
        self.assertEqual(b.post.await_args.args[1], recap_lines_ui.note(4, 1_700_000_000))
        self.assertIsNotNone(b.post.await_args.kwargs["view"])

    async def test_the_secret_count_line_is_posted_with_its_button(self) -> None:
        b = bot(headless=False)
        await b._say_story_note(GUILD, CID, STORY_SECRET_COUNT, {"n": 1})
        self.assertIn("1 thing", b.post.await_args.args[1])

    async def test_a_gone_campaign_or_channel_says_nothing(self) -> None:
        b = bot(headless=False, channel=None)
        await b._say_story_note(GUILD, CID, STORY_MET_LIST, {"n": 2})
        b.post.assert_not_awaited()
        b.campaigns.get = AsyncMock(return_value=None)
        await b._say_story_note(GUILD, CID, STORY_MET_LIST, {"n": 2})
        b.post.assert_not_awaited()

    async def test_a_refused_post_raises_so_the_row_is_tried_again(self) -> None:
        b = bot(headless=False, posted=False)
        with self.assertRaises(RuntimeError):
            await b._say_story_note(GUILD, CID, STORY_MET_LIST, {"n": 2})


class TheHeldBackNames(IsolatedAsyncioTestCase):
    async def test_the_private_message_has_a_count_and_no_name(self) -> None:
        text = names_ui.held_back_count_text(3)
        self.assertIn("3 new names to check", text)
        self.assertNotIn("**Bram**", text)
        self.assertIn("1 new name to check", names_ui.held_back_count_text(1))

    async def test_delivery_asks_the_dms_privately_with_a_count_pointer(self) -> None:
        b = bot(headless=False)
        with patch("dmbot.bot.pointer_note.send", AsyncMock(return_value=True)) as sent:
            await b._say_story_note(GUILD, CID, STORY_HELD_BACK, {"sid": "a" * 32, "n": 2})
        args = cast(Any, sent).await_args.args
        self.assertEqual(args[2], names_ui.held_back_count_text(2))
        self.assertEqual(args[4], names_ui.held_back_pointer(2))

    async def test_a_refused_private_message_raises_so_the_row_is_tried_again(self) -> None:
        b = bot(headless=False)
        with (
            patch("dmbot.bot.pointer_note.send", AsyncMock(return_value=False)),
            self.assertRaises(RuntimeError),
        ):
            await b._say_story_note(GUILD, CID, STORY_HELD_BACK, {"sid": "a" * 32, "n": 2})

    async def test_a_gone_campaign_or_no_names_says_nothing(self) -> None:
        b = bot(headless=False)
        with patch("dmbot.bot.pointer_note.send", AsyncMock(return_value=True)) as sent:
            await b._say_story_note(GUILD, CID, STORY_HELD_BACK, {"sid": "a" * 32, "n": 0})
            b.campaigns.get = AsyncMock(return_value=None)
            await b._say_story_note(GUILD, CID, STORY_HELD_BACK, {"sid": "a" * 32, "n": 2})
        sent.assert_not_awaited()


class TheThreadsAndJoinsNotes(IsolatedAsyncioTestCase):
    async def test_a_worker_queues_the_threads_line_as_a_row_of_counts_and_a_date(self) -> None:
        b = bot(headless=True)
        b._tell_threads = DMBot._tell_threads.__get__(b)
        campaign = SimpleNamespace(id=CID, guild_id=GUILD)
        await b._tell_threads(campaign, 2, "call limit", "the session of 1 Jan", 1, started_at=900)
        args, kwargs = b.outbox.add_now.await_args
        self.assertEqual(args[2], STORY_THREADS)
        self.assertEqual(args[3], {"made": 2, "changes": 1, "stopped": True, "at": 900})
        self.assertTrue(kwargs["unique"])
        b.post.assert_not_awaited()

    async def test_the_bot_posts_the_threads_line_itself_as_before(self) -> None:
        b = bot(headless=False)
        b._tell_threads = DMBot._tell_threads.__get__(b)
        campaign = SimpleNamespace(id=CID, guild_id=GUILD)
        await b._tell_threads(campaign, 2, "", "the session of 1 Jan", 0, started_at=900)
        b.outbox.add_now.assert_not_awaited()
        self.assertIn("2 possible story threads", b.post.await_args.args[1])

    async def test_delivery_says_the_threads_line_with_its_button_and_the_joins_count(self) -> None:
        b = bot(headless=False)
        await b._say_story_note(
            GUILD, CID, STORY_THREADS, {"made": 3, "changes": 0, "stopped": False, "at": 900}
        )
        self.assertIn("3 possible story threads", b.post.await_args.args[1])
        self.assertIsNotNone(b.post.await_args.kwargs["view"])
        await b._say_story_note(GUILD, CID, STORY_JOINED, {"sid": "a" * 32, "n": 2})
        self.assertIn("matched 2 names on its own", b.post.await_args.args[1])
        b.post = AsyncMock(return_value=False)
        with self.assertRaises(RuntimeError):
            await b._say_story_note(GUILD, CID, STORY_JOINED, {"sid": "a" * 32, "n": 2})
