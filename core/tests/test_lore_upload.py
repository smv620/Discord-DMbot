"""A lore upload from the website is read by the bot (#3022): the same read as Discord's Find names,
once, with the text blanked when it ends, and every refusal as one word. Invented names and text."""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot import lore_upload
from dmbot.memory import lore_queue as q
from dmbot.memory.lore_queue import LoreQueue
from dmbot.memory.lore_read import LORE_TIME_LIMIT_S
from dmbot.ui import name_background as ui_bg
from dmbot.ui import name_lists
from dmbot.ui.name_lists import Upload, _ai_busy, _ai_reads
from tests import lore_fake
from tests.test_background_connections import LINKS, NAMES, TEXT, name
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest

SECRET_WORDS = "Mirelle the Pale serves the Order of the Grey Hand"


class TheWorker(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _ai_busy.clear()
        _ai_reads.clear()
        ai = lore_fake.lore_ai(json.dumps({"names": NAMES, "connections": LINKS}))
        self.bot.background_ai = ai  # type: ignore[assignment]
        self.bot.background_read_ai = ai  # type: ignore[assignment]
        self.ai = ai
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.campaigns.record_confirmation = AsyncMock()  # type: ignore[method-assign]
        from dmbot.memory.backstory_links import BackstoryLinks

        self.bot.backstory_links = BackstoryLinks(self.db)
        self.queue = LoreQueue(self.db)

    async def asyncTearDown(self) -> None:
        _ai_busy.clear()
        _ai_reads.clear()
        await super().asyncTearDown()

    async def add(
        self,
        *,
        text: str = TEXT,
        link: str = "",
        source: str = "paste",
        user: int = DM,
        state: str = "waiting",
    ) -> str:
        now = int(time.time())
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, text, link,"
                " choice, rights_by, rights_at, created_at, updated_at, state) VALUES"
                " (%s, %s, replace(gen_random_uuid()::text, '-', ''), %s, %s, %s, %s, 'world', %s,"
                " %s, %s, %s, %s) RETURNING id",
                (GUILD, self.campaign.id, user, source, text, link, user, now, now, now, state),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def row(self, upload_id: str) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT * FROM lore_uploads WHERE guild_id = %s AND id = %s", (GUILD, upload_id)
            )
            return dict(await cur.fetchone() or {})

    async def work(self) -> q.Claim:
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        await lore_upload.run_upload(self.bot, self.queue, claim, GUILD)
        return claim

    async def entries(self) -> int:
        return len(await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"]))

    async def test_a_queued_upload_is_read_once_and_the_text_is_blanked(self) -> None:
        uid = await self.add()
        await self.work()
        done = await self.row(uid)
        self.assertEqual((done["state"], done["error"], done["stage"]), ("done", "", "done"))
        self.assertGreater(done["names_found"], 0)
        self.assertEqual((done["text"], done["link"]), ("", ""))
        self.assertGreater(await self.entries(), 0)  # names wait in Check new names
        self.assertIsNone(await self.queue.claim_next(GUILD))  # nothing is read a second time

    async def test_the_claims_choice_reaches_the_read_and_the_flags(self) -> None:
        await self.add()  # (the page said: world general knowledge)
        asked: list[dict[str, Any]] = []
        real = ui_bg.read_background

        async def spy(*args: Any, **kwargs: Any) -> Any:
            asked.append(kwargs)
            return await real(*args, **kwargs)

        with patch.object(lore_upload, "read_background", spy):
            await self.work()
        self.assertEqual([k["choice"] for k in asked], ["world"])
        names = await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
        self.assertTrue(names and not any(e.hidden_from_players for e in names))

    async def test_a_sender_who_stopped_being_a_dm_reads_nothing(self) -> None:
        uid = await self.add(user=MANAGER)  # (a manager is no DM of the campaign)
        await self.work()
        done = await self.row(uid)
        self.assertEqual((done["state"], done["error"]), ("failed", q.NOT_ALLOWED))
        self.assertEqual((done["text"], await self.entries()), ("", 0))

    async def test_an_over_long_text_fails_with_its_word_and_reads_nothing(self) -> None:
        uid = await self.add()
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        long = dataclasses.replace(claim, text="x" * (lore_upload.TEXT_MAX + 1))
        await lore_upload.run_upload(self.bot, self.queue, long, GUILD)
        done = await self.row(uid)
        self.assertEqual((done["state"], done["error"]), ("failed", q.TOO_LONG))
        self.assertEqual((done["text"], await self.entries()), ("", 0))

    async def test_no_ai_fails_with_its_word(self) -> None:
        uid = await self.add()
        self.bot.background_ai = None
        await self.work()
        self.assertEqual((await self.row(uid))["error"], q.NO_AI)

    async def test_a_claim_taken_again_is_not_read_twice(self) -> None:
        uid = await self.add()
        first = await self.queue.claim_next(GUILD)
        assert first is not None
        async with self.db.guild(GUILD) as conn:  # the first worker went quiet
            await conn.execute("UPDATE lore_uploads SET updated_at = updated_at - 700")
        await self.queue.sweep(GUILD)
        second = await self.queue.claim_next(GUILD)
        assert second is not None
        self.assertNotEqual(first.claimed_at, second.claimed_at)
        # the first worker finishing late writes nothing: it no longer holds the claim
        self.assertFalse(await self.queue.finish(GUILD, first, names=9))
        await lore_upload.run_upload(self.bot, self.queue, second, GUILD)
        self.assertEqual((await self.row(uid))["state"], "done")
        reads = [c for c in self.ai.complete.await_args_list if not c.kwargs.get("schema")]
        self.assertLessEqual(len(reads), 2)  # pass 1 once for the one read, not for both

    async def test_the_sweep_ends_what_nobody_took_and_blanks_it(self) -> None:
        uid = await self.add()
        old = await self.add(text="another", state="failed")
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE lore_uploads SET created_at = created_at - 2000,"
                " updated_at = updated_at - 2000 WHERE id = %s",
                (uid,),
            )
            await conn.execute(
                "UPDATE lore_uploads SET created_at = created_at - 90000 WHERE id = %s",
                (old,),
            )
        await self.queue.sweep(GUILD)
        done = await self.row(uid)
        self.assertEqual((done["state"], done["error"], done["text"]), ("failed", "not_taken", ""))
        self.assertEqual(await self.row(old), {})  # older than a day: gone

    async def test_a_released_upload_is_not_failed_as_never_taken(self) -> None:
        recent = await self.add()
        async with self.db.guild(GUILD) as conn:  # made 40 minutes ago, put back 5 minutes ago
            await conn.execute(
                "UPDATE lore_uploads SET created_at = created_at - 2400,"
                " updated_at = updated_at - 300 WHERE id = %s",
                (recent,),
            )
        await self.queue.sweep(GUILD)
        self.assertEqual((await self.row(recent))["state"], "waiting")
        async with self.db.guild(GUILD) as conn:  # ...but quiet for 31 minutes
            await conn.execute(
                "UPDATE lore_uploads SET updated_at = updated_at - 1560 WHERE id = %s", (recent,)
            )
        await self.queue.sweep(GUILD)
        done = await self.row(recent)
        self.assertEqual((done["state"], done["error"], done["text"]), ("failed", "not_taken", ""))

    async def test_a_link_is_read_by_the_shared_reader_and_blanked(self) -> None:
        uid = await self.add(text="", link="https://example.org/lore", source="link")
        got = (Upload(TEXT, "the linked page", True), None)
        with patch.object(lore_upload, "read_link_once", AsyncMock(return_value=got)) as opened:
            await self.work()
        opened.assert_awaited_once()
        done = await self.row(uid)
        self.assertEqual((done["state"], done["link"]), ("done", ""))

    async def test_a_link_that_cannot_be_opened_fails_with_its_word(self) -> None:
        uid = await self.add(text="", link="https://example.org/lore", source="link")
        with patch.object(
            lore_upload, "read_link_once", AsyncMock(return_value=(None, "Not public."))
        ):
            await self.work()
        done = await self.row(uid)
        self.assertEqual(
            (done["state"], done["error"], done["link"]), ("failed", "link_failed", "")
        )

    async def test_a_busy_server_puts_the_claim_back_for_the_next_pass(self) -> None:
        uid = await self.add()
        with patch.object(lore_upload, "start_ai_read", return_value=name_lists.BUSY):
            await self.work()
        self.assertEqual((await self.row(uid))["state"], "waiting")
        self.assertIsNotNone(await self.queue.claim_next(GUILD))

    async def test_nothing_from_the_document_is_logged(self) -> None:
        await self.add()
        with self.assertLogs("dmbot", level=logging.DEBUG) as logged:
            await self.work()
        everything = "\n".join(r.getMessage() for r in logged.records)
        for word in ("Mirelle", "Grey Hand", "Brannoch", SECRET_WORDS):
            self.assertNotIn(word, everything)

    async def test_a_cancelled_read_puts_the_claim_back_for_the_next_start(self) -> None:
        import asyncio

        uid = await self.add()
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        started = asyncio.Event()

        async def hang(*_a: Any, **_k: Any) -> Any:
            started.set()
            await asyncio.sleep(3600)

        with patch.object(lore_upload, "read_background", hang):
            task = asyncio.ensure_future(lore_upload.run_upload(self.bot, self.queue, claim, GUILD))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual((await self.row(uid))["state"], "waiting")

    async def test_a_hung_read_ends_as_out_of_time_with_the_text_blanked(self) -> None:
        import asyncio

        uid = await self.add()

        async def hang(*_a: Any, **_k: Any) -> Any:
            await asyncio.sleep(3600)

        with (
            patch.object(lore_upload, "read_background", hang),
            patch.object(lore_upload, "READ_MARGIN_S", -LORE_TIME_LIMIT_S),
        ):
            await asyncio.wait_for(self.work(), 30)
        done = await self.row(uid)
        self.assertEqual(
            (done["state"], done["error"], done["text"]), ("failed", q.OUT_OF_TIME, "")
        )

    async def test_an_error_mid_read_ends_the_row_failed_and_blanked(self) -> None:
        uid = await self.add()
        boom = AsyncMock(side_effect=RuntimeError("secret words of the document"))
        with patch.object(lore_upload, "read_background", boom):
            await self.work()
        done = await self.row(uid)
        self.assertEqual((done["state"], done["error"], done["text"]), ("failed", q.FAILED, ""))

    async def test_a_confirmer_who_stopped_being_a_dm_reads_nothing(self) -> None:
        uid = await self.add()
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE lore_uploads SET rights_by = %s", (MANAGER,))
        await self.work()
        self.assertEqual((await self.row(uid))["error"], q.NOT_ALLOWED)

    async def test_a_busy_link_reader_waits_without_using_the_days_allowance(self) -> None:
        uid = await self.add(text="", link="https://example.org/lore", source="link")
        with patch.object(lore_upload, "link_is_busy", return_value=True):
            for _ in range(3):
                claim = await self.queue.claim_next(GUILD)
                assert claim is not None
                await lore_upload.run_upload(self.bot, self.queue, claim, GUILD)
        self.assertEqual((await self.row(uid))["state"], "waiting")
        self.assertEqual(sum(_ai_reads.values()), 0)

    async def test_the_days_allowance_is_shared_and_used_up(self) -> None:
        uid = await self.add()
        for _ in range(name_lists.AI_READS_PER_DAY):
            self.assertIsNone(name_lists.start_ai_read(GUILD, time.time()))
            name_lists.end_ai_read(GUILD)
        await self.work()
        self.assertEqual((await self.row(uid))["error"], q.NO_AI)

    async def test_a_lost_claim_stops_the_read_and_writes_nothing(self) -> None:
        import asyncio

        uid = await self.add()
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        async with self.db.guild(GUILD) as conn:  # another worker holds it now
            await conn.execute("UPDATE lore_uploads SET claimed_at = claimed_at + 1")

        async def slow(*_a: Any, **_k: Any) -> Any:
            await asyncio.sleep(3600)

        with (
            patch.object(lore_upload, "read_background", slow),
            patch.object(lore_upload, "PROGRESS_EVERY_S", 0.05),
        ):
            await asyncio.wait_for(lore_upload.run_upload(self.bot, self.queue, claim, GUILD), 30)
        self.assertEqual((await self.row(uid))["state"], "working")  # (its new owner ends it)

    async def test_nothing_found_is_a_word_not_a_silent_done(self) -> None:
        uid = await self.add()
        empty = lore_fake.lore_ai(json.dumps({"names": [], "connections": []}))
        self.bot.background_ai = self.bot.background_read_ai = empty  # type: ignore[assignment]
        await self.work()
        self.assertEqual((await self.row(uid))["error"], q.NOTHING_FOUND)


_ = name  # (shared fixtures)
