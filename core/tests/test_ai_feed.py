"""The AI feed (#3710): the first 500 characters of each prompt and reply of a lore read or an
import, for the DM's live windows. Cut before anything is written, DM-only data, deleted by a sweep
and by Stop recording me, never in a backup, never slowing or failing a real call. Invented
fixtures only."""

from __future__ import annotations

import asyncio
import logging
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

import psycopg

from dmbot import ai_feed
from dmbot.ai import AIError, AIModels, AIModelTier, AnthropicClient, Reply
from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from tests.pg import DatabaseTest

GUILD, DM, PLAYER = 91, 5, 6
LONG = "The old lighthouse keeper says " + "x" * 600


async def settle() -> None:
    """Let the feed's scheduled writes finish (they run beside the call, never in it)."""
    for _ in range(50):
        if not ai_feed._tasks:
            return
        await asyncio.sleep(0.01)


class Pure(unittest.TestCase):
    def test_the_cut_keeps_500_characters_then_marks_more(self) -> None:
        self.assertEqual(ai_feed.cut("a" * 500), "a" * 500)
        cut = ai_feed.cut("a" * 501)
        self.assertEqual(cut, "a" * 500 + "[...]")
        self.assertEqual(len(ai_feed.cut(LONG)), 505)

    def test_nothing_is_captured_with_no_job_or_with_the_feed_off(self) -> None:
        ai_feed.configure(None, on=True)
        self.assertIsNone(ai_feed.begin("hello", "fast"))

    def test_a_job_is_set_for_its_block_only_and_a_nested_one_does_not_leak(self) -> None:
        self.assertIsNone(ai_feed._current.get())
        with ai_feed.job(1, "c1", "lore", "u1", step="reading part 1 of 2"):
            with ai_feed.job(1, "c2", "import", "i1"):
                self.assertEqual(ai_feed._current.get().campaign_id, "c2")  # type: ignore[union-attr]
            self.assertEqual(ai_feed._current.get().job_id, "u1")  # type: ignore[union-attr]
            ai_feed.step("reading part 2 of 2")
            self.assertEqual(ai_feed._current.get().step, "reading part 2 of 2")  # type: ignore[union-attr]
        self.assertIsNone(ai_feed._current.get())


class WithFeed(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(ai_feed.configure, None, on=False)
        self.campaigns = CampaignStore(self.db)
        self.camp = await self.campaigns.create(GUILD, "Feed", DM)
        ai_feed.configure(self.db, on=True)

    async def rows(self, camp: str | None = None) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT * FROM ai_feed WHERE guild_id = %s AND campaign_id = %s ORDER BY n",
                (GUILD, camp or self.camp.id),
            )
            return [dict(r) for r in await cur.fetchall()]

    def job(self, step: str = "reading part 1 of 3") -> Any:
        return ai_feed.job(GUILD, self.camp.id, "lore", "up1", step=step)


class TheFeed(WithFeed):
    async def test_a_prompt_and_its_reply_are_cut_and_matched_by_number(self) -> None:
        with self.job():
            first = ai_feed.begin(LONG, "careful")
            second = ai_feed.begin("short", "fast")
            await settle()
            ai_feed.done(first, "y" * 700)
            ai_feed.failed(second, "The AI service couldn't do that.")
            await settle()
        marker, one, two = await self.rows()
        self.assertEqual(marker["n"], 0)
        self.assertEqual((one["n"], two["n"]), (1, 2))
        self.assertEqual(one["prompt"], ai_feed.cut(LONG))
        self.assertEqual(len(one["prompt"]), 505)
        self.assertEqual((one["reply"], one["state"]), ("y" * 500 + "[...]", "done"))
        self.assertEqual(
            (two["state"], two["reason"]), ("failed", "The AI service couldn't do that.")
        )
        self.assertEqual((one["service"], one["model_label"]), ("Anthropic", "larger model"))
        self.assertEqual(one["step"], "reading part 1 of 3")

    async def test_the_table_itself_refuses_a_longer_text(self) -> None:
        with self.job():
            ai_feed.begin("hi", "fast")
            await settle()
        with self.assertRaises(psycopg.errors.CheckViolation):
            async with self.db.guild(GUILD) as conn:
                await conn.execute(
                    "UPDATE ai_feed SET prompt = %s WHERE guild_id = %s AND n = 1",
                    ("x" * 506, GUILD),
                )

    async def test_calls_started_at_the_same_moment_get_different_numbers(self) -> None:
        with self.job():
            entries = [ai_feed.begin(f"call {n}", "fast") for n in range(12)]
            await settle()
            for e in entries:
                ai_feed.done(e, "ok")
            await settle()
        numbers = sorted(r["n"] for r in await self.rows() if r["n"] > 0)
        self.assertEqual(numbers, list(range(1, 13)))

    async def test_with_the_feed_off_nothing_is_written(self) -> None:
        ai_feed.configure(self.db, on=False)
        with self.job():
            self.assertIsNone(ai_feed.begin("secret words", "fast"))
        await settle()
        self.assertEqual(await self.rows(), [])

    async def test_an_image_is_shown_as_the_word_image(self) -> None:
        with self.job():
            ai_feed.begin("look at this", "fast", images=[object()])
            await settle()
        [_, row] = await self.rows()
        self.assertTrue(row["prompt"].startswith("[image]"))

    async def test_a_feed_write_that_hangs_never_holds_up_the_call_and_is_counted(self) -> None:
        async def hang(*_a: Any, **_k: Any) -> None:
            await asyncio.sleep(30)

        client = AnthropicClient("sk-test-key", AIModels.same("m"))
        reply = Reply("answer", False)
        with (
            patch.object(ai_feed, "WRITE_TIMEOUT_S", 0.1),
            patch.object(ai_feed, "_insert", hang),
            patch.object(client, "_complete", AsyncMock(return_value=reply)),
            self.job(),
        ):
            got = await asyncio.wait_for(
                client.complete("system", LONG, tier=AIModelTier.FAST), timeout=1
            )
            self.assertIs(got, reply)  # the call finished long before the write timed out
            await settle()
        self.assertEqual((ai_feed._pending, len(ai_feed._tasks)), (0, 0))
        [marker] = await self.rows()
        self.assertEqual(
            (marker["n"], marker["lost"]), (0, 1)
        )  # counted, "Some calls aren't shown"

    async def test_a_failed_feed_write_does_not_fail_the_call_and_the_real_error_still_comes(
        self,
    ) -> None:
        client = AnthropicClient("sk-test-key", AIModels.same("m"))
        with (
            patch.object(ai_feed, "_insert", AsyncMock(side_effect=RuntimeError("db down"))),
            patch.object(client, "_complete", AsyncMock(side_effect=AIError("It broke."))),
            self.job(),
        ):
            with self.assertRaises(AIError):
                await client.complete("system", "text", tier=AIModelTier.FAST)
            await settle()

    async def test_no_key_or_instruction_text_is_stored_and_nothing_is_logged(self) -> None:
        client = AnthropicClient("sk-test-key-9999", AIModels.same("m"))
        with (
            patch.object(client, "_complete", AsyncMock(return_value=Reply("fine", False))),
            self.job(),
            self.assertLogs(level="DEBUG") as logged,
        ):
            logging.getLogger("probe").info("probe")  # (assertLogs needs one record)
            await client.complete("INSTRUCTIONS-123", "the lore words", tier=AIModelTier.FAST)
            await settle()
        every = " ".join(str(v) for r in await self.rows() for v in r.values())
        self.assertNotIn("sk-test-key-9999", every)
        self.assertNotIn("INSTRUCTIONS-123", every)
        self.assertIn("the lore words", every)
        self.assertFalse(any("the lore words" in line for line in logged.output))

    async def test_a_call_outside_a_job_is_not_captured(self) -> None:
        client = AnthropicClient("sk-test-key", AIModels.same("m"))
        with patch.object(client, "_complete", AsyncMock(return_value=Reply("fine", False))):
            await client.complete("s", "no job here", tier=AIModelTier.FAST)
        await settle()
        self.assertEqual(await self.rows(), [])


class TheEdges(WithFeed):
    async def test_a_reply_that_comes_before_the_slow_row_is_written_is_still_kept(self) -> None:
        real = ai_feed._insert

        async def slow(entry: Any) -> None:
            await asyncio.sleep(0.2)
            await real(entry)

        with patch.object(ai_feed, "_insert", slow), self.job():
            entry = ai_feed.begin("a question", "fast")
            ai_feed.done(entry, "the answer")  # the call ended before the row was written
            await settle()
        [_, row] = await self.rows()
        self.assertEqual((row["state"], row["reply"]), ("done", "the answer"))

    async def test_too_many_writes_waiting_are_skipped_and_counted_not_queued(self) -> None:
        gate = asyncio.Event()

        async def stuck(entry: Any) -> None:
            await gate.wait()

        with patch.object(ai_feed, "MAX_PENDING", 2), patch.object(ai_feed, "_insert", stuck):
            with self.job():
                entries = [ai_feed.begin(f"call {n}", "fast") for n in range(4)]
            self.assertEqual([e is None for e in entries], [False, False, True, True])
            self.assertLessEqual(ai_feed._pending, 2)
            gate.set()
            await settle()
        await asyncio.sleep(0.1)
        [marker] = await self.rows()
        self.assertEqual(marker["lost"], 2)

    async def test_the_real_error_and_a_cancel_come_through_unchanged_and_are_noted(self) -> None:
        from dmbot.ai import AIOutOfFunds

        client = AnthropicClient("sk-test-key", AIModels.same("m"))
        for error in (AIOutOfFunds("No money."), asyncio.CancelledError()):
            with (
                patch.object(client, "_complete", AsyncMock(side_effect=error)),
                self.job(),
            ):
                with self.assertRaises(type(error)):
                    await client.complete("s", "text", tier=AIModelTier.FAST)
                await settle()
        failed = [r for r in await self.rows() if r["n"] > 0]
        self.assertEqual([r["state"] for r in failed], ["failed", "failed"])
        self.assertEqual(failed[1]["reason"], "Stopped before it answered.")

    async def test_a_write_in_flight_when_a_person_stops_is_dropped(self) -> None:
        with self.job():
            entry = ai_feed.begin("words of someone who then stops", "fast")
            ai_feed.forget_server(GUILD)  # Stop recording me, before the write ran
            await settle()
            ai_feed.done(entry, "reply")
            await settle()
        self.assertEqual(await self.rows(), [])

    async def test_the_bookkeeping_row_moves_with_the_job_so_the_sweep_never_takes_it(self) -> None:
        with self.job():
            ai_feed.begin("first", "fast", wall=1_000_000)
            await settle()
            ai_feed.begin("second", "fast", wall=1_900_000_000)
            await settle()
        marker = (await self.rows())[0]
        self.assertEqual((marker["n"], marker["next_n"], marker["at"]), (0, 2, 1_900_000_000))

    async def test_a_dropped_call_row_is_not_a_call_the_reader_would_show(self) -> None:
        # (the migration says it: n = 0 is the job's bookkeeping, a reader shows n > 0 only)
        with self.job():
            ai_feed.begin("one", "fast")
            await settle()
        shown = [r for r in await self.rows() if r["n"] > 0]
        self.assertEqual([r["prompt"] for r in shown], ["one"])


class TheLifeOfAnEntry(WithFeed):
    async def seed(self, at: int) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO ai_feed (guild_id, campaign_id, kind, job_id, n, at, prompt, state)"
                " VALUES (%s, %s, 'lore', 'u', 1, %s, 'p', 'done')",
                (GUILD, self.camp.id, at),
            )

    async def test_the_sweep_deletes_what_is_older_than_a_day_and_nothing_else(self) -> None:
        now = 2_000_000_000
        await self.seed(now - 24 * 3600 - 5)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO ai_feed (guild_id, campaign_id, kind, job_id, n, at, prompt, state)"
                " VALUES (%s, %s, 'lore', 'u', 2, %s, 'p', 'done')",
                (GUILD, self.camp.id, now - 60),
            )
        self.assertEqual(await ai_feed.sweep(self.db, GUILD, now=now), 1)
        self.assertEqual([r["n"] for r in await self.rows()], [2])

    async def test_stop_recording_me_deletes_every_entry_of_the_server_at_once(self) -> None:
        await self.seed(1_900_000_000)
        other = await self.campaigns.create(GUILD, "Second", DM)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO ai_feed (guild_id, campaign_id, kind, job_id, n, at, prompt, state)"
                " VALUES (%s, %s, 'import', 'i', 1, 1900000000, 'p', 'done')",
                (GUILD, other.id),
            )
        consent = ConsentStore(self.db)
        await consent.grant(GUILD, PLAYER)
        await consent.revoke(GUILD, PLAYER)
        self.assertEqual(await self.rows(), [])
        self.assertEqual(await self.rows(other.id), [])

    async def test_a_deleted_campaign_takes_its_entries(self) -> None:
        await self.seed(1_900_000_000)
        await self.campaigns.delete(GUILD, self.camp.id)
        self.assertEqual(await self.rows(), [])
