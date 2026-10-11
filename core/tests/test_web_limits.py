"""The web API's own per-address limits (#1082): the sliding count, the ceiling on addresses
held, and the routes it guards. Fake clock throughout; the limiter itself needs no database."""

from __future__ import annotations

import unittest
from typing import Any

import httpx

from dmbot.web.app import create_app
from dmbot.web.limits import LIMITS, RateLimit
from tests.pg import DatabaseTest
from tests.test_web_api import API, SITE, FakeDiscord, settings

HEADERS = {"X-DMbot-Request": "1", "Origin": SITE}


class Sliding(unittest.TestCase):
    def setUp(self) -> None:
        self.time = 0.0
        self.limit = RateLimit(seconds=600, clock=lambda: self.time, count=3, most=2)

    def test_under_at_and_over_the_limit(self) -> None:
        self.assertEqual([self.limit.take("a") for _ in range(3)], ["ok"] * 3)
        self.assertEqual(self.limit.take("a"), "wait")
        self.assertEqual(self.limit.take("b"), "ok")  # another address has its own count

    def test_the_window_slides(self) -> None:
        for second in (0, 100, 200):
            self.time = second
            self.assertEqual(self.limit.take("a"), "ok")
        self.time = 599
        self.assertEqual(self.limit.take("a"), "wait")
        self.assertEqual(self.limit.retry_after("a"), 2)  # the first turn leaves at 600
        self.time = 600
        self.assertEqual(self.limit.take("a"), "ok")  # only the turn from 0 left the window
        self.assertEqual(self.limit.take("a"), "wait")
        self.time = 700
        self.assertEqual(self.limit.take("a"), "ok")

    def test_the_ceiling_on_addresses_holds(self) -> None:
        self.assertEqual(self.limit.take("a"), "ok")
        self.assertEqual(self.limit.take("b"), "ok")
        self.assertEqual(self.limit.take("c"), "busy")
        self.assertEqual(len(self.limit), 2)
        self.time = 601  # everyone's window is over: room again
        self.assertEqual(self.limit.take("c"), "ok")
        self.assertEqual(len(self.limit), 1)

    def test_an_address_that_keeps_coming_is_not_forgotten_early(self) -> None:
        self.limit = RateLimit(seconds=600, clock=lambda: self.time, count=2, most=5)
        self.assertEqual(self.limit.take("a"), "ok")
        self.time = 300
        self.assertEqual(self.limit.take("b"), "ok")
        self.assertEqual(self.limit.take("a"), "ok")
        self.time = 700  # a's first turn is out, its second (300) is not
        self.assertEqual(self.limit.take("a"), "ok")
        self.assertEqual(self.limit.take("a"), "wait")

    def test_giving_a_turn_back(self) -> None:
        self.assertEqual([self.limit.take("a") for _ in range(3)], ["ok"] * 3)
        self.limit.give_back("a")
        self.assertEqual(self.limit.take("a"), "ok")


class Routes(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.time = 0.0
        self.make_client()

    def make_client(self, **limits: RateLimit) -> None:
        route_limits = {
            name: RateLimit(group.seconds, clock=lambda: self.time, count=group.count)
            for name, group in LIMITS.items()
        }
        route_limits.update(limits)
        app = create_app(
            settings(client_ip_header="CF-Connecting-IP"),
            self.db,
            FakeDiscord(),
            route_limits=route_limits,
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=API)

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def hit(
        self, method: str, path: str, ip: str = "203.0.113.5", **extra: Any
    ) -> httpx.Response:
        return await self.client.request(
            method, path, headers={**HEADERS, "CF-Connecting-IP": ip}, **extra
        )

    async def test_sign_in_and_install_share_twenty_per_ten_minutes_each_group(self) -> None:
        for n in range(20):
            path = "/auth/discord/start" if n % 2 else "/auth/discord/callback"
            under = await self.hit("GET", path)
            self.assertNotIn("slow", under.headers["location"], path)
        over = await self.hit("GET", "/auth/discord/start")
        self.assertEqual(over.status_code, 302)
        self.assertEqual(over.headers["location"], f"{SITE}/account?signin=slow")
        self.assertIn("Retry-After", over.headers)
        # The install routes are their own group, with their own count.
        first = await self.hit("GET", "/install")
        self.assertNotIn("slow", first.headers["location"])

    async def test_install_over_the_limit_goes_back_to_the_site_with_a_message(self) -> None:
        for _ in range(20):
            await self.hit("GET", "/install")
        over = await self.hit("GET", "/install/callback")
        self.assertEqual(over.status_code, 302)
        self.assertEqual(over.headers["location"], f"{SITE}/account?install=slow")

    async def test_billing_is_ten_per_ten_minutes_and_answers_429(self) -> None:
        for n in range(10):
            path = "/billing/checkout" if n % 2 else "/billing/portal"
            self.assertEqual((await self.hit("POST", path)).status_code, 401, path)  # signed out
        over = await self.hit("POST", "/billing/portal")
        self.assertEqual(over.status_code, 429)
        self.assertEqual(over.json(), {"error": "slow_down"})
        self.assertGreaterEqual(int(over.headers["Retry-After"]), 1)
        self.time = 601
        self.assertEqual((await self.hit("POST", "/billing/portal")).status_code, 401)

    async def test_account_delete_is_five_per_hour(self) -> None:
        for _ in range(5):
            self.assertEqual((await self.hit("POST", "/account/delete/request")).status_code, 401)
        self.assertEqual((await self.hit("POST", "/account/delete/request")).status_code, 429)
        self.time = 3000  # still within the hour
        self.assertEqual((await self.hit("POST", "/account/delete/request")).status_code, 429)
        self.time = 3601
        self.assertEqual((await self.hit("POST", "/account/delete/request")).status_code, 401)

    async def test_each_address_has_its_own_count(self) -> None:
        for _ in range(5):
            await self.hit("POST", "/account/delete/request", ip="203.0.113.5")
        other = await self.hit("POST", "/account/delete/request", ip="203.0.113.6")
        self.assertEqual(other.status_code, 401)

    async def test_a_missing_or_garbage_proxy_header_is_still_limited(self) -> None:
        for header in ({}, {"CF-Connecting-IP": "not-an-address"}):
            self.make_client()
            statuses = [
                (
                    await self.client.post("/account/delete/request", headers={**HEADERS, **header})
                ).status_code
                for _ in range(6)
            ]
            self.assertEqual(statuses, [401] * 5 + [429], header)

    async def test_the_payment_webhook_is_never_limited(self) -> None:
        for _ in range(60):
            response = await self.client.post(
                "/webhooks/nobody", content=b"{}", headers={"CF-Connecting-IP": "203.0.113.5"}
            )
            self.assertEqual(response.status_code, 404)  # no provider: not 429

    async def test_a_flood_of_addresses_gets_busy(self) -> None:
        self.make_client(
            delete=RateLimit(3600, clock=lambda: self.time, count=5, most=2),
        )
        for n in range(2):
            await self.hit("POST", "/account/delete/request", ip=f"203.0.113.{n}")
        over = await self.hit("POST", "/account/delete/request", ip="203.0.113.99")
        self.assertEqual((over.status_code, over.json()), (503, {"error": "busy"}))

    async def test_the_preflight_and_other_routes_are_not_counted(self) -> None:
        for _ in range(30):
            await self.client.options("/account/delete/request", headers={"Origin": SITE})
            self.assertEqual((await self.hit("GET", "/health")).status_code, 200)
        self.assertEqual((await self.hit("POST", "/account/delete/request")).status_code, 401)
