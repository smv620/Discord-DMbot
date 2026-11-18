"""The bot counts a staged lore PDF before the DM's yes (#4762 step 2, migration 0547): only the
DM's own staged file, no AI (the reader is a stand-in here and nothing else is called), the three
numbers written back and the file's words never kept, a file that expired or was replaced answers
"gone", and a file the reader refuses says why in plain words. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import lore_pdfs as lp
from dmbot.memory import web_actions as wa
from dmbot.memory.name_documents import DocumentError
from dmbot.pictures.bucket import BucketError
from dmbot.web import lore_pdf
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_lore_pdf import FakeBucket, Staged

PDF = b"%PDF-1.7\n" + b"x" * 200


class StagingGet(FakeBucket):
    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise BucketError("gone")
        return self.objects[key]


class Estimating(Staged):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.staging = StagingGet()
        self.bucket = self.staging
        self.counts: list[bytes] = []
        self.found = lp.Counted(chars=40_000, pages=12, pictures=5)

    async def count(self, raw: bytes) -> lp.Counted:
        self.counts.append(raw)
        return self.found

    def parts(self, count: Any = "own") -> lp.LorePdfs:
        return lp.LorePdfs(
            self.staging, lp.StagedPdfs(self.db), self.count if count == "own" else count, 100 << 20
        )

    async def bot_loop(self, parts: lp.LorePdfs | None = None) -> list[wa.Outcome]:
        """The bot's loop with this test's reader: claim, apply, finish."""
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(
                self.memory, self.g, {ALICE.id}, action, lore_pdfs=parts or self.parts()
            )
            await self.actions.finish(
                self.g, action, error=outcome.error, result_id=outcome.result_id,
                result_other=outcome.result_other, result_counts=outcome.counts,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def test_the_dm_counts_the_staged_file_and_the_numbers_are_written_back(self) -> None:
        await self.stage()
        await lore_pdf.estimate(self.web, self.session, self.cref)
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, "")
        self.assertEqual(self.counts, [PDF])  # (read from the bucket, once)
        got = await lore_pdf.read(self.web, self.session, self.cref)
        self.assertEqual((got["state"], got["pages"], got["pictures"]), ("estimated", 12, 5))
        self.assertGreater(got["estimateUsd"], 0)

    async def test_the_estimate_is_discords_own_price_for_the_lore_and_its_pictures(self) -> None:
        from dmbot import credits

        await self.stage()
        await lore_pdf.estimate(self.web, self.session, self.cref)
        await self.bot_loop()
        got = await lore_pdf.read(self.web, self.session, self.cref)
        want = credits.lore_and_pictures(40_000, 5).credits / 1000
        self.assertAlmostEqual(got["estimateUsd"], want, places=3)

    async def test_the_website_never_sees_the_staged_id_only_its_short_hash(self) -> None:
        await self.stage()
        got = await lore_pdf.read(self.web, self.session, self.cref)
        (key,) = self.bucket.objects
        token = key.removeprefix("p/").removesuffix(".pdf")
        self.assertNotIn(token, str(got))
        self.assertEqual(got["ref"], lp.ref_of(token))

    async def test_nothing_staged_is_not_found_and_a_non_dm_is_refused(self) -> None:
        with self.assertRaises(RecapError) as none:
            await lore_pdf.estimate(self.web, self.session, self.cref)
        self.assertEqual(none.exception.code, "not_found")
        await self.stage()
        bob = await self.signed(BOB, THURSDAY)  # DMs Two, not One
        with self.assertRaises(RecapError) as refused:
            await lore_pdf.estimate(self.web, bob, self.cref)
        self.assertEqual(refused.exception.code, "not_allowed")

    async def test_a_file_replaced_before_the_count_is_gone_and_not_counted(self) -> None:
        await self.stage()
        await lore_pdf.estimate(self.web, self.session, self.cref)
        await self.stage(b"%PDF-1.4\n" + b"z" * 50)  # (a newer file replaces it)
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)
        self.assertEqual(self.counts, [])

    async def test_an_object_that_expired_is_gone_with_its_word_and_the_row_says_failed(
        self,
    ) -> None:
        await self.stage()
        await lore_pdf.estimate(self.web, self.session, self.cref)
        self.bucket.objects.clear()  # (the bucket's one-day rule took it)
        (outcome,) = await self.bot_loop()
        self.assertEqual((outcome.error, outcome.result_other), (wa.GONE, lp.EXPIRED))
        self.assertEqual(
            (await lore_pdf.read(self.web, self.session, self.cref))["state"], "failed"
        )

    async def test_a_file_the_reader_refuses_says_why_in_plain_words(self) -> None:
        async def refuse(raw: bytes) -> lp.Counted:
            raise DocumentError("That PDF is pictures of pages (scanned).")

        await self.stage()
        await lore_pdf.estimate(self.web, self.session, self.cref)
        (outcome,) = await self.bot_loop(self.parts(refuse))
        self.assertEqual(
            (outcome.error, outcome.result_other),
            (wa.BAD_TARGET, "That PDF is pictures of pages (scanned)."),
        )
        self.assertEqual(
            (await lore_pdf.read(self.web, self.session, self.cref))["state"], "failed"
        )

    async def test_a_missing_piece_fails_the_action_to_be_sent_again(self) -> None:
        await self.stage()
        await lore_pdf.estimate(self.web, self.session, self.cref)
        (outcome,) = await self.bot_loop(lp.LorePdfs(None, None, None, 1))
        self.assertEqual(outcome.error, wa.FAILED)

    async def test_the_sweep_clears_a_row_older_than_a_day_and_gives_its_key(self) -> None:
        await self.stage()
        staged = lp.StagedPdfs(self.db)
        self.assertEqual(await staged.sweep(self.g, now=1_000 + lp.ROW_KEEP_S - 1), [])
        (key,) = self.bucket.objects
        self.assertEqual(await staged.sweep(self.g, now=1_000 + lp.ROW_KEEP_S + 1), [key])
        self.assertEqual(await lore_pdf.read(self.web, self.session, self.cref), {"state": "none"})

    async def set_state(self, state: str) -> None:
        await self.sql(
            "UPDATE lore_pdf_staged SET state = %s WHERE guild_id = %s",
            state, self.g,
        )  # fmt: skip

    async def test_a_row_being_read_is_not_counted_again(self) -> None:
        await self.stage()
        await lore_pdf.estimate(self.web, self.session, self.cref)
        await self.set_state("reading")
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.BAD_TARGET)
        self.assertEqual(self.counts, [])
        self.assertEqual(
            (await lore_pdf.read(self.web, self.session, self.cref))["state"], "reading"
        )

    async def test_sending_it_again_recovers_a_row_stuck_in_estimating(self) -> None:
        await self.stage()
        await self.set_state("estimating")  # (a worker died mid-count)
        await lore_pdf.estimate(self.web, self.session, self.cref)
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, "")
        self.assertEqual(
            (await lore_pdf.read(self.web, self.session, self.cref))["state"], "estimated"
        )

    async def test_bytes_over_the_limit_are_refused_whatever_the_row_says(self) -> None:
        await self.stage()
        await lore_pdf.estimate(self.web, self.session, self.cref)
        small = lp.LorePdfs(self.staging, lp.StagedPdfs(self.db), self.count, 10)
        (outcome,) = await self.bot_loop(small)
        self.assertEqual(outcome.error, wa.BAD_TARGET)
        self.assertEqual(self.counts, [])

    async def test_a_crash_in_the_reader_puts_the_row_back_and_the_action_is_sent_again(
        self,
    ) -> None:
        async def crash(raw: bytes) -> lp.Counted:
            raise RuntimeError("reader died")

        await self.stage()
        await lore_pdf.estimate(self.web, self.session, self.cref)
        with self.assertRaises(RuntimeError):
            await self.bot_loop(self.parts(crash))
        self.assertEqual(
            (await lore_pdf.read(self.web, self.session, self.cref))["state"], "staged"
        )

    async def test_a_file_replaced_while_it_is_counted_is_gone_and_the_new_row_is_left_alone(
        self,
    ) -> None:
        async def replace(raw: bytes) -> lp.Counted:
            await self.stage(b"%PDF-1.4\n" + b"q" * 50)  # (the DM sends another while it counts)
            return self.found

        await self.stage()
        await lore_pdf.estimate(self.web, self.session, self.cref)
        (outcome,) = await self.bot_loop(self.parts(replace))
        self.assertEqual(outcome.error, wa.GONE)
        got = await lore_pdf.read(self.web, self.session, self.cref)
        self.assertEqual((got["state"], got["pages"]), ("staged", None))  # (nothing written to it)

    async def test_the_sweep_never_clears_a_row_being_read(self) -> None:
        await self.stage()
        await self.set_state("reading")
        self.assertEqual(
            await lp.StagedPdfs(self.db).sweep(self.g, now=1_000 + 10 * lp.ROW_KEEP_S), []
        )
