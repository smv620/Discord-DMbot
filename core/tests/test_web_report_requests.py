"""The bot half of the website's "make the report again" (#2805): taking the oldest request, the
checks made again, the row closed with one short word, a stale one taken again, and nothing of any
story in a failed row. A stand-in for the bot; invented ids."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import web_report_requests as wrr
from tests.test_memory_store import GUILD_A, MemoryTest

DM = 7
OTHER = 99
START = 1_700_000_000


class Host:
    """Stands in for the bot: a campaign with DM 7, and a report step that gives a word."""

    def __init__(self, campaign: Any, word: str = "") -> None:
        self.word = word
        self.made: list[tuple[int, str, int]] = []
        self._campaign = campaign
        self.raises = False
        self.delay = 0.0

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Any:
        return self._campaign

    async def report_for_session_at(self, guild_id: int, campaign_id: str, started_at: int) -> str:
        if self.raises:
            raise RuntimeError("the secret story words")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.made.append((guild_id, campaign_id, started_at))
        return self.word


class Requests(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.t = float(START)
        self.store = wrr.RequestStore(self.db, clock=lambda: self.t)
        self.campaign = SimpleNamespace(id=self.c, dm_user_ids=frozenset({DM}))
        self.n = 0

    async def ask(self, *, user: int = DM, at: int = START, state: str = "waiting",
                  updated: int | None = None) -> str:  # fmt: skip
        self.n += 1
        rid = f"{self.n:032x}"
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO web_report_requests (guild_id, campaign_id, id, user_id, session_at,"
                " state, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (GUILD_A, self.c, rid, user, at, state, START, updated or START),
            )
        return rid

    async def row(self, rid: str) -> dict[str, Any]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT state, error FROM web_report_requests WHERE id = %s", (rid,)
            )
            row = await cur.fetchone()
        assert row is not None
        return dict(row)

    async def take_and_run(self, host: Host) -> bool:
        req = await self.store.claim_next(GUILD_A)
        assert req is not None
        return await wrr.run_one(host, self.store, GUILD_A, req)

    async def test_the_oldest_waiting_request_is_taken_first_and_marked_working(self) -> None:
        first, second = await self.ask(at=START), await self.ask(at=START + 1)
        req = await self.store.claim_next(GUILD_A)
        assert req is not None
        self.assertEqual((req.id, req.session_at, req.user_id), (first, START, DM))
        self.assertEqual((await self.row(first))["state"], "working")
        again = await self.store.claim_next(GUILD_A)
        assert again is not None
        self.assertEqual(again.id, second)
        self.assertIsNone(await self.store.claim_next(GUILD_A))

    async def test_a_request_taken_and_gone_quiet_is_taken_again_a_live_one_is_not(self) -> None:
        stale = await self.ask(state="working", updated=START - wrr.RETAKE_AFTER_S - 5)
        await self.ask(at=START + 1, state="working", updated=START - 30)
        req = await self.store.claim_next(GUILD_A)
        assert req is not None
        self.assertEqual(req.id, stale)
        self.assertIsNone(await self.store.claim_next(GUILD_A))

    async def test_a_made_report_closes_the_row_done_with_no_word(self) -> None:
        rid = await self.ask()
        host = Host(self.campaign)
        self.assertTrue(await self.take_and_run(host))
        self.assertEqual(await self.row(rid), {"state": "done", "error": ""})
        self.assertEqual(host.made, [(GUILD_A, self.c, START)])

    async def test_the_asker_must_still_be_a_dm_and_nothing_is_made_otherwise(self) -> None:
        rid = await self.ask(user=OTHER)
        host = Host(self.campaign)
        await self.take_and_run(host)
        self.assertEqual(await self.row(rid), {"state": "failed", "error": "not_a_dm"})
        self.assertEqual(host.made, [])

    async def test_a_campaign_that_is_gone_fails_the_row(self) -> None:
        rid = await self.ask()
        host = Host(None)
        await self.take_and_run(host)
        self.assertEqual(await self.row(rid), {"state": "failed", "error": "failed"})
        self.assertEqual(host.made, [])

    async def test_each_word_the_step_gives_back_is_the_rows_error(self) -> None:
        for word in (wrr.PLAN, wrr.LIMIT, wrr.UNAVAILABLE, wrr.FAILED):
            with self.subTest(word=word):
                rid = await self.ask(at=START + self.n + 1)
                await self.take_and_run(Host(self.campaign, word))
                self.assertEqual(await self.row(rid), {"state": "failed", "error": word})

    async def test_a_bug_ends_the_row_as_failed_with_a_word_never_the_message(self) -> None:
        rid = await self.ask()
        host = Host(self.campaign)
        host.raises = True
        with self.assertLogs("dmbot.memory.web_report_requests", "WARNING") as logs:
            self.assertTrue(await self.take_and_run(host))
        self.assertEqual(await self.row(rid), {"state": "failed", "error": "failed"})
        self.assertNotIn("secret story words", " ".join(logs.output))  # the type only

    async def test_two_requests_are_made_in_the_order_asked(self) -> None:
        await self.ask(at=START)
        await self.ask(at=START + 5)
        host = Host(self.campaign)
        await self.take_and_run(host)
        await self.take_and_run(host)
        self.assertEqual([m[2] for m in host.made], [START, START + 5])

    async def test_only_the_worker_that_holds_the_claim_can_close_a_row(self) -> None:
        rid = await self.ask()
        req = await self.store.claim_next(GUILD_A)
        assert req is not None
        taken_over = wrr.Request(req.id, req.campaign_id, req.user_id, req.session_at, 1)
        self.assertFalse(await self.store.finish(GUILD_A, taken_over, ""))
        self.assertTrue(await self.store.finish(GUILD_A, req, ""))
        self.assertEqual((await self.row(rid))["state"], "done")
        self.assertFalse(await self.store.finish(GUILD_A, req, ""))  # already closed

    async def test_a_touch_that_raises_does_not_end_the_keeping_alive(self) -> None:
        await self.ask()
        host = Host(self.campaign)
        host.delay = 0.2
        old = wrr.TOUCH_EVERY_S
        wrr.TOUCH_EVERY_S = 0.03
        calls: list[int] = []
        real = self.store.touch

        async def flaky(guild_id: int, req: wrr.Request) -> bool:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("database hiccup")
            return await real(guild_id, req)

        self.store.touch = flaky  # type: ignore[method-assign]
        try:
            req = await self.store.claim_next(GUILD_A)
            assert req is not None
            with self.assertLogs("dmbot.memory.web_report_requests", "WARNING"):
                await wrr.apply(host, self.store, GUILD_A, req)
        finally:
            wrr.TOUCH_EVERY_S = old
        self.assertGreater(len(calls), 1)  # it went on after the error

    async def test_a_running_report_touches_its_row_so_it_is_not_taken_again(self) -> None:
        rid = await self.ask()
        host = Host(self.campaign)
        host.delay = 0.15
        old = wrr.TOUCH_EVERY_S
        wrr.TOUCH_EVERY_S = 0.03
        try:
            req = await self.store.claim_next(GUILD_A)
            assert req is not None
            self.t += 500  # the clock moves on while the report is made
            await wrr.apply(host, self.store, GUILD_A, req)
        finally:
            wrr.TOUCH_EVERY_S = old
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT updated_at FROM web_report_requests WHERE id = %s", (rid,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(int(row["updated_at"]), START + 500)

    async def test_another_servers_request_is_never_taken(self) -> None:
        await self.ask()
        self.assertIsNone(await self.store.claim_next(GUILD_A + 1))

    async def test_a_failed_row_holds_only_a_short_lower_case_word(self) -> None:
        for word in (wrr.UNAVAILABLE, wrr.LIMIT, wrr.PLAN, wrr.NOT_A_DM, wrr.FAILED):
            self.assertRegex(word, r"^[a-z_]{1,40}$")


if __name__ == "__main__":
    unittest.main()
