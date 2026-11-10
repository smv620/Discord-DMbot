"""The bot's application of the website's "Undo this upload" press (#4078): one claimed at a time,
the Discord button's own code run (so every check is made again), the outcome written as counts and
a short word, a lost claim never written over, and a retaken press. Invented names and ids."""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot import upload_undo
from dmbot.ui import upload_notice as un
from dmbot.upload_undo import UploadUndos
from tests.test_memory_names import DM, GUILD, NamesTest

NOW = int(time.time())  # (the round uses the real clock)
NOTICE = "a" * 32


class ThePureRules(unittest.TestCase):
    def test_each_refusal_ends_with_its_word_and_nothing_was_taken_back(self) -> None:
        for answer, word in (
            (un.GONE, "gone"),
            (un.NOT_YOURS, "not_yours"),
            (un.ALREADY, "already"),
            (un.TOO_LATE, "too_late"),
            (un.COULD_NOT, "failed"),
        ):
            self.assertEqual(upload_undo.outcome(un.Undone(answer)), (word, 0), answer)

    def test_everything_or_some_taken_back_is_done_with_the_count(self) -> None:
        self.assertEqual(upload_undo.outcome(un.Undone(un.NOTHING_LEFT, 3, 3)), ("", 3))
        partly = un.PARTLY.format(done=2, total=3)
        self.assertEqual(upload_undo.outcome(un.Undone(partly, 2, 3)), ("", 2))

    def test_an_undo_that_stopped_part_way_is_failed_but_says_how_many(self) -> None:
        stopped = un.STOPPED.format(done=1, total=3)
        self.assertEqual(upload_undo.outcome(un.Undone(stopped, 1, 3, stopped=True)), ("failed", 1))

    def test_every_word_fits_the_column(self) -> None:
        self.assertTrue(all(len(w) <= 40 for w in upload_undo.WORDS))


class Undos(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = float(NOW)
        self.undos = UploadUndos(self.db, clock=lambda: self.now)
        self.n = 0

    async def queue(self, user: int = DM, *, at: int = NOW, notice: str | None = None) -> str:
        self.n += 1
        rid = f"{self.n:032x}"
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO upload_undos (guild_id, campaign_id, id, user_id, notice_id,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (GUILD, self.campaign.id, rid, user, notice or self.notice_for(self.n), at),
            )  # fmt: skip
        return rid

    @staticmethod
    def notice_for(n: int) -> str:
        return f"{n:032x}".replace("0", "b")  # (32 hex characters, as the column checks)

    async def row(self, rid: str) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT * FROM upload_undos WHERE id = %s", (rid,))
            row = await cur.fetchone()
        assert row is not None
        return dict(row)

    def fake_bot(self) -> Any:
        return SimpleNamespace(
            memory=object(),
            upload_notices=object(),
            campaigns=SimpleNamespace(db=self.db),
        )

    async def round(self, result: un.Undone | Exception) -> tuple[int, AsyncMock]:
        undo = (
            AsyncMock(side_effect=result)
            if isinstance(result, Exception)
            else AsyncMock(return_value=result)
        )
        with patch.object(un, "undo_upload_result", undo):
            took = await upload_undo.run_round(self.fake_bot(), GUILD)
        return took, undo

    async def test_the_oldest_waiting_press_is_claimed_once(self) -> None:
        first = await self.queue(at=NOW - 20)
        await self.queue(at=NOW - 10)
        got = await self.undos.claim_next(GUILD)
        assert got is not None
        self.assertEqual((got.id, got.tries), (first, 1))
        again = await self.undos.claim_next(GUILD)
        assert again is not None and again.id != first
        self.assertIsNone(await self.undos.claim_next(GUILD))

    async def test_a_press_that_stopped_moving_is_retaken_and_an_old_one_never(self) -> None:
        rid = await self.queue()
        await self.undos.claim_next(GUILD)
        self.now += upload_undo.RETAKE_AFTER_S + 5
        retaken = await self.undos.claim_next(GUILD)
        assert retaken is not None and (retaken.id, retaken.tries) == (rid, 2)
        self.now += upload_undo.GIVE_UP_S + 5
        self.assertIsNone(await self.undos.claim_next(GUILD))

    async def test_a_done_round_trip_writes_the_count_and_runs_the_buttons_code(self) -> None:
        rid = await self.queue()
        took, undo = await self.round(un.Undone(un.NOTHING_LEFT, 3, 3))
        self.assertEqual(took, 1)
        args = undo.await_args.args
        self.assertEqual((args[1], args[3], args[4]), (GUILD, self.notice_for(1), DM))
        row = await self.row(rid)
        self.assertEqual((row["state"], row["error"], row["undone"]), ("done", "", 3))

    async def test_a_refusal_is_failed_with_its_word_and_never_its_words(self) -> None:
        rid = await self.queue()
        await self.round(un.Undone(un.TOO_LATE))
        row = await self.row(rid)
        self.assertEqual((row["state"], row["error"], row["undone"]), ("failed", "too_late", 0))
        self.assertNotIn("7 days", row["error"])

    async def test_an_unexpected_error_is_failed(self) -> None:
        rid = await self.queue()
        await self.round(RuntimeError("boom"))
        row = await self.row(rid)
        self.assertEqual((row["state"], row["error"]), ("failed", "failed"))

    async def test_a_lost_row_is_not_applied(self) -> None:
        """The door fails a press older than 30 minutes ("lost"): it is never claimed."""
        rid = await self.queue(at=NOW - upload_undo.GIVE_UP_S - 60)
        took, undo = await self.round(un.Undone(un.NOTHING_LEFT, 1, 1))
        self.assertEqual(took, 0)
        undo.assert_not_awaited()
        self.assertEqual((await self.row(rid))["state"], "waiting")

    async def test_a_late_write_after_the_claim_was_lost_does_not_overwrite(self) -> None:
        rid = await self.queue()
        mine = await self.undos.claim_next(GUILD)
        assert mine is not None
        self.now += upload_undo.RETAKE_AFTER_S + 5
        other = await self.undos.claim_next(GUILD)  # another worker retakes it
        assert other is not None
        self.assertFalse(await self.undos.end(GUILD, mine, ok=True, undone=9))
        self.assertEqual((await self.row(rid))["state"], "working")
        async with self.db.guild(GUILD) as conn:  # the door wrote "lost"
            await conn.execute(
                "UPDATE upload_undos SET state = 'failed', error = 'lost' WHERE id = %s", (rid,)
            )
        self.assertFalse(await self.undos.end(GUILD, other, ok=True, undone=9))
        row = await self.row(rid)
        self.assertEqual((row["state"], row["error"], row["undone"]), ("failed", "lost", 0))

    async def test_an_undo_tried_too_often_is_failed_not_applied(self) -> None:
        rid = await self.queue()
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE upload_undos SET tries = %s", (upload_undo.MAX_TRIES,))
        took, undo = await self.round(un.Undone(un.NOTHING_LEFT, 1, 1))
        undo.assert_not_awaited()
        self.assertEqual((took, (await self.row(rid))["error"]), (1, "failed"))

    async def test_a_long_undo_moves_its_claim_while_it_runs(self) -> None:
        rid = await self.queue()
        press = await self.undos.claim_next(GUILD)
        assert press is not None
        before = press.claimed_at
        self.now += 7
        self.assertTrue(await self.undos.moved(GUILD, press))
        self.assertGreater(press.claimed_at, before)
        self.assertEqual((await self.row(rid))["claimed_at"], press.claimed_at)
        # a worker that has lost the claim cannot move it
        self.now += upload_undo.RETAKE_AFTER_S + 5
        await self.undos.claim_next(GUILD)
        self.assertFalse(await self.undos.moved(GUILD, press))

    async def test_the_heartbeat_runs_during_the_undo(self) -> None:
        await self.queue()
        slow = asyncio.Event()

        async def slow_undo(*_a: Any, **_k: Any) -> un.Undone:
            await asyncio.sleep(0.05)
            slow.set()
            return un.Undone(un.NOTHING_LEFT, 1, 1)

        with (
            patch.object(un, "undo_upload_result", slow_undo),
            patch.object(upload_undo, "WATCH_EVERY_S", 0.01),
            patch.object(UploadUndos, "moved", AsyncMock(return_value=True)) as moved,
        ):
            await upload_undo.run_round(self.fake_bot(), GUILD)
        self.assertGreaterEqual(moved.await_count, 1)

    async def test_without_memory_nothing_is_taken(self) -> None:
        rid = await self.queue()
        me = self.fake_bot()
        me.memory = None
        self.assertEqual(await upload_undo.run_round(me, GUILD), 0)
        self.assertEqual((await self.row(rid))["state"], "waiting")
