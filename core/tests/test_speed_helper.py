"""The helper that stretches a wall-clock limit on a busy machine (#2490)."""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

from tests import speed


class Allowed(unittest.TestCase):
    def test_a_quiet_machine_gets_the_plain_limit(self) -> None:
        with patch.object(speed, "slowdown", return_value=1.0):
            self.assertEqual(speed.allowed(0.5), 0.5)

    def test_a_busy_machine_gets_more_room_by_the_worst_slowdown_seen(self) -> None:
        with patch.object(speed, "slowdown", return_value=2.0):
            self.assertEqual(speed.allowed(0.5, 3.0), 1.5)
            self.assertEqual(speed.allowed(0.5), 1.0)

    def test_the_slowdown_is_between_one_and_the_backstop(self) -> None:
        self.assertGreaterEqual(speed.slowdown(), 1.0)
        self.assertLessEqual(speed.slowdown(), speed.MOST)
        with patch.object(speed, "_work", return_value=100.0):
            self.assertEqual(speed.slowdown(), speed.MOST)  # a runaway still fails


class DatabaseSlowdown(unittest.IsolatedAsyncioTestCase):
    """A query-bound test measures the database, not only the CPU (#2694)."""

    class Conn:
        def __init__(self, took: float) -> None:
            self.took = took

        async def execute(self, sql: str) -> None:
            await asyncio.sleep(self.took)

    async def test_a_quiet_database_gets_no_extra_room(self) -> None:
        self.assertEqual(await speed.db_slowdown(self.Conn(0.0)), 1.0)

    async def test_a_starved_database_gets_room_by_how_slow_it_is(self) -> None:
        got = await speed.db_slowdown(self.Conn(speed.QUIET_DB_S * 3))
        self.assertGreater(got, 2.5)
        self.assertLess(got, 5.0)

    async def test_it_never_goes_past_the_backstop(self) -> None:
        self.assertEqual(await speed.db_slowdown(self.Conn(speed.QUIET_DB_S * 50)), speed.MOST)


class Fastest(unittest.IsolatedAsyncioTestCase):
    """One run stretched by a pause must not fail a test (#2671); slow code must still fail."""

    def test_the_best_of_several_runs_is_taken_so_one_pause_does_not_count(self) -> None:
        pauses = iter([0.2, 0.0, 0.0, 0.0, 0.0])

        def work() -> None:
            time.sleep(next(pauses))

        self.assertLess(speed.fastest(work), 0.05)

    def test_code_that_is_slow_in_every_run_is_still_slow(self) -> None:
        self.assertGreater(speed.fastest(lambda: time.sleep(0.05), runs=3), 0.04)

    async def test_the_awaited_form_takes_the_best_too(self) -> None:
        pauses = iter([0.2, 0.0, 0.0])

        async def work() -> None:
            await asyncio.sleep(next(pauses))

        self.assertLess(await speed.fastest_async(work, runs=3), 0.05)


if __name__ == "__main__":
    unittest.main()
