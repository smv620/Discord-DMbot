"""A lore PDF sent from the website is staged (#4762 step 1, migration 0545): a DM of the campaign
only, a PDF by its own first bytes, at most the website's cap, one for each campaign (a new one
replaces the old one and the old object goes), and dropped on "Never mind". Ids and numbers are all
the row holds. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.memory.name_documents import MAX_PDF_BYTES
from dmbot.pictures.bucket import BucketError
from dmbot.web import lore_pdf, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings
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
        self.assertNotIn(first, self.bucket.objects)  # (the replaced file's object is gone)
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
        self.assertEqual(
            sorted(row),
            ["campaign_id", "created_at", "estimate_usd", "guild_id", "id", "object_key", "pages",
             "pictures", "sha256", "size_bytes", "state", "updated_at", "user_id"],
        )  # fmt: skip
        self.assertLessEqual(lore_pdf.MAX_BYTES, MAX_PDF_BYTES)

    async def test_a_dm_of_another_campaign_cannot_drop_or_read_this_ones(self) -> None:
        await self.stage()
        bob = await self.signed(BOB, THURSDAY)  # DMs Two, not One
        with self.assertRaises(RecapError) as dropped:
            await lore_pdf.drop(self.web, bob, self.cref, self.bucket)
        self.assertEqual(dropped.exception.code, "not_allowed")
        self.assertEqual(len(self.bucket.objects), 1)  # (nothing was touched)
        self.assertEqual(
            (await lore_pdf.read(self.web, self.session, self.cref))["state"], "staged"
        )

    async def test_never_mind_with_nothing_staged_is_fine_and_a_failed_put_keeps_no_row(
        self,
    ) -> None:
        await lore_pdf.drop(self.web, self.session, self.cref, None)  # (no row, no bucket)

        class Failing(FakeBucket):
            async def put(self, key: str, data: bytes) -> None:
                raise BucketError("no space")

        self.assertEqual(await self.refused(bucket=Failing()), "failed")
        self.assertEqual(await lore_pdf.read(self.web, self.session, self.cref), {"state": "none"})

    async def test_two_stages_at_once_leave_one_row_and_one_object(self) -> None:
        import asyncio

        await asyncio.gather(self.stage(PDF + b"1"), self.stage(PDF + b"2"))
        self.assertEqual(len(self.bucket.objects), 1)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM lore_pdf_staged")
            self.assertEqual((await cur.fetchone() or {})["n"], 1)


class TheRoute(Staged):
    """The three routes over HTTP: who may call them, what a bad body gets, and the limits."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW, upload_bucket=self.bucket,
        )  # fmt: skip
        self.url = f"/me/campaigns/{self.cref}/lore-pdf"
        self.client = await self.signed_client(ALICE)

    async def signed_client(self, who: Any) -> httpx.AsyncClient:
        token = await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
            headers={"X-DMbot-Request": "1"},  # (a website call, as the page sends)
        )
        client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_a_dm_stages_reads_and_drops_over_http(self) -> None:
        sent = await self.client.post(self.url, content=PDF)
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.json()["state"], "staged")
        self.assertEqual((await self.client.get(self.url)).json()["state"], "staged")
        self.assertEqual((await self.client.delete(self.url)).status_code, 204)
        self.assertEqual((await self.client.get(self.url)).json(), {"state": "none"})
        self.assertEqual(self.bucket.objects, {})

    async def test_the_yes_is_a_202_with_progress_and_its_refusals_have_their_words(self) -> None:
        await self.stage()
        await self.sql("UPDATE lore_pdf_staged SET state = 'estimated'")
        ref = (await self.client.get(self.url)).json()["ref"]
        body = {"ref": ref, "choice": "world", "speaker": "", "rights": True}
        go = f"{self.url}/go"
        self.assertEqual((await self.client.post(go, json=body)).status_code, 404)  # (switch off)
        self.app = create_app(
            settings(lore_on=True), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
            upload_bucket=self.bucket,
        )  # fmt: skip
        self.client = await self.signed_client(ALICE)
        unticked = await self.client.post(go, json={**body, "rights": False})
        self.assertEqual((unticked.status_code, unticked.json()["error"]), (409, "needs_confirm"))
        gone = await self.client.post(go, json={**body, "ref": "0" * 32})
        self.assertEqual((gone.status_code, gone.json()["error"]), (404, "not_found"))
        sent = await self.client.post(go, json=body)
        self.assertEqual(sent.status_code, 202)
        self.assertEqual(sent.json()["upload"]["state"], "waiting")
        again = await self.client.post(go, json=body)
        self.assertEqual((again.status_code, again.json()["error"]), (409, "not_counted"))
        other = await self.signed_client(BOB)
        self.assertEqual((await other.post(go, json=body)).status_code, 403)

    async def test_a_dm_of_another_campaign_is_refused_before_the_body_is_read(self) -> None:
        bob = await self.signed_client(BOB)  # DMs Two, not One
        for call in (bob.get(self.url), bob.post(self.url, content=PDF), bob.delete(self.url)):
            got = await call
            self.assertEqual(
                (got.status_code, got.json()), (403, {"error": "not_allowed"}), got.request.method
            )
        self.assertEqual(self.bucket.objects, {})

    async def test_a_file_that_is_not_a_pdf_is_a_400_and_a_lying_length_is_a_413(self) -> None:
        bad = await self.client.post(self.url, content=b"GIF89a....")
        self.assertEqual((bad.status_code, bad.json()), (400, {"error": "bad_file"}))
        big = await self.client.post(
            self.url,
            content=b"%PDF-" + b"x" * (lore_pdf.MAX_BYTES + 1),
        )
        self.assertEqual((big.status_code, big.json()), (413, {"error": "bad_file"}))
        self.assertEqual(self.bucket.objects, {})

    async def test_a_signed_out_caller_gets_401(self) -> None:
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            headers={"X-DMbot-Request": "1"},
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.post(self.url, content=PDF)).status_code, 401)
