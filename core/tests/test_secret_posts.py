"""Questions that show a secret name are pulled back when the DM screen opens up (#2097)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.dm_screen import secret_posts

G, C = 5, "c" * 32


def message(answered: bool = False) -> SimpleNamespace:
    # An open question still has its buttons (components); an answered one has none.
    return SimpleNamespace(edit=AsyncMock(), components=[] if answered else [object()])


class ThePulledBackQuestions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        secret_posts._posts.clear()
        secret_posts._searched.clear()

    async def test_every_open_question_is_edited_to_the_plain_line_with_no_buttons(self) -> None:
        a, b = message(), message()
        secret_posts.remember(G, C, a)
        secret_posts.remember(G, C, b)
        self.assertEqual(await secret_posts.hide(G, C), 2)
        for sent in (a, b):
            sent.edit.assert_awaited_once()
            kw = sent.edit.await_args.kwargs
            self.assertEqual((kw["content"], kw["view"]), (secret_posts.HIDDEN, None))
        self.assertEqual(
            secret_posts.HIDDEN,
            "This question was about a secret name. Answer it from the name's card.",
        )
        self.assertEqual(secret_posts.pending(G, C), 0)
        self.assertEqual(await secret_posts.hide(G, C), 0)  # a second change edits nothing

    async def test_an_answered_question_gets_the_second_line(self) -> None:
        done = message(answered=True)
        secret_posts.remember(G, C, done)
        await secret_posts.hide(G, C)
        self.assertEqual(done.edit.await_args.kwargs["content"], secret_posts.HIDDEN_ANSWERED)
        self.assertEqual(
            secret_posts.HIDDEN_ANSWERED, "This was about a secret name. See the name's card."
        )

    async def test_a_copy_whose_edit_failed_is_kept_and_tried_again(self) -> None:
        stuck = message()
        stuck.edit.side_effect = [RuntimeError("busy"), None]
        secret_posts.remember(G, C, stuck)
        self.assertEqual(await secret_posts.hide(G, C), 0)
        self.assertEqual(secret_posts.pending(G, C), 1)  # remembered for the next change
        self.assertEqual(await secret_posts.hide(G, C), 1)
        self.assertEqual(secret_posts.pending(G, C), 0)

    async def test_another_campaign_or_server_is_left_alone(self) -> None:
        mine, other, elsewhere = message(), message(), message()
        secret_posts.remember(G, C, mine)
        secret_posts.remember(G, "d" * 32, other)
        secret_posts.remember(6, C, elsewhere)
        await secret_posts.hide(G, C)
        other.edit.assert_not_awaited()
        elsewhere.edit.assert_not_awaited()

    async def test_a_message_that_cannot_be_edited_does_not_stop_the_rest(self) -> None:
        gone, fine = message(), message()
        gone.edit.side_effect = RuntimeError("deleted")
        secret_posts.remember(G, C, gone)
        secret_posts.remember(G, C, fine)
        self.assertEqual(await secret_posts.hide(G, C), 1)
        fine.edit.assert_awaited_once()

    async def test_only_the_newest_few_are_kept(self) -> None:
        for _ in range(secret_posts.KEPT_PER_CAMPAIGN + 5):
            secret_posts.remember(G, C, message())
        self.assertEqual(secret_posts.pending(G, C), secret_posts.KEPT_PER_CAMPAIGN)


class WhatARestartForgot(unittest.IsolatedAsyncioTestCase):
    async def test_open_questions_are_found_on_the_screen_by_their_buttons(self) -> None:
        secret_posts._posts.clear()
        secret_posts._searched.clear()

        def msg(author: int, custom_id: str | None) -> SimpleNamespace:
            children = [SimpleNamespace(custom_id=custom_id)] if custom_id else []
            return SimpleNamespace(
                id=id(children), author=SimpleNamespace(id=author), edit=AsyncMock(),
                components=[SimpleNamespace(children=children)],
            )  # fmt: skip

        keep = msg(9, "dmbot:secret:keep:" + "c" * 32 + ":" + "a" * 32)
        reveal = msg(9, "dmbot:rv:y:" + "c" * 32 + ":" + "a" * 32 + ":5")
        asked = msg(9, "dmbot:sa:k:" + "c" * 32 + ":" + "a" * 32 + ":5")  # the newer secret id
        other_button = msg(9, "dmbot:recap:read:x")
        players = msg(8, "dmbot:secret:keep:x")  # not the bot's own
        plain = msg(9, None)

        async def history(limit: int) -> Any:
            for m in (keep, reveal, asked, other_button, players, plain):
                yield m

        # The whole history is searched, not only the last few: nothing limits the read.
        seen: list[Any] = []

        async def history_all(limit: int | None) -> Any:
            seen.append(limit)
            return
            yield

        await secret_posts.hide(G, C, channel=SimpleNamespace(history=history_all), bot_id=9)
        self.assertEqual(seen, [None])
        secret_posts._searched.clear()  # the search above found nothing to remember
        channel = SimpleNamespace(history=history)
        self.assertEqual(await secret_posts.hide(G, C, channel=channel, bot_id=9), 3)
        keep.edit.assert_awaited_once()
        asked.edit.assert_awaited_once()
        reveal.edit.assert_awaited_once()
        other_button.edit.assert_not_awaited()
        players.edit.assert_not_awaited()

    async def test_the_history_is_searched_once_per_campaign_per_process(self) -> None:
        secret_posts._posts.clear()
        secret_posts._searched.clear()
        reads = 0

        async def history(limit: int | None) -> Any:
            nonlocal reads
            reads += 1
            return
            yield

        channel = SimpleNamespace(history=history)
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        await secret_posts.hide(G, C, channel=channel, bot_id=9)  # the second set-up
        self.assertEqual(reads, 1)
        await secret_posts.hide(G + 1, C, channel=channel, bot_id=9)  # another server
        self.assertEqual(reads, 2)

    async def test_a_question_dropped_from_the_list_means_the_screen_is_read_again(self) -> None:
        secret_posts._posts.clear()
        secret_posts._searched.clear()
        reads = 0

        async def history(limit: int | None) -> Any:
            nonlocal reads
            reads += 1
            return
            yield

        channel = SimpleNamespace(history=history)
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        for _ in range(secret_posts.KEPT_PER_CAMPAIGN):  # exactly full: nothing dropped yet
            secret_posts.remember(G, C, message())
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        self.assertEqual(reads, 1)
        for _ in range(secret_posts.KEPT_PER_CAMPAIGN + 1):  # the 21st drops the first
            secret_posts.remember(G, C, message())
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        self.assertEqual(reads, 2)

    async def test_a_new_screen_channel_is_read_again(self) -> None:
        secret_posts._posts.clear()
        secret_posts._searched.clear()
        reads: list[int] = []

        def channel(channel_id: int) -> Any:
            async def history(limit: int | None) -> Any:
                reads.append(channel_id)
                return
                yield

            return SimpleNamespace(id=channel_id, history=history)

        await secret_posts.hide(G, C, channel=channel(1), bot_id=9)
        await secret_posts.hide(G, C, channel=channel(1), bot_id=9)
        await secret_posts.hide(G, C, channel=channel(2), bot_id=9)  # the screen was remade
        self.assertEqual(reads, [1, 2])

    async def test_a_question_dropped_during_the_search_leaves_the_campaign_unmarked(self) -> None:
        secret_posts._posts.clear()
        secret_posts._searched.clear()
        reads = 0

        async def history(limit: int | None) -> Any:
            nonlocal reads
            reads += 1
            if reads == 1:  # a question is posted while the screen is being read
                for _ in range(secret_posts.KEPT_PER_CAMPAIGN + 1):
                    secret_posts.remember(G, C, message())
            return
            yield

        channel = SimpleNamespace(history=history)
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        self.assertEqual(reads, 2)  # not marked after the first: it was overtaken
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        self.assertEqual(reads, 2)  # and marked once a search ran without a drop

    async def test_a_failed_edit_list_that_overflows_means_the_screen_is_read_again(self) -> None:
        secret_posts._posts.clear()
        secret_posts._searched.clear()
        reads = 0

        async def history(limit: int | None) -> Any:
            nonlocal reads
            reads += 1
            return
            yield

        channel = SimpleNamespace(history=history)
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        for _ in range(secret_posts.KEPT_PER_CAMPAIGN):
            gone = message()
            gone.edit.side_effect = RuntimeError("deleted")
            secret_posts.remember(G, C, gone)
        extra = message()
        extra.edit.side_effect = RuntimeError("deleted")
        secret_posts._posts[(G, C)].append(extra)  # one over, as if posted meanwhile
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        self.assertEqual(secret_posts.pending(G, C), secret_posts.KEPT_PER_CAMPAIGN)
        self.assertEqual(reads, 1)  # (the kept list is trimmed after this pass)
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        self.assertEqual(reads, 2)  # the overflow was dropped, so it was searched again

    async def test_a_failed_search_is_tried_again(self) -> None:
        secret_posts._searched.clear()
        reads = 0

        async def broken(limit: int | None) -> Any:
            nonlocal reads
            reads += 1
            raise RuntimeError("no")
            yield

        channel = SimpleNamespace(history=broken)
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        await secret_posts.hide(G, C, channel=channel, bot_id=9)
        self.assertEqual(reads, 2)

    def test_a_question_is_exposed_only_in_a_server_channel_with_players_able_to_look(self) -> None:
        self.assertTrue(secret_posts.is_exposed("peek", True))
        self.assertTrue(secret_posts.is_exposed("open", True))
        self.assertFalse(secret_posts.is_exposed("private", True))
        self.assertFalse(secret_posts.is_exposed("open", False))  # a private message


class TheBotsPost(unittest.IsolatedAsyncioTestCase):
    async def test_post_remembers_a_secret_question_only_when_it_is_marked(self) -> None:
        from dmbot.bot import DMBot

        secret_posts._posts.clear()
        secret_posts._searched.clear()
        sent = message()
        fake = SimpleNamespace(
            post_message=AsyncMock(return_value=sent),
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(dm_screen_visibility="private"))
            ),
        )
        await DMBot.post(fake, 1, "plain")  # type: ignore[arg-type]
        self.assertEqual(secret_posts.pending(G, C), 0)
        await DMBot.post(fake, 1, "secret", secret_of=(G, C))  # type: ignore[arg-type]
        self.assertEqual(secret_posts.pending(G, C), 1)
        # The screen opened up while it was being sent: it is pulled back at once, not kept.
        fake.campaigns.get = AsyncMock(return_value=SimpleNamespace(dm_screen_visibility="peek"))
        late = message()
        fake.post_message = AsyncMock(return_value=late)
        await DMBot.post(fake, 1, "secret", secret_of=(G, C))  # type: ignore[arg-type]
        late.edit.assert_awaited_once()
        self.assertEqual(secret_posts.pending(G, C), 1)
        fake.post_message = AsyncMock(return_value=None)  # a post that failed is not kept
        await DMBot.post(fake, 1, "secret", secret_of=(G, C))  # type: ignore[arg-type]
        self.assertEqual(secret_posts.pending(G, C), 1)


if __name__ == "__main__":
    unittest.main()
