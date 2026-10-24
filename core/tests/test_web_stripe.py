"""The Stripe adapter (#1110): the signature, each event mapping, the first-payment rule,
checkout building, the billing page and cancel, with the HTTP calls faked and made-up payloads
(example.com emails, invented ids). The route tests need Postgres."""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from collections.abc import Callable
from typing import Any, ClassVar
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

import aiohttp
import httpx

from dmbot import entitlements, plans
from dmbot.account_id import to_dmbot_id
from dmbot.config import ConfigError
from dmbot.web import entitlements_writer
from dmbot.web.app import create_app
from dmbot.web.payments import NotResumable, PaymentError, ResumeRefused
from dmbot.web.settings import load_web_settings
from dmbot.web.stripe import API_VERSION, HttpTransport, StripeConfig, StripeProvider
from tests.pg import DatabaseTest
from tests.test_web_api import ALICE, FakeDiscord, settings
from tests.test_web_payments import PaymentSettings

SECRET = b"w" * 32
USER = 424242424242
CONFIG = StripeConfig(
    secret_key="sk_test_not_real",
    prices={"table": "price_table", "two-tables": "price_two", "guild": "price_guild"},
    extra_hours_price="price_extra",
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


def _clock_at(moment: float) -> Callable[[], float]:
    """A clock that always says `moment`."""
    return lambda: moment


class Fake:
    """A fake Stripe API: records calls, answers from `answers`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.keys: list[str | None] = []  # the idempotency key of each call, in order
        self.answers: dict[tuple[str, str], tuple[int, Any]] = {}

    async def __call__(
        self, method: str, path: str, form: Any, idempotency_key: str | None = None
    ) -> tuple[int, Any]:
        self.calls.append((method, path, form))
        self.keys.append(idempotency_key)
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

    def test_the_payers_address_is_kept_from_the_invoice(self) -> None:
        parsed = self.provider.parse(event("invoice.paid", invoice()))
        assert parsed is not None
        self.assertEqual(parsed.email, "player@example.com")
        none = self.provider.parse(event("invoice.paid", invoice(customer_email="not an address")))
        assert none is not None
        self.assertIsNone(none.email)

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
        unchanged: list[dict[str, Any]] = [{}, {"default_payment_method": None}, {"metadata": {}}]
        for previous in unchanged:
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

    def test_a_plan_change_while_a_cancel_is_pending_is_the_cancel_and_says_so_in_the_log(
        self,
    ) -> None:
        body = event(
            "customer.subscription.updated",
            subscription(cancel_at_period_end=True),  # now on the table plan
            previous={"items": {"data": [{"price": {"id": "price_other"}}]}},
        )
        with self.assertLogs("dmbot.web.stripe", level="INFO") as logs:
            parsed = self.provider.parse(body)
        assert parsed is not None
        # Decided on #1122: one event, the cancel; only the end date is stored.
        self.assertEqual(parsed.kind, "subscription_cancelled")
        self.assertEqual(parsed.period_end, PERIOD_END)
        self.assertTrue(any("only the end date is stored" in r.getMessage() for r in logs.records))
        # A plain cancel logs nothing of the kind.
        plain = event(
            "customer.subscription.updated",
            subscription(cancel_at_period_end=True),
            previous={"cancel_at_period_end": False},
        )
        with self.assertNoLogs("dmbot.web.stripe", level="INFO"):
            self.provider.parse(plain)

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
        nobody: dict[str, Any] = {"metadata": {}}
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
        nobody: dict[str, Any] = {"metadata": {}}
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
        previous: dict[str, Any] = {
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

    async def test_every_checkout_carries_the_owners_checkout_studio_parameters(self) -> None:
        """#1446: the same fixed parameters on all three calls; `payment_method_collection`
        only on the two subscriptions; the address is saved to the customer when there is one."""
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        self.http.answers[("POST", "/v1/customers")] = (200, {"id": "cus_new1"})
        audio = StripeProvider(
            StripeConfig(
                secret_key="sk_test_not_real",
                prices=CONFIG.prices,
                extra_hours_price="price_extra",
                recap_audio_price="price_audio",
            ),
            SECRET,
            transport=self.http,
        )
        where = "https://dmbot.example/account"
        plan = plans.load().by_id["table"]
        await self.provider.checkout_url(user_id=USER, email=None, plan=plan, return_url=where)
        await self.provider.extra_hours_checkout_url(user_id=USER, email=None, return_url=where)
        await audio.recap_audio_checkout_url(
            user_id=USER, email=None, return_url=where, customer_id="cus_old9"
        )
        studio = {
            "ui_mode": "hosted_page",
            "billing_address_collection": "auto",
            "phone_number_collection[enabled]": "false",
            "automatic_tax[enabled]": "true",
            "allow_promotion_codes": "true",
            "submit_type": "auto",
            "integration_identifier": "hosted_web_0001",
            "origin_context": "web",
        }
        forms = [form for _, path, form in self.http.calls if path == "/v1/checkout/sessions"]
        self.assertEqual([f["mode"] for f in forms], ["subscription", "payment", "subscription"])
        for form in forms:
            for key, value in studio.items():
                self.assertEqual(form[key], value, key)
            # What the code kept: the real mode, addresses, line items and the person.
            self.assertEqual(form["success_url"], where)
            self.assertEqual(form["cancel_url"], where)
            self.assertEqual(form["line_items[0][quantity]"], "1")
            self.assertEqual(form["metadata[user_id]"], str(USER))
        self.assertEqual(forms[0]["payment_method_collection"], "always")
        self.assertNotIn("payment_method_collection", forms[1])  # not on a one-time payment
        self.assertEqual(forms[2]["payment_method_collection"], "always")
        # Only a call with a customer asks to save the address onto it.
        self.assertNotIn("customer_update[address]", forms[0])
        self.assertEqual(forms[2]["customer"], "cus_old9")
        self.assertEqual(forms[2]["customer_update[address]"], "auto")

    async def test_a_double_click_sends_the_same_idempotency_key_and_expiry(self) -> None:
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})

        async def ask(user: int = USER, email: str | None = None) -> None:
            await self.provider.checkout_url(
                user_id=user,
                email=email,
                plan=plans.load().by_id["table"],
                return_url="https://dmbot.example/account",
            )

        await ask()
        await ask()  # the same request again, moments later
        first, second = self.http.keys
        assert first is not None
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("dmbot-checkout-"))
        self.assertLessEqual(len(first), 255)
        forms = [call[2] for call in self.http.calls]
        self.assertEqual(forms[0]["expires_at"], forms[1]["expires_at"])  # or Stripe refuses it
        # Another person, plan or address is another checkout.
        await ask(user=USER + 1)
        await ask(email="player@example.com")
        self.assertEqual(len({*self.http.keys}), 3)
        self.assertNotIn("player@example.com", " ".join(k or "" for k in self.http.keys))

    async def test_the_expiry_is_at_least_thirty_minutes_away_and_a_new_window_is_a_new_key(
        self,
    ) -> None:
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        start = (int(NOW) // 300 + 1) * 300.0  # the first second of a window
        for now in (start, start + 299.0, start + 301.0):  # its first, its last, the next's
            provider = StripeProvider(CONFIG, SECRET, transport=self.http, clock=_clock_at(now))
            await provider.extra_hours_checkout_url(
                user_id=USER, email=None, return_url="https://dmbot.example/account"
            )
            expires = int(self.http.calls[-1][2]["expires_at"])
            self.assertGreaterEqual(expires - now, 1800 + 60)  # a margin even in the last second
            self.assertLessEqual(expires - now, 1800 + 120 + 300)
        keys = self.http.keys
        self.assertEqual(len(keys), 3)
        self.assertNotEqual(keys[0], keys[2])  # five minutes later: a new checkout is allowed

    async def test_only_checkout_creation_carries_a_key(self) -> None:
        self.http.answers[("POST", "/v1/billing_portal/sessions")] = (
            200,
            {"url": "https://billing.stripe.com/p/x"},
        )
        await self.provider.billing_url(customer_id="cus_777", return_url="https://x.example")
        self.http.answers[("GET", "/v1/subscriptions/sub_55")] = (
            200,
            {"status": "active", "cancel_at_period_end": False},
        )
        self.http.answers[("POST", "/v1/subscriptions/sub_55")] = (200, {})
        await self.provider.cancel(subscription_id="sub_55")
        self.assertEqual(set(self.http.keys), {None})

    async def test_a_moved_account_points_the_customer_and_subscription_at_the_new_id(self) -> None:
        self.http.answers[("POST", "/v1/customers/cus_777")] = (200, {})
        self.http.answers[("POST", "/v1/subscriptions/sub_55")] = (200, {})
        await self.provider.retarget(customer_id="cus_777", subscription_id="sub_55", user_id=99)
        self.assertEqual(
            self.http.calls,
            [
                ("POST", "/v1/customers/cus_777", {"metadata[user_id]": "99"}),
                ("POST", "/v1/subscriptions/sub_55", {"metadata[user_id]": "99"}),
            ],
        )
        self.http.answers[("POST", "/v1/customers/cus_777")] = (500, {})
        with self.assertRaises(PaymentError):
            await self.provider.retarget(customer_id="cus_777", subscription_id=None, user_id=99)
        self.http.answers[("POST", "/v1/customers/cus_777")] = (404, {})
        await self.provider.retarget(
            customer_id="cus_777", subscription_id=None, user_id=99
        )  # gone
        with self.assertRaises(PaymentError):  # an id is checked before it goes into a path
            await self.provider.retarget(customer_id="../x", subscription_id=None, user_id=99)

    async def test_managed_payments_is_asked_for_only_when_switched_on(self) -> None:
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        plan = plans.load().by_id["table"]
        for factory in (self.provider.extra_hours_checkout_url,):
            await factory(user_id=USER, email=None, return_url="https://dmbot.example/account")
        await self.provider.checkout_url(
            user_id=USER,
            email=None,
            plan=plan,
            return_url="https://x.example",
        )
        for _, _, form in self.http.calls:
            self.assertNotIn("managed_payments[enabled]", form)
        managed = StripeProvider(
            StripeConfig(
                secret_key="sk_test_not_real",
                prices=CONFIG.prices,
                extra_hours_price="price_extra",
                managed_payments=True,
            ),
            SECRET,
            transport=self.http,
        )
        self.http.calls.clear()
        await managed.checkout_url(
            user_id=USER,
            email=None,
            plan=plan,
            return_url="https://x.example",
        )
        await managed.extra_hours_checkout_url(
            user_id=USER, email=None, return_url="https://x.example"
        )
        for _, _, form in self.http.calls:
            self.assertEqual(form["managed_payments[enabled]"], "true")

    async def test_a_new_customer_gets_the_dmbot_id_as_invoice_prefix(self) -> None:
        self.http.answers[("POST", "/v1/customers")] = (200, {"id": "cus_new1"})
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        await self.provider.checkout_url(
            user_id=USER,
            email="player@example.com",
            plan=plans.load().by_id["table"],
            return_url="https://dmbot.example/account",
            dmbot_id="ABC123",
        )
        looked, made, session = self.http.calls  # first it looks for one made earlier
        self.assertEqual(looked[1].split("?")[0], "/v1/customers/search")
        self.assertEqual(made[1], "/v1/customers")
        self.assertEqual(made[2]["invoice_prefix"], "ABC123")
        self.assertEqual(made[2]["metadata[user_id]"], str(USER))
        self.assertEqual(session[2]["customer"], "cus_new1")
        self.assertNotIn("customer_email", session[2])  # Stripe takes one or the other
        self.assertTrue((self.http.keys[1] or "").startswith("dmbot-customer-"))  # same request,
        # same customer within Stripe's key window

    async def test_a_customer_made_earlier_is_found_not_made_again(self) -> None:
        query = urlencode({"query": f"metadata['user_id']:'{USER}'", "limit": "1"})
        self.http.answers[("GET", f"/v1/customers/search?{query}")] = (
            200,
            {"data": [{"id": "cus_early7"}]},
        )
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        await self.provider.checkout_url(
            user_id=USER,
            email=None,
            plan=plans.load().by_id["table"],
            return_url="https://dmbot.example/account",
            dmbot_id="ABC123",
        )
        paths = [(m, p.split("?")[0]) for m, p, _ in self.http.calls]
        self.assertEqual(
            paths, [("GET", "/v1/customers/search"), ("POST", "/v1/checkout/sessions")]
        )
        self.assertEqual(self.http.calls[-1][2]["customer"], "cus_early7")

    async def test_an_id_too_short_for_stripe_makes_the_customer_without_a_prefix(self) -> None:
        self.http.answers[("POST", "/v1/customers")] = (200, {"id": "cus_short1"})
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        await self.provider.checkout_url(
            user_id=USER,
            email=None,
            plan=plans.load().by_id["table"],
            return_url="https://dmbot.example/account",
            dmbot_id="AB",
        )
        made = next(f for m, p, f in self.http.calls if (m, p) == ("POST", "/v1/customers"))
        self.assertNotIn("invoice_prefix", made)
        with self.assertRaises(PaymentError):
            await self.provider.set_invoice_prefix("cus_short1", "AB")

    async def test_a_known_customer_is_reused_not_made_again(self) -> None:
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        await self.provider.checkout_url(
            user_id=USER,
            email=None,
            plan=plans.load().by_id["table"],
            return_url="https://dmbot.example/account",
            dmbot_id="ABC123",
            customer_id="cus_old9",
        )
        [(_, path, form)] = self.http.calls
        self.assertEqual((path, form["customer"]), ("/v1/checkout/sessions", "cus_old9"))

    async def test_a_customer_stripe_would_not_make_stops_the_checkout(self) -> None:
        self.http.answers[("POST", "/v1/customers")] = (400, None)
        with self.assertRaises(PaymentError):
            await self.provider.checkout_url(
                user_id=USER,
                email=None,
                plan=plans.load().by_id["table"],
                return_url="https://dmbot.example/account",
                dmbot_id="ABC123",
            )
        # It looked, it tried to make the customer, and went no further: no checkout.
        self.assertEqual(len(self.http.calls), 2)

    async def test_an_existing_customer_can_be_given_the_prefix(self) -> None:
        self.http.answers[("POST", "/v1/customers/cus_old9")] = (200, {})
        await self.provider.set_invoice_prefix("cus_old9", "ABC123")
        self.assertEqual(self.http.calls[0][2], {"invoice_prefix": "ABC123"})
        with self.assertRaises(PaymentError):
            await self.provider.set_invoice_prefix("not a customer", "ABC123")

    async def test_a_plan_with_no_price_is_refused(self) -> None:
        with self.assertRaises(PaymentError):
            await self.provider.checkout_url(
                user_id=USER,
                email=None,
                plan=plans.load().by_id["pro"],
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

    async def test_change_plan_opens_on_the_plan_list_with_a_subscription_update_flow(self) -> None:
        # #2165: the portal opens straight on choosing a plan, not on Stripe's overview.
        self.http.answers[("POST", "/v1/billing_portal/sessions")] = (200, {"url": PORTAL_URL})
        got = await self.provider.billing_url(
            customer_id="cus_777",
            return_url="https://dmbot.example/account",
            subscription_id="sub_55",
        )
        self.assertEqual(got, PORTAL_URL)
        self.assertEqual(len(self.http.calls), 1)
        self.assertEqual(
            self.http.calls[0][2],
            {
                "customer": "cus_777",
                "return_url": "https://dmbot.example/account",
                "flow_data[type]": "subscription_update",
                "flow_data[subscription_update][subscription]": "sub_55",
            },
        )

    async def test_if_stripe_refuses_the_plan_flow_the_ordinary_page_opens(self) -> None:
        answers = [(400, {"error": {"message": "flow not allowed"}}), (200, {"url": PORTAL_URL})]
        # An outage or a rate limit is not that refusal: it is an error, and no second call is made.
        self.http.answers[("POST", "/v1/billing_portal/sessions")] = (503, None)
        for status in (401, 429, 503):
            self.http.answers[("POST", "/v1/billing_portal/sessions")] = (status, None)
            self.http.calls.clear()
            with self.assertRaises(PaymentError):
                await self.provider.billing_url(
                    customer_id="cus_777", return_url="https://x.example", subscription_id="sub_55"
                )
            self.assertEqual(len(self.http.calls), 1, status)
        self.http.calls.clear()

        class Queue(Fake):
            async def __call__(
                self, method: str, path: str, form: Any, idempotency_key: str | None = None
            ) -> tuple[int, Any]:
                await super().__call__(method, path, form, idempotency_key)
                return answers[len(self.calls) - 1]

        self.http = Queue()
        provider = StripeProvider(CONFIG, SECRET, transport=self.http, clock=lambda: NOW)
        got = await provider.billing_url(
            customer_id="cus_777", return_url="https://x.example", subscription_id="sub_55"
        )
        self.assertEqual(got, PORTAL_URL)
        self.assertIn("flow_data[type]", self.http.calls[0][2])
        self.assertNotIn("flow_data[type]", self.http.calls[1][2])

    async def test_resume_clears_a_cancellation_and_does_nothing_if_there_is_none(self) -> None:
        path = ("GET", "/v1/subscriptions/sub_55")
        self.http.answers[path] = (200, {"status": "active", "cancel_at_period_end": True})
        self.http.answers[("POST", "/v1/subscriptions/sub_55")] = (200, {})
        await self.provider.resume(subscription_id="sub_55")
        self.assertEqual(self.http.calls[1][2], {"cancel_at_period_end": "false"})
        # A cancel date (the other way the portal cancels) is cleared too.
        self.http.calls.clear()
        self.http.answers[path] = (200, {"status": "active", "cancel_at": T0 + 99})
        await self.provider.resume(subscription_id="sub_55")
        self.assertEqual(self.http.calls[1][2], {"cancel_at_period_end": "false", "cancel_at": ""})
        # Not cancelled: no write.
        self.http.calls.clear()
        self.http.answers[path] = (200, {"status": "active", "cancel_at_period_end": False})
        await self.provider.resume(subscription_id="sub_55")
        self.assertEqual([c[0] for c in self.http.calls], ["GET"])

    async def test_resume_fails_for_an_ended_subscription_and_a_refusal(self) -> None:
        path = ("GET", "/v1/subscriptions/sub_55")
        # Ended, or overdue (its failed invoice would be retried): never resumed.
        for status in ("canceled", "incomplete_expired", "past_due", "unpaid", "incomplete"):
            self.http.answers[path] = (200, {"status": status, "cancel_at_period_end": True})
            with self.assertRaises(NotResumable, msg=status):
                await self.provider.resume(subscription_id="sub_55")
        self.assertEqual([c[0] for c in self.http.calls], ["GET"] * 5)  # no write
        self.http.answers[path] = (200, {"status": "active", "cancel_at_period_end": True})
        self.http.answers[("POST", "/v1/subscriptions/sub_55")] = (400, None)
        with self.assertRaises(ResumeRefused):  # a scheduled change can refuse a direct update
            await self.provider.resume(subscription_id="sub_55")
        # Only a 400 is that refusal: a 401, 403, 429 or 5xx is an error.
        for code in (401, 403, 429, 500):
            self.http.answers[("POST", "/v1/subscriptions/sub_55")] = (code, None)
            with self.assertRaises(PaymentError) as caught:
                await self.provider.resume(subscription_id="sub_55")
            self.assertNotIsInstance(caught.exception, ResumeRefused, code)
        self.http.answers[path] = (200, {"status": "canceled"})
        self.http.answers[path] = (200, {"status": "active", "cancel_at_period_end": True})
        self.http.answers[("POST", "/v1/subscriptions/sub_55")] = (500, None)
        with self.assertRaises(PaymentError):
            await self.provider.resume(subscription_id="sub_55")
        with self.assertRaises(PaymentError):
            await self.provider.resume(subscription_id="../x")

    async def test_the_owner_of_a_subscription_is_its_metadata_unless_it_is_the_add_on(
        self,
    ) -> None:
        path = ("GET", "/v1/subscriptions/sub_55")
        self.http.answers[path] = (200, {"metadata": {"user_id": str(USER)}})
        self.assertEqual(await self.provider.subscription_owner(subscription_id="sub_55"), USER)
        self.http.answers[path] = (200, {"metadata": {"user_id": str(USER), "item": "recap_audio"}})
        self.assertIsNone(await self.provider.subscription_owner(subscription_id="sub_55"))
        self.http.answers[path] = (200, {"metadata": {}})
        self.assertIsNone(await self.provider.subscription_owner(subscription_id="sub_55"))
        self.http.answers[path] = (200, {"metadata": {"user_id": "not-a-number"}})
        self.assertIsNone(await self.provider.subscription_owner(subscription_id="sub_55"))
        self.http.answers[path] = (404, None)  # gone at the company: not ours
        self.assertIsNone(await self.provider.subscription_owner(subscription_id="sub_55"))
        self.http.answers[path] = (500, None)
        with self.assertRaises(PaymentError):
            await self.provider.subscription_owner(subscription_id="sub_55")

    # A plan change set for the end of the period (#2165)

    def _schedule(self, **changes: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": "sub_sched_1",
            "object": "subscription_schedule",
            "status": "active",
            "customer": "cus_777",
            "subscription": "sub_55",
            "current_phase": {"start_date": T0, "end_date": PERIOD_END},
            "phases": [
                {"start_date": T0, "end_date": PERIOD_END, "items": [{"price": "price_guild"}]},
                {"start_date": PERIOD_END, "items": [{"price": "price_table"}]},
            ],
        }
        body.update(changes)
        return body

    def test_a_schedule_with_a_later_phase_on_another_plan_is_a_plan_change_set_for_the_end(
        self,
    ) -> None:
        for name in ("subscription_schedule.created", "subscription_schedule.updated"):
            got = self.provider.parse(event(name, self._schedule()))
            assert got is not None
            self.assertEqual(
                (got.kind, got.plan, got.period_start, got.subscription_id, got.user_id),
                ("plan_scheduled", "table", PERIOD_END, "sub_55", 0),
            )

    def test_a_schedule_that_ends_or_is_undone_clears_the_pending_plan(self) -> None:
        for name in (
            "subscription_schedule.released",
            "subscription_schedule.canceled",
            "subscription_schedule.completed",
            "subscription_schedule.aborted",
        ):
            got = self.provider.parse(event(name, self._schedule(status="released")))
            assert got is not None
            self.assertEqual((got.kind, got.subscription_id), ("plan_unscheduled", "sub_55"), name)
        # An update that no longer has a later phase, or a schedule that is not running, clears too.
        one = self._schedule()
        one["phases"] = one["phases"][:1]
        got = self.provider.parse(event("subscription_schedule.updated", one))
        assert got is not None
        self.assertEqual(got.kind, "plan_unscheduled")
        got = self.provider.parse(
            event("subscription_schedule.updated", self._schedule(status="canceled"))
        )
        assert got is not None
        self.assertEqual(got.kind, "plan_unscheduled")

    def test_a_schedule_on_a_price_dmbot_does_not_sell_or_without_a_subscription_is_ignored(
        self,
    ) -> None:
        other = self._schedule()
        other["phases"][1]["items"] = [{"price": "price_somebody_elses"}]
        self.assertIsNone(self.provider.parse(event("subscription_schedule.updated", other)))
        self.assertIsNone(
            self.provider.parse(
                event("subscription_schedule.updated", self._schedule(subscription=None))
            )
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
        self.assertEqual(
            kwargs["headers"],
            {"Authorization": "Bearer sk_test_not_real", "Stripe-Version": API_VERSION},
        )
        self.assertEqual(kwargs["data"], {"mode": "payment"})
        self.assertFalse(kwargs["allow_redirects"])

    def test_the_pinned_api_version_is_the_one_the_webhook_endpoint_uses(self) -> None:
        # The owner's test-mode endpoint was created on endive because the dashboard no longer
        # offers dahlia (#1703): calls and events must agree. Moving this is a decision.
        self.assertEqual(API_VERSION, "2026-09-30.endive")

    async def test_it_sends_the_idempotency_key_as_a_header_only_when_given(self) -> None:
        transport = HttpTransport("sk_test_not_real")
        context = _answering(200, {"ok": True})
        with mock.patch.object(aiohttp.ClientSession, "request", return_value=context) as sent:
            await transport(
                "POST", "/v1/checkout/sessions", {"mode": "payment"}, "dmbot-checkout-x"
            )
            await transport("GET", "/v1/subscriptions/sub_1", None)
        await transport.close()
        with_key, without = (call.kwargs["headers"] for call in sent.call_args_list)
        self.assertEqual(with_key["Idempotency-Key"], "dmbot-checkout-x")
        self.assertEqual(with_key["Authorization"], "Bearer sk_test_not_real")
        self.assertNotIn("Idempotency-Key", without)

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
    }

    def test_it_reads_the_ids_and_hides_the_key(self) -> None:
        loaded = load_web_settings(self.ENV)
        config = loaded.stripe
        assert config is not None
        self.assertEqual(config.prices["two-tables"], "price_2")
        self.assertEqual(config.extra_hours_price, "price_4")
        self.assertFalse(config.live)
        self.assertFalse(config.managed_payments)
        self.assertNotIn("key_not_real", repr(loaded))
        self.assertNotIn("key_not_real", repr(config))

    def test_managed_payments_is_a_switch_that_must_be_a_switch(self) -> None:
        on = load_web_settings({**self.ENV, "STRIPE_MANAGED_PAYMENTS": "1"})
        assert on.stripe is not None
        self.assertTrue(on.stripe.managed_payments)
        with self.assertRaisesRegex(ConfigError, "STRIPE_MANAGED_PAYMENTS"):
            load_web_settings({**self.ENV, "STRIPE_MANAGED_PAYMENTS": "maybe"})

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
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
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
        self.http.answers[("POST", "/v1/customers")] = (200, {"id": "cus_route1"})
        self.http.answers[("POST", "/v1/checkout/sessions")] = (200, {"url": CHECKOUT_URL})
        response = await self.client.post(
            "/billing/checkout", headers={"X-DMbot-Request": "1"}, json={"plan": "two-tables"}
        )
        self.assertEqual((response.status_code, response.json()), (200, {"url": CHECKOUT_URL}))
        customer = next(f for m, p, f in self.http.calls if (m, p) == ("POST", "/v1/customers"))
        self.assertEqual(customer["metadata[user_id]"], str(ALICE.id))
        # Test accounts have small ids, too short for Stripe's 3-character prefix: no prefix.
        self.assertEqual(
            customer.get("invoice_prefix"),
            (to_dmbot_id(ALICE.id) if len(to_dmbot_id(ALICE.id)) >= 3 else None),
        )
        form = self.http.calls[-1][2]
        self.assertEqual(form["customer"], "cus_route1")
        self.assertEqual(form["metadata[user_id]"], str(ALICE.id))
        self.assertEqual(form["line_items[0][price]"], "price_two")
