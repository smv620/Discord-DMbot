"""The web API's own per-address limits (#1082): the sliding count, the ceiling on addresses
held, and the routes it guards. Fake clock throughout; the limiter itself needs no database."""

from __future__ import annotations

import unittest
from typing import Any

import httpx

from dmbot.web.app import create_app
from dmbot.web.limits import FREE_TICKETS_PER_DAY, LIMITS, RateLimit, free_ticket_refusal
from tests.pg import DatabaseTest
from tests.test_web_api import API, SITE, FakeDiscord, settings

HEADERS = {"X-DMbot-Request": "1", "Origin": SITE}


class FreeTickets(unittest.TestCase):
    """The free-account guard for the Support form (#1435): a pure rule, no database."""

    def test_one_waiting_at_a_time_and_three_a_day(self) -> None:
        self.assertIsNone(free_ticket_refusal(0, 0))
        self.assertIsNone(free_ticket_refusal(0, FREE_TICKETS_PER_DAY - 1))
        self.assertEqual(free_ticket_refusal(0, FREE_TICKETS_PER_DAY), "daily")
        self.assertEqual(free_ticket_refusal(1, 1), "one_waiting")
        # One waiting is said first: the person can fix that by waiting for the answer.
        self.assertEqual(free_ticket_refusal(1, 9), "one_waiting")
        self.assertEqual(FREE_TICKETS_PER_DAY, 3)


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

    def test_a_refused_try_does_not_extend_the_wait(self) -> None:
        self.limit = RateLimit(seconds=600, clock=lambda: self.time, count=1)
        self.assertEqual(self.limit.take("a"), "ok")
        self.time = 500
        self.assertEqual(self.limit.take("a"), "wait")
        self.assertEqual(self.limit.retry_after("a"), 101)
        self.time = 600
        self.assertEqual(self.limit.take("a"), "ok")

    def test_a_count_below_one_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            RateLimit(count=0)

    def test_giving_the_last_turn_back_forgets_the_address(self) -> None:
        self.limit = RateLimit(seconds=600, clock=lambda: self.time, count=1)
        self.limit.take("a")
        self.limit.give_back("a")
        self.assertEqual(len(self.limit), 0)

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
            settings(client_ip_header="X-Test-Client-IP"),
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
            method, path, headers={**HEADERS, "X-Test-Client-IP": ip}, **extra
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
        for header in ({}, {"X-Test-Client-IP": "not-an-address"}):
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
                "/webhooks/nobody", content=b"{}", headers={"X-Test-Client-IP": "203.0.113.5"}
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

    async def test_a_cross_site_post_is_refused_before_it_is_counted(self) -> None:
        for _ in range(10):
            forged = await self.client.post(
                "/account/delete/request", headers={"X-Test-Client-IP": "203.0.113.5"}
            )
            self.assertEqual(forged.status_code, 403)
        self.assertEqual((await self.hit("POST", "/account/delete/request")).status_code, 401)

    async def test_the_refusal_is_never_cached(self) -> None:
        for _ in range(5):
            await self.hit("POST", "/account/delete/request")
        over = await self.hit("POST", "/account/delete/request")
        self.assertEqual(over.headers["Cache-Control"], "no-store")
        self.assertEqual(over.headers["X-Content-Type-Options"], "nosniff")

    async def test_busy_on_a_sign_in_goes_back_to_the_site_and_says_wait_a_minute(self) -> None:
        self.make_client(**{"sign-in": RateLimit(600, clock=lambda: self.time, count=20, most=1)})
        await self.hit("GET", "/auth/discord/start", ip="203.0.113.1")
        over = await self.hit("GET", "/auth/discord/start", ip="203.0.113.2")
        self.assertEqual(over.headers["location"], f"{SITE}/account?signin=slow")
        self.assertEqual(over.headers["Retry-After"], "60")

    async def test_an_ipv6_block_counts_as_one_address(self) -> None:
        for n in range(5):
            await self.hit("POST", "/account/delete/request", ip=f"2001:db8:0:1::{n + 1}")
        same_block = await self.hit("POST", "/account/delete/request", ip="2001:db8:0:1:9::1")
        self.assertEqual(same_block.status_code, 429)
        other = await self.hit("POST", "/account/delete/request", ip="2001:db8:0:2::1")
        self.assertEqual(other.status_code, 401)

    async def test_head_is_not_counted(self) -> None:
        for _ in range(30):
            await self.hit("HEAD", "/install")
        self.assertNotIn("slow", (await self.hit("GET", "/install")).headers["location"])

    async def test_the_real_limits_are_wired_by_default(self) -> None:
        app = create_app(settings(client_ip_header="X-Test-Client-IP"), self.db, FakeDiscord())
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=API)
        self.addAsyncCleanup(client.aclose)
        codes = [
            (
                await client.post(
                    "/account/delete/request",
                    headers={**HEADERS, "X-Test-Client-IP": "203.0.113.5"},
                )
            ).status_code
            for _ in range(6)
        ]
        self.assertEqual(codes, [401] * 5 + [429])
