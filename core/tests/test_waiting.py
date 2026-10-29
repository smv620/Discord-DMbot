"""The waiting message (#2559, #2564): edited in place, at most every 10 s, elapsed only once long,
"last change" and a named stall, and no more edits once the message is gone. A fake clock and a
fake sleep drive it, so nothing here waits or races."""

from __future__ import annotations

import asyncio
import unittest

from dmbot.waiting import WaitingMessage, ago_text, elapsed_text


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def _nothing(_: str) -> None:
    return None


class TheText(unittest.TestCase):
    def test_elapsed_reads_in_plain_words(self) -> None:
        self.assertEqual(elapsed_text(45), "45 s")
        self.assertEqual(elapsed_text(120), "2 min")
        self.assertEqual(elapsed_text(125), "2 min 5 s")
        self.assertEqual(ago_text(61), "1 min")
        self.assertEqual(ago_text(200), "3 min")

    def test_part_n_of_m_shows_only_when_the_total_is_known(self) -> None:
        waiting = WaitingMessage(_nothing, "Reading…", clock=Clock())
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
        clock.now = 45
        self.assertIn("Step 1 of 2, 45 s so far", waiting.text())

    def test_the_elapsed_time_starts_from_the_click_not_from_the_message(self) -> None:
        clock = Clock()
        clock.now = 100
        waiting = WaitingMessage(_nothing, "t", clock=clock, started=60)
        waiting.step("a")
        self.assertIn("40 s so far", waiting.text())

    def test_last_change_appears_after_a_minute_and_clears_on_the_next_step(self) -> None:
        clock = Clock()
        waiting = WaitingMessage(_nothing, "t", clock=clock)
        waiting.step("Step 1 of 2")
        clock.now = 59
        self.assertNotIn("Last change", waiting.text())
        clock.now = 130
        self.assertTrue(waiting.text().endswith("Last change 2 min ago."))
        waiting.step("Step 2 of 2", 1, 4)
        self.assertNotIn("Last change", waiting.text())

    def test_a_stall_is_named_past_the_limit_with_what_happens_next(self) -> None:
        clock = Clock()
        waiting = WaitingMessage(_nothing, "t", clock=clock, stall_after_s=240, stops_at_s=840)
        waiting.step("Step 1 of 2: reading it through")
        clock.now = 239
        self.assertNotIn("No change", waiting.text())
        clock.now = 250
        self.assertIn(
            "No change for 4 min. DMbot will stop by itself at 14 min and keep what it has.",
            waiting.text(),
        )
        self.assertNotIn("Last change", waiting.text())  # the stall line replaces it
        waiting.step("Step 2 of 2", 1, 4)  # the next step clears it
        self.assertNotIn("No change", waiting.text())

    def test_a_stage_that_has_made_nothing_yet_says_nothing_will_be_added(self) -> None:
        clock = Clock()
        waiting = WaitingMessage(_nothing, "t", clock=clock, stall_after_s=240, stops_at_s=840)
        waiting.step("Step 1 of 2: reading it through", keeps=False)
        clock.now = 250
        self.assertIn("stop by itself at 14 min and add nothing.", waiting.text())
        waiting.step("Step 2 of 2", 1, 4)
        clock.now = 500
        self.assertIn("and keep what it has.", waiting.text())

    def test_another_call_of_the_same_pass_counts_as_movement(self) -> None:
        clock = Clock()
        waiting = WaitingMessage(_nothing, "t", clock=clock, stall_after_s=240)
        waiting.step("Step 1 of 2: reading it through")
        clock.now = 200
        waiting.moved()  # a call went round again: the same words, but it is movement
        clock.now = 400  # 200 s since the second call: under the limit
        self.assertNotIn("No change", waiting.text())
        waiting.moved()
        clock.now = 450
        self.assertNotIn("Last change", waiting.text())

    def test_the_same_step_again_is_not_movement(self) -> None:
        clock = Clock()
        waiting = WaitingMessage(_nothing, "t", clock=clock)
        waiting.step("a", 1, 4)
        clock.now = 100
        waiting.step("a", 1, 4)
        self.assertIn("Last change 1 min ago.", waiting.text())


class Ticks:
    """A sleep that waits for the test to let one interval pass."""

    def __init__(self) -> None:
        self.turns: asyncio.Queue[None] = asyncio.Queue()
        self.asked: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.asked.append(seconds)
        await self.turns.get()

    async def pass_one(self) -> None:
        self.turns.put_nowait(None)
        for _ in range(5):
            await asyncio.sleep(0)


class TheEdits(unittest.IsolatedAsyncioTestCase):
    async def test_it_edits_every_ten_seconds_and_only_when_the_text_changed(self) -> None:
        sent: list[str] = []

        async def edit(text: str) -> None:
            sent.append(text)

        ticks = Ticks()
        waiting = WaitingMessage(edit, "Reading…", sleep=ticks.sleep, show_after=999)
        async with waiting:
            await ticks.pass_one()
            self.assertEqual(sent, [])  # nothing moved: no edit
            for done in range(1, 21):  # a burst of steps is one edit
                waiting.step("Step 2 of 2", done, 20)
            await ticks.pass_one()
            self.assertEqual(sent, ["Reading…\nStep 2 of 2 (20 of 20)"])
            await ticks.pass_one()
            self.assertEqual(len(sent), 1)
        self.assertEqual(set(ticks.asked), {10.0})

    async def test_a_failed_edit_does_not_stop_the_wait_and_is_tried_again(self) -> None:
        calls: list[str] = []

        async def edit(text: str) -> None:
            calls.append(text)
            if len(calls) == 1:
                raise RuntimeError("discord hiccup")

        ticks = Ticks()
        async with WaitingMessage(edit, "t", sleep=ticks.sleep, show_after=999) as waiting:
            waiting.step("a")
            await ticks.pass_one()
            await ticks.pass_one()
        self.assertEqual(calls, ["t\na", "t\na"])

    async def test_no_more_edits_once_discord_says_the_message_is_gone(self) -> None:
        calls = [0]

        class Gone(Exception):
            pass

        async def edit(text: str) -> None:
            calls[0] += 1
            raise Gone

        ticks = Ticks()
        waiting = WaitingMessage(
            edit, "t", sleep=ticks.sleep, show_after=999, gone=lambda e: isinstance(e, Gone)
        )
        async with waiting:
            waiting.step("a")
            await ticks.pass_one()
            waiting.step("b")
            await ticks.pass_one()
            await ticks.pass_one()
        self.assertEqual(calls[0], 1)

    async def test_nothing_is_edited_after_it_ends(self) -> None:
        sent: list[str] = []

        async def edit(text: str) -> None:
            sent.append(text)

        ticks = Ticks()
        waiting = WaitingMessage(edit, "t", sleep=ticks.sleep, show_after=999)
        async with waiting:
            pass
        waiting.step("late")
        await ticks.pass_one()
        self.assertEqual(sent, [])
