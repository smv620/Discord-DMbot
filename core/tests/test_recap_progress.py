"""How far a recap being made has got (#2636): the stage, the parts read, when it last moved, and a
stalled or failed state with a fixed code, in the shared shape of every website wait. Counts, codes
and times only: nothing from a transcript or a recap."""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any

from dmbot.ai import AIError
from dmbot.recaps import critics
from dmbot.recaps import pipeline as pl
from dmbot.recaps import progress as rp
from dmbot.recaps.store import Job
from tests import recap_fixtures as fx
from tests.test_recap_jobs import NOW, WorkerHarness

NOW_S = 1_800_000_000


def row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": "queued", "status": "draft", "window_count": 0, "window_done": 0, "calls": 0,
        "errors": 0, "last_error": "", "next_at": 0, "critics": None,
        "created_at": NOW_S - 100, "updated_at": NOW_S - 50,
    }  # fmt: skip
    return {**base, **kw}


def of(**kw: Any) -> dict[str, Any]:
    kw.setdefault("created_at", NOW_S - 86400)  # (a recap that began yesterday, if not said)
    out = rp.of(row(**kw), now=NOW_S)
    assert out is not None
    return out


class Stages(unittest.TestCase):
    def test_each_step_of_the_pipeline_has_its_own_stage(self) -> None:
        self.assertEqual(of()["stage"], rp.READING)
        self.assertEqual(of(state="beats")["stage"], rp.BEATS)
        self.assertEqual(of(state="checked")["stage"], rp.WRITING)
        self.assertEqual(of(state="prose")["stage"], rp.CHECKING)
        self.assertEqual(of(state="housekeeping")["stage"], rp.FINISHING)
        self.assertEqual(
            (of(state="ready")["stage"], of(state="ready")["state"]), (rp.READY, "done")
        )

    def test_a_long_session_counts_its_parts(self) -> None:
        got = of(window_count=4, window_done=2)
        self.assertEqual(
            (got["stage"], got["done"], got["total"], got["unit"]), ("recap_reading", 2, 4, "parts")
        )
        self.assertNotIn(
            "done", of(state="beats", window_count=4, window_done=4)
        )  # a part count is for reading

    def test_the_wait_for_the_name_pass_is_its_own_stage(self) -> None:
        self.assertEqual(of(next_at=NOW_S + 20)["stage"], rp.NAMES)
        # put off after an error, or after a call was made, is not that wait
        self.assertEqual(of(next_at=NOW_S + 20, errors=1, last_error="x")["stage"], rp.READING)
        self.assertEqual(of(next_at=NOW_S + 20, calls=1)["stage"], rp.READING)

    def test_a_written_recap_waiting_to_be_checked_is_never_called_stalled(self) -> None:
        got = of(state="prose", last_error=critics.CHECK_WAIT, updated_at=NOW_S - 10 * 3600)
        self.assertEqual((got["stage"], got["state"]), (rp.CHECK_WAITING, "running"))

    def test_the_times_never_go_backwards_and_the_server_clock_comes_with_them(self) -> None:
        got = of(created_at=NOW_S - 100, updated_at=0)  # (a row never updated: it began now)
        self.assertEqual(
            (got["startedAt"], got["updatedAt"], got["serverNow"]), (NOW_S - 100,) * 2 + (NOW_S,)
        )

    def test_nothing_once_the_recap_is_posted_or_dropped(self) -> None:
        for status in ("posting", "posted", "dropped"):
            self.assertIsNone(rp.of(row(state="ready", status=status), now=NOW_S))


class Stalling(unittest.TestCase):
    def test_nothing_moving_for_longer_than_the_steps_usual_time_is_stalled(self) -> None:
        for state, limit in (("beats", 10 * 60), ("checked", 30 * 60), ("housekeeping", 10 * 60)):
            fresh = of(state=state, updated_at=NOW_S - limit + 5)
            stalled = of(state=state, updated_at=NOW_S - limit - 5)
            self.assertEqual((fresh["state"], stalled["state"]), ("running", "stalled"), state)
            self.assertEqual(stalled["reason"], "stalled")
        self.assertEqual(of(updated_at=NOW_S - 21 * 60)["state"], "stalled")  # reading, 20 minutes

    def test_a_step_that_moves_turns_it_back_to_running(self) -> None:
        self.assertEqual(of(state="beats", updated_at=NOW_S - 3600)["state"], "stalled")
        self.assertEqual(of(state="beats", updated_at=NOW_S - 5)["state"], "running")


class Failing(unittest.TestCase):
    def test_a_failure_is_a_fixed_code_never_the_stored_sentence(self) -> None:
        for sentence, code in (
            (pl.CAP, "limit"), (pl.DAILY, "daily_limit"), (pl.SERVICE, "service"),
            (pl.NOTHING, "nothing_said"), (pl.CUT_OFF, "cut_off"), (pl.NO_NAMES, "names"),
        ):  # fmt: skip
            got = of(state="failed", last_error=sentence)
            self.assertEqual((got["state"], got["reason"]), ("failed", code))
        odd = of(state="failed", last_error="Mira Voss stole the Silver Lock.")
        self.assertEqual(odd["reason"], "recap_failed")  # nothing of the recap leaks
        self.assertNotIn("Mira", repr(odd))

    def test_a_running_recap_carries_no_reason(self) -> None:
        self.assertIsNone(of(state="beats", last_error="a sentence")["reason"])


class AsThePipelineWorks(WorkerHarness):
    """A real job through the real worker: the progress moves at each step and says so."""

    def progress_of(self, job: Job, now: int) -> dict[str, Any]:
        got = rp.of(dataclasses.asdict(job), now=now)
        assert got is not None
        return got

    async def watch(self) -> list[dict[str, Any]]:
        """Record the progress after every save the worker makes, one clock tick apart."""
        seen: list[dict[str, Any]] = []
        save = self.store.update

        async def spy(*args: Any, **kw: Any) -> Any:
            self.clock.now += 10
            out = await save(*args, **kw)
            job = await self.store.get(1, self.one.id, 100)
            assert job is not None
            seen.append(self.progress_of(job, int(self.clock.now)))
            return out

        self.store.update = spy  # type: ignore[method-assign]
        return seen

    async def test_the_stage_and_the_last_move_advance_through_the_steps(self) -> None:
        happy = fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        await self.setUpWorker(happy)
        seen = await self.watch()
        job = await self.go()
        self.assertEqual(job.state, "ready")
        stages = [p["stage"] for p in seen]
        order = [s for i, s in enumerate(stages) if i == 0 or s != stages[i - 1]]
        self.assertEqual(order[-1], rp.READY)
        self.assertLess(order.index(rp.BEATS), order.index(rp.WRITING))
        self.assertLess(order.index(rp.WRITING), order.index(rp.READY))
        # the last move changes with each save, and the running clock goes with it
        moved = [p["updatedAt"] for p in seen]
        self.assertEqual(moved, sorted(moved))
        self.assertEqual(len(set(moved)), len(moved))
        self.assertEqual((seen[-1]["state"], seen[-1]["reason"]), ("done", None))
        self.assertTrue(all(p["startedAt"] == seen[0]["startedAt"] for p in seen))

    async def test_a_long_session_shows_part_x_of_y_as_each_part_is_read(self) -> None:
        from dmbot.ai import Reply  # noqa: F401  (documented: the canned answers are Replies)
        from dmbot.memory.extractor import readable
        from dmbot.recaps import beats as bt
        from dmbot.transcript.models import Line  # noqa: F401

        rows = [(str(s.number), "x", s.text) for s in readable(fx.lines())]
        windows = len(bt.windows(rows, 150))
        self.assertGreaterEqual(windows, 3)
        happy = fx.Canned(
            *[fx.BEATS_ANSWER] * windows, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER
        )
        await self.setUpWorker(happy, window_chars=150)
        seen = await self.watch()
        await self.go()
        reading = [p for p in seen if p["stage"] == rp.READING and "done" in p]
        done = [p["done"] for p in reading]  # (each part is saved more than once)
        self.assertEqual(done, sorted(done))
        self.assertEqual(sorted(set(done)), list(range(1, windows)))
        self.assertTrue(all(p["total"] == windows and p["unit"] == "parts" for p in reading))

    async def test_a_failed_recap_gives_a_code(self) -> None:
        await self.setUpWorker(fx.Canned(AIError("x"), AIError("x"), AIError("x")))
        seen = await self.watch()
        job = await self.go()
        # waiting after an error: still working, and it moved
        self.assertEqual((seen[-1]["state"], seen[-1]["reason"]), ("running", None))
        for wait in (61, 241):
            self.clock.now += wait
            job = await self.go()
        self.assertEqual(job.state, "failed")
        self.assertEqual((seen[-1]["state"], seen[-1]["reason"]), ("failed", "service"))
        self.assertEqual(seen[-1]["stage"], rp.FAILED)
        self.assertNotIn("The writing service", repr(seen[-1]))  # the code, not the sentence


del NOW
