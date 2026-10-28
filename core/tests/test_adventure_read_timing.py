"""The Shared story card's timing for an adventure read (#2564): how long a read waited and ran,
when it last moved, the stall line, the bot's clock, and the card keeping itself current while the
read goes. Pure logic: no database."""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.dm_screen import adventure_card as ui
from dmbot.memory import plan_reader as reader
from dmbot.memory import story_plan as sp
from dmbot.memory.adventure import Document as AdventureDocument
from dmbot.waiting import EDIT_FOR_S, STOPPED_UPDATING, keep_card_current

GUILD_A = 111
DM = 7
MODEL = "claude-sonnet-5-5"


def documents(n: int = 1) -> list[AdventureDocument]:
    return [
        AdventureDocument(
            id=f"{i:032x}",
            file_name=("a very long file name " * 5 + str(i))[:80],
            characters=50_000,
            chunks=5,
            added_by=DM,
            added_at=1_800_000_000,
        )
        for i in range(n)
    ]


def reading(doc: AdventureDocument) -> sp.ReadState:
    return sp.ReadState(doc.id, "reading", 5, 10, "", 0)


class CardWords(unittest.TestCase):
    def test_the_card_says_how_long_a_read_has_run_and_when_it_last_moved(self) -> None:
        (doc,) = documents()
        running = frozenset({doc.id})
        ages = {doc.id: ui.ReadAges(0, 130, 70)}
        text = ui.card_text([doc], {doc.id: reading(doc)}, {}, running, frozenset(), ages)
        self.assertIn("reading… 50% (running for 2 min 10 s, last change 1 min ago)", text)
        self.assertNotIn("running for", ui.card_text([doc], {doc.id: reading(doc)}, {}, running))

    def test_a_read_waiting_for_a_turn_says_how_long_it_has_waited(self) -> None:
        (doc,) = documents()
        both = frozenset({doc.id})
        ages = {doc.id: ui.ReadAges(600, 0, 0)}
        text = ui.card_text([doc], {doc.id: reading(doc)}, {}, both, both, ages)
        self.assertIn("waiting for a turn (asked 10 min ago)", text)

    def test_a_read_that_stopped_moving_says_so_and_when_dmbot_gives_up(self) -> None:
        words = ui.age_words(ui.ReadAges(0, 3000, ui.STALL_AFTER_S + 1))
        self.assertIn("no change for 16 min", words)
        hours = int(reader.MAX_RUN_S // 3600)
        self.assertIn(f"stops by itself after {hours} hours at most", words)
        self.assertIn("or sooner if the AI keeps failing", words)
        self.assertEqual(ui.age_words(ui.ReadAges(0, 10, 10)), "")
        self.assertEqual(ui.age_words(ui.ReadAges(0, 40, 5)), "running for 40 s")

    def test_the_stall_limit_covers_every_call_timing_out_before_the_read_gives_up(self) -> None:
        longest = reader.FAILURES_BEFORE_STOP * reader.CALL_TIMEOUT_S + sum(
            reader.PAUSES_S[: reader.FAILURES_BEFORE_STOP - 1]
        )
        self.assertGreater(ui.STALL_AFTER_S, longest)

    def test_a_busy_card_with_many_files_shows_the_timing_in_plain_words_and_fits(self) -> None:
        docs = documents(10)
        states = {d.id: reading(d) for d in docs}
        running = frozenset(d.id for d in docs[:3])
        ages = {d.id: ui.ReadAges(0, 7200, ui.STALL_AFTER_S + 1) for d in docs[:3]}
        text = ui.card_text(docs, states, {}, running, frozenset(), ages)  # type: ignore[arg-type]
        self.assertLess(len(text), 2000)
        self.assertIn("reading 50% (no change for 16 min)", text)
        calm = {d.id: ui.ReadAges(0, 240, 10) for d in docs[:3]}
        self.assertIn(
            "reading 50% (running 4 min)",
            ui.card_text(docs, states, {}, running, frozenset(), calm),  # type: ignore[arg-type]
        )
        both = frozenset(d.id for d in docs[:2])
        queued = {d.id: ui.ReadAges(600, 0, 0) for d in docs[:2]}
        self.assertIn(
            "waiting (asked 10 min ago)",
            ui.card_text(docs, states, {}, both, both, queued),  # type: ignore[arg-type]
        )

    def test_the_card_gets_ages_from_the_bots_clocks_for_this_server_only(self) -> None:
        now = time.monotonic()
        client = SimpleNamespace(
            plan_clocks={
                (GUILD_A, "x" * 32): reader.ReadClock(now - 300, now - 200, now - 100),
                (999, "y" * 32): reader.ReadClock(now, now, now),
            }
        )
        got = ui._ages(cast(Any, SimpleNamespace(client=client)), GUILD_A)
        self.assertEqual(list(got), ["x" * 32])
        ages = got["x" * 32]
        self.assertEqual(
            (round(ages.queued_s), round(ages.running_s), round(ages.still_s)), (300, 200, 100)
        )


def running_bot() -> Any:
    return SimpleNamespace(
        plan=SimpleNamespace(finish=AsyncMock()),
        plan_ai=SimpleNamespace(model=MODEL),
        plan_reads={},
        plan_waiting=set(),
        plan_clocks={},
        _plan_slots=asyncio.Semaphore(1),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=None)),
        plan_allows=AsyncMock(return_value=True),
        _plan_read_notice=AsyncMock(),
    )


class TheBotsClock(unittest.IsolatedAsyncioTestCase):
    async def test_the_bot_keeps_a_clock_for_a_read_and_drops_it_when_the_read_ends(self) -> None:
        seen: dict[str, Any] = {}
        bot = running_bot()

        async def run(*args: Any, progress: Any = None, **kw: Any) -> Any:
            clock = bot.plan_clocks[(GUILD_A, "d" * 32)]
            seen["before"] = (clock.asked, clock.began, clock.moved)
            await progress(3)
            seen["after"] = (clock.asked, clock.began, clock.moved)
            return reader.Report()

        with mock.patch("dmbot.bot.plan_reader.run", run):
            await DMBot._read_plan(bot, GUILD_A, "c" * 32, "d" * 32, 0)
        self.assertGreaterEqual(seen["after"][2], seen["before"][2])  # a call back is movement
        self.assertEqual(seen["before"][0], seen["after"][0])  # asked-at never changes
        self.assertEqual(bot.plan_clocks, {})

    async def test_the_clock_goes_when_a_read_is_cancelled_too(self) -> None:
        started = asyncio.Event()
        bot = running_bot()

        async def run(*args: Any, **kw: Any) -> Any:
            started.set()
            await asyncio.sleep(60)

        with mock.patch("dmbot.bot.plan_reader.run", run):
            task = asyncio.create_task(DMBot._read_plan(bot, GUILD_A, "c" * 32, "d" * 32, 0))
            await started.wait()
            self.assertEqual(len(bot.plan_clocks), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(bot.plan_clocks, {})

    async def test_a_read_that_runs_past_its_limit_is_stopped_even_inside_a_call(self) -> None:
        bot = running_bot()

        async def run(*args: Any, **kw: Any) -> Any:
            await asyncio.sleep(60)  # a call that never comes back

        with (
            mock.patch("dmbot.bot.plan_reader.run", run),
            mock.patch("dmbot.bot.plan_reader.MAX_RUN_S", 0.05),
        ):
            await DMBot._read_plan(bot, GUILD_A, "c" * 32, "d" * 32, 0)
        bot.plan.finish.assert_awaited_once_with(
            GUILD_A, "c" * 32, "d" * 32, "stopped", reader.STOP_TIME
        )
        self.assertEqual(bot.plan_clocks, {})


class KeepingTheCardCurrent(unittest.IsolatedAsyncioTestCase):
    """`keep_card_current` with a fake clock and sleep: it edits as the card changes, ends with one
    last draw when the job ends, and ends on "stopped updating" at the token's limit."""

    def setUp(self) -> None:
        self.now = 0.0
        self.edits: list[str] = []
        self.draws = 0

    async def sleep(self, seconds: float) -> None:
        self.now += seconds

    def clock(self) -> float:
        return self.now

    async def render(self) -> tuple[str, Any]:
        self.draws += 1
        return f"card at {int(self.now)} s", None

    async def edit(self, text: str, view: Any) -> None:
        self.edits.append(text)

    async def test_it_edits_as_the_card_changes_and_ends_with_one_last_draw_when_the_read_ends(
        self,
    ) -> None:
        await keep_card_current(
            self.render,
            self.edit,
            lambda: self.now < 35,
            every=10.0,
            clock=self.clock,
            sleep=self.sleep,
        )
        self.assertEqual(
            self.edits, ["card at 10 s", "card at 20 s", "card at 30 s", "card at 40 s"]
        )
        self.assertNotIn(STOPPED_UPDATING, self.edits[-1])

    async def test_a_card_that_does_not_change_is_not_edited_again(self) -> None:
        async def same() -> tuple[str, Any]:
            return "same", None

        await keep_card_current(
            same, self.edit, lambda: self.now < 45, every=10.0, clock=self.clock, sleep=self.sleep
        )
        self.assertEqual(self.edits, ["same"])

    async def test_a_read_that_outlasts_the_token_ends_on_stopped_updating(self) -> None:
        await keep_card_current(
            self.render,
            self.edit,
            lambda: True,
            every=60.0,
            limit_s=EDIT_FOR_S,
            clock=self.clock,
            sleep=self.sleep,
        )
        self.assertTrue(self.edits[-1].endswith(STOPPED_UPDATING))
        self.assertLessEqual(self.now, EDIT_FOR_S + 60)  # never past the token's life
        self.assertEqual(sum(STOPPED_UPDATING in e for e in self.edits), 1)

    async def test_a_newer_card_takes_over_and_the_old_loop_goes_quiet(self) -> None:
        await keep_card_current(
            self.render,
            self.edit,
            lambda: True,
            current=lambda: self.now < 25,
            every=10.0,
            clock=self.clock,
            sleep=self.sleep,
        )
        self.assertEqual(self.edits, ["card at 10 s", "card at 20 s"])

    async def test_a_message_that_is_gone_ends_the_loop_without_noise(self) -> None:
        import discord

        async def gone(text: str, view: Any) -> None:
            raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "gone")

        await keep_card_current(
            self.render, gone, lambda: True, every=10.0, clock=self.clock, sleep=self.sleep
        )
        self.assertEqual(self.draws, 1)

    async def test_an_edit_that_fails_for_another_reason_is_tried_again(self) -> None:
        calls = 0

        async def flaky(text: str, view: Any) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("hiccup")
            self.edits.append(text)

        with self.assertLogs("dmbot.waiting", "WARNING"):
            await keep_card_current(
                self.render,
                flaky,
                lambda: self.now < 25,
                every=10.0,
                clock=self.clock,
                sleep=self.sleep,
            )
        self.assertTrue(self.edits)

    async def test_cancelling_it_stops_it_cleanly(self) -> None:
        async def forever(seconds: float) -> None:
            await asyncio.sleep(60)

        task = asyncio.create_task(
            keep_card_current(self.render, self.edit, lambda: True, sleep=forever)
        )
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


class StartingTheWatch(unittest.IsolatedAsyncioTestCase):
    def interaction(self, reads: dict[Any, Any]) -> Any:
        return SimpleNamespace(
            id=5,
            message=SimpleNamespace(id=99),
            client=SimpleNamespace(plan_reads=reads),
            edit_original_response=AsyncMock(),
        )

    async def test_nothing_is_watched_when_no_read_is_going(self) -> None:
        seen = AsyncMock()
        campaign = SimpleNamespace(guild_id=GUILD_A)
        with mock.patch.object(ui, "keep_card_current", seen):
            await ui.watch_card(self.interaction({}), cast(Any, campaign))
        seen.assert_not_awaited()

    async def test_a_newer_press_on_the_same_card_takes_over_from_the_older_loop(self) -> None:
        campaign = cast(Any, SimpleNamespace(guild_id=GUILD_A))
        reads = {(GUILD_A, "d" * 32): object()}
        currents: list[Any] = []

        async def fake(render: Any, edit: Any, going: Any, *, current: Any, **kw: Any) -> None:
            currents.append(current)

        with mock.patch.object(ui, "keep_card_current", fake):
            await ui.watch_card(self.interaction(reads), campaign)
            first = currents[0]
            self.assertFalse(first())  # the loop is over, its slot freed: nothing left to hold
        # Two overlapping presses: the older loop is told it is no longer current.
        gate = asyncio.Event()
        order: list[bool] = []

        async def held(render: Any, edit: Any, going: Any, *, current: Any, **kw: Any) -> None:
            order.append(current())
            await gate.wait()
            order.append(current())

        with mock.patch.object(ui, "keep_card_current", held):
            older = asyncio.create_task(ui.watch_card(self.interaction(reads), campaign))
            await asyncio.sleep(0)
            newer = asyncio.create_task(ui.watch_card(self.interaction(reads), campaign))
            await asyncio.sleep(0)
            gate.set()
            await asyncio.gather(older, newer)
        self.assertEqual(order[:2], [True, True])  # both started current
        self.assertFalse(order[2])  # the older one found itself replaced
