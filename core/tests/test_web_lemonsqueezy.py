"""The Lemon Squeezy adapter (#1083): the signature, each event mapping, the first-payment
rule, checkout building, the billing page and cancel, with the HTTP calls faked and made-up
payloads (example.com emails, invented ids). The route tests need Postgres."""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import httpx

from dmbot import entitlements, plans
from dmbot.config import ConfigError
from dmbot.web import entitlements_writer
from dmbot.web.app import create_app
from dmbot.web.lemonsqueezy import LemonSqueezyConfig, LemonSqueezyProvider
from dmbot.web.payments import PaymentError
from dmbot.web.settings import load_web_settings
from tests.pg import DatabaseTest
from tests.test_web_api import ALICE, API, FakeDiscord, settings
from tests.test_web_payments import PaymentSettings

SECRET = b"w" * 32
USER = 424242424242
CONFIG = LemonSqueezyConfig(
    api_key="test-key-not-real",
    store_id="111",
    variants={"table": "1001", "two-tables": "1002", "guild": "1003"},
    extra_hours_variant="2001",
    first_month_codes={"table": "FIRST199"},
)
CHECKOUT_URL = "https://dmbot-test.lemonsqueezy.com/checkout/custom/abc?signature=xyz"


def ts(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


T0 = 1_800_000_000  # 2027-01-15 08:00:00 UTC
RENEWS = 1_802_592_000  # 2027-02-14: a month later, near enough for the period maths


class Fake:
    """A fake Lemon Squeezy API: records calls, answers from `answers`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.answers: dict[tuple[str, str], tuple[int, Any]] = {}

    async def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        return self.answers.get((method, path), (404, None))


def event(
    name: str,
    *,
    data_id: str = "55",
    kind: str = "subscriptions",
    test_mode: bool = False,
    custom: Any = None,
    **attributes: Any,
) -> bytes:
    attributes.setdefault("created_at", ts(T0))
    attributes.setdefault("updated_at", ts(T0))
    meta: dict[str, Any] = {"event_name": name, "test_mode": test_mode}
    meta["custom_data"] = {"user_id": str(USER)} if custom is None else custom
    return json.dumps(
        {"meta": meta, "data": {"type": kind, "id": data_id, "attributes": attributes}}
    ).encode()


def subscription(**changes: Any) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "store_id": 111,
        "customer_id": 777,
        "variant_id": 1001,
        "status": "active",
        "renews_at": ts(RENEWS),
        "user_email": "player@example.com",
    }
    attributes.update(changes)
    return attributes


class Provider(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.http = Fake()
        self.provider = LemonSqueezyProvider(CONFIG, SECRET, transport=self.http)

    def sign(self, body: bytes) -> str:
        return hmac.new(SECRET, body, hashlib.sha256).hexdigest()

    # Signature

    def test_a_good_signature_passes(self) -> None:
        body = event("subscription_created", **subscription())
        self.assertTrue(self.provider.verify(body, {"x-signature": self.sign(body)}))

    def test_a_bad_or_missing_signature_is_refused(self) -> None:
        body = event("subscription_created", **subscription())
        self.assertFalse(self.provider.verify(body, {"x-signature": "0" * 64}))
        self.assertFalse(self.provider.verify(body, {}))
        self.assertFalse(self.provider.verify(body + b" ", {"x-signature": self.sign(body)}))

    def test_a_signature_with_non_ascii_text_is_a_refusal_not_a_crash(self) -> None:
        body = event("subscription_created", **subscription())
        self.assertFalse(self.provider.verify(body, {"x-signature": "é" * 64}))
        self.assertFalse(self.provider.verify(body, {"x-signature": "日本語"}))

    # Events

    def test_a_new_active_subscription_starts_the_plan_with_its_period(self) -> None:
        parsed = self.provider.parse(event("subscription_created", **subscription()))
        assert parsed is not None
        self.assertEqual(
            (parsed.kind, parsed.user_id, parsed.plan, parsed.provider),
            ("subscription_started", USER, "table", "lemonsqueezy"),
        )
        self.assertEqual(parsed.occurred_at, T0)
        self.assertEqual(parsed.period_end, RENEWS)
        assert parsed.period_start is not None
        self.assertLess(parsed.period_start, RENEWS)
        self.assertEqual((parsed.customer_id, parsed.subscription_id), ("777", "55"))

    def test_a_subscription_that_is_not_active_starts_nothing(self) -> None:
        for state in ("on_trial", "unpaid", "past_due", "paused"):
            body = event("subscription_created", **subscription(status=state))
            self.assertIsNone(self.provider.parse(body), state)

    def test_a_failed_payment_is_payment_failed_with_no_plan_needed(self) -> None:
        body = event(
            "subscription_payment_failed",
            data_id="900",
            kind="subscription-invoices",
            subscription_id=55,
            customer_id=777,
            billing_reason="initial",
            status="pending",
        )
        parsed = self.provider.parse(body)
        assert parsed is not None
        self.assertEqual((parsed.kind, parsed.user_id), ("payment_failed", USER))
        self.assertEqual(parsed.subscription_id, "55")

    def test_an_active_update_is_a_renewal_or_a_change_at_the_moment_it_takes_effect(self) -> None:
        renewed = self.provider.parse(event("subscription_updated", **subscription()))
        assert renewed is not None
        self.assertEqual((renewed.kind, renewed.plan), ("subscription_renewed", "table"))
        # A downgrade: the new variant is the new plan, sent when Lemon Squeezy makes it.
        down = self.provider.parse(
            event("subscription_updated", **subscription(variant_id=1002, updated_at=ts(T0 + 5)))
        )
        assert down is not None
        self.assertEqual(
            (down.kind, down.plan, down.occurred_at), ("subscription_renewed", "two-tables", T0 + 5)
        )

    def test_a_cancelled_plan_runs_to_its_end_and_ends_only_when_it_expires(self) -> None:
        cancelled = event("subscription_updated", **subscription(status="cancelled"))
        self.assertIsNone(self.provider.parse(cancelled))
        self.assertIsNone(self.provider.parse(event("subscription_cancelled", **subscription())))
        expired = self.provider.parse(
            event("subscription_expired", **subscription(status="expired"))
        )
        assert expired is not None
        self.assertEqual((expired.kind, expired.subscription_id), ("subscription_ended", "55"))

    def test_paid_extra_hours_are_bought_and_plan_orders_are_not_counted(self) -> None:
        extra = event(
            "order_created",
            data_id="31",
            kind="orders",
            status="paid",
            customer_id=777,
            first_order_item={"variant_id": 2001},
        )
        parsed = self.provider.parse(extra)
        assert parsed is not None
        self.assertEqual((parsed.kind, parsed.user_id), ("extra_hours_bought", USER))
        plan_order = event(
            "order_created", kind="orders", status="paid", first_order_item={"variant_id": 1001}
        )
        self.assertIsNone(self.provider.parse(plan_order))
        unpaid = event(
            "order_created", kind="orders", status="pending", first_order_item={"variant_id": 2001}
        )
        self.assertIsNone(self.provider.parse(unpaid))

    def test_events_dmbot_does_not_use_are_ignored(self) -> None:
        for name in ("subscription_payment_success", "subscription_resumed", "license_key_created"):
            self.assertIsNone(self.provider.parse(event(name, **subscription())), name)

    def test_an_unknown_variant_has_no_plan_so_the_writer_rejects_it(self) -> None:
        parsed = self.provider.parse(event("subscription_created", **subscription(variant_id=9)))
        assert parsed is not None
        self.assertIsNone(parsed.plan)

    def test_test_events_are_ignored_in_live_mode_and_live_events_in_test_mode(self) -> None:
        test_event = event("subscription_created", test_mode=True, **subscription())
        self.assertIsNone(self.provider.parse(test_event))
        live = event("subscription_created", **subscription())
        testing = LemonSqueezyProvider(
            LemonSqueezyConfig(
                api_key="k",
                store_id="111",
                variants={"table": "1001"},
                extra_hours_variant="2001",
                test_mode=True,
            ),
            SECRET,
            transport=self.http,
        )
        self.assertIsNone(testing.parse(live))
        self.assertIsNotNone(testing.parse(test_event))

    def test_an_event_without_a_person_is_unreadable_not_ignored(self) -> None:
        for custom in ({}, {"user_id": "nope"}, {"user_id": "0"}):
            body = event("subscription_created", custom=custom, **subscription())
            with self.assertRaises((ValueError, KeyError)):
                self.provider.parse(body)

    def test_the_event_id_is_the_same_on_redelivery_and_new_for_a_new_change(self) -> None:
        one = self.provider.parse(event("subscription_updated", **subscription()))
        again = self.provider.parse(event("subscription_updated", **subscription()))
        later = self.provider.parse(
            event("subscription_updated", **subscription(updated_at=ts(T0 + 60)))
        )
        assert one and again and later
        self.assertEqual(one.event_id, again.event_id)
        self.assertNotEqual(one.event_id, later.event_id)

    # Checkout

    async def test_checkout_sends_the_email_to_lemon_squeezy_and_never_in_the_address(self) -> None:
        self.http.answers[("POST", "/v1/checkouts")] = (
            201,
            {"data": {"attributes": {"url": CHECKOUT_URL}}},
        )
        url = await self.provider.checkout_url(
            user_id=USER,
            email="player@example.com",
            plan=plans.load().by_id["table"],
            first_month_cents=None,
            return_url="https://dmbot.example/account",
        )
        self.assertEqual(url, CHECKOUT_URL)
        self.assertNotIn("example.com", url)
        self.assertNotIn("@", url)
        [(method, path, body)] = self.http.calls
        self.assertEqual((method, path), ("POST", "/v1/checkouts"))
        attributes = body["data"]["attributes"]
        self.assertEqual(attributes["checkout_data"]["email"], "player@example.com")
        self.assertEqual(attributes["checkout_data"]["custom"], {"user_id": str(USER)})
        self.assertNotIn("discount_code", attributes["checkout_data"])
        self.assertEqual(
            attributes["product_options"]["redirect_url"], "https://dmbot.example/account"
        )
        relations = body["data"]["relationships"]
        self.assertEqual(relations["variant"]["data"]["id"], "1001")  # the plan's variant
        self.assertEqual(relations["store"]["data"]["id"], "111")

    async def test_the_first_month_uses_the_once_only_discount_code(self) -> None:
        self.http.answers[("POST", "/v1/checkouts")] = (
            201,
            {"data": {"attributes": {"url": CHECKOUT_URL}}},
        )
        await self.provider.checkout_url(
            user_id=USER,
            email=None,
            plan=plans.load().by_id["table"],
            first_month_cents=199,
            return_url="https://dmbot.example/account",
        )
        body = self.http.calls[0][2]
        data = body["data"]["attributes"]["checkout_data"]
        self.assertEqual(data["discount_code"], "FIRST199")
        self.assertNotIn("email", data)  # none known: none sent

    async def test_a_first_month_without_its_code_is_refused_not_charged_in_full(self) -> None:
        with self.assertRaises(PaymentError):
            await self.provider.checkout_url(
                user_id=USER,
                email=None,
                plan=plans.load().by_id["two-tables"],
                first_month_cents=199,
                return_url="https://dmbot.example/account",
            )
        self.assertEqual(self.http.calls, [])

    async def test_a_plan_with_no_variant_is_refused(self) -> None:
        with self.assertRaises(PaymentError):
            await self.provider.checkout_url(
                user_id=USER,
                email=None,
                plan=plans.load().by_id["pro"],
                first_month_cents=None,
                return_url="https://dmbot.example/account",
            )

    async def test_extra_hours_checkout_uses_the_extra_hours_variant(self) -> None:
        self.http.answers[("POST", "/v1/checkouts")] = (
            201,
            {"data": {"attributes": {"url": CHECKOUT_URL}}},
        )
        await self.provider.extra_hours_checkout_url(
            user_id=USER, email=None, return_url="https://dmbot.example/account"
        )
        variant = self.http.calls[0][2]["data"]["relationships"]["variant"]["data"]["id"]
        self.assertEqual(variant, "2001")

    async def test_a_checkout_address_that_is_not_lemon_squeezys_is_refused(self) -> None:
        for bad in ("http://x.lemonsqueezy.com/c", "https://evil.example/c", None, 5):
            self.http.answers[("POST", "/v1/checkouts")] = (
                201,
                {"data": {"attributes": {"url": bad}}},
            )
            with self.assertRaises(PaymentError, msg=str(bad)):
                await self.provider.checkout_url(
                    user_id=USER,
                    email=None,
                    plan=plans.load().by_id["table"],
                    first_month_cents=None,
                    return_url="https://dmbot.example/account",
                )

    async def test_a_refused_checkout_is_a_payment_error_without_the_body(self) -> None:
        self.http.answers[("POST", "/v1/checkouts")] = (422, {"errors": ["player@example.com"]})
        with self.assertRaises(PaymentError) as caught:
            await self.provider.checkout_url(
                user_id=USER,
                email="player@example.com",
                plan=plans.load().by_id["table"],
                first_month_cents=None,
                return_url="https://dmbot.example/account",
            )
        self.assertNotIn("example.com", str(caught.exception))

    # Billing page and cancel

    async def test_the_billing_page_is_the_customers_portal(self) -> None:
        portal = "https://dmbot-test.lemonsqueezy.com/billing?expires=1&signature=s"
        self.http.answers[("GET", "/v1/customers/777")] = (
            200,
            {"data": {"attributes": {"urls": {"customer_portal": portal}}}},
        )
        got = await self.provider.billing_url(customer_id="777", return_url="https://x.example")
        self.assertEqual(got, portal)

    async def test_a_customer_id_that_is_not_a_number_is_never_put_in_a_path(self) -> None:
        with self.assertRaises(PaymentError):
            await self.provider.billing_url(customer_id="../subscriptions/1", return_url="x")
        self.assertEqual(self.http.calls, [])

    async def test_cancel_deletes_the_subscription_which_runs_to_the_periods_end(self) -> None:
        self.http.answers[("GET", "/v1/subscriptions/55")] = (
            200,
            {"data": {"attributes": {"status": "active"}}},
        )
        self.http.answers[("DELETE", "/v1/subscriptions/55")] = (200, {})
        await self.provider.cancel(subscription_id="55")
        self.assertEqual([c[0] for c in self.http.calls], ["GET", "DELETE"])

    async def test_cancelling_one_already_cancelled_or_gone_succeeds(self) -> None:
        self.http.answers[("GET", "/v1/subscriptions/55")] = (
            200,
            {"data": {"attributes": {"status": "cancelled"}}},
        )
        await self.provider.cancel(subscription_id="55")
        self.assertEqual([c[0] for c in self.http.calls], ["GET"])  # nothing to delete
        await self.provider.cancel(subscription_id="56")  # a 404: already gone

    async def test_a_cancel_lemon_squeezy_refuses_is_an_error_so_deletion_retries(self) -> None:
        self.http.answers[("GET", "/v1/subscriptions/55")] = (
            200,
            {"data": {"attributes": {"status": "active"}}},
        )
        self.http.answers[("DELETE", "/v1/subscriptions/55")] = (500, None)
        with self.assertRaises(PaymentError):
            await self.provider.cancel(subscription_id="55")


class Config(unittest.TestCase):
    ENV: ClassVar[dict[str, str]] = {
        **PaymentSettings.ENV,
        "PAYMENT_PROVIDER": "lemonsqueezy",
        "PAYMENT_WEBHOOK_SECRET": "s" * 20,
        "LEMONSQUEEZY_API_KEY": "key-not-real",
        "LEMONSQUEEZY_STORE_ID": "111",
        "LEMONSQUEEZY_VARIANT_TABLE": "1001",
        "LEMONSQUEEZY_VARIANT_TWO_TABLES": "1002",
        "LEMONSQUEEZY_VARIANT_GUILD": "1003",
        "LEMONSQUEEZY_VARIANT_EXTRA_HOURS": "2001",
        "LEMONSQUEEZY_FIRST_MONTH_CODE_TABLE": "FIRST199",
    }

    def test_it_reads_the_ids_and_hides_the_key(self) -> None:
        loaded = load_web_settings(self.ENV)
        config = loaded.lemonsqueezy
        assert config is not None
        self.assertEqual(config.variants["two-tables"], "1002")
        self.assertEqual(config.first_month_codes, {"table": "FIRST199"})
        self.assertFalse(config.test_mode)
        self.assertNotIn("key-not-real", repr(loaded))
        self.assertNotIn("key-not-real", repr(config))

    def test_a_missing_id_stops_the_api_starting(self) -> None:
        for name in (
            "LEMONSQUEEZY_API_KEY",
            "LEMONSQUEEZY_STORE_ID",
            "LEMONSQUEEZY_VARIANT_GUILD",
            "LEMONSQUEEZY_VARIANT_EXTRA_HOURS",
        ):
            with self.assertRaisesRegex(ConfigError, "LEMONSQUEEZY"):
                load_web_settings({**self.ENV, name: ""})

    def test_it_needs_the_webhook_secret_and_a_valid_mode(self) -> None:
        with self.assertRaisesRegex(ConfigError, "PAYMENT_WEBHOOK_SECRET"):
            load_web_settings({**self.ENV, "PAYMENT_WEBHOOK_SECRET": ""})
        with self.assertRaisesRegex(ConfigError, "LEMONSQUEEZY_TEST_MODE"):
            load_web_settings({**self.ENV, "LEMONSQUEEZY_TEST_MODE": "maybe"})
        self.assertTrue(
            load_web_settings({**self.ENV, "LEMONSQUEEZY_TEST_MODE": "1"}).lemonsqueezy.test_mode  # type: ignore[union-attr]
        )


class Routes(DatabaseTest):
    """The whole path with the real writer: a signed Lemon Squeezy event changes the plan."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.http = Fake()
        self.provider = LemonSqueezyProvider(CONFIG, SECRET, transport=self.http)
        self.now = T0 + 10
        app = create_app(
            settings(), self.db, FakeDiscord(), payments=self.provider, clock=lambda: self.now
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        start = await self.client.get("/auth/discord/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await self.client.get(
            "/auth/discord/callback", params={"state": state, "code": "good-code"}
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def deliver(self, body: bytes, signature: str | None = None) -> httpx.Response:
        sign = signature or hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        return await self.client.post(
            "/webhooks/lemonsqueezy",
            content=body,
            headers={"X-Signature": sign, "Content-Type": "application/json"},
        )

    def mine(self, name: str, **attributes: Any) -> bytes:
        return event(name, custom={"user_id": str(ALICE.id)}, **attributes)

    async def test_a_signed_subscription_starts_the_plan_and_a_replay_changes_nothing(self) -> None:
        body = self.mine("subscription_created", **subscription())
        self.assertEqual((await self.deliver(body)).status_code, 200)
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        self.assertEqual((got.plan, got.status), ("table", "active"))
        self.assertEqual((await self.deliver(body)).status_code, 200)  # a retry: once only

    async def test_a_bad_signature_changes_nothing(self) -> None:
        body = self.mine("subscription_created", **subscription())
        self.assertEqual((await self.deliver(body, "0" * 64)).status_code, 400)
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))

    async def test_a_failed_first_payment_starts_no_plan(self) -> None:
        await entitlements_writer.start_try_it(self.db, ALICE.id, now=self.now)
        body = self.mine(
            "subscription_payment_failed", kind="subscription-invoices", subscription_id=55
        )
        # The writer waits for a "started" that never comes (503, so Lemon Squeezy retries
        # for a while and gives up); nothing changes and no grace is given.
        self.assertEqual((await self.deliver(body)).status_code, 503)
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        self.assertEqual((got.plan, got.status), ("try-it", "active"))

    async def test_checkout_through_the_route_builds_a_lemon_squeezy_checkout(self) -> None:
        self.http.answers[("POST", "/v1/checkouts")] = (
            201,
            {"data": {"attributes": {"url": CHECKOUT_URL}}},
        )
        response = await self.client.post(
            "/billing/checkout", headers={"X-DMbot-Request": "1"}, json={"plan": "two-tables"}
        )
        self.assertEqual((response.status_code, response.json()), (200, {"url": CHECKOUT_URL}))
        body = self.http.calls[0][2]
        self.assertEqual(
            body["data"]["attributes"]["checkout_data"]["custom"], {"user_id": str(ALICE.id)}
        )
