"""Questions that show a secret name are pulled back when the DM screen opens up (#2097)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dmbot.dm_screen import secret_posts

G, C = 5, "c" * 32


def message() -> SimpleNamespace:
    return SimpleNamespace(edit=AsyncMock())


class ThePulledBackQuestions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        secret_posts._posts.clear()

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


class TheBotsPost(unittest.IsolatedAsyncioTestCase):
    async def test_post_remembers_a_secret_question_only_when_it_is_marked(self) -> None:
        from dmbot.bot import DMBot

        secret_posts._posts.clear()
        sent = message()
        fake = SimpleNamespace(post_message=AsyncMock(return_value=sent))
        await DMBot.post(fake, 1, "plain")  # type: ignore[arg-type]
        self.assertEqual(secret_posts.pending(G, C), 0)
        await DMBot.post(fake, 1, "secret", secret_of=(G, C))  # type: ignore[arg-type]
        self.assertEqual(secret_posts.pending(G, C), 1)
        fake.post_message = AsyncMock(return_value=None)  # a post that failed is not kept
        await DMBot.post(fake, 1, "secret", secret_of=(G, C))  # type: ignore[arg-type]
        self.assertEqual(secret_posts.pending(G, C), 1)


if __name__ == "__main__":
    unittest.main()
