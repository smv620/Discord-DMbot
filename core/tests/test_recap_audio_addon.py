"""The recap audio add-on and its entitlement (#1389): who has spoken recaps (each plan, a grant,
free access), the add-on as a second monthly subscription that never touches the plan, its
webhook on and off, the checkout hidden until its price is set, and cancelling with the plan and
the account. No money moves: the fake payment company."""

from __future__ import annotations

import json
import unittest
from functools import partial
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from dmbot import entitlements, plan_rules
from dmbot.db import Database
from dmbot.entitlements import (
    NO_ACCESS,
    Entitlement,
    Grant,
    access_for,
    configure_free_users,
)
from dmbot.web import accounts, entitlements_writer, strays
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider, PaymentError
from dmbot.web.stripe import RECAP_AUDIO_ITEM, StripeConfig, StripeProvider
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_api import ALICE, SITE, FakeDiscord, settings
from tests.test_web_payments import DAY, HOOK_SECRET
from tests.test_web_role import ensure_web_role, web_url

NOW = 1_800_000_000


def plan(name: str, **kw: Any) -> Entitlement:
    caps = {
        "try-it": (8, 1),
        "table": (18, 1),
        "two-tables": (43, 2),
        "guild": (87, 5),
        "pro": (217, 20),
    }
    hours, campaigns = caps[name]
    base: dict[str, Any] = {
        "user_id": 5, "plan": name, "status": "active", "hours_cap": hours, "extra_hours": 0,
        "campaign_cap": campaigns, "period_start": NOW - DAY, "period_end": NOW + 29 * DAY,
        "grace_ends_at": None, "lapsed_at": None, "plan_changed_at": 0,
    }  # fmt: skip
    return Entitlement(**{**base, **kw})


class WhoHasSpokenRecaps(unittest.TestCase):
    def setUp(self) -> None:
        configure_free_users([])

    def has(self, p: Entitlement | None, grant: Grant | None = None, now: int = NOW) -> bool:
        return plan_rules.can_recap_audio(access_for(5, p, grant, now))

    def test_guild_and_pro_include_it(self) -> None:
        self.assertTrue(self.has(plan("guild")))
        self.assertTrue(self.has(plan("pro")))

    def test_the_cheaper_plans_and_try_it_do_not(self) -> None:
        for name in ("try-it", "table", "two-tables"):
            self.assertFalse(self.has(plan(name)), name)

    def test_no_plan_and_an_ended_plan_have_none(self) -> None:
        self.assertFalse(self.has(None))
        self.assertFalse(plan_rules.can_recap_audio(None))
        self.assertFalse(plan_rules.can_recap_audio(NO_ACCESS))
        self.assertFalse(self.has(plan("guild", status="lapsed")))
        self.assertFalse(self.has(plan("guild"), now=NOW + 40 * DAY))  # well past its period

    def test_a_grant_at_guild_level_or_above_and_free_access_have_it(self) -> None:
        for level in ("guild", "unlimited"):
            self.assertTrue(self.has(None, Grant(5, level, None, None)), level)
        self.assertFalse(self.has(None, Grant(5, "guild", NOW - 1, None)))  # ended
        self.assertFalse(self.has(None, Grant(5, "guild", None, NOW - 5)))  # revoked
        configure_free_users([5])
        self.assertTrue(self.has(None))

    def test_the_add_on_gives_it_to_a_working_paid_plan_until_it_runs_out(self) -> None:
        table = plan("table", recap_audio_until=NOW + 10 * DAY)
        self.assertTrue(self.has(table))
        self.assertFalse(self.has(table, now=NOW + 10 * DAY))  # run out
        self.assertFalse(self.has(plan("table", recap_audio_until=None)))
        self.assertFalse(self.has(plan("table", recap_audio_until=NOW + 10 * DAY, status="lapsed")))

    def test_the_add_on_never_gives_it_to_try_it(self) -> None:
        self.assertFalse(self.has(plan("try-it", recap_audio_until=NOW + 10 * DAY)))

    def test_the_refusal_says_where_to_get_it_and_only_the_owner_hears_why(self) -> None:
        table = access_for(5, plan("table"), None, NOW)
        mine = plan_rules.refusal("recap_audio", table, is_owner=True, site_url="https://x.example")
        assert mine is not None
        self.assertIn("Guild", mine)
        self.assertIn("https://x.example", mine)
        other = plan_rules.refusal("recap_audio", table, is_owner=False)
        assert other is not None
        self.assertIn("Ask the campaign's owner", other)
        self.assertNotIn("Guild", other)  # nobody learns another person's plan
        guild = access_for(5, plan("guild"), None, NOW)
        self.assertIsNone(plan_rules.refusal("recap_audio", guild, is_owner=True))


def invoice(reason: str, price: str = "price_audio", status: str = "paid") -> dict[str, Any]:
    return {
        "id": "evt_1", "type": "invoice.paid", "created": NOW, "livemode": False,
        "data": {"object": {
            "id": "in_1", "customer": "cus_1", "subscription": "sub_audio", "status": status,
            "amount_paid": 500,
            "billing_reason": reason, "customer_email": "alice@example.com",
            "subscription_details": {"metadata": {"user_id": "1001", "item": "recap_audio"}},
            "lines": {"data": [{"price": {"id": price},
                                "period": {"start": NOW, "end": NOW + 30 * DAY}}]},
        }},
    }  # fmt: skip


def provider(price: str = "price_audio") -> StripeProvider:
    config = StripeConfig(
        secret_key="sk_test_x",
        prices={"table": "price_table", "guild": "price_guild"},
        extra_hours_price="price_extra",
        recap_audio_price=price,
    )
    return StripeProvider(config, b"w" * 32, clock=lambda: float(NOW))


class StripeSide(unittest.IsolatedAsyncioTestCase):
    def parse(self, raw: dict[str, Any], p: StripeProvider | None = None) -> Any:
        return (p or provider()).parse(json.dumps(raw).encode())

    def test_a_first_and_a_renewal_payment_are_the_add_ons_not_the_plans(self) -> None:
        started = self.parse(invoice("subscription_create"))
        self.assertEqual(
            (started.kind, started.user_id, started.plan), ("recap_audio_started", 1001, None)
        )
        self.assertEqual((started.period_start, started.period_end), (NOW, NOW + 30 * DAY))
        self.assertEqual(started.subscription_id, "sub_audio")
        self.assertEqual(self.parse(invoice("subscription_cycle")).kind, "recap_audio_renewed")

    def test_other_invoices_a_failed_payment_and_a_wrong_price_change_nothing(self) -> None:
        self.assertIsNone(self.parse(invoice("subscription_update")))
        self.assertIsNone(self.parse(invoice("subscription_create", status="open")))
        failed = invoice("subscription_cycle")
        failed["type"] = "invoice.payment_failed"
        self.assertIsNone(self.parse(failed))  # no grace: it runs out at the end of the period
        self.assertIsNone(self.parse(invoice("subscription_create", price="price_other")))
        free = invoice("subscription_create")
        free["data"]["object"]["amount_paid"] = 0  # a coupon made it free: no payment, no month
        self.assertIsNone(self.parse(free))
        self.assertIsNone(self.parse(invoice("subscription_create"), provider(price="")))

    def test_the_subscription_ending_is_the_add_ons_end_and_an_update_is_nothing(self) -> None:
        sub = {
            "id": "sub_audio", "customer": "cus_1", "status": "canceled",
            "metadata": {"user_id": "1001", "item": "recap_audio"},
            "items": {"data": [{"price": {"id": "price_audio"}}]},
        }  # fmt: skip
        ended = self.parse({"id": "e", "type": "customer.subscription.deleted", "created": NOW,
                            "livemode": False, "data": {"object": sub}})  # fmt: skip
        self.assertEqual(
            (ended.kind, ended.user_id, ended.subscription_id),
            ("recap_audio_ended", 1001, "sub_audio"),
        )
        updated = {**sub, "status": "active", "cancel_at_period_end": True}
        self.assertIsNone(self.parse({"id": "e", "type": "customer.subscription.updated",
                                      "created": NOW, "livemode": False,
                                      "data": {"object": updated}}))  # fmt: skip

    async def test_the_checkout_is_a_marked_monthly_subscription(self) -> None:
        sent: list[dict[str, str] | None] = []

        async def transport(
            method: str, path: str, form: dict[str, str] | None, idempotency_key: str | None = None
        ) -> tuple[int, Any]:
            sent.append(form)
            return 200, {"url": "https://checkout.stripe.com/c/pay/x", "id": "cus_1"}

        p = StripeProvider(
            StripeConfig("sk_test_x", {"table": "price_table"}, "price_extra", "price_audio"),
            b"w" * 32, transport=transport, clock=lambda: float(NOW),
        )  # fmt: skip
        self.assertTrue(p.recap_audio_for_sale)
        await p.recap_audio_checkout_url(
            user_id=1001, email=None, return_url="https://x.example/account", customer_id="cus_1"
        )
        form = sent[-1]
        assert form is not None
        self.assertEqual(form["mode"], "subscription")
        self.assertEqual(form["line_items[0][price]"], "price_audio")
        self.assertEqual(form["subscription_data[metadata][item]"], RECAP_AUDIO_ITEM)
        self.assertEqual(form["metadata[item]"], RECAP_AUDIO_ITEM)

    async def test_with_no_price_it_is_not_for_sale_and_cannot_be_started(self) -> None:
        p = provider(price="")
        self.assertFalse(p.recap_audio_for_sale)
        with self.assertRaises(PaymentError):
            await p.recap_audio_checkout_url(user_id=1, email=None, return_url="https://x.example")

    @staticmethod
    def read(env: dict[str, str], name: str) -> str:
        return env.get(name, "")

    def test_the_price_setting_is_optional_but_must_look_like_a_price(self) -> None:
        env = {
            "STRIPE_SECRET_KEY": "sk_test_x",
            "STRIPE_PRICE_TABLE": "price_1", "STRIPE_PRICE_TWO_TABLES": "price_2",
            "STRIPE_PRICE_GUILD": "price_3", "STRIPE_PRICE_EXTRA_HOURS": "price_4",
        }  # fmt: skip
        from dmbot.web.settings import _stripe

        self.assertEqual(_stripe(env.get_default if False else (lambda k: env.get(k, "")))
                         .recap_audio_price, "")  # fmt: skip
        self.assertEqual(
            _stripe(
                lambda k: {**env, "STRIPE_PRICE_RECAP_AUDIO": "price_5"}.get(k, "")
            ).recap_audio_price,
            "price_5",
        )
        from dmbot.config import ConfigError

        for bad in ("nope", "price_1"):
            with self.assertRaises(ConfigError):
                _stripe(partial(self.read, {**env, "STRIPE_PRICE_RECAP_AUDIO": bad}))


class Payments(DatabaseTest):
    """The payments tests' setup and helpers (as tests/test_web_payments.py has them)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_800_000_000
        self.provider = FakeProvider(HOOK_SECRET, SITE)
        self.app = create_app(
            settings(), self.db, FakeDiscord(), payments=self.provider, clock=lambda: self.now
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        self.events = 0
        start = await self.client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def post(self, path: str, body: Any = None) -> httpx.Response:
        return await self.client.post(path, headers={"X-DMbot-Request": "1"}, json=body)

    async def send(self, **event: Any) -> httpx.Response:
        self.events += 1
        payload = {"id": f"evt-{self.events}", "user_id": ALICE.id, "occurred_at": self.now}
        payload.update(event)
        body = json.dumps(payload).encode()
        return await self.client.post(
            "/webhooks/fake",
            content=body,
            headers={"x-fake-signature": self.provider.sign(body)},
        )

    async def start_table(self, **changes: Any) -> httpx.Response:
        event: dict[str, Any] = {
            "kind": "subscription_started", "plan": "table", "period_start": self.now,
            "period_end": self.now + 30 * DAY, "customer_id": "cus_1", "subscription_id": "sub_1",
        }  # fmt: skip
        event.update(changes)
        return await self.send(**event)

    async def plan(self) -> entitlements.Entitlement:
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        return got


class Webhook(Payments):
    async def audio(self, kind: str = "recap_audio_started", **changes: Any) -> Any:
        event: dict[str, Any] = {
            "kind": kind, "subscription_id": "sub_audio", "customer_id": "cus_1",
            "period_start": self.now, "period_end": self.now + 30 * DAY,
        }  # fmt: skip
        event.update(changes)
        return await self.send(**event)

    async def access(self) -> Any:
        return (await entitlements.plan_and_access(self.db, ALICE.id, self.now))[1]

    async def test_bought_on_table_it_gives_audio_until_the_period_ends_and_leaves_the_plan_alone(
        self,
    ) -> None:
        await self.start_table()
        before = await self.plan()
        self.assertFalse((await self.access()).recap_audio)
        self.assertEqual((await self.audio()).status_code, 200)
        got = await self.plan()
        self.assertEqual(
            (got.plan, got.period_end, got.provider if hasattr(got, "provider") else None),
            (before.plan, before.period_end, None),
        )
        self.assertEqual(got.recap_audio_until, self.now + 30 * DAY)
        self.assertTrue((await self.access()).recap_audio)
        self.now += 31 * DAY
        self.assertFalse((await self.access()).recap_audio)

    async def test_a_renewal_moves_the_end_forward_and_a_late_one_never_back(self) -> None:
        await self.start_table()
        await self.audio()
        await self.audio(
            "recap_audio_renewed",
            occurred_at=self.now + 10,
            period_start=self.now + 30 * DAY,
            period_end=self.now + 60 * DAY,
        )
        self.assertEqual((await self.plan()).recap_audio_until, self.now + 60 * DAY)
        # An older delivery arriving late changes nothing.
        late = await self.audio(
            "recap_audio_renewed", occurred_at=self.now + 5, period_end=self.now + 45 * DAY
        )
        self.assertEqual(late.status_code, 200)
        self.assertEqual((await self.plan()).recap_audio_until, self.now + 60 * DAY)

    async def test_the_same_event_twice_is_applied_once(self) -> None:
        await self.start_table()
        body = json.dumps(
            {
                "id": "evt-a",
                "user_id": ALICE.id,
                "occurred_at": self.now,
                "kind": "recap_audio_started",
                "subscription_id": "sub_audio",
                "period_start": self.now,
                "period_end": self.now + DAY,
            }
        ).encode()
        for _ in range(3):
            r = await self.client.post(
                "/webhooks/fake",
                content=body,
                headers={"x-fake-signature": self.provider.sign(body)},
            )
            self.assertEqual(r.status_code, 200)
        self.assertEqual((await self.plan()).recap_audio_until, self.now + DAY)

    async def test_the_end_clears_it_and_an_older_subscription_ending_does_not(self) -> None:
        await self.start_table()
        await self.audio()
        await self.audio("recap_audio_ended", subscription_id="sub_old", occurred_at=self.now + 5)
        self.assertIsNotNone((await self.plan()).recap_audio_until)
        await self.audio("recap_audio_ended", occurred_at=self.now + 9)
        self.assertIsNone((await self.plan()).recap_audio_until)
        self.assertFalse((await self.access()).recap_audio)

    async def test_an_end_that_arrives_before_a_late_start_wins(self) -> None:
        await self.start_table()
        await self.audio("recap_audio_ended", occurred_at=self.now + 20)
        await self.audio(occurred_at=self.now + 10)  # the older "started", delivered late
        self.assertIsNone((await self.plan()).recap_audio_until)

    async def test_no_working_paid_plan_means_no_add_on(self) -> None:
        self.assertEqual((await self.audio()).status_code, 200)  # no plan at all: ignored
        await self.post("/plan/try-it")
        await self.audio(occurred_at=self.now + 1)
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        self.assertIsNone(got.recap_audio_until)

    async def test_an_event_without_a_usable_period_is_refused(self) -> None:
        await self.start_table()
        bad = await self.audio(period_end=self.now)
        self.assertEqual(bad.status_code, 422)

    async def test_the_add_on_counts_as_a_payment_but_not_as_a_second_plan_subscription(
        self,
    ) -> None:
        await self.start_table()
        await self.audio()
        await self.audio(
            "recap_audio_renewed", occurred_at=self.now + 3, period_end=self.now + 60 * DAY
        )
        got = await self.plan()
        self.assertEqual(
            (got.plan, got.status), ("table", "active")
        )  # never the "second subscription" path

    async def test_guild_has_it_without_the_add_on_and_pro_too(self) -> None:
        await self.start_table(plan="guild")
        self.assertTrue((await self.access()).recap_audio)

    async def test_the_plan_ending_cancels_the_add_on_at_the_company(self) -> None:
        await self.start_table()
        await self.audio()
        self.assertEqual(self.provider.cancelled, [])
        await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 9
        )
        self.assertEqual(self.provider.cancelled, ["sub_audio"])
        self.assertIsNone((await self.plan()).recap_audio_until)

    async def test_the_plan_ending_without_an_add_on_cancels_nothing(self) -> None:
        await self.start_table()
        await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 9
        )
        self.assertEqual(self.provider.cancelled, [])

    async def test_a_failed_cancel_is_kept_for_later_and_the_entitlement_still_needs_the_plan(
        self,
    ) -> None:
        await self.start_table()
        await self.audio()

        async def broken(*, subscription_id: str) -> None:
            raise PaymentError("down")

        self.provider.cancel = broken  # type: ignore[method-assign]
        await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 9
        )
        self.assertEqual(
            await entitlements_writer.recap_audio_subscription(self.db, ALICE.id), "sub_audio"
        )
        self.assertFalse((await self.access()).recap_audio)  # the plan is gone: nothing is free

    async def test_deleting_the_account_cancels_the_add_on_too(self) -> None:
        await self.start_table()
        await self.audio()
        self.assertEqual(await accounts.active_subscription(self.db, ALICE.id), ("fake", "sub_1"))
        step1 = await self.post("/account/delete/request")
        token = step1.json()["confirm_token"]
        done = await self.post("/account/delete/confirm", {"confirm_token": token})
        self.assertEqual(done.status_code, 204)
        self.assertEqual(sorted(self.provider.cancelled), ["sub_1", "sub_audio"])


class Billing(Payments):
    """Money safety: nothing is ever charged twice, or left charging for nothing."""

    async def audio(self, kind: str = "recap_audio_started", **changes: Any) -> Any:
        event: dict[str, Any] = {
            "kind": kind, "subscription_id": "sub_audio", "customer_id": "cus_1",
            "period_start": self.now, "period_end": self.now + 30 * DAY,
        }  # fmt: skip
        event.update(changes)
        return await self.send(**event)

    async def test_a_second_add_on_subscription_is_cancelled_and_the_first_kept(self) -> None:
        await self.start_table()
        await self.audio()
        await self.audio(subscription_id="sub_second", occurred_at=self.now + 5)
        self.assertEqual(self.provider.cancelled, ["sub_second"])
        got = await self.plan()
        self.assertEqual(got.recap_audio_until, self.now + 30 * DAY)
        self.assertEqual(
            await entitlements_writer.recap_audio_subscription(self.db, ALICE.id), "sub_audio"
        )

    async def test_the_same_subscription_renewing_is_not_a_second_one(self) -> None:
        await self.start_table()
        await self.audio()
        await self.audio(
            "recap_audio_renewed", occurred_at=self.now + 5, period_end=self.now + 60 * DAY
        )
        self.assertEqual(self.provider.cancelled, [])

    async def test_upgrading_to_guild_cancels_the_add_on_that_is_now_included(self) -> None:
        await self.start_table()
        await self.audio()
        await self.start_table(plan="guild", subscription_id="sub_2", occurred_at=self.now + 9)
        self.assertEqual(self.provider.cancelled, ["sub_audio"])
        self.assertIsNone(await entitlements_writer.recap_audio_subscription(self.db, ALICE.id))
        access = (await entitlements.plan_and_access(self.db, ALICE.id, self.now))[1]
        self.assertTrue(access.recap_audio)  # Guild includes it

    async def test_a_plan_change_between_plans_without_audio_keeps_the_add_on(self) -> None:
        await self.start_table()
        await self.audio()
        await self.start_table(plan="two-tables", subscription_id="sub_2", occurred_at=self.now + 9)
        self.assertEqual(self.provider.cancelled, [])

    async def test_an_add_on_paid_with_no_working_plan_is_cancelled_not_just_logged(self) -> None:
        self.assertEqual((await self.audio()).status_code, 200)  # no plan at all
        self.assertEqual(self.provider.cancelled, ["sub_audio"])
        await self.post("/plan/try-it")
        await self.audio(subscription_id="sub_tryit", occurred_at=self.now + 1)
        self.assertEqual(self.provider.cancelled, ["sub_audio", "sub_tryit"])

    async def test_a_cancel_that_fails_never_fails_the_webhook(self) -> None:
        async def broken(*, subscription_id: str) -> None:
            raise PaymentError("down")

        self.provider.cancel = broken  # type: ignore[method-assign]
        self.assertEqual((await self.audio()).status_code, 200)

    async def web_role(self) -> Any:
        """The website's own database role, as the internet-facing API connects."""
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.addAsyncCleanup(web.close)
        return web

    async def stray_rows(self) -> list[str]:
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT subscription_id FROM recap_audio_strays")
            return [str(r["subscription_id"]) for r in await cur.fetchall()]

    async def test_a_stray_the_company_refused_is_swept_later_once_an_hour_until_it_works(
        self,
    ) -> None:
        down = True
        tried: list[str] = []
        real = self.provider.cancel

        async def flaky(*, subscription_id: str) -> None:
            tried.append(subscription_id)
            if down:
                raise PaymentError("down")
            await real(subscription_id=subscription_id)

        self.provider.cancel = flaky  # type: ignore[method-assign]
        self.assertEqual((await self.audio()).status_code, 200)  # no plan: a stray, refused
        self.assertEqual(await self.stray_rows(), ["sub_audio"])
        self.assertEqual(await strays.sweep(self.db, self.provider), 0)  # tried a moment ago
        self.assertEqual(tried, ["sub_audio"])
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE recap_audio_strays SET last_try_at = 0")  # an hour on
        self.assertEqual(await strays.sweep(self.db, self.provider), 0)  # still refused
        self.assertEqual(tried, ["sub_audio", "sub_audio"])
        self.assertEqual(await strays.sweep(self.db, self.provider), 0)  # not due again yet
        self.assertEqual(len(tried), 2)
        down = False
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE recap_audio_strays SET last_try_at = 0")
        self.assertEqual(await strays.sweep(self.db, self.provider), 1)
        self.assertEqual(self.provider.cancelled, ["sub_audio"])
        self.assertEqual(await self.stray_rows(), [])  # done: nothing is kept

    async def test_a_cancel_that_works_at_once_keeps_nothing(self) -> None:
        await self.audio()
        self.assertEqual(self.provider.cancelled, ["sub_audio"])
        self.assertEqual(await self.stray_rows(), [])

    async def test_an_add_on_whose_plan_ended_and_whose_cancel_failed_is_swept_too(self) -> None:
        await self.start_table()
        await self.audio()

        async def broken(*, subscription_id: str) -> None:
            raise PaymentError("down")

        real = self.provider.cancel
        self.provider.cancel = broken  # type: ignore[method-assign]
        await self.send(
            kind="subscription_ended", subscription_id="sub_1", occurred_at=self.now + 9
        )
        self.assertEqual(await self.stray_rows(), ["sub_audio"])
        self.provider.cancel = real  # type: ignore[method-assign]
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE recap_audio_strays SET last_try_at = 0")
        self.assertEqual(await strays.sweep(self.db, self.provider), 1)
        self.assertEqual(self.provider.cancelled, ["sub_audio"])

    async def test_a_refusal_that_lasts_a_day_says_a_person_is_needed(self) -> None:
        async def broken(*, subscription_id: str) -> None:
            raise PaymentError("403")

        self.provider.cancel = broken  # type: ignore[method-assign]
        await self.audio()
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE recap_audio_strays SET attempts = 23, last_try_at = 0")
        with self.assertLogs("dmbot.web.strays", "ERROR") as logged:
            await strays.sweep(self.db, self.provider)
        self.assertIn("needs a person", logged.output[0])
        self.assertNotIn("sub_audio", logged.output[0])

    async def test_only_a_company_subscription_id_can_be_kept(self) -> None:
        await strays.note(self.db, "not a subscription")  # never raises, keeps nothing
        self.assertEqual(await self.stray_rows(), [])

    async def test_the_websites_own_role_can_use_the_doors_and_not_the_table(self) -> None:
        web = await self.web_role()
        await strays.note(web, "sub_x")
        self.assertEqual(await self.stray_rows(), ["sub_x"])  # the web role's door kept it
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE recap_audio_strays SET last_try_at = 0")  # an hour on
        async with web.unscoped() as conn:
            cur = await conn.execute("SELECT subscription_id FROM dmbot_strays_to_cancel()")
            self.assertEqual([r["subscription_id"] for r in await cur.fetchall()], ["sub_x"])
            await conn.execute("SELECT dmbot_stray_addon_tried('sub_x')")
            await conn.execute("SELECT old_user_id FROM dmbot_moves_to_sync()")
        async with web.unscoped() as conn:
            with self.assertRaises(Exception):  # noqa: B017 - permission denied, whichever class
                await conn.execute("SELECT * FROM recap_audio_strays")
        async with web.unscoped() as conn:
            await conn.execute("SELECT dmbot_stray_addon_done('sub_x')")
        self.assertEqual(await self.stray_rows(), [])

    async def test_no_second_checkout_while_a_subscription_is_held(self) -> None:
        await self.start_table()
        await self.audio()
        async with self.db.plan_writer(ALICE.id) as conn:  # it ran out unpaid; the id is still held
            await conn.execute(
                "UPDATE entitlements SET recap_audio_until = %s WHERE user_id = %s",
                (self.now - 1, ALICE.id),
            )
        again = await self.post("/billing/recap-audio")
        self.assertEqual((again.status_code, again.json()), (409, {"error": "already_subscribed"}))
        me = (await self.client.get("/me")).json()["recapAudio"]
        self.assertTrue(me["included"] is False)

    async def test_a_plan_with_no_customer_at_the_company_is_not_sold_it(self) -> None:
        await self.start_table(customer_id=None)
        refused = await self.post("/billing/recap-audio")
        self.assertEqual((refused.status_code, refused.json()), (409, {"error": "no_paid_plan"}))
        me = (await self.client.get("/me")).json()["recapAudio"]
        self.assertFalse(me["canBuy"])


class Checkout(Payments):
    async def test_it_is_hidden_until_the_price_is_set(self) -> None:
        await self.start_table()
        self.provider.recap_audio_for_sale = False
        hidden = await self.post("/billing/recap-audio")
        self.assertEqual((hidden.status_code, hidden.json()), (404, {"error": "not_found"}))
        me = (await self.client.get("/me")).json()["recapAudio"]
        self.assertEqual(me, {"included": False, "addOn": False, "canBuy": False})

    async def test_it_is_sold_over_a_working_paid_plan_that_lacks_it(self) -> None:
        none = await self.post("/billing/recap-audio")
        self.assertEqual((none.status_code, none.json()), (409, {"error": "no_paid_plan"}))
        await self.post("/plan/try-it")
        self.assertEqual((await self.post("/billing/recap-audio")).status_code, 409)
        await self.start_table()
        me = (await self.client.get("/me")).json()["recapAudio"]
        self.assertEqual(me, {"included": False, "addOn": False, "canBuy": True})
        paid = await self.post("/billing/recap-audio")
        self.assertEqual(paid.status_code, 200)
        self.assertIn("fake_recap_audio", paid.json()["url"])

    async def test_guild_and_the_add_on_owner_are_not_sold_it_again(self) -> None:
        await self.start_table(plan="guild")
        again = await self.post("/billing/recap-audio")
        self.assertEqual((again.status_code, again.json()), (409, {"error": "already_included"}))
        me = (await self.client.get("/me")).json()["recapAudio"]
        self.assertEqual(me, {"included": True, "addOn": False, "canBuy": False})

    async def test_someone_who_has_the_add_on_sees_when_it_ends_and_cannot_buy_another(
        self,
    ) -> None:
        await self.start_table()
        await self.send(kind="recap_audio_started", subscription_id="sub_audio",
                        period_start=self.now, period_end=self.now + 30 * DAY)  # fmt: skip
        me = (await self.client.get("/me")).json()["recapAudio"]
        self.assertTrue(me["addOn"] and not me["canBuy"] and "endsOn" in me)
        self.assertEqual((await self.post("/billing/recap-audio")).status_code, 409)

    async def test_when_the_company_fails_it_says_so(self) -> None:
        await self.start_table()

        async def broken(**_: object) -> str:
            raise PaymentError("down")

        self.provider.recap_audio_checkout_url = broken  # type: ignore[method-assign]
        failed = await self.post("/billing/recap-audio")
        self.assertEqual(
            (failed.status_code, failed.json()), (502, {"error": "payments_unavailable"})
        )

    async def test_it_needs_a_session_and_the_site_header(self) -> None:
        self.assertEqual((await self.client.post("/billing/recap-audio")).status_code, 403)
        await self.client.post("/auth/logout", headers={"X-DMbot-Request": "1"})
        self.assertEqual((await self.post("/billing/recap-audio")).status_code, 401)


class TheMigration(DatabaseTest):
    async def test_the_columns_exist_and_start_empty(self) -> None:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema ="
                " current_schema() AND table_name = 'entitlements'"
                " AND column_name LIKE 'recap_audio%'"
            )
            names = {r["column_name"] for r in await cur.fetchall()}
        self.assertEqual(
            names, {"recap_audio_until", "recap_audio_subscription_id", "recap_audio_event_at"}
        )


if __name__ == "__main__":
    unittest.main()
