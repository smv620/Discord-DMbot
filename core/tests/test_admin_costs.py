"""The admin Costs figures (#3671): a key reads only its own service, one failure shows on its
own row, nothing is read without a key, the cache holds figures only, and Anthropic is never
asked."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

import aiohttp
import httpx

from dmbot.web import admin_costs
from dmbot.web.admin import AdminSessions, FailedTries
from dmbot.web.app import create_app
from tests.pg import DatabaseTest
from tests.test_web_admin import ADMIN, HASH, HEADERS, PASSWORD
from tests.test_web_api import FakeDiscord, settings

NOW = 1_800_000_000  # 2027-01-15


class Costs(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str]]] = []
        self.cache: admin_costs.Cache = {}

    async def fetch(self, url: str, headers: Mapping[str, str]) -> Any:
        self.calls.append((url, headers))
        if url.endswith("/v1/projects"):
            return {"projects": [{"project_id": "abcd1234-ef56"}]}
        if "billing/breakdown" in url:
            return {"results": [{"dollars": 4.5}, {"dollars": 1.25}]}
        return {"character_count": 1200, "character_limit": 30000}

    async def read(self, dg: str = "dg", el: str = "el", month: str = "2027-01") -> dict[str, Any]:
        return await admin_costs.read_costs(
            month=month,
            now=NOW,
            deepgram_key=dg,
            elevenlabs_key=el,
            cache=self.cache,
            fetch=self.fetch,
        )

    def row(self, got: dict[str, Any], sid: str) -> dict[str, Any]:
        return next(r for r in got["services"] if r["id"] == sid)

    def test_months(self) -> None:
        self.assertEqual(admin_costs.this_month(NOW), "2027-01")
        self.assertEqual(admin_costs.month_range("2027-02"), ("2027-02-01", "2027-02-28"))
        self.assertEqual(admin_costs.month_range("2026-12"), ("2026-12-01", "2026-12-31"))
        self.assertIsNone(admin_costs.MONTH.fullmatch("2027-13"))

    async def test_deepgram_is_summed_in_cents_for_the_month(self) -> None:
        got = await self.read()
        dg = self.row(got, "deepgram")
        self.assertEqual((dg["state"], dg["amount_cents"], dg["read_at"]), ("read", 575, NOW))
        self.assertIn("start=2027-01-01&end=2027-01-31", self.calls[1][0])
        self.assertEqual(got["spent_cents"], 575)
        self.assertEqual(got["counted"], ["deepgram"])

    async def test_each_key_goes_only_to_its_own_service(self) -> None:
        await self.read(dg="DGKEY", el="ELKEY")
        for url, headers in self.calls:
            if "deepgram" in url:
                self.assertEqual(headers, {"Authorization": "Token DGKEY"})
            else:
                self.assertEqual(headers, {"xi-api-key": "ELKEY"})

    async def test_no_key_means_not_connected_and_nothing_is_asked(self) -> None:
        got = await self.read(dg="", el="")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.row(got, "deepgram")["state"], "not_connected")
        self.assertEqual(self.row(got, "elevenlabs")["state"], "not_connected")

    async def test_anthropic_speechmatics_voyage_are_not_recorded_with_a_link(self) -> None:
        got = await self.read()
        for sid in (
            "anthropic-fast",
            "anthropic-careful",
            "anthropic-deep",
            "speechmatics",
            "voyage",
        ):
            row = self.row(got, sid)
            self.assertEqual(row["state"], "not_recorded", sid)
            self.assertTrue(row["link"].startswith("https://"))
        self.assertFalse(any("anthropic" in url for url, _ in self.calls))

    async def test_every_service_has_a_description_and_a_link(self) -> None:
        for svc in admin_costs.SERVICES:
            self.assertTrue(svc["covers"].strip(), svc["id"])
            self.assertTrue(svc["link"].startswith("https://"), svc["id"])

    async def test_one_failure_shows_on_its_own_row_and_never_leaks_the_error(self) -> None:
        async def broken(url: str, headers: Mapping[str, str]) -> Any:
            if "deepgram" in url:
                raise aiohttp.ClientError("secret-token-in-message")
            return await self.fetch(url, headers)

        got = await admin_costs.read_costs(
            month="2027-01",
            now=NOW,
            deepgram_key="dg",
            elevenlabs_key="el",
            cache=self.cache,
            fetch=broken,
        )
        dg = self.row(got, "deepgram")
        self.assertEqual(dg["state"], "unavailable")
        self.assertNotIn("secret", str(got))
        self.assertEqual(self.row(got, "elevenlabs")["state"], "read")

    async def test_the_cache_holds_figures_and_expires(self) -> None:
        await self.read()
        n = len(self.calls)
        await self.read()
        self.assertEqual(len(self.calls), n)
        self.assertNotIn("dg", str(self.cache))
        self.cache.clear()
        await self.read()
        self.assertGreater(len(self.calls), n)

    async def test_income_is_listed_for_every_plan_but_not_guessed(self) -> None:
        got = await self.read()
        self.assertEqual(
            [i["plan"] for i in got["income"]], ["free", "try-me", "table", "two-table", "guild"]
        )
        self.assertTrue(all(i["state"] == "not_available" for i in got["income"]))


class CostsRoute(DatabaseTest):
    """The route itself: admin only, month checked, and no key means nothing is asked."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(admin_emails=(ADMIN,), admin_password_hash=HASH),
            self.db,
            FakeDiscord(),
            admin_sessions=AdminSessions(clock=lambda: 0.0),
            admin_tries=FailedTries(clock=lambda: 0.0),
            clock=lambda: NOW,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
        )
        self.addAsyncCleanup(self.client.aclose)

    async def test_signed_out_is_refused(self) -> None:
        self.assertEqual((await self.client.get("/admin/costs")).status_code, 401)

    async def test_signed_in_gets_this_month_with_no_keys_connected(self) -> None:
        signed = await self.client.post(
            "/admin/auth/password", json={"email": ADMIN, "password": PASSWORD}, headers=HEADERS
        )
        self.assertEqual(signed.status_code, 204)
        got = await self.client.get("/admin/costs")
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertEqual(body["month"], "2027-01")
        states = {s["id"]: s["state"] for s in body["services"]}
        self.assertEqual(states["deepgram"], "not_connected")
        self.assertEqual(states["anthropic-deep"], "not_recorded")
        self.assertEqual((await self.client.get("/admin/costs?month=2027-13")).status_code, 400)
