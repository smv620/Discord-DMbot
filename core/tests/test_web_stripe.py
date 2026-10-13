"""The Stripe adapter (#1110): the signature, each event mapping, the first-payment rule,
checkout building, the billing page and cancel, with the HTTP calls faked and made-up payloads
(example.com emails, invented ids). The route tests need Postgres."""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from typing import Any, ClassVar
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import aiohttp
import httpx

from dmbot import entitlements, plans
from dmbot.config import ConfigError
from dmbot.web import entitlements_writer
from dmbot.web.app import create_app
from dmbot.web.payments import PaymentError
from dmbot.web.settings import load_web_settings
from dmbot.web.stripe import HttpTransport, StripeConfig, StripeProvider
from tests.pg import DatabaseTest
from tests.test_web_api import ALICE, API, FakeDiscord, settings
from tests.test_web_payments import PaymentSettings

SECRET = b"w" * 32
USER = 424242424242
CONFIG = StripeConfig(
    secret_key="sk_test_not_real",
    prices={"table": "price_table", "two-tables": "price_two", "guild": "price_guild"},
    extra_hours_price="price_extra",
    first_month_coupons={"table": "FIRST199"},
)
CHECKOUT_URL = "https://checkout.stripe.com/c/pay/cs_test_abc"
PORTAL_URL = "https://billing.stripe.com/p/session/test_abc"

T0 = 1_800_000_000  # 2027-01-15 08:00:00 UTC
PERIOD_END = T0 + 30 * 86400
NOW = float(T0 + 10)


def event(
    kind: str,
    obj: dict[str, Any],
    *,
    event_id: str = "evt_1",
    created: int = T0,
    livemode: bool = False,
    previous: dict[str, Any] | None = None,
) -> bytes:
    data: dict[str, Any] = {"object": obj}
    if previous is not None:
        data["previous_attributes"] = previous
    return json.dumps(
        {"id": event_id, "type": kind, "created": created, "livemode": livemode, "data": data}
    ).encode()


def invoice(reason: str = "subscription_create", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "in_1",
        "status": "paid",
        "billing_reason": reason,
        "customer": "cus_777",
        "customer_email": "player@example.com",
        "subscription": "sub_55",
        "subscription_details": {"metadata": {"user_id": str(USER)}},
        "lines": {
            "data": [
                {
                    "price": {"id": "price_table"},
                    "period": {"start": T0, "end": PERIOD_END},
                }
            ]
        },
    }
    body.update(changes)
    return body


def subscription(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "sub_55",
        "customer": "cus_777",
        "status": "active",
        "cancel_at_period_end": False,
        "cancel_at": None,
        "current_period_start": T0,
        "current_period_end": PERIOD_END,
        "metadata": {"user_id": str(USER)},
        "items": {"data": [{"price": {"id": "price_table"}}]},
    }
    body.update(changes)
    return body


def session(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "cs_1",
        "mode": "payment",
        "payment_status": "paid",
        "customer": "cus_777",
        "metadata": {"user_id": str(USER), "item": "extra_hours"},
    }
    body.update(changes)
    return body


def sign(body: bytes, *, at: int = int(NOW), secret: bytes = SECRET) -> str:
    digest = hmac.new(secret, f"{at}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={at},v1={digest}"


class Fake:
    """A fake Stripe API: records calls, answers from `answers`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.answers: dict[tuple[str, str], tuple[int, Any]] = {}

    async def __call__(self, method: str, path: str, form: Any) -> tuple[int, Any]:
        self.calls.append((method, path, form))
        return self.answers.get((method, path), (404, None))


class Provider(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.http = Fake()
        self.provider = StripeProvider(CONFIG, SECRET, transport=self.http, clock=lambda: NOW)

    # Signature

    def test_a_good_signature_passes(self) -> None:
        body = event("invoice.paid", invoice())
        self.assertTrue(self.provider.verify(body, {"stripe-signature": sign(body)}))

    def test_a_bad_or_missing_signature_is_refused(self) -> None:
        body = event("invoice.paid", invoice())
        wrong = sign(body, secret=b"x" * 32)
        self.assertFalse(self.provider.verify(body, {"stripe-signature": wrong}))
        self.assertFalse(self.provider.verify(body, {}))
        self.assertFalse(self.provider.verify(body + b" ", {"stripe-signature": sign(body)}))
        # The timestamp is part of what is signed: moving it breaks the signature.
        other = sign(body).replace(f"t={int(NOW)}", f"t={int(NOW) + 1}")
        self.assertFalse(self.provider.verify(body, {"stripe-signature": other}))

    def test_an_old_or_far_future_delivery_is_refused_even_with_a_good_signature(self) -> None:
        body = event("invoice.paid", invoice())
        for at in (int(NOW) - 301, int(NOW) + 301, 0):
            self.assertFalse(self.provider.verify(body, {"stripe-signature": sign(body, at=at)}))
        edge = sign(body, at=int(NOW) - 300)
        self.assertTrue(self.provider.verify(body, {"stripe-signature": edge}))

    def test_a_malformed_header_is_a_refusal_not_a_crash(self) -> None:
        body = event("invoice.paid", invoice())
        good = sign(body)
        digest = good.split("v1=")[1]
        for header in (
            "",
            "garbage",
            "t=,v1=",
            f"t=abc,v1={digest}",
            f"t=-5,v1={digest}",
            f"v1={digest}",  # no timestamp
            f"t={int(NOW)}",  # no signature
            f"t={int(NOW)},v1={'é' * 64}",
            "日本語",
            f"t={int(NOW)}9999999999999999999999,v1={digest}",
        ):
            self.assertFalse(self.provider.verify(body, {"stripe-signature": header}), header)

    def test_any_one_of_several_signatures_may_match_while_a_secret_is_rolled(self) -> None:
        body = event("invoice.paid", invoice())
        good = sign(body)
        header = f"t={int(NOW)},v1={'0' * 64}," + good.split(",")[1]
        self.assertTrue(self.provider.verify(body, {"stripe-signature": header}))

    # Started, renewed, failed

    def test_a_first_payment_starts_the_plan_with_its_period(self) -> None:
        parsed = self.provider.parse(event("invoice.paid", invoice()))
        assert parsed is not None
        self.assertEqual(
            (parsed.kind, parsed.user_id, parsed.plan, parsed.provider),
            ("subscription_started", USER, "table", "stripe"),
        )
        self.assertEqual((parsed.period_start, parsed.period_end), (T0, PERIOD_END))
        self.assertEqual((parsed.customer_id, parsed.subscription_id), ("cus_777", "sub_55"))
        self.assertEqual((parsed.event_id, parsed.occurred_at), ("evt_1", T0))

    def test_the_start_fires_only_on_payment_never_when_the_subscription_is_made(self) -> None:
        # A Stripe subscription exists before it is paid: creating it starts nothing.
        for kind in ("customer.subscription.created", "checkout.session.completed"):
            body = event(kind, subscription(status="incomplete"))
            self.assertIsNone(self.provider.parse(body), kind)
        # And an invoice that isn't paid yet starts nothing either.
        unpaid = event("invoice.paid", invoice(status="open"))
        self.assertIsNone(self.provider.parse(unpaid))
        # A plan's checkout session completing is not extra hours.
        plan_session = event("checkout.session.completed", session(mode="subscription"))
        self.assertIsNone(self.provider.parse(plan_session))

    def test_a_cycle_invoice_is_a_renewal(self) -> None:
        parsed = self.provider.parse(event("invoice.paid", invoice("subscription_cycle")))
        assert parsed is not None
        self.assertEqual((parsed.kind, parsed.plan), ("subscription_renewed", "table"))
        self.assertEqual(parsed.period_end, PERIOD_END)

    def test_other_invoices_change_nothing(self) -> None:
        for reason in ("subscription_update", "manual", "upcoming", "subscription_threshold"):
            self.assertIsNone(self.provider.parse(event("invoice.paid", invoice(reason))), reason)
        one_off = invoice(subscription=None, subscription_details={})
        self.assertIsNone(self.provider.parse(event("invoice.paid", one_off)))

    def test_the_newer_api_shape_is_read_too(self) -> None:
        # Newer API versions put the subscription under `parent` and the price under `pricing`.
        newer = invoice(
            subscription=None,
            subscription_details=None,
            parent={
                "subscription_details": {
                    "subscription": "sub_55",
                    "metadata": {"user_id": str(USER)},
                }
            },
            lines={
                "data": [
                    {
                        "pricing": {"price_details": {"price": "price_two"}},
                        "period": {"start": T0, "end": PERIOD_END},
                    }
                ]
            },
        )
        parsed = self.provider.parse(event("invoice.paid", newer))
        assert parsed is not None
        self.assertEqual((parsed.plan, parsed.subscription_id), ("two-tables", "sub_55"))

    def test_a_failed_payment_is_payment_failed_with_no_plan_needed(self) -> None:
        body = event("invoice.payment_failed", invoice("subscription_cycle", status="open"))
        parsed = self.provider.parse(body)
        assert parsed is not None
        self.assertEqual((parsed.kind, parsed.user_id), ("payment_failed", USER))
        self.assertEqual(parsed.subscription_id, "sub_55")

    def test_an_unknown_price_has_no_plan_so_the_writer_rejects_it(self) -> None:
        odd = invoice(lines={"data": [{"price": {"id": "price_other"}, "period": {}}]})
        parsed = self.provider.parse(event("invoice.paid", odd))
        assert parsed is not None
        self.assertIsNone(parsed.plan)

    # Subscription changes

    def test_a_price_change_that_has_taken_effect_is_a_renewal_with_the_new_plan(self) -> None:
        now_two = subscription(items={"data": [{"price": {"id": "price_two"}}]})
        body = event(
            "customer.subscription.updated",
            now_two,
            created=T0 + 5,
            previous={"items": {"data": [{"price": {"id": "price_table"}}]}},
        )
        parsed = self.provider.parse(body)
        assert parsed is not None
        self.assertEqual(
            (parsed.kind, parsed.plan, parsed.occurred_at),
            ("subscription_renewed", "two-tables", T0 + 5),
        )

    def test_updates_that_change_no_price_or_cancel_state_are_ignored(self) -> None:
        for previous in ({}, {"default_payment_method": None}, {"metadata": {}}):
            body = event("customer.subscription.updated", subscription(), previous=previous)
            self.assertIsNone(self.provider.parse(body), str(previous))
        no_previous = event("customer.subscription.updated", subscription())
        self.assertIsNone(self.provider.parse(no_previous))

    def test_setting_cancel_at_period_end_stores_the_end_and_clearing_it_resumes(self) -> None:
        cancelled = event(
            "customer.subscription.updated",
            subscription(cancel_at_period_end=True),
            previous={"cancel_at_period_end": False},
        )
        parsed = self.provider.parse(cancelled)
        assert parsed is not None
        self.assertEqual((parsed.kind, parsed.plan), ("subscription_cancelled", "table"))
        self.assertEqual(parsed.period_end, PERIOD_END)
        self.assertIsNone(parsed.period_start)
        resumed = event(
            "customer.subscription.updated",
            subscription(),
            previous={"cancel_at_period_end": True},
        )
        back = self.provider.parse(resumed)
        assert back is not None
        self.assertEqual(back.kind, "subscription_renewed")  # active again: clears a cancel

    def test_a_cancel_date_works_like_cancel_at_period_end(self) -> None:
        end = PERIOD_END + 86400
        dated = event("customer.subscription.updated", subscription(cancel_at=end))
        parsed = self.provider.parse(dated)
        assert parsed is not None
        self.assertEqual((parsed.kind, parsed.period_end), ("subscription_cancelled", end))
        cleared = event(
            "customer.subscription.updated", subscription(), previous={"cancel_at": end}
        )
        back = self.provider.parse(cleared)
        assert back is not None
        self.assertEqual(back.kind, "subscription_renewed")

    def test_a_subscription_that_is_not_active_changes_nothing(self) -> None:
        for state in ("past_due", "unpaid", "incomplete", "paused"):
            body = event(
                "customer.subscription.updated",
                subscription(status=state, cancel_at_period_end=True),
                previous={"items": {}},
            )
            self.assertIsNone(self.provider.parse(body), state)

    def test_a_deleted_subscription_ends_the_plan(self) -> None:
        deleted = event("customer.subscription.deleted", subscription(status="canceled"))
        parsed = self.provider.parse(deleted)
        assert parsed is not None
        self.assertEqual((parsed.kind, parsed.subscription_id), ("subscription_ended", "sub_55"))

    # Extra hours

    def test_a_paid_extra_hours_payment_is_bought_and_nothing_else_is(self) -> None:
        parsed = self.provider.parse(event("checkout.session.completed", session()))
        assert parsed is not None
        self.assertEqual((parsed.kind, parsed.user_id), ("extra_hours_bought", USER))
        unpaid = session(payment_status="unpaid")
        self.assertIsNone(self.provider.parse(event("checkout.session.completed", unpaid)))
        # A payment-mode session that isn't the extra hours (no marker) is not ours to count.
        other = session(metadata={"user_id": str(USER)})
        self.assertIsNone(self.provider.parse(event("checkout.session.completed", other)))

    # Everything else, mode, people

    def test_events_dmbot_does_not_use_are_ignored(self) -> None:
        for kind in ("charge.succeeded", "customer.created", "invoice.created", "ping"):
            self.assertIsNone(self.provider.parse(event(kind, invoice())), kind)

    def test_test_events_are_ignored_with_a_live_key_and_live_events_with_a_test_key(self) -> None:
        live_event = event("invoice.paid", invoice(), livemode=True)
        test_event = event("invoice.paid", invoice())
        self.assertIsNone(self.provider.parse(live_event))  # the test key
        live_key = StripeProvider(
            StripeConfig(
                secret_key="sk_live_not_real",
                prices=CONFIG.prices,
                extra_hours_price="price_extra",
            ),
            SECRET,
            transport=self.http,
        )
        self.assertIsNone(live_key.parse(test_event))
        self.assertIsNotNone(live_key.parse(live_event))
        self.assertTrue(live_key._config.live)
        self.assertFalse(self.provider._config.live)

    def test_a_user_id_that_is_not_a_number_is_unreadable_not_ignored(self) -> None:
        for metadata in ({"user_id": "nope"}, {"user_id": "0"}, {"user_id": "-4"}):
            body = event("invoice.paid", invoice(subscription_details={"metadata": metadata}))
            with self.assertRaises((ValueError, KeyError)):
                self.provider.parse(body)
            gone = event("customer.subscription.deleted", subscription(metadata=metadata))
            with self.assertRaises((ValueError, KeyError)):
                self.provider.parse(gone)

    def test_events_dmbot_ignores_need_no_person(self) -> None:
        # A subscription or invoice made outside DMbot's checkout, with nothing to apply: a
        # quiet ignore, not an error someone is asked to look at.
        nobody = {"metadata": {}}
        self.assertIsNone(
            self.provider.parse(
                event("invoice.paid", invoice("subscription_update", subscription_details=nobody))
            )
        )
        self.assertIsNone(
            self.provider.parse(event("customer.subscription.updated", subscription(**nobody)))
        )
        self.assertIsNone(
            self.provider.parse(event("checkout.session.completed", session(metadata={})))
        )

    def test_a_subscription_dmbot_did_not_make_is_a_quiet_ignore_for_every_event(self) -> None:
        # Made by hand in the dashboard, or sold some other way: no user_id, so not ours.
        # A 400 here would be retried by Stripe for days.
        nobody = {"metadata": {}}
        bodies = (
            event("invoice.paid", invoice(subscription_details=nobody)),
            event("invoice.payment_failed", invoice(subscription_details=nobody)),
            event("customer.subscription.deleted", subscription(metadata={})),
            event(
                "customer.subscription.updated",
                subscription(cancel_at_period_end=True, metadata={}),
            ),
        )
        for body in bodies:
            self.assertIsNone(self.provider.parse(body))

    def test_a_renewal_shown_as_an_update_with_the_same_price_is_not_a_second_renewal(self) -> None:
        # At renewal Stripe also sends an update whose previous items hold the old period:
        # the renewal invoice already renewed it, so this must not renew it again.
        previous = {
            "items": {"data": [{"price": {"id": "price_table"}, "current_period_end": T0}]},
            "current_period_end": T0,
        }
        body = event("customer.subscription.updated", subscription(), previous=previous)
        self.assertIsNone(self.provider.parse(body))
        # An older-API price change names the old plan.
        older = event(
            "customer.subscription.updated",
            subscription(items={"data": [{"price": {"id": "price_two"}}]}),
            previous={"plan": {"id": "price_table"}},
        )
        changed = self.provider.parse(older)
        assert changed is not None
        self.assertEqual(changed.plan, "two-tables")

    def test_extra_hours_paid_later_is_credited_when_the_payment_settles(self) -> None:
        unpaid = event("checkout.session.completed", session(payment_status="unpaid"))
        self.assertIsNone(self.provider.parse(unpaid))
        settled = event("checkout.session.async_payment_succeeded", session())
        parsed = self.provider.parse(settled)
        assert parsed is not None
        self.assertEqual(parsed.kind, "extra_hours_bought")

    def test_a_renewal_after_a_plan_change_uses_the_plans_line_not_a_proration(self) -> None:
        old_dates = {"start": T0 - 1000, "end": T0 - 500}
        lines = [
            {"price": {"id": "price_table"}, "proration": True, "period": old_dates},
            {
                "pricing": {"price_details": {"price": "price_table"}},
                "parent": {"subscription_item_details": {"proration": True}},
                "period": old_dates,
            },
            {"price": {"id": "price_two"}, "period": {"start": T0, "end": PERIOD_END}},
        ]
        body = event("invoice.paid", invoice("subscription_cycle", lines={"data": lines}))
        parsed = self.provider.parse(body)
        assert parsed is not None
        self.assertEqual((parsed.plan, parsed.period_end), ("two-tables", PERIOD_END))

    def test_a_signature_timestamp_too_long_to_be_a_time_is_a_refusal_not_a_500(self) -> None:
        body = event("invoice.paid", invoice())
        digest = sign(body).split("v1=")[1]
        for stamp in ("9" * 400, "9" * 13, "1" * 4000):
            header = f"t={stamp},v1={digest}"
            self.assertFalse(self.provider.verify(body, {"stripe-signature": header}))

    def test_a_body_that_is_not_an_object_is_unreadable(self) -> None:
        for body in (b"[]", b"3", b"not json"):
            with self.assertRaises(ValueError):
                self.provider.parse(body)
        no_object = json.dumps(
            {"id": "e", "type": "invoice.paid", "created": T0, "livemode": False, "data": {}}
        ).encode()
        with self.assertRaises(ValueError):
            self.provider.parse(no_object)

    def test_the_event_id_is_stripes_own_so_a_redelivery_is_the_same(self) -> None:
        body = event("invoice.paid", invoice(), event_id="evt_same")
        one = self.provider.parse(body)
        again = self.provider.parse(body)
        other = self.provider.parse(event("invoice.paid", invoice(), event_id="evt_new"))
        assert one and again and other
        self.assertEqual(one.event_id, again.event_id)
        self.assertNotEqual(one.event_id, other.event_id)

    # Checkout

    async def test_checkout_sends_the_email_to_stripe_and_never_in_the_address(self) -> None:
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
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
        [(method, path, form)] = self.http.calls
        self.assertEqual((method, path), ("POST", "/v1/checkout/sessions"))
        self.assertEqual(form["mode"], "subscription")
        self.assertEqual(form["customer_email"], "player@example.com")
        self.assertEqual(form["line_items[0][price]"], "price_table")  # the plan's price
        self.assertEqual(form["metadata[user_id]"], str(USER))
        # Copied to the subscription, so renewals and failures find the person.
        self.assertEqual(form["subscription_data[metadata][user_id]"], str(USER))
        self.assertEqual(form["success_url"], "https://dmbot.example/account")
        self.assertNotIn("discounts[0][coupon]", form)

    async def test_the_first_month_uses_the_once_only_coupon(self) -> None:
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        await self.provider.checkout_url(
            user_id=USER,
            email=None,
            plan=plans.load().by_id["table"],
            first_month_cents=199,
            return_url="https://dmbot.example/account",
        )
        form = self.http.calls[0][2]
        self.assertEqual(form["discounts[0][coupon]"], "FIRST199")
        self.assertNotIn("customer_email", form)  # none known: none sent

    async def test_a_first_month_without_its_coupon_is_refused_not_charged_in_full(self) -> None:
        with self.assertRaises(PaymentError):
            await self.provider.checkout_url(
                user_id=USER,
                email=None,
                plan=plans.load().by_id["two-tables"],
                first_month_cents=199,
                return_url="https://dmbot.example/account",
            )
        self.assertEqual(self.http.calls, [])

    async def test_a_plan_with_no_price_is_refused(self) -> None:
        with self.assertRaises(PaymentError):
            await self.provider.checkout_url(
                user_id=USER,
                email=None,
                plan=plans.load().by_id["pro"],
                first_month_cents=None,
                return_url="https://dmbot.example/account",
            )
        self.assertEqual(self.http.calls, [])

    async def test_extra_hours_is_a_one_time_payment_for_the_extra_hours_price(self) -> None:
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        await self.provider.extra_hours_checkout_url(
            user_id=USER, email="player@example.com", return_url="https://dmbot.example/account"
        )
        form = self.http.calls[0][2]
        self.assertEqual(form["mode"], "payment")
        self.assertEqual(form["line_items[0][price]"], "price_extra")
        self.assertEqual(form["metadata[item]"], "extra_hours")  # what the webhook looks for
        self.assertEqual(form["metadata[user_id]"], str(USER))
        # And the event it makes is read back as extra hours: the two halves agree.
        parsed = self.provider.parse(
            event(
                "checkout.session.completed",
                session(
                    metadata={"user_id": form["metadata[user_id]"], "item": form["metadata[item]"]}
                ),
            )
        )
        assert parsed is not None
        self.assertEqual(parsed.kind, "extra_hours_bought")

    async def test_a_checkout_address_that_is_not_stripes_is_refused(self) -> None:
        bad_urls = (
            "http://checkout.stripe.com/c",
            "https://evil.example/c",
            "https://checkout.stripe.com.evil.example/c",
            "https://evilcheckout.stripe.com/c",
            None,
            5,
        )
        for bad in bad_urls:
            self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": bad})
            with self.assertRaises(PaymentError, msg=str(bad)):
                await self.provider.checkout_url(
                    user_id=USER,
                    email=None,
                    plan=plans.load().by_id["table"],
                    first_month_cents=None,
                    return_url="https://dmbot.example/account",
                )

    async def test_a_refused_checkout_is_a_payment_error_without_the_body(self) -> None:
        self.http.answers[("POST", "/v1/checkout/sessions")] = (
            400,
            {"error": {"message": "player@example.com"}},
        )
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

    async def test_the_billing_page_is_a_customer_portal_session(self) -> None:
        self.http.answers[("POST", "/v1/billing_portal/sessions")] = (200, {"url": PORTAL_URL})
        got = await self.provider.billing_url(
            customer_id="cus_777", return_url="https://dmbot.example/account"
        )
        self.assertEqual(got, PORTAL_URL)
        form = self.http.calls[0][2]
        self.assertEqual(
            form, {"customer": "cus_777", "return_url": "https://dmbot.example/account"}
        )

    async def test_the_billing_page_refuses_a_foreign_address_and_failures(self) -> None:
        path = ("POST", "/v1/billing_portal/sessions")
        answers: list[tuple[int, Any]] = [
            (200, {"url": "https://evil.example/p"}),
            (200, {"url": "http://billing.stripe.com/p"}),
            (200, {}),
            (200, None),
            (404, None),
            (500, None),
        ]
        for answer in answers:
            self.http.answers[path] = answer
            with self.assertRaises(PaymentError):
                await self.provider.billing_url(
                    customer_id="cus_777", return_url="https://x.example"
                )

    async def test_an_id_that_is_not_stripes_is_never_put_in_a_path(self) -> None:
        for bad in ("../subscriptions/1", "cus_", "", "sub_1", "cus_1/../x", "cus_é", "cus_1\n"):
            with self.assertRaises(PaymentError, msg=bad):
                await self.provider.billing_url(customer_id=bad, return_url="x")
        for bad in ("../customers/1", "sub_", "cus_1", "sub_1?x=1", "five", "sub_1\n"):
            with self.assertRaises(PaymentError, msg=bad):
                await self.provider.cancel(subscription_id=bad)
        self.assertEqual(self.http.calls, [])

    async def test_cancel_sets_cancel_at_period_end_so_the_paid_period_runs_out(self) -> None:
        self.http.answers[("GET", "/v1/subscriptions/sub_55")] = (
            200,
            {"status": "active", "cancel_at_period_end": False},
        )
        self.http.answers[("POST", "/v1/subscriptions/sub_55")] = (200, {})
        await self.provider.cancel(subscription_id="sub_55")
        self.assertEqual(
            self.http.calls[1],
            ("POST", "/v1/subscriptions/sub_55", {"cancel_at_period_end": "true"}),
        )
        # Never an immediate cancel (a DELETE ends it now and may refund).
        self.assertNotIn("DELETE", [call[0] for call in self.http.calls])

    async def test_cancelling_one_already_cancelled_or_gone_succeeds(self) -> None:
        for answer in (
            {"status": "canceled"},
            {"status": "incomplete_expired"},
            {"status": "active", "cancel_at_period_end": True},
        ):
            self.http.calls.clear()
            self.http.answers[("GET", "/v1/subscriptions/sub_55")] = (200, answer)
            await self.provider.cancel(subscription_id="sub_55")
            self.assertEqual([c[0] for c in self.http.calls], ["GET"])  # nothing to change
        await self.provider.cancel(subscription_id="sub_56")  # a 404: already gone

    async def test_cancel_failures_are_errors_so_deletion_retries(self) -> None:
        self.http.answers[("GET", "/v1/subscriptions/sub_55")] = (500, None)
        with self.assertRaises(PaymentError):
            await self.provider.cancel(subscription_id="sub_55")
        self.http.answers[("GET", "/v1/subscriptions/sub_55")] = (200, {"status": "active"})
        self.http.answers[("POST", "/v1/subscriptions/sub_55")] = (500, None)
        with self.assertRaises(PaymentError):
            await self.provider.cancel(subscription_id="sub_55")

    def test_the_key_is_not_in_the_configs_repr(self) -> None:
        self.assertNotIn("sk_test_not_real", repr(CONFIG))


def _answering(status: int, json_result: Any) -> mock.MagicMock:
    """What `aiohttp.ClientSession.request(...)` returns: an async context manager."""
    answer = mock.MagicMock()
    answer.status = status
    answer.json = mock.AsyncMock(
        side_effect=json_result if isinstance(json_result, Exception) else None,
        return_value=json_result,
    )
    context = mock.MagicMock()
    context.__aenter__ = mock.AsyncMock(return_value=answer)
    context.__aexit__ = mock.AsyncMock(return_value=False)
    return context


class Transport(unittest.IsolatedAsyncioTestCase):
    """The real HTTP transport, with the network call patched out."""

    async def test_network_trouble_is_a_payment_error_without_the_key(self) -> None:
        transport = HttpTransport("sk_test_not_real")
        for failure in (aiohttp.ClientConnectionError("down"), TimeoutError()):
            patched = mock.patch.object(aiohttp.ClientSession, "request", side_effect=failure)
            with patched, self.assertRaises(PaymentError) as caught:
                await transport("GET", "/v1/subscriptions/sub_1", None)
            self.assertNotIn("sk_test", str(caught.exception))
        await transport.close()
        await transport.close()  # twice is harmless

    async def test_it_sends_the_key_as_a_bearer_form_and_never_follows_redirects(self) -> None:
        transport = HttpTransport("sk_test_not_real")
        context = _answering(200, {"ok": True})
        with mock.patch.object(aiohttp.ClientSession, "request", return_value=context) as sent:
            got = await transport("POST", "/v1/checkout/sessions", {"mode": "payment"})
        await transport.close()
        self.assertEqual(got, (200, {"ok": True}))
        args, kwargs = sent.call_args
        self.assertEqual(args, ("POST", "https://api.stripe.com/v1/checkout/sessions"))
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer sk_test_not_real"})
        self.assertEqual(kwargs["data"], {"mode": "payment"})
        self.assertFalse(kwargs["allow_redirects"])

    async def test_an_answer_that_is_not_json_is_none_not_a_crash(self) -> None:
        transport = HttpTransport("sk_test_not_real")
        context = _answering(502, ValueError("not json"))
        with mock.patch.object(aiohttp.ClientSession, "request", return_value=context):
            self.assertEqual(await transport("GET", "/v1/x", None), (502, None))
        await transport.close()


class Config(unittest.TestCase):
    ENV: ClassVar[dict[str, str]] = {
        **PaymentSettings.ENV,
        "PAYMENT_PROVIDER": "stripe",
        "PAYMENT_WEBHOOK_SECRET": "s" * 20,
        "STRIPE_SECRET_KEY": "sk_test_key_not_real",
        "STRIPE_PRICE_TABLE": "price_1",
        "STRIPE_PRICE_TWO_TABLES": "price_2",
        "STRIPE_PRICE_GUILD": "price_3",
        "STRIPE_PRICE_EXTRA_HOURS": "price_4",
        "STRIPE_FIRST_MONTH_COUPON_TABLE": "FIRST199",
    }

    def test_it_reads_the_ids_and_hides_the_key(self) -> None:
        loaded = load_web_settings(self.ENV)
        config = loaded.stripe
        assert config is not None
        self.assertEqual(config.prices["two-tables"], "price_2")
        self.assertEqual(config.extra_hours_price, "price_4")
        self.assertEqual(config.first_month_coupons, {"table": "FIRST199"})
        self.assertFalse(config.live)
        self.assertNotIn("key_not_real", repr(loaded))
        self.assertNotIn("key_not_real", repr(config))

    def test_a_live_key_is_live(self) -> None:
        live = load_web_settings({**self.ENV, "STRIPE_SECRET_KEY": "sk_live_key_not_real"})
        assert live.stripe is not None
        self.assertTrue(live.stripe.live)

    def test_a_missing_or_wrong_id_stops_the_api_starting(self) -> None:
        for name in (
            "STRIPE_SECRET_KEY",
            "STRIPE_PRICE_TABLE",
            "STRIPE_PRICE_GUILD",
            "STRIPE_PRICE_EXTRA_HOURS",
        ):
            with self.assertRaisesRegex(ConfigError, "STRIPE"):
                load_web_settings({**self.ENV, name: ""})
        with self.assertRaisesRegex(ConfigError, "STRIPE_SECRET_KEY"):
            load_web_settings({**self.ENV, "STRIPE_SECRET_KEY": "pk_test_publishable"})
        with self.assertRaisesRegex(ConfigError, "STRIPE_PRICE_TABLE"):
            load_web_settings({**self.ENV, "STRIPE_PRICE_TABLE": "prod_1"})
        with self.assertRaisesRegex(ConfigError, "COUPON"):
            load_web_settings({**self.ENV, "STRIPE_FIRST_MONTH_COUPON_TABLE": "two words"})

    def test_two_plans_cannot_share_a_price(self) -> None:
        with self.assertRaisesRegex(ConfigError, "different"):
            load_web_settings({**self.ENV, "STRIPE_PRICE_GUILD": "price_1"})
        with self.assertRaisesRegex(ConfigError, "different"):
            load_web_settings({**self.ENV, "STRIPE_PRICE_EXTRA_HOURS": "price_2"})

    def test_it_needs_the_webhook_secret_and_the_old_company_is_gone(self) -> None:
        with self.assertRaisesRegex(ConfigError, "PAYMENT_WEBHOOK_SECRET"):
            load_web_settings({**self.ENV, "PAYMENT_WEBHOOK_SECRET": ""})
        with self.assertRaisesRegex(ConfigError, "stripe"):
            load_web_settings({**self.ENV, "PAYMENT_PROVIDER": "lemonsqueezy"})


class Routes(DatabaseTest):
    """The whole path with the real writer: a signed Stripe event changes the plan."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.http = Fake()
        self.now = T0 + 10
        self.provider = StripeProvider(
            CONFIG, SECRET, transport=self.http, clock=lambda: float(self.now)
        )
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

    async def deliver(self, body: bytes, header: str | None = None) -> httpx.Response:
        return await self.client.post(
            "/webhooks/stripe",
            content=body,
            headers={
                "Stripe-Signature": header or sign(body, at=self.now),
                "Content-Type": "application/json",
            },
        )

    @staticmethod
    def mine() -> dict[str, str]:
        return {"user_id": str(ALICE.id)}

    def started(self, *, event_id: str = "evt_start", created: int = T0) -> bytes:
        return event(
            "invoice.paid",
            invoice(subscription_details={"metadata": self.mine()}),
            event_id=event_id,
            created=created,
        )

    async def test_a_signed_first_payment_starts_the_plan_and_a_replay_changes_nothing(
        self,
    ) -> None:
        body = self.started()
        self.assertEqual((await self.deliver(body)).status_code, 200)
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        self.assertEqual((got.plan, got.status), ("table", "active"))
        self.assertEqual((await self.deliver(body)).status_code, 200)  # a retry: once only

    async def test_a_bad_or_stale_signature_changes_nothing(self) -> None:
        body = self.started()
        bad = sign(body, at=self.now, secret=b"x" * 32)
        self.assertEqual((await self.deliver(body, bad)).status_code, 400)
        stale = sign(body, at=self.now - 3600)
        self.assertEqual((await self.deliver(body, stale)).status_code, 400)
        self.assertIsNone(await entitlements.get(self.db, ALICE.id))

    async def test_a_failed_first_payment_starts_no_plan(self) -> None:
        await entitlements_writer.start_try_it(self.db, ALICE.id, now=self.now)
        body = event(
            "invoice.payment_failed",
            invoice(status="open", subscription_details={"metadata": self.mine()}),
        )
        # The writer waits for a "started" that never comes (503, so Stripe retries for a
        # while and gives up); nothing changes and no grace is given.
        self.assertEqual((await self.deliver(body)).status_code, 503)
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        self.assertEqual((got.plan, got.status), ("try-it", "active"))

    async def test_a_cancel_is_stored_a_resume_clears_it_and_a_delete_ends_the_plan(self) -> None:
        await self.deliver(self.started())
        cancelled = event(
            "customer.subscription.updated",
            subscription(cancel_at_period_end=True, metadata=self.mine()),
            event_id="evt_cancel",
            created=T0 + 100,
            previous={"cancel_at_period_end": False},
        )
        self.assertEqual((await self.deliver(cancelled)).status_code, 200)
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        self.assertEqual((got.status, got.ends_at), ("active", PERIOD_END))
        resumed = event(
            "customer.subscription.updated",
            subscription(metadata=self.mine()),
            event_id="evt_resume",
            created=T0 + 200,
            previous={"cancel_at_period_end": True},
        )
        self.assertEqual((await self.deliver(resumed)).status_code, 200)
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        self.assertIsNone(got.ends_at)
        deleted = event(
            "customer.subscription.deleted",
            subscription(status="canceled", metadata=self.mine()),
            event_id="evt_deleted",
            created=T0 + 300,
        )
        self.assertEqual((await self.deliver(deleted)).status_code, 200)
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        self.assertEqual(got.status, "lapsed")

    async def test_paid_extra_hours_are_credited(self) -> None:
        await self.deliver(self.started())
        extra = event(
            "checkout.session.completed",
            session(metadata={**self.mine(), "item": "extra_hours"}),
            event_id="evt_extra",
            created=T0 + 50,
        )
        self.assertEqual((await self.deliver(extra)).status_code, 200)
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        self.assertEqual(got.extra_hours, 10)

    async def test_unused_events_are_a_quiet_200_and_unreadable_signed_ones_a_400(self) -> None:
        unused = event("charge.succeeded", {"id": "ch_1"})
        self.assertEqual((await self.deliver(unused)).status_code, 200)
        no_person = event(
            "invoice.paid", invoice(subscription_details={"metadata": {"user_id": "nope"}})
        )
        self.assertEqual((await self.deliver(no_person)).status_code, 400)
        self.assertEqual((await self.deliver(b"not json")).status_code, 400)

    async def test_an_older_change_arriving_late_changes_nothing(self) -> None:
        await self.deliver(self.started(created=T0 + 500))
        older = event(
            "customer.subscription.updated",
            subscription(items={"data": [{"price": {"id": "price_two"}}]}, metadata=self.mine()),
            event_id="evt_old",
            created=T0 + 100,
            previous={"items": {}},
        )
        self.assertEqual((await self.deliver(older)).status_code, 200)
        got = await entitlements.get(self.db, ALICE.id)
        assert got is not None
        self.assertEqual(got.plan, "table")

    async def test_a_billing_failure_is_payments_unavailable(self) -> None:
        await self.deliver(self.started())
        self.http.answers[("POST", "/v1/billing_portal/sessions")] = (500, None)
        response = await self.client.post("/billing/portal", headers={"X-DMbot-Request": "1"})
        self.assertEqual(
            (response.status_code, response.json()), (502, {"error": "payments_unavailable"})
        )

    async def test_checkout_through_the_route_builds_a_stripe_checkout(self) -> None:
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        response = await self.client.post(
            "/billing/checkout", headers={"X-DMbot-Request": "1"}, json={"plan": "two-tables"}
        )
        self.assertEqual((response.status_code, response.json()), (200, {"url": CHECKOUT_URL}))
        form = self.http.calls[0][2]
        self.assertEqual(form["metadata[user_id]"], str(ALICE.id))
        self.assertEqual(form["line_items[0][price]"], "price_two")
