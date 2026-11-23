"""The one message that shows a live session's after-session reads (#5124): it opens once, shows
the stage and part n of m, ends with what was not read, and never breaks a read. Stand-ins, no
Discord."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.ui import after_session as card


class _Message:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit(self, *, content: str) -> None:
        self.edits.append(content)


class TheCard(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.posted: list[str] = []
        self.message = _Message()

    async def post(self, text: str) -> Any:
        self.posted.append(text)
        return self.message

    async def test_it_opens_once_and_shows_the_stage_with_its_part(self) -> None:
        c = card.AfterSessionCard(self.post)
        await c.stage(card.STORY, 1, 4)
        await c.stage(card.STORY, 2, 4)
        self.assertEqual(self.posted, [card.TITLE])
        assert c._waiting is not None
        self.assertIn("Reading the story (2 of 4)", c._waiting.text())
        await c.finish()

    async def test_it_ends_with_the_last_word_and_what_was_not_read(self) -> None:
        c = card.AfterSessionCard(self.post)
        await c.stage(card.NAMES, 1, 2)
        c.note(card.not_read(2))
        c.note(card.not_read(2))  # (said once)
        await c.finish()
        self.assertEqual(
            self.message.edits[-1],
            f"📖 {card.DONE_BUT}\n"
            "2 parts of the session could not be read, so some names may be missing.",
        )
        self.assertEqual(
            card.not_read(1, "story threads"),
            "1 part of the session could not be read, so some story threads may be missing.",
        )

    async def test_a_clean_read_ends_on_finished_alone(self) -> None:
        c = card.AfterSessionCard(self.post)
        await c.stage(card.STORY, 1, 1)
        await c.finish()
        self.assertEqual(self.message.edits[-1], f"📖 {card.DONE}")

    async def test_a_session_with_no_reads_posts_nothing(self) -> None:
        c = card.AfterSessionCard(self.post)
        await c.finish()
        self.assertEqual((self.posted, self.message.edits), ([], []))

    async def test_a_card_that_cannot_be_posted_never_breaks_a_read(self) -> None:
        async def refused(text: str) -> Any:
            return None

        c = card.AfterSessionCard(refused)
        await c.stage(card.THREADS, 1, 3)
        await c.stage(card.THREADS, 2, 3)
        await c.finish()

        async def broken(text: str) -> Any:
            raise RuntimeError("boom")

        b = card.AfterSessionCard(broken)
        with self.assertLogs("dmbot.ui.after_session", level="WARNING") as logs:
            await b.stage(card.STORY, 1, 1)
        await b.finish()
        self.assertNotIn("boom", " ".join(logs.output))

    async def test_a_stage_that_stops_moving_says_so(self) -> None:
        now = [0.0]
        c = card.AfterSessionCard(self.post)
        await c.stage(card.STORY, 1, 4)
        assert c._waiting is not None
        c._waiting._clock = lambda: now[0]
        c._waiting._started = c._waiting._moved = 0.0
        now[0] = card.STALL_AFTER_S + 5
        text = c._waiting.text()
        self.assertIn("No change for", text)
        self.assertIn(card.STALL_NOTE, text)
        await c.finish()

    async def test_notes_are_said_once_even_when_the_card_never_opened(self) -> None:
        c = card.AfterSessionCard(self.post)
        c.note(card.not_read(1))
        await c.finish()
        self.assertEqual(
            self.posted,
            [
                f"📖 {card.DONE_BUT}\n"
                "1 part of the session could not be read, so some names may be missing."
            ],
        )

    async def test_a_hung_post_does_not_hold_up_the_read(self) -> None:
        import asyncio
        from unittest.mock import patch

        async def hung(text: str) -> Any:
            await asyncio.sleep(3600)

        c = card.AfterSessionCard(hung)
        with (
            patch.object(card, "POST_TIMEOUT_S", 0.05),
            self.assertLogs("dmbot.ui.after_session", level="WARNING"),
        ):
            await c.stage(card.STORY, 1, 2)
        await c.finish()
