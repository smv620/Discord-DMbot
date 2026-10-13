"""Stripe as the payment company (#1110, #498 item 5; owner's choice, 2026-10-13).

Stripe holds all card details. This module only turns DMbot's questions into its API calls and
its signed webhook events into `PaymentEvent`s, and meets every rule in the `payments.py`
docstring:

- **Signatures** are the `Stripe-Signature` header (`t=<seconds>,v1=<hex>`): the HMAC-SHA256 of
  `"<t>.<raw body>"` with the endpoint's signing secret, compared as bytes. An event older (or
  newer) than five minutes is refused, so a captured delivery can't be replayed later. A
  malformed header or non-ASCII text is a refusal, not a crash.
- **Started only after the first payment.** A Stripe subscription exists before it is paid, so
  `customer.subscription.created` starts nothing; `invoice.paid` with `billing_reason`
  `subscription_create` does. A failed payment arrives as `invoice.payment_failed`, sent as
  `payment_failed`: the writer gives the grace only to someone who has paid before.
- **Changes take effect when they happen.** A renewal is `invoice.paid` with
  `subscription_cycle`; a plan change is `customer.subscription.updated` whose price has just
  changed (Stripe sends it when the change is made, and a scheduled one when its phase begins).
- **Cancelling** (`cancel_at_period_end`) is stored with the period's end so the account page
  can say so (#824); clearing it is a resume, sent as a renewal. `customer.subscription.deleted`
  ends the plan.
- **`occurred_at` is Stripe's own** event `created`, and the event id is Stripe's `id`, so a
  re-delivery is the same id.
- **The email goes in the API call body**, never in the checkout address.
- **`cancel` sets `cancel_at_period_end`** (the paid period runs out, no refund, no new charge);
  it is a no-op for one already cancelled or gone.
- **Test and live** are told apart by the key (`sk_test_` or `sk_live_`): an event whose
  `livemode` doesn't match is ignored, so a mix-up never changes a plan.

The person is the `user_id` in the checkout's metadata, copied into the subscription's
metadata so renewals and failures find them. Keys, secrets and payloads with personal data are
never logged.

API: https://docs.stripe.com/api. Webhooks: https://docs.stripe.com/webhooks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from dmbot.plans import Plan
from dmbot.web.payments import EventKind, PaymentError, PaymentEvent

log = logging.getLogger(__name__)

API_BASE = "https://api.stripe.com"
SIGNATURE_HEADER = "stripe-signature"
# How far a signed timestamp may be from now, either way (Stripe's own default).
TOLERANCE_SECONDS = 300

# (method, path, form fields or None) -> (HTTP status, parsed JSON or None). Faked in tests.
# Stripe takes form-encoded bodies; nested fields are written the way it reads them
# ("line_items[0][price]").
Transport = Callable[[str, str, "dict[str, str] | None"], Awaitable["tuple[int, Any]"]]

_CUSTOMER = re.compile(r"^cus_[A-Za-z0-9]+$")
_SUBSCRIPTION = re.compile(r"^sub_[A-Za-z0-9]+$")


@dataclass(frozen=True, slots=True)
class StripeConfig:
    """Everything from the environment (names in .env.example). Never logged."""

    secret_key: str = field(repr=False)
    # A paid plan's id (plans.json) -> its recurring Price id in Stripe.
    prices: Mapping[str, str]
    extra_hours_price: str
    # A paid plan's id -> the once-only coupon that takes the price down to the first-month
    # price (#435). Missing: a first-month checkout for that plan is refused, so nobody is
    # charged more than was promised.
    first_month_coupons: Mapping[str, str] = field(default_factory=dict)

    @property
    def live(self) -> bool:
        """Stripe's test keys start `sk_test_` (or `rk_test_`), so there is no separate test
        switch to get out of step with the key."""
        return not self.secret_key.startswith(("sk_test_", "rk_test_"))


class HttpTransport:
    """The real calls, with short timeouts: a person is waiting on the button."""

    def __init__(self, secret_key: str, base: str = API_BASE) -> None:
        self._headers = {"Authorization": f"Bearer {secret_key}"}
        self._base = base
        self._http: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def __call__(
        self, method: str, path: str, form: dict[str, str] | None
    ) -> tuple[int, Any]:
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6, connect=3))
        try:
            async with self._http.request(
                method, self._base + path, data=form, headers=self._headers, allow_redirects=False
            ) as response:
                try:
                    parsed = await response.json(content_type=None)
                except ValueError:
                    parsed = None
                return response.status, parsed
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise PaymentError(f"Couldn't reach Stripe ({type(exc).__name__})") from exc


def _stripe_page(url: object, host: str, what: str) -> str:
    """The browser is sent here, so it must be Stripe's own page over https."""
    parts = urlsplit(url) if isinstance(url, str) else None
    if parts is None or parts.scheme != "https" or parts.hostname != host:
        raise PaymentError(f"Stripe's {what} address was unexpected")
    assert isinstance(url, str)
    return url


def _checked(value: str, pattern: re.Pattern[str], what: str) -> str:
    """An id from the database, checked before it goes into a path."""
    if not pattern.match(value):
        raise PaymentError(f"{what} is not a Stripe id")
    return value


class StripeProvider:
    name = "stripe"

    def __init__(
        self,
        config: StripeConfig,
        webhook_secret: bytes,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._secret = webhook_secret
        self._clock = clock
        self._http: Transport = transport or HttpTransport(config.secret_key)
        self._plan_of_price = {price: plan for plan, price in config.prices.items()}

    async def close(self) -> None:
        if isinstance(self._http, HttpTransport):
            await self._http.close()

    # --- asking Stripe ---

    async def _checkout(self, form: dict[str, str], email: str | None) -> str:
        if email:
            form["customer_email"] = email  # server-side only: never in the address returned
        status, answer = await self._http("POST", "/v1/checkout/sessions", form)
        if status != 200:
            raise PaymentError(f"Stripe answered {status} to a checkout")
        url = answer.get("url") if isinstance(answer, dict) else None
        return _stripe_page(url, "checkout.stripe.com", "checkout")

    async def checkout_url(
        self,
        *,
        user_id: int,
        email: str | None,
        plan: Plan,
        first_month_cents: int | None,
        return_url: str,
    ) -> str:
        price = self._config.prices.get(plan.id)
        if price is None:
            raise PaymentError(f"No Stripe price is set for the {plan.id} plan")
        form = {
            "mode": "subscription",
            "line_items[0][price]": price,
            "line_items[0][quantity]": "1",
            "success_url": return_url,
            "cancel_url": return_url,
            "metadata[user_id]": str(user_id),
            # Renewals and failures carry the subscription's metadata, not the checkout's.
            "subscription_data[metadata][user_id]": str(user_id),
        }
        if first_month_cents is not None:
            # The $1.99 first month (#435) is a once-only coupon made in Stripe; without one
            # set up, refuse rather than charge the full price.
            coupon = self._config.first_month_coupons.get(plan.id)
            if coupon is None:
                raise PaymentError(f"No first-month coupon is set for the {plan.id} plan")
            form["discounts[0][coupon]"] = coupon
        return await self._checkout(form, email)

    async def extra_hours_checkout_url(
        self, *, user_id: int, email: str | None, return_url: str
    ) -> str:
        """The page to buy +10 hours for this month (plans.json `extraHours`); sold by
        `POST /billing/extra-hours` (#1089). A one-time payment, marked in the metadata so the
        webhook knows it from any other checkout."""
        form = {
            "mode": "payment",
            "line_items[0][price]": self._config.extra_hours_price,
            "line_items[0][quantity]": "1",
            "success_url": return_url,
            "cancel_url": return_url,
            "metadata[user_id]": str(user_id),
            "metadata[item]": EXTRA_HOURS_ITEM,
        }
        return await self._checkout(form, email)

    async def billing_url(self, *, customer_id: str, return_url: str) -> str:
        """The customer portal (change plan, fix a payment, stop). Stripe makes a short-lived
        session per visit, so it is asked for each time."""
        form = {
            "customer": _checked(customer_id, _CUSTOMER, "customer id"),
            "return_url": return_url,
        }
        status, answer = await self._http("POST", "/v1/billing_portal/sessions", form)
        if status != 200:
            raise PaymentError(f"Stripe answered {status} for a billing page")
        url = answer.get("url") if isinstance(answer, dict) else None
        return _stripe_page(url, "billing.stripe.com", "billing")

    async def cancel(self, *, subscription_id: str) -> None:
        path = f"/v1/subscriptions/{_checked(subscription_id, _SUBSCRIPTION, 'subscription id')}"
        status, answer = await self._http("GET", path, None)
        if status == 404:
            return  # already gone: a retried deletion still succeeds
        if status != 200:
            raise PaymentError(f"Stripe answered {status} for a subscription")
        current = answer if isinstance(answer, dict) else {}
        if current.get("status") in ("canceled", "incomplete_expired"):
            return
        if current.get("cancel_at_period_end") is True:
            return  # already set: nothing to do
        # Runs to the end of the period already paid for: no refund, no new charge.
        status, _ = await self._http("POST", path, {"cancel_at_period_end": "true"})
        if status not in (200, 404):
            raise PaymentError(f"Stripe answered {status} to a cancel")

    # --- Stripe asking us ---

    def verify(self, body: bytes, headers: Mapping[str, str]) -> bool:
        try:
            header = headers.get(SIGNATURE_HEADER, "").strip()
            # "t=1700000000,v1=<hex>[,v1=<hex>]": more than one v1 while a secret is rolled.
            stamp = ""
            signatures: list[bytes] = []
            for part in header.split(","):
                key, _, value = part.strip().partition("=")
                if key == "t":
                    stamp = value
                elif key == "v1":
                    signatures.append(value.encode("ascii"))
            if not stamp.isascii() or not stamp.isdigit() or not signatures:
                return False
            if abs(self._clock() - int(stamp)) > TOLERANCE_SECONDS:
                return False
        except (UnicodeEncodeError, ValueError):
            return False  # not a signature Stripe could have made
        signed = stamp.encode("ascii") + b"." + body
        expected = hmac.new(self._secret, signed, hashlib.sha256).hexdigest().encode("ascii")
        return any(hmac.compare_digest(expected, given) for given in signatures)

    def parse(self, body: bytes) -> PaymentEvent | None:
        raw = json.loads(body)
        if not isinstance(raw, dict):
            raise ValueError("a payment event must be a JSON object")
        name = str(raw.get("type", ""))
        if name not in _USED:
            log.info("Stripe event %.40r isn't one DMbot uses", name)
            return None
        # A test event with a live key (or the reverse) never changes a plan.
        if bool(raw.get("livemode")) != self._config.live:
            log.warning("Stripe event %.40r ignored: wrong mode (test/live)", name)
            return None
        data = raw.get("data")
        obj = data.get("object") if isinstance(data, dict) else None
        if not isinstance(obj, dict):
            raise ValueError("a payment event has no object")
        event_id = str(raw["id"])
        when = int(raw["created"])
        # The person is needed only for an event that will be applied: one DMbot ignores (a
        # subscription made outside its checkout, say) must be a quiet 200, not an error
        # someone is asked to look at.
        if name == "checkout.session.completed":
            return self._extra_hours(obj, event_id, when)
        if name.startswith("invoice."):
            return self._invoice(name, obj, event_id, when)
        return self._subscription(name, obj, raw.get("data", {}), event_id, when)

    def _extra_hours(
        self, session: dict[str, Any], event_id: str, when: int
    ) -> PaymentEvent | None:
        """Extra hours only when it is a paid one-time payment DMbot's own checkout marked:
        the plan's checkout also completes a session, and that is not ours to count."""
        if session.get("mode") != "payment" or session.get("payment_status") != "paid":
            return None
        metadata = session.get("metadata") or {}
        if not isinstance(metadata, dict) or metadata.get("item") != EXTRA_HOURS_ITEM:
            return None
        return PaymentEvent(
            provider=self.name,
            event_id=event_id,
            kind="extra_hours_bought",
            user_id=_person(metadata),
            occurred_at=when,
            customer_id=_text(session.get("customer")),
        )

    def _invoice(
        self, name: str, invoice: dict[str, Any], event_id: str, when: int
    ) -> PaymentEvent | None:
        details = _subscription_details(invoice)
        subscription_id = _text(details.get("subscription") or invoice.get("subscription"))
        if subscription_id is None:
            return None  # a one-off invoice, not a subscription's
        common: dict[str, Any] = {
            "provider": self.name,
            "event_id": event_id,
            "occurred_at": when,
            "customer_id": _text(invoice.get("customer")),
            "subscription_id": subscription_id,
        }
        if name == "invoice.payment_failed":
            return PaymentEvent(
                kind="payment_failed", user_id=_person(details["metadata"]), **common
            )
        reason = invoice.get("billing_reason")
        kind: EventKind
        if reason == "subscription_create":
            kind = "subscription_started"
        elif reason == "subscription_cycle":
            kind = "subscription_renewed"
        else:
            return None  # a plan change's proration invoice, or a manual one
        if invoice.get("status") != "paid":
            return None
        line = _first(invoice.get("lines"))
        price = _price_id(line)
        plan = self._plan_of_price.get(price or "")
        if plan is None:
            # A price missing from the settings: the writer will refuse it (422) and Stripe
            # will retry, so say which, for the person setting this up.
            log.error(
                "Stripe event %.60s has price %.40r, which no plan is set to", event_id, price
            )
        period = line.get("period") or {}
        return PaymentEvent(
            kind=kind,
            user_id=_person(details["metadata"]),
            plan=plan,
            period_start=_int(period.get("start")),
            period_end=_int(period.get("end")),
            **common,
        )

    def _subscription(
        self,
        name: str,
        subscription: dict[str, Any],
        data: dict[str, Any],
        event_id: str,
        when: int,
    ) -> PaymentEvent | None:
        metadata = subscription.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("a subscription has no metadata")
        item = _first(subscription.get("items"))
        price = _price_id(item)
        plan = self._plan_of_price.get(price or "")
        start = _int(subscription.get("current_period_start") or item.get("current_period_start"))
        end = _int(subscription.get("current_period_end") or item.get("current_period_end"))
        common: dict[str, Any] = {
            "provider": self.name,
            "event_id": event_id,
            "occurred_at": when,
            "customer_id": _text(subscription.get("customer")),
            "subscription_id": _text(subscription.get("id")),
        }
        if name == "customer.subscription.deleted":
            return PaymentEvent(kind="subscription_ended", user_id=_person(metadata), **common)
        if subscription.get("status") != "active":
            return None  # incomplete, past_due, unpaid: a failed payment arrives as its own event
        changed = data.get("previous_attributes") or {}
        if not isinstance(changed, dict):
            changed = {}
        cancel_at = _int(subscription.get("cancel_at"))
        if subscription.get("cancel_at_period_end") is True or cancel_at is not None:
            # Cancelled on the payment page: the plan works to the end of the paid period and
            # then ends (stored so the page can say so, #824). The portal sets either
            # `cancel_at_period_end` or a `cancel_at` date, depending on the API version.
            return PaymentEvent(
                kind="subscription_cancelled",
                user_id=_person(metadata),
                plan=plan,
                period_end=cancel_at or end,
                **common,
            )
        resumed = changed.get("cancel_at_period_end") is True or (
            changed.get("cancel_at") is not None
        )
        # A price change that has taken effect: Stripe's "previous" holds the old items.
        if resumed or "items" in changed or "plan" in changed:
            if plan is None:
                log.error(
                    "Stripe event %.60s has price %.40r, which no plan is set to", event_id, price
                )
            return PaymentEvent(
                kind="subscription_renewed",
                user_id=_person(metadata),
                plan=plan,
                period_start=start,
                period_end=end,
                **common,
            )
        return None


EXTRA_HOURS_ITEM = "extra_hours"

_USED = (
    "invoice.paid",
    "invoice.payment_failed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "checkout.session.completed",
)


def _person(metadata: object) -> int:
    """Our Discord user id, from the checkout's (or subscription's) metadata."""
    user_id = int(metadata["user_id"]) if isinstance(metadata, dict) else 0
    if user_id <= 0:
        raise ValueError("a payment event has no user_id in its metadata")
    return user_id


def _subscription_details(invoice: dict[str, Any]) -> dict[str, Any]:
    """The subscription an invoice belongs to, as both API shapes put it: older versions on
    the invoice itself, newer ones under `parent`. Always has `metadata` (maybe empty)."""
    parent = invoice.get("parent")
    nested = parent.get("subscription_details") if isinstance(parent, dict) else None
    details = nested or invoice.get("subscription_details") or {}
    if not isinstance(details, dict):
        details = {}
    return {"metadata": {}, **details}


def _first(listing: object) -> dict[str, Any]:
    """The first entry of a Stripe list (`{"data": [...]}`): DMbot's plans have one line."""
    rows = listing.get("data") if isinstance(listing, dict) else None
    first = rows[0] if isinstance(rows, list) and rows else None
    return first if isinstance(first, dict) else {}


def _price_id(line: dict[str, Any]) -> str | None:
    """The price of an invoice line or subscription item, under either API shape."""
    price = line.get("price")
    if isinstance(price, dict):
        return _text(price.get("id"))
    pricing = line.get("pricing")
    details = pricing.get("price_details") if isinstance(pricing, dict) else None
    return _text(details.get("price")) if isinstance(details, dict) else None


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
