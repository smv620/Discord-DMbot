"""Payments in the web API (#435): the signed webhook is the only thing that changes a
paid plan, each event once and never backwards; Try It once; prices only from plans.json."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import httpx

from dmbot import entitlements
from dmbot.config import ConfigError
from dmbot.web import entitlements_writer
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider, PaymentEvent
from dmbot.web.settings import load_web_settings
from tests.pg import DatabaseTest
from tests.test_web_api import ALICE, API, SITE, FakeDiscord, settings

DAY = 86400
HOOK_SECRET = b"h" * 32


class Payments(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_800_000_000
        self.provider = FakeProvider(HOOK_SECRET, SITE)
        self.app = create_app(
            settings(), self.db, FakeDiscord(), payments=self.provider, clock=lambda: self.now
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.events = 0
        await self.sign_in()

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def sign_in(self) -> None:
        start = await self.client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )

    async def post(self, path: str, body: Any = None) -> httpx.Response:
        return await self.client.post(
            path, headers={"X-DMbot-Request": "1"}, json=body if body is not None else None
        )

    async def send(self, signed: bool = True, **event: Any) -> httpx.Response:
        self.events += 1
        payload = {"id": f"evt-{self.events}", "user_id": ALICE.id, "occurred_at": self.now}
        payload.update(event)
        body = json.dumps(payload).encode()
        signature = self.provider.sign(body) if signed else "0" * 64
        return await self.client.post(
            "/webhooks/fake",
            content=body,
            headers={"x-fake-signature": signature, "content-type": "application/json"},
        )

    async def start_table(self, **changes: Any) -> httpx.Response:
        event: dict[str, Any] = {
            "kind": "subscription_started",
            "plan": "table",
            "period_start": self.now,
            "period_end": self.now + 30 * DAY,
            "customer_id": "cus_1",
            "subscription_id": "sub_1",
        }
        event.update(changes)
        return await self.send(**event)

    async def plan(self) -> entitlements.Entitlement:
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        return got

    # The webhook

    async def test_a_signed_event_starts_a_plan_with_the_caps_from_plans_json(self) -> None:
        self.assertEqual((await self.start_table(hours_cap=999)).status_code, 200)
        got = await self.plan()
        self.assertEqual(
            (got.plan, got.status, got.hours_cap, got.campaign_cap), ("table", "active", 18, 1)
        )
        me = (await self.client.get("/me")).json()["plan"]
        self.assertEqual((me["id"], me["hoursCap"]), ("table", 18))

    async def test_an_unsigned_event_changes_nothing(self) -> None:
        response = await self.send(
            signed=False,
            kind="subscription_started",
            plan="guild",
            period_start=self.now,
            period_end=self.now + DAY,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))

    async def test_the_webhook_needs_no_site_header_but_its_signature(self) -> None:
        # No X-DMbot-Request header and no Origin: the payment company can't send them.
        self.assertEqual((await self.start_table()).status_code, 200)

    async def test_the_same_event_twice_is_applied_once(self) -> None:
        await self.start_table()
        body = json.dumps(
            {
                "id": "evt-x",
                "user_id": ALICE.id,
                "occurred_at": self.now + 5,
                "kind": "extra_hours_bought",
            }
        ).encode()
        headers = {"x-fake-signature": self.provider.sign(body)}
        for _ in range(3):
            await self.client.post("/webhooks/fake", content=body, headers=headers)
        self.assertEqual((await self.plan()).extra_hours, 10)

    async def test_an_older_event_arriving_late_changes_nothing(self) -> None:
        await self.start_table()
        await self.send(kind="subscription_ended", occurred_at=self.now + 100)
        await self.send(kind="payment_failed", occurred_at=self.now + 50)  # delivered late
        got = await self.plan()
        self.assertEqual((got.status, got.grace_ends_at), ("lapsed", None))

    async def test_a_failed_payment_gives_seven_days_then_the_plan_lapses(self) -> None:
        await self.start_table()
        await self.send(kind="payment_failed", occurred_at=self.now + 10)
        got = await self.plan()
        self.assertEqual((got.status, got.grace_ends_at), ("grace", self.now + 10 + 7 * DAY))
        await self.send(kind="payment_failed", occurred_at=self.now + 20)  # retry fails too
        self.assertEqual((await self.plan()).grace_ends_at, self.now + 10 + 7 * DAY)
        await self.send(kind="subscription_ended", occurred_at=self.now + 8 * DAY)
        got = await self.plan()
        self.assertEqual((got.status, got.lapsed_at), ("lapsed", self.now + 8 * DAY))

    async def test_a_renewal_resets_extra_hours_and_keeps_the_plan_change_time(self) -> None:
        await self.start_table()
        await self.send(kind="extra_hours_bought", occurred_at=self.now + 1)
        self.assertEqual((await self.plan()).hours_this_period, 28)
        await self.start_table(
            kind="subscription_renewed",
            occurred_at=self.now + 30 * DAY,
            period_start=self.now + 30 * DAY,
            period_end=self.now + 60 * DAY,
        )
        got = await self.plan()
        self.assertEqual((got.extra_hours, got.plan_changed_at), (0, self.now))

    async def test_a_plan_change_records_when(self) -> None:
        await self.start_table()
        await self.start_table(plan="guild", occurred_at=self.now + 5)
        got = await self.plan()
        self.assertEqual(
            # When it changed, by DMbot's clock (campaign start times use the same clock).
            (got.plan, got.campaign_cap, got.plan_changed_at),
            ("guild", 5, self.now),
        )

    async def test_unknown_or_unsold_plans_are_refused_and_not_recorded(self) -> None:
        for plan in ("free-forever", "pro", "try-it"):
            self.assertEqual((await self.start_table(plan=plan)).status_code, 422)
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))
        async with self.db.user(ALICE.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM payment_events")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # so a fixed replay can still be applied

    async def test_events_for_people_who_never_signed_in_are_ignored(self) -> None:
        response = await self.start_table(user_id=424242)
        self.assertEqual(response.status_code, 200)

    async def test_an_unreadable_or_oversized_body_is_refused(self) -> None:
        bad = b"not json"
        response = await self.client.post(
            "/webhooks/fake", content=bad, headers={"x-fake-signature": self.provider.sign(bad)}
        )
        self.assertEqual(response.status_code, 400)
        big = b"x" * (64 * 1024 + 1)
        response = await self.client.post(
            "/webhooks/fake", content=big, headers={"x-fake-signature": self.provider.sign(big)}
        )
        self.assertEqual(response.status_code, 413)

    async def test_other_providers_paths_dont_exist(self) -> None:
        response = await self.client.post("/webhooks/paddle", content=b"{}")
        self.assertEqual(response.status_code, 404)

    async def test_an_end_before_its_start_is_retried_not_lost(self) -> None:
        ended = await self.send(kind="subscription_ended", subscription_id="sub_1")
        self.assertEqual(ended.status_code, 503)  # not recorded: the company sends it again
        await self.start_table(occurred_at=self.now - 5)
        body = json.dumps(
            {
                "id": "evt-1",
                "user_id": ALICE.id,
                "occurred_at": self.now,
                "kind": "subscription_ended",
                "subscription_id": "sub_1",
            }
        ).encode()
        again = await self.client.post(
            "/webhooks/fake", content=body, headers={"x-fake-signature": self.provider.sign(body)}
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual((await self.plan()).status, "lapsed")

    async def test_an_old_subscription_ending_doesnt_stop_the_new_one(self) -> None:
        await self.start_table(subscription_id="sub_old")
        await self.start_table(plan="guild", subscription_id="sub_new", occurred_at=self.now + 5)
        await self.send(
            kind="subscription_ended", subscription_id="sub_old", occurred_at=self.now + 9
        )
        got = await self.plan()
        self.assertEqual((got.plan, got.status), ("guild", "active"))

    async def test_the_company_cannot_stop_try_it(self) -> None:
        await self.post("/plan/try-it")
        response = await self.send(kind="subscription_ended", subscription_id="sub_1")
        self.assertEqual(response.status_code, 503)
        self.assertEqual((await self.plan()).status, "active")

    async def test_a_renewal_ends_the_grace(self) -> None:
        await self.start_table()
        await self.send(kind="payment_failed", occurred_at=self.now + 10)
        await self.start_table(
            kind="subscription_renewed",
            occurred_at=self.now + 20,
            period_start=self.now + 30 * DAY,
            period_end=self.now + 60 * DAY,
        )
        got = await self.plan()
        self.assertEqual((got.status, got.grace_ends_at), ("active", None))

    async def test_extra_hours_are_credited_even_after_a_later_renewal(self) -> None:
        await self.start_table()
        await self.start_table(
            kind="subscription_renewed",
            occurred_at=self.now + 100,
            period_start=self.now + 30 * DAY,
            period_end=self.now + 60 * DAY,
        )
        await self.send(kind="extra_hours_bought", occurred_at=self.now + 50)  # late delivery
        self.assertEqual((await self.plan()).extra_hours, 10)

    async def test_extra_hours_need_a_working_plan(self) -> None:
        self.assertEqual((await self.send(kind="extra_hours_bought")).status_code, 200)
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))
        await self.start_table()
        await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 1
        )
        await self.send(kind="extra_hours_bought", occurred_at=self.now + 2)
        self.assertEqual((await self.plan()).extra_hours, 0)

    async def test_two_events_at_once_for_a_new_person_keep_the_newer(self) -> None:
        for _ in range(5):
            await self.reset_plan()
            older = self.start_table(plan="table", occurred_at=self.now)
            newer = self.start_table(plan="guild", occurred_at=self.now + 10)
            await asyncio.gather(older, newer)
            self.assertEqual((await self.plan()).plan, "guild")

    async def test_try_it_and_a_first_payment_at_once_keep_the_payment(self) -> None:
        await asyncio.gather(self.post("/plan/try-it"), self.start_table())
        self.assertEqual((await self.plan()).plan, "table")

    async def test_a_bad_body_is_a_400_not_a_crash(self) -> None:
        not_a_number = {
            "id": "e",
            "user_id": ALICE.id,
            "occurred_at": 1,
            "kind": "subscription_started",
            "plan": "table",
            "period_start": "soon",
            "period_end": 2,
        }
        for body in (b"[1, 2]", b'"text"', json.dumps(not_a_number).encode()):
            response = await self.client.post(
                "/webhooks/fake",
                content=body,
                headers={"x-fake-signature": self.provider.sign(body)},
            )
            self.assertEqual(response.status_code, 400, body)

    async def test_a_streamed_oversized_body_is_cut_off(self) -> None:
        async def chunks() -> Any:
            for _ in range(100):
                yield b"x" * 1024

        response = await self.client.post("/webhooks/fake", content=chunks())
        self.assertEqual(response.status_code, 413)

    async def test_only_the_webhook_skips_the_site_header(self) -> None:
        response = await self.client.post("/plan/try-it")  # cookie, but no header
        self.assertEqual(response.status_code, 403)

    async def reset_plan(self) -> None:
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (ALICE.id,))

    # Try It

    async def test_try_it_starts_once(self) -> None:
        self.assertEqual((await self.post("/plan/try-it")).status_code, 204)
        got = await self.plan()
        self.assertEqual(
            (got.plan, got.hours_cap, got.period_end), ("try-it", 8, self.now + 30 * DAY)
        )
        again = await self.post("/plan/try-it")
        self.assertEqual((again.status_code, again.json()), (409, {"error": "try_it_has_plan"}))

    async def test_try_it_cannot_start_again_after_it_ends(self) -> None:
        await self.post("/plan/try-it")
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute(
                "UPDATE entitlements SET status = 'lapsed', lapsed_at = 1 WHERE user_id = %s",
                (ALICE.id,),
            )
        again = await self.post("/plan/try-it")
        self.assertEqual(again.json(), {"error": "try_it_used"})

    async def test_try_it_is_not_offered_over_a_paid_plan(self) -> None:
        await self.start_table()
        response = await self.post("/plan/try-it")
        self.assertEqual(response.json(), {"error": "try_it_has_plan"})
        self.assertEqual((await self.plan()).plan, "table")

    # Checkout and the billing page

    async def test_checkout_takes_only_a_plan_id_and_the_price_from_plans_json(self) -> None:
        response = await self.post("/billing/checkout", {"plan": "two-tables", "price": 1})
        url = response.json()["url"]
        self.assertEqual(parse_qs(urlsplit(url).query), {"fake_checkout": ["two-tables"]})
        for bad in ({"plan": "pro"}, {"plan": "try-it"}, {"plan": "x"}, {}, [1]):
            response = await self.post("/billing/checkout", bad)
            self.assertEqual(
                (response.status_code, response.json()), (400, {"error": "unknown_plan"})
            )

    async def test_the_first_month_of_table_after_try_it_is_discounted_once(self) -> None:
        plain = (await self.post("/billing/checkout", {"plan": "table"})).json()["url"]
        self.assertNotIn("first_month", plain)
        await self.post("/plan/try-it")
        offer = (await self.post("/billing/checkout", {"plan": "table"})).json()["url"]
        self.assertEqual(parse_qs(urlsplit(offer).query)["first_month"], ["199"])
        guild = (await self.post("/billing/checkout", {"plan": "guild"})).json()["url"]
        self.assertNotIn("first_month", guild)
        await self.start_table()  # paid: a working plan refuses a second checkout
        again = await self.post("/billing/checkout", {"plan": "table"})
        self.assertEqual((again.status_code, again.json()), (409, {"error": "has_paid_plan"}))
        await self.send(kind="subscription_ended", subscription_id="sub_1")
        after = (await self.post("/billing/checkout", {"plan": "table"})).json()["url"]
        self.assertNotIn("first_month", after)  # paid before: no second first month

    async def test_the_billing_page_needs_a_paid_plan(self) -> None:
        none = await self.post("/billing/portal")
        self.assertEqual((none.status_code, none.json()), (409, {"error": "no_paid_plan"}))
        await self.start_table()
        self.assertIn("fake_billing", (await self.post("/billing/portal")).json()["url"])

    async def test_buying_needs_a_session_and_the_site_header(self) -> None:
        self.assertEqual(
            (await self.client.post("/billing/checkout", json={"plan": "table"})).status_code, 403
        )
        self.client.cookies.clear()
        self.assertEqual((await self.post("/billing/checkout", {"plan": "table"})).status_code, 401)


class NoPaymentCompanyYet(DatabaseTest):
    async def test_buying_says_payments_are_off(self) -> None:
        app = create_app(settings(), self.db, FakeDiscord(), clock=lambda: 1_800_000_000)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=API) as c:
            start = await c.get("/auth/discord/start")
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            await c.get("/auth/discord/callback", params={"state": state, "code": "good-code"})
            response = await c.post(
                "/billing/checkout", headers={"X-DMbot-Request": "1"}, json={"plan": "table"}
            )
            self.assertEqual(
                (response.status_code, response.json()), (503, {"error": "payments_off"})
            )
            self.assertEqual((await c.post("/webhooks/fake", content=b"{}")).status_code, 404)


class WriterDirectly(DatabaseTest):
    async def test_an_event_without_a_period_is_ignored(self) -> None:
        async with self.db.user(ALICE.id) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, created_at, last_sign_in_at) VALUES (%s, 0, 0)",
                (ALICE.id,),
            )
        event = PaymentEvent(
            provider="fake",
            event_id="e",
            kind="subscription_started",
            user_id=ALICE.id,
            occurred_at=1,
            plan="table",
        )
        self.assertEqual(await entitlements_writer.apply_event(self.db, event, now=1), "rejected")


class PaymentSettings(unittest.TestCase):
    ENV: ClassVar[dict[str, str]] = {
        "DATABASE_URL": "postgresql://x",
        "DISCORD_CLIENT_ID": "1",
        "DISCORD_CLIENT_SECRET": "s",
        "WEB_SECRET_KEY": "k" * 40,
        "WEB_SITE_URL": "http://localhost:4321",
        "WEB_API_URL": "http://localhost:8080",
    }

    def test_no_payment_company_by_default(self) -> None:
        self.assertEqual(load_web_settings(self.ENV).payment_provider, "")

    def test_the_fake_company_only_on_localhost_and_with_a_secret(self) -> None:
        fake = {**self.ENV, "PAYMENT_PROVIDER": "fake", "PAYMENT_WEBHOOK_SECRET": "s" * 20}
        self.assertEqual(load_web_settings(fake).payment_provider, "fake")
        with self.assertRaisesRegex(ConfigError, "only for testing on localhost"):
            load_web_settings({**fake, "WEB_API_URL": "https://api.dmbot.example"})
        with self.assertRaisesRegex(ConfigError, "PAYMENT_WEBHOOK_SECRET"):
            load_web_settings({**fake, "PAYMENT_WEBHOOK_SECRET": ""})

    def test_companies_not_built_yet_are_refused(self) -> None:
        with self.assertRaisesRegex(ConfigError, "isn't built yet"):
            load_web_settings({**self.ENV, "PAYMENT_PROVIDER": "paddle"})
