"""A live session's slow after-session reads in the worker (#4905 slice 4a): with the worker off the
steps are exactly today's; with it on they become two hand-overs, which the bot waits for and falls
back to running itself when no worker takes them; the worker runs each block from ids, one failing
step never stops the next, and one holder takes a block. Stand-ins, no database."""

from __future__ import annotations

import unittest
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest import mock

from dmbot import bot as botmod
from dmbot.bot import DMBot
from dmbot.transcript import after_session_jobs as jobs


class _Jobs:
    """The queue, in memory: just what the bot's side uses."""

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.added: list[tuple[str, tuple[int, ...]]] = []
        self.holder: dict[str, str] = {}
        self.renewed: list[tuple[str, str]] = []
        self.progress: dict[str, tuple[str, int, int]] = {}
        self.dead: set[str] = set()  # blocks whose running lease has run out

    async def add(self, g: int, c: str, s: str, block: str, dm_ids: Any) -> bool:
        self.rows.setdefault(block, jobs.WAITING)
        self.added.append((block, tuple(sorted(dm_ids))))
        return True

    async def state(self, g: int, c: str, s: str, block: str) -> str | None:
        return self.rows.get(block)

    async def status(self, g: int, c: str, s: str, block: str) -> jobs.Status | None:
        state = await self.state(g, c, s, block)
        if state is None:
            return None
        if self.progress.get(block) and state == jobs.RUNNING:
            return jobs.Status(state, *self.progress[block])
        return jobs.Status(state)

    async def claim(self, g: int, c: str, s: str, block: str, who: str) -> bool:
        running_dead = self.rows.get(block) == jobs.RUNNING and block in self.dead
        if self.rows.get(block) != jobs.WAITING and not running_dead:
            return False
        self.rows[block], self.holder[block] = jobs.RUNNING, who
        return True

    async def renew(self, g: int, c: str, s: str, block: str, who: str) -> bool:
        self.renewed.append((block, who))
        return self.holder.get(block) == who

    async def release(self, g: int, c: str, s: str, block: str, who: str) -> bool:
        if self.holder.get(block) != who or self.rows.get(block) != jobs.RUNNING:
            return False
        self.rows[block], self.holder[block] = jobs.WAITING, ""
        return True

    async def finish(self, g: int, c: str, s: str, block: str, who: str, *, ok: bool) -> bool:
        if self.holder.get(block) != who:
            return False
        self.rows[block] = jobs.DONE if ok else jobs.FAILED
        return True


def _bot(*, worker_on: bool, queue: _Jobs | None = None) -> Any:
    me: Any = SimpleNamespace(
        settings=SimpleNamespace(worker_on=worker_on),
        after_jobs=queue,
        headless=False,
        calls=[],
    )
    me._worker_does_after_session = partial(DMBot._worker_does_after_session, me)
    me._or_worker = partial(DMBot._or_worker, me)
    me._hand_to_worker = partial(DMBot._hand_to_worker, me)
    me._after_lease = partial(DMBot._after_lease, me)
    me._after_card = partial(DMBot._after_card, me)
    me.post_message = mock.AsyncMock(return_value=None)
    for name in (
        "extract_claims",
        "walk_story_chain",
        "write_scenes",
        "write_who",
        "grade_contradictions",
        "make_report",
        "write_importance",
        "read_session_threads",
    ):

        async def step(table: Any, *a: Any, _n: str = name) -> None:
            me.calls.append(_n)

        setattr(me, name, step)
    return me


def _table() -> Any:
    return SimpleNamespace(
        guild_id=1, campaign_id="c" * 32, transcript_session_id="s" * 8,
        dm_user_id=7, dm_user_ids=frozenset({3}),
    )  # fmt: skip


READS = [
    "story extractor",
    "where the story is",
    "scenes",
    "who is this",
    "contradiction grades",
    "after-session report",
]


class WithTheWorkerOff(unittest.IsolatedAsyncioTestCase):
    async def test_the_steps_are_exactly_todays_and_run_here(self) -> None:
        me = _bot(worker_on=False)
        table = _table()
        reads = DMBot._after_reads(me, table, 5)
        late = DMBot._after_late(me, table)
        self.assertEqual([n for n, _ in reads], READS)
        self.assertEqual([n for n, _ in late], ["importance", "story threads"])
        for _, step in [*reads, *late]:
            await step()
        self.assertEqual(
            me.calls,
            [
                "extract_claims", "walk_story_chain", "write_scenes", "write_who",
                "grade_contradictions", "make_report", "write_importance",
                "read_session_threads",
            ],
        )  # fmt: skip

    async def test_a_queue_alone_with_the_worker_off_changes_nothing(self) -> None:
        me = _bot(worker_on=False, queue=_Jobs())
        self.assertEqual([n for n, _ in DMBot._after_reads(me, _table(), 5)], READS)


class WithTheWorkerOn(unittest.IsolatedAsyncioTestCase):
    async def test_each_block_is_one_hand_over(self) -> None:
        me = _bot(worker_on=True, queue=_Jobs())
        reads = DMBot._after_reads(me, _table(), 5)
        late = DMBot._after_late(me, _table())
        self.assertEqual([n for n, _ in reads], ["story reads (worker)"])
        self.assertEqual([n for n, _ in late], ["late reads (worker)"])

    async def test_it_waits_for_the_worker_and_runs_nothing_itself(self) -> None:
        queue = _Jobs()
        me = _bot(worker_on=True, queue=queue)
        ((_, hand),) = DMBot._after_reads(me, _table(), 5)

        async def worker_finishes(_: float) -> None:
            queue.rows[jobs.READS] = jobs.DONE

        with mock.patch.object(botmod.asyncio, "sleep", worker_finishes):
            await hand()
        self.assertEqual(queue.added, [(jobs.READS, (3, 7))])
        self.assertEqual(me.calls, [])

    async def test_a_block_nobody_takes_is_run_by_the_bot_itself_once(self) -> None:
        queue = _Jobs()
        me = _bot(worker_on=True, queue=queue)
        ((_, hand),) = DMBot._after_late(me, _table())
        with mock.patch.object(botmod, "WORKER_TAKE_S", 0):
            await hand()
        self.assertEqual(me.calls, ["write_importance", "read_session_threads"])
        self.assertTrue(queue.holder[jobs.LATE].startswith("bot-"))
        self.assertEqual(queue.rows[jobs.LATE], jobs.DONE)

    async def test_a_failed_block_lets_the_steps_after_it_go_on(self) -> None:
        queue = _Jobs()
        me = _bot(worker_on=True, queue=queue)
        ((_, hand),) = DMBot._after_reads(me, _table(), 5)
        queue.rows[jobs.READS] = jobs.FAILED
        with self.assertLogs("dmbot.bot", level="WARNING"):
            await hand()
        self.assertEqual(me.calls, [])


class WithAWorkerThatDiesOrASlowOne(unittest.IsolatedAsyncioTestCase):
    async def test_a_block_whose_worker_died_is_taken_over_by_the_bot(self) -> None:
        queue = _Jobs()
        me = _bot(worker_on=True, queue=queue)
        ((_, hand),) = DMBot._after_late(me, _table())
        await queue.add(1, "c", "s", jobs.LATE, {3})
        queue.rows[jobs.LATE], queue.holder[jobs.LATE] = jobs.RUNNING, "worker-dead"
        queue.dead.add(jobs.LATE)  # (its lease has run out)
        with mock.patch.object(botmod, "WORKER_TAKE_S", 0):
            await hand()
        self.assertEqual(me.calls, ["write_importance", "read_session_threads"])
        self.assertTrue(queue.holder[jobs.LATE].startswith("bot-"))

    async def test_a_slow_worker_with_a_live_lease_is_left_alone_not_doubled(self) -> None:
        queue = _Jobs()
        me = _bot(worker_on=True, queue=queue)
        ((_, hand),) = DMBot._after_late(me, _table())
        await queue.add(1, "c", "s", jobs.LATE, {3})
        queue.rows[jobs.LATE], queue.holder[jobs.LATE] = jobs.RUNNING, "worker-live"
        ticks = [0]

        async def tick(_: float) -> None:
            ticks[0] += 1
            if ticks[0] == 3:
                queue.rows[jobs.LATE] = jobs.DONE

        with (
            mock.patch.object(botmod, "WORKER_TAKE_S", 0),
            mock.patch.object(botmod.asyncio, "sleep", tick),
        ):
            await hand()
        self.assertEqual(me.calls, [])  # (the worker's lease was live: the bot never ran it)

    async def test_a_row_that_is_gone_ends_the_wait_at_once(self) -> None:
        me = _bot(worker_on=True, queue=_Jobs())
        ((_, hand),) = DMBot._after_late(me, _table())

        class Vanishing(_Jobs):
            async def status(self, *a: Any) -> jobs.Status | None:
                return None

        me.after_jobs = Vanishing()
        await hand()  # (does not hang for hours)
        self.assertEqual(me.calls, [])

    async def test_a_database_error_while_waiting_is_tried_again_then_given_up(self) -> None:
        class Broken(_Jobs):
            async def status(self, *a: Any) -> jobs.Status | None:
                raise RuntimeError("the database")

        me = _bot(worker_on=True, queue=Broken())
        ((_, hand),) = DMBot._after_late(me, _table())

        async def quick(_: float) -> None:
            return None

        with (
            mock.patch.object(botmod.asyncio, "sleep", quick),
            self.assertLogs("dmbot.bot", level="WARNING") as logs,
        ):
            await hand()
        self.assertNotIn("the database", " ".join(logs.output))

    async def test_the_bots_own_run_keeps_the_lease_fresh(self) -> None:
        import asyncio

        queue = _Jobs()
        me = _bot(worker_on=True, queue=queue)
        ((_, hand),) = DMBot._after_late(me, _table())

        async def slow(table: Any) -> None:
            await asyncio.sleep(0.15)
            me.calls.append("slow")

        me.write_importance = slow
        with (
            mock.patch.object(botmod, "WORKER_TAKE_S", 0),
            mock.patch.object(botmod, "AFTER_LEASE_EVERY_S", 0.03),
        ):
            await hand()
        self.assertGreaterEqual(len(queue.renewed), 2)


class TheCardFollowsTheWorker(unittest.IsolatedAsyncioTestCase):
    async def test_the_waiting_card_shows_the_workers_stage_and_part_n_of_m(self) -> None:
        queue = _Jobs()
        me = _bot(worker_on=True, queue=queue)
        ((_, hand),) = DMBot._after_reads(me, _table(), 5)
        shown: list[tuple[str, int, int]] = []

        class Card:
            async def stage(self, words: str, part: int = 0, parts: int = 0) -> None:
                shown.append((words, part, parts))

        await queue.add(1, "c", "s", jobs.READS, {3})
        queue.rows[jobs.READS] = jobs.RUNNING
        queue.holder[jobs.READS] = "worker-live"
        queue.progress[jobs.READS] = (jobs.STORY, 2, 5)
        ticks = [0]

        async def tick(_: float) -> None:
            ticks[0] += 1
            if ticks[0] == 1:
                queue.progress[jobs.READS] = (jobs.THREADS, 1, 3)
            else:
                queue.rows[jobs.READS] = jobs.DONE

        with (
            mock.patch.object(botmod.asyncio, "sleep", tick),
            mock.patch.object(DMBot, "_after_card", lambda self, table: Card()),
        ):
            await hand()
        self.assertEqual(
            shown[1:],
            [("Reading the story", 2, 5), ("Reading story threads", 1, 3)],
        )
        self.assertEqual(shown[0][1:], (0, 0))  # (first: only that it goes on in the background)


class TheWorkersSide(unittest.IsolatedAsyncioTestCase):
    def worker(self) -> Any:
        me: Any = SimpleNamespace(
            consent=SimpleNamespace(consenting=mock.AsyncMock(return_value=frozenset())),
            calls=[],
        )
        for name in (
            "_extract_session",
            "_walk_story_chain",
            "_write_scenes",
            "_write_who",
            "_grade_contradictions_for",
            "_make_report_for",
            "_read_importance",
            "_read_threads",
        ):

            async def step(*a: Any, _n: str = name, **kw: Any) -> None:
                me.calls.append((_n, a, kw))

            setattr(me, name, step)
        return me

    async def test_the_reads_block_runs_in_todays_order_from_ids_alone(self) -> None:
        me = self.worker()
        self.assertTrue(await DMBot._run_after_block(me, 1, "c", "s", {3, 7}, jobs.READS))
        self.assertEqual(
            [c[0] for c in me.calls],
            ["_extract_session", "_walk_story_chain", "_write_scenes", "_write_who",
             "_grade_contradictions_for", "_make_report_for"],
        )  # fmt: skip
        extract = me.calls[0]
        self.assertEqual(extract[1], (1, "c", "s"))
        self.assertIsNone(extract[2]["tally"])  # (the bot's own log tally is not the worker's)

    async def test_the_block_tells_the_row_its_stage_and_part_as_it_goes(self) -> None:
        me = self.worker()
        said: list[tuple[str, int, int]] = []

        async def report(stage: str, part: int, parts: int) -> None:
            said.append((stage, part, parts))

        async def extract(*a: Any, progress: Any = None, **kw: Any) -> None:
            await progress(2, 5)

        async def threads(*a: Any, step: Any = None, **kw: Any) -> None:
            await step(1, 3)

        me._extract_session = extract
        me._read_threads = threads
        await DMBot._run_after_block(me, 1, "c", "s", {3}, jobs.READS, report)
        await DMBot._run_after_block(me, 1, "c", "s", {3}, jobs.LATE, report)
        self.assertEqual(said[0], ("story", 2, 5))
        self.assertIn(("working", 0, 0), said)
        self.assertEqual(said[-1], ("threads", 1, 3))

    async def test_a_progress_report_that_fails_never_breaks_the_block(self) -> None:
        me = self.worker()

        async def report(stage: str, part: int, parts: int) -> None:
            raise RuntimeError("a word of the story")

        with self.assertLogs("dmbot.bot", level="WARNING") as logs:
            ok = await DMBot._run_after_block(me, 1, "c", "s", {3}, jobs.LATE, report)
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in me.calls], ["_read_importance", "_read_threads"])
        self.assertNotIn("a word of the story", " ".join(logs.output))

    async def test_nothing_in_a_block_adds_to_the_combined_questions(self) -> None:
        """The DM's questions are collected in `table.asks` and sent as one message (#3210). The
        blocks' readers take ids, not a table, and none of them calls `ask_dm`, so moving them to
        the worker leaves that single message to the bot's later steps exactly as it was."""
        import inspect

        for name in (
            "_extract_session", "_read_threads", "_walk_story_chain", "_write_scenes",
            "_write_who", "_make_report_for", "_read_importance", "_grade_contradictions_for",
            "_tell_threads",
        ):  # fmt: skip
            source = inspect.getsource(getattr(DMBot, name))
            self.assertNotIn("ask_dm(", source, name)
            self.assertNotIn("table.asks", source, name)

    async def test_the_late_block_is_importance_then_threads(self) -> None:
        me = self.worker()
        await DMBot._run_after_block(me, 1, "c", "s", {3}, jobs.LATE)
        self.assertEqual([c[0] for c in me.calls], ["_read_importance", "_read_threads"])

    async def test_one_failing_step_never_stops_the_next_and_logs_only_its_type(self) -> None:
        me = self.worker()

        async def boom(*a: Any, **kw: Any) -> None:
            raise RuntimeError("a word of the story")

        me._write_scenes = boom
        with self.assertLogs("dmbot.bot", level="WARNING") as logs:
            ok = await DMBot._run_after_block(me, 1, "c", "s", {3}, jobs.READS)
        self.assertFalse(ok)  # (the block is reported as not whole)
        self.assertEqual(me.calls[-1][0], "_make_report_for")
        self.assertNotIn("a word of the story", " ".join(logs.output))
        self.assertIn("RuntimeError", " ".join(logs.output))

    async def test_a_taken_block_is_left_alone_and_a_run_one_is_ended(self) -> None:
        queue = _Jobs()
        me = self.worker()
        me.after_jobs = queue
        me._run_after_block = partial(DMBot._run_after_block, me)
        me._after_lease = partial(DMBot._after_lease, me)
        job = jobs.Job(1, "c", "s", jobs.LATE, (3,), "waiting")
        await queue.add(1, "c", "s", jobs.LATE, {3})
        queue.rows[jobs.LATE] = jobs.RUNNING  # (someone else holds it)
        await DMBot._take_after_job(me, job)
        self.assertEqual(me.calls, [])
        queue.rows[jobs.LATE] = jobs.WAITING
        await DMBot._take_after_job(me, job)
        self.assertEqual([c[0] for c in me.calls], ["_read_importance", "_read_threads"])
        self.assertEqual(queue.rows[jobs.LATE], jobs.DONE)

    async def test_a_worker_stopped_mid_block_gives_it_back_for_the_next_round(self) -> None:
        import asyncio

        queue = _Jobs()
        me = self.worker()
        me.after_jobs = queue
        me._after_lease = partial(DMBot._after_lease, me)

        async def stopped(*a: Any, **kw: Any) -> bool:
            raise asyncio.CancelledError  # (a deploy stops the worker's tasks)

        me._run_after_block = stopped
        await queue.add(1, "c", "s", jobs.LATE, {3})
        job = jobs.Job(1, "c", "s", jobs.LATE, (3,), "waiting")
        with self.assertRaises(asyncio.CancelledError):
            await DMBot._take_after_job(me, job)
        self.assertEqual(queue.rows[jobs.LATE], jobs.WAITING)  # (not failed: it is taken again)
        self.assertEqual(queue.holder[jobs.LATE], "")

    async def test_each_take_uses_an_id_of_its_own(self) -> None:
        queue = _Jobs()
        me = self.worker()
        me.after_jobs = queue
        me._run_after_block = partial(DMBot._run_after_block, me)
        me._after_lease = partial(DMBot._after_lease, me)
        job = jobs.Job(1, "c", "s", jobs.LATE, (3,), "waiting")
        await queue.add(1, "c", "s", jobs.LATE, {3})
        await DMBot._take_after_job(me, job)
        first = queue.holder[jobs.LATE]
        queue.rows[jobs.LATE] = jobs.WAITING
        await DMBot._take_after_job(me, job)
        self.assertTrue(first.startswith("worker-"))
        self.assertNotEqual(first, queue.holder[jobs.LATE])
