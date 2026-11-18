"""A lore PDF sent from the website is staged (#4762 step 1, migration 0545): a DM of the campaign
only, a PDF by its own first bytes, at most the website's cap, one for each campaign (a new one
replaces the old one and the old object goes), and dropped on "Never mind". Ids and numbers are all
the row holds. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory.name_documents import MAX_PDF_BYTES
from dmbot.web import lore_pdf
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_name_actions import Acting

PDF = b"%PDF-1.7\n" + b"x" * 200


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class Staged(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bucket = FakeBucket()

    async def stage(
        self, data: bytes = PDF, who: Any = None, bucket: Any = "own"
    ) -> dict[str, Any]:
        return await lore_pdf.stage(
            self.web, who or self.session, self.cref,
            self.bucket if bucket == "own" else bucket, data, 1_000,
        )  # fmt: skip

    async def refused(self, *args: Any, **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.stage(*args, **kw)
        return caught.exception.code

    async def test_a_pdf_is_staged_and_read_back_in_numbers(self) -> None:
        got = await self.stage()
        self.assertEqual((got["state"], got["sizeBytes"]), ("staged", len(PDF)))
        self.assertEqual((got["pages"], got["pictures"], got["estimateUsd"]), (None, None, None))
        (key,) = self.bucket.objects
        self.assertTrue(key.startswith("p/") and key.endswith(".pdf"))
        self.assertEqual(self.bucket.objects[key], PDF)
        self.assertEqual(
            (await lore_pdf.read(self.web, self.session, self.cref))["state"], "staged"
        )

    async def test_nothing_is_staged_to_read_before_one_is_sent(self) -> None:
        self.assertEqual(await lore_pdf.read(self.web, self.session, self.cref), {"state": "none"})

    async def test_a_new_file_replaces_the_old_one_and_its_object_goes(self) -> None:
        await self.stage()
        (first,) = self.bucket.objects
        await self.stage(b"%PDF-1.4\n" + b"y" * 50)
        (second,) = self.bucket.objects
        self.assertNotEqual(first, second)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM lore_pdf_staged")
            self.assertEqual((await cur.fetchone() or {})["n"], 1)

    async def test_a_file_that_is_not_a_pdf_empty_or_too_big_is_refused_and_nothing_is_kept(
        self,
    ) -> None:
        for data in (b"", b"GIF89a....", b"not a pdf at all", b"%PDF-" + b"x" * lore_pdf.MAX_BYTES):
            self.assertEqual(await self.refused(data), "bad_file")
        self.assertEqual(self.bucket.objects, {})
        self.assertEqual(await lore_pdf.read(self.web, self.session, self.cref), {"state": "none"})

    async def test_no_bucket_is_uploads_off(self) -> None:
        self.assertEqual(await self.refused(bucket=None), "uploads_off")

    async def test_only_a_dm_of_the_campaign_may_stage_or_read(self) -> None:
        bob = await self.signed(BOB, THURSDAY)  # DMs Two, not One
        self.assertEqual(await self.refused(who=bob), "not_allowed")
        self.assertEqual(self.bucket.objects, {})
        with self.assertRaises(RecapError) as caught:
            await lore_pdf.read(self.web, bob, self.cref)
        self.assertEqual(caught.exception.code, "not_allowed")

    async def test_never_mind_removes_the_row_and_the_object(self) -> None:
        await self.stage()
        await lore_pdf.drop(self.web, self.session, self.cref, self.bucket)
        self.assertEqual(self.bucket.objects, {})
        self.assertEqual(await lore_pdf.read(self.web, self.session, self.cref), {"state": "none"})

    async def test_the_row_holds_ids_and_numbers_only_and_the_cap_is_under_the_cores(self) -> None:
        await self.stage()
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT * FROM lore_pdf_staged")
            row = await cur.fetchone()
        assert row is not None
        self.assertNotIn(PDF[:20].decode(), str(row))
        self.assertLessEqual(lore_pdf.MAX_BYTES, MAX_PDF_BYTES)
