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

    async def add(self, g: int, c: str, s: str, block: str, dm_ids: Any) -> bool:
        self.rows.setdefault(block, jobs.WAITING)
        self.added.append((block, tuple(sorted(dm_ids))))
        return True

    async def state(self, g: int, c: str, s: str, block: str) -> str | None:
        return self.rows.get(block)

    async def claim(self, g: int, c: str, s: str, block: str, who: str) -> bool:
        if self.rows.get(block) != jobs.WAITING:
            return False
        self.rows[block], self.holder[block] = jobs.RUNNING, who
        return True

    async def renew(self, *a: Any) -> bool:
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
        self.assertEqual((queue.holder[jobs.LATE], queue.rows[jobs.LATE]), ("bot", jobs.DONE))

    async def test_a_failed_block_lets_the_steps_after_it_go_on(self) -> None:
        queue = _Jobs()
        me = _bot(worker_on=True, queue=queue)
        ((_, hand),) = DMBot._after_reads(me, _table(), 5)
        queue.rows[jobs.READS] = jobs.FAILED
        with self.assertLogs("dmbot.bot", level="WARNING"):
            await hand()
        self.assertEqual(me.calls, [])


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
        await DMBot._run_after_block(me, 1, "c", "s", {3, 7}, jobs.READS)
        self.assertEqual(
            [c[0] for c in me.calls],
            ["_extract_session", "_walk_story_chain", "_write_scenes", "_write_who",
             "_grade_contradictions_for", "_make_report_for"],
        )  # fmt: skip
        extract = me.calls[0]
        self.assertEqual(extract[1], (1, "c", "s"))
        self.assertIsNone(extract[2]["tally"])  # (the bot's own log tally is not the worker's)

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
            await DMBot._run_after_block(me, 1, "c", "s", {3}, jobs.READS)
        self.assertEqual(me.calls[-1][0], "_make_report_for")
        self.assertNotIn("a word of the story", " ".join(logs.output))
        self.assertIn("RuntimeError", " ".join(logs.output))

    async def test_a_taken_block_is_left_alone_and_a_run_one_is_ended(self) -> None:
        queue = _Jobs()
        me = self.worker()
        me.after_jobs = queue
        me._run_after_block = partial(DMBot._run_after_block, me)
        job = jobs.Job(1, "c", "s", jobs.LATE, (3,), "waiting")
        await queue.add(1, "c", "s", jobs.LATE, {3})
        queue.rows[jobs.LATE] = jobs.RUNNING  # (someone else holds it)
        await DMBot._take_after_job(me, job)
        self.assertEqual(me.calls, [])
        queue.rows[jobs.LATE] = jobs.WAITING
        await DMBot._take_after_job(me, job)
        self.assertEqual([c[0] for c in me.calls], ["_read_importance", "_read_threads"])
        self.assertEqual(queue.rows[jobs.LATE], jobs.DONE)
