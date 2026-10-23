"""The waiting message (#2559): edited in place, at most every 10 s, elapsed only once long."""

from __future__ import annotations

import asyncio
import unittest

from dmbot.waiting import WaitingMessage, elapsed_text


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TheText(unittest.TestCase):
    def test_elapsed_reads_in_plain_words(self) -> None:
        self.assertEqual(elapsed_text(45), "45 s")
        self.assertEqual(elapsed_text(120), "2 min")
        self.assertEqual(elapsed_text(125), "2 min 5 s")

    def test_part_n_of_m_shows_only_when_the_total_is_known(self) -> None:
        clock = Clock()
        waiting = WaitingMessage(_nothing, "Reading…", clock=clock)
        waiting.step("Step 1 of 2")
        self.assertEqual(waiting.text(), "Reading…\nStep 1 of 2")
        waiting.step("Step 2 of 2", 3, 9)
        self.assertEqual(waiting.text(), "Reading…\nStep 2 of 2 (3 of 9)")

    def test_elapsed_time_appears_only_after_about_thirty_seconds(self) -> None:
        clock = Clock()
        waiting = WaitingMessage(_nothing, "Reading…", clock=clock)
        waiting.step("Step 1 of 2")
        clock.now = 29
        self.assertNotIn("so far", waiting.text())
        clock.now = 95
        self.assertTrue(waiting.text().endswith("Step 1 of 2, 1 min 35 s so far"))


async def _nothing(_: str) -> None:
    return None


class TheEdits(unittest.IsolatedAsyncioTestCase):
    async def test_it_edits_the_one_message_and_only_when_the_text_changed(self) -> None:
        sent: list[str] = []

        async def edit(text: str) -> None:
            sent.append(text)

        waiting = WaitingMessage(edit, "Reading…", every=0.02, show_after=999)
        async with waiting:
            await asyncio.sleep(0.07)
            self.assertEqual(sent, [])  # nothing moved: no edit
            waiting.step("Step 2 of 2", 1, 4)
            await asyncio.sleep(0.07)
        self.assertEqual(sent, ["Reading…\nStep 2 of 2 (1 of 4)"])

    async def test_a_burst_of_steps_is_one_edit_per_interval(self) -> None:
        sent: list[str] = []

        async def edit(text: str) -> None:
            sent.append(text)

        waiting = WaitingMessage(edit, "t", every=0.2, show_after=999)
        async with waiting:
            for done in range(1, 21):
                waiting.step("sorting", done, 20)
            await asyncio.sleep(0.5)
        self.assertLessEqual(len(sent), 3)
        self.assertEqual(sent[0], "t\nsorting (20 of 20)")

    async def test_a_failed_edit_does_not_stop_the_wait_or_leak_out(self) -> None:
        calls = [0]

        async def edit(text: str) -> None:
            calls[0] += 1
            raise RuntimeError("discord hiccup")

        waiting = WaitingMessage(edit, "t", every=0.02, show_after=999)
        async with waiting:
            waiting.step("a")
            await asyncio.sleep(0.05)
            waiting.step("b")
            await asyncio.sleep(0.05)
        self.assertGreaterEqual(calls[0], 2)

    async def test_nothing_is_edited_after_it_ends(self) -> None:
        sent: list[str] = []

        async def edit(text: str) -> None:
            sent.append(text)

        waiting = WaitingMessage(edit, "t", every=0.02, show_after=999)
        async with waiting:
            pass
        waiting.step("late")
        await asyncio.sleep(0.06)
        self.assertEqual(sent, [])
