"""Cleaning imported sessions side by side (#4551): one campaign's sessions go one after another,
up to the server's cap of campaigns together. A stand-in store and runner; no database."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from dmbot.transcript.import_job import ImportJob


class Store:
    def __init__(self, waiting: list[tuple[str, str]]) -> None:
        self._waiting = waiting

    async def resume(self, guild_id: int) -> int:
        return 0

    async def waiting(self, guild_id: int) -> list[tuple[str, str]]:
        return self._waiting

    async def claim(self, guild_id: int, session_id: str) -> bool:
        return session_id != "taken"


def job(waiting: list[tuple[str, str]], log: list[str], gauge: dict[str, int]) -> Any:
    made: Any = object.__new__(ImportJob)
    made.store = Store(waiting)

    async def run(guild_id: int, campaign_id: str, session_id: str) -> None:
        gauge["now"] += 1
        gauge["peak"] = max(gauge["peak"], gauge["now"])
        log.append(f"start {session_id}")
        await asyncio.sleep(0.01)
        log.append(f"end {session_id}")
        gauge["now"] -= 1

    made.run = run
    return made


WAITING = [("a", "a1"), ("a", "a2"), ("b", "b1"), ("c", "c1")]


class RoundOfImports(unittest.IsolatedAsyncioTestCase):
    async def test_by_default_one_campaign_at_a_time(self) -> None:
        log: list[str] = []
        gauge = {"now": 0, "peak": 0}
        done = await job(WAITING, log, gauge).run_round(1)
        self.assertEqual((done, gauge["peak"]), (4, 1))

    async def test_a_cap_of_two_runs_two_campaigns_together_and_one_campaigns_sessions_in_order(
        self,
    ) -> None:
        log: list[str] = []
        gauge = {"now": 0, "peak": 0}
        done = await job(WAITING, log, gauge).run_round(1, at_once=2)
        self.assertEqual((done, gauge["peak"]), (4, 2))
        self.assertLess(log.index("end a1"), log.index("start a2"))  # (one campaign: in order)

    async def test_a_session_another_bot_holds_is_skipped(self) -> None:
        log: list[str] = []
        gauge = {"now": 0, "peak": 0}
        done = await job([("a", "taken"), ("b", "b1")], log, gauge).run_round(1, at_once=5)
        self.assertEqual(done, 1)
        self.assertNotIn("start taken", log)
