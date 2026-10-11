"""Lemon Squeezy as the payment company (#1083, #498 item 5; owner's choice, #922).

Lemon Squeezy is the seller (a merchant of record) and holds all card details. This module
only turns DMbot's questions into its API calls and its signed webhook events into
`PaymentEvent`s, and meets every rule in the `payments.py` docstring:

- **Signatures** are compared as bytes (`X-Signature` is the HMAC-SHA256 of the raw body with
  the webhook secret, in hex). A header with non-ASCII text is a refusal, not a crash.
- **Started only after the first payment.** `subscription_created` is sent as
  `subscription_started` only when Lemon Squeezy says the subscription is `active` (it is
  not created until the first payment has gone through). A failed payment arrives as
  `subscription_payment_failed` and is sent as `payment_failed`: the writer gives the grace
  only to someone who has paid before.
- **Changes take effect when they happen.** A plan change or a renewal arrives as
  `subscription_updated` and is sent when Lemon Squeezy makes it, never when it is scheduled.
- **`occurred_at` is Lemon Squeezy's own** `updated_at`, never the delivery time.
- **The email goes in the API call**, never in the checkout address.
- **`cancel` cancels at the end of the paid period** (Lemon Squeezy's DELETE on a
  subscription); it is a no-op for one already cancelled or gone.

Lemon Squeezy's events carry no id of their own, so an event's id here is the event name, the
object's id and its `updated_at`: a re-delivery is the same id, a new change is a new one.
The person is found by the `user_id` in the checkout's custom data. Keys, secrets and
payloads with personal data are never logged.

API: https://docs.lemonsqueezy.com/api. Webhooks: https://docs.lemonsqueezy.com/help/webhooks.
"""

from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from dmbot.plans import Plan
from dmbot.web.payments import EventKind, PaymentError, PaymentEvent

log = logging.getLogger(__name__)

API_BASE = "https://api.lemonsqueezy.com"
SIGNATURE_HEADER = "x-signature"
JSON_API = "application/vnd.api+json"

# (method, path, JSON body or None) -> (HTTP status, parsed JSON or None). Faked in tests.
Transport = Callable[[str, str, "dict[str, Any] | None"], Awaitable["tuple[int, Any]"]]


@dataclass(frozen=True, slots=True)
class LemonSqueezyConfig:
    """Everything from the environment (names in .env.example). Never logged."""

    api_key: str = field(repr=False)
    store_id: str
    # A paid plan's id (plans.json) -> its variant id in Lemon Squeezy.
    variants: Mapping[str, str]
    extra_hours_variant: str
    # A paid plan's id -> the discount code that takes the price down to the first-month
    # price once (#435). Missing: a first-month checkout for that plan is refused, so
    # nobody is charged more than was promised.
    first_month_codes: Mapping[str, str] = field(default_factory=dict)
    # Test mode: checkouts are test checkouts and only test events are accepted; live mode
    # ignores test events (and the other way round), so a mix-up never changes a plan.
    test_mode: bool = False


class HttpTransport:
    """The real calls, with short timeouts: a person is waiting on the button."""

    def __init__(self, api_key: str, base: str = API_BASE) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": JSON_API,
            "Content-Type": JSON_API,
        }
        self._base = base
        self._http: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def __call__(
        self, method: str, path: str, body: dict[str, Any] | None
    ) -> tuple[int, Any]:
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6, connect=3))
        try:
            async with self._http.request(
                method, self._base + path, json=body, headers=self._headers, allow_redirects=False
            ) as response:
                try:
                    parsed = await response.json(content_type=None)
                except ValueError:
                    parsed = None
                return response.status, parsed
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise PaymentError(f"Couldn't reach Lemon Squeezy ({type(exc).__name__})") from exc


def _data(answer: Any) -> dict[str, Any]:
    """The `data` object of a JSON:API answer, or a PaymentError."""
    data = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(data, dict):
        raise PaymentError("Lemon Squeezy's answer was unexpected")
    return data


def _only_digits(value: str, what: str) -> str:
    if not value.isascii() or not value.isdigit():
        raise PaymentError(f"{what} is not a Lemon Squeezy id")
    return value


class LemonSqueezyProvider:
    name = "lemonsqueezy"

    def __init__(
        self,
        config: LemonSqueezyConfig,
        webhook_secret: bytes,
        *,
        transport: Transport | None = None,
    ) -> None:
        self._config = config
        self._secret = webhook_secret
        self._http: Transport = transport or HttpTransport(config.api_key)
        self._plan_of_variant = {variant: plan for plan, variant in config.variants.items()}

    async def close(self) -> None:
        if isinstance(self._http, HttpTransport):
            await self._http.close()

    # --- asking Lemon Squeezy ---

    async def _checkout(
        self,
        *,
        variant: str,
        user_id: int,
        email: str | None,
        return_url: str,
        discount_code: str | None = None,
    ) -> str:
        checkout_data: dict[str, Any] = {"custom": {"user_id": str(user_id)}}
        if email:
            checkout_data["email"] = email  # server-side only: never in the address returned
        if discount_code:
            checkout_data["discount_code"] = discount_code
        body = {
            "data": {
                "type": "checkouts",
                "attributes": {
                    "checkout_data": checkout_data,
                    "product_options": {
                        "redirect_url": return_url,
                        "receipt_link_url": return_url,
                        "receipt_button_text": "Back to DMbot",
                    },
                    "test_mode": self._config.test_mode,
                },
                "relationships": {
                    "store": {"data": {"type": "stores", "id": self._config.store_id}},
                    "variant": {"data": {"type": "variants", "id": variant}},
                },
            }
        }
        status, answer = await self._http("POST", "/v1/checkouts", body)
        if status not in (200, 201):
            raise PaymentError(f"Lemon Squeezy answered {status} to a checkout")
        url = (_data(answer).get("attributes") or {}).get("url")
        parts = urlsplit(url) if isinstance(url, str) else None
        # The browser is sent here, so it must be Lemon Squeezy's own page over https.
        if (
            parts is None
            or parts.scheme != "https"
            or not (parts.hostname or "").endswith(".lemonsqueezy.com")
        ):
            raise PaymentError("Lemon Squeezy's checkout address was unexpected")
        assert isinstance(url, str)
        return url

    async def checkout_url(
        self,
        *,
        user_id: int,
        email: str | None,
        plan: Plan,
        first_month_cents: int | None,
        return_url: str,
    ) -> str:
        variant = self._config.variants.get(plan.id)
        if variant is None:
            raise PaymentError(f"No Lemon Squeezy variant is set for the {plan.id} plan")
        code = None
        if first_month_cents is not None:
            # The $1.99 first month (#435) is a once-only discount code made in Lemon
            # Squeezy; without one set up, refuse rather than charge the full price.
            code = self._config.first_month_codes.get(plan.id)
            if code is None:
                raise PaymentError(f"No first-month discount code is set for the {plan.id} plan")
        return await self._checkout(
            variant=variant,
            user_id=user_id,
            email=email,
            return_url=return_url,
            discount_code=code,
        )

    async def extra_hours_checkout_url(
        self, *, user_id: int, email: str | None, return_url: str
    ) -> str:
        """The page to buy +10 hours for this month (plans.json `extraHours`); sold by
        `POST /billing/extra-hours` (#1089)."""
        return await self._checkout(
            variant=self._config.extra_hours_variant,
            user_id=user_id,
            email=email,
            return_url=return_url,
        )

    async def billing_url(self, *, customer_id: str, return_url: str) -> str:
        """The customer portal (change plan, fix a payment, stop). Lemon Squeezy makes the
        link per customer and it expires after a day, so it is asked for each time."""
        status, answer = await self._http(
            "GET", f"/v1/customers/{_only_digits(customer_id, 'customer id')}", None
        )
        if status != 200:
            raise PaymentError(f"Lemon Squeezy answered {status} for a billing page")
        attributes = _data(answer).get("attributes") or {}
        url = (attributes.get("urls") or {}).get("customer_portal")
        parts = urlsplit(url) if isinstance(url, str) else None
        if (
            parts is None
            or parts.scheme != "https"
            or not (parts.hostname or "").endswith(".lemonsqueezy.com")
        ):
            raise PaymentError("Lemon Squeezy's billing address was unexpected")
        assert isinstance(url, str)
        return url

    async def cancel(self, *, subscription_id: str) -> None:
        path = f"/v1/subscriptions/{_only_digits(subscription_id, 'subscription id')}"
        status, answer = await self._http("GET", path, None)
        if status == 404:
            return  # already gone: a retried deletion still succeeds
        if status != 200:
            raise PaymentError(f"Lemon Squeezy answered {status} for a subscription")
        state = (_data(answer).get("attributes") or {}).get("status")
        if state in ("cancelled", "expired"):
            return
        # DELETE cancels at the end of the period already paid for: no refund, no new charge.
        status, _ = await self._http("DELETE", path, None)
        if status not in (200, 204, 404):
            raise PaymentError(f"Lemon Squeezy answered {status} to a cancel")

    # --- Lemon Squeezy asking us ---

    def verify(self, body: bytes, headers: Mapping[str, str]) -> bool:
        try:
            given = headers.get(SIGNATURE_HEADER, "").strip().encode("latin-1")
        except UnicodeEncodeError:
            return False  # not a signature Lemon Squeezy could have made
        expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest().encode("ascii")
        return hmac.compare_digest(expected, given)

    def parse(self, body: bytes) -> PaymentEvent | None:
        raw = json.loads(body)
        if not isinstance(raw, dict):
            raise ValueError("a payment event must be a JSON object")
        meta = raw.get("meta") or {}
        data = raw.get("data") or {}
        if not isinstance(meta, dict) or not isinstance(data, dict):
            raise ValueError("a payment event has no meta or data")
        name = str(meta.get("event_name", ""))
        if name not in _USED:
            log.info("Lemon Squeezy event %.40r isn't one DMbot uses", name)
            return None
        # A test event in live mode (or the reverse) never changes a plan.
        if bool(meta.get("test_mode")) != self._config.test_mode:
            log.warning("Lemon Squeezy event %.40r ignored: wrong mode (test/live)", name)
            return None
        attributes = data.get("attributes") or {}
        if not isinstance(attributes, dict):
            raise ValueError("a payment event has no attributes")
        object_id = str(data["id"])
        when = _seconds(attributes.get("updated_at") or attributes["created_at"])
        stamp = str(attributes.get("updated_at") or attributes["created_at"])
        event_id = f"{name}:{object_id}:{stamp}"
        # The person is needed only for an event that will be applied: one DMbot ignores (an
        # order or subscription made outside its checkout, say) must be a quiet 200, not an
        # error someone is asked to look at.
        if name == "order_created":
            return self._order(attributes, event_id, meta, when)
        if name == "subscription_payment_failed":
            return PaymentEvent(
                provider=self.name,
                event_id=event_id,
                kind="payment_failed",
                user_id=_person(meta),
                occurred_at=when,
                customer_id=_text(attributes.get("customer_id")),
                subscription_id=_text(attributes.get("subscription_id")),
            )
        # The subscription's own events: its id is the object's.
        kind = self._subscription_kind(name, attributes)
        if kind is None:
            return None
        plan = self._plan_of_variant.get(str(attributes.get("variant_id")))
        if plan is None and kind != "subscription_ended":
            # A variant id missing from the settings: the writer will refuse it (422) and
            # Lemon Squeezy will retry, so say which, for the person setting this up.
            log.error(
                "Lemon Squeezy event %.60s has variant %.20r, which no plan is set to",
                event_id,
                str(attributes.get("variant_id")),
            )
        period_start, period_end = _period(attributes)
        return PaymentEvent(
            provider=self.name,
            event_id=event_id,
            kind=kind,
            user_id=_person(meta),
            occurred_at=when,
            plan=plan,
            period_start=period_start,
            period_end=period_end,
            customer_id=_text(attributes.get("customer_id")),
            subscription_id=object_id,
        )

    def _order(
        self, attributes: dict[str, Any], event_id: str, meta: dict[str, Any], when: int
    ) -> PaymentEvent | None:
        """An order is extra hours only when it is for that variant and paid: every plan
        payment also makes an order, and those are not ours to count twice."""
        item = attributes.get("first_order_item") or {}
        if str(item.get("variant_id")) != self._config.extra_hours_variant:
            return None
        if attributes.get("status") != "paid":
            return None
        return PaymentEvent(
            provider=self.name,
            event_id=event_id,
            kind="extra_hours_bought",
            user_id=_person(meta),
            occurred_at=when,
            customer_id=_text(attributes.get("customer_id")),
        )

    @staticmethod
    def _subscription_kind(name: str, attributes: dict[str, Any]) -> EventKind | None:
        state = attributes.get("status")
        if name == "subscription_created":
            # Created only once the first payment has gone through; anything but active
            # (a trial, unpaid) starts nothing.
            return "subscription_started" if state == "active" else None
        if name == "subscription_updated":
            # A renewal or a plan change, once it has taken effect. Other states (paused,
            # past_due, cancelled-until-the-end) change nothing here: a failed payment
            # arrives as its own event, and a cancelled plan runs to its end.
            return "subscription_renewed" if state == "active" else None
        if name == "subscription_expired":
            return "subscription_ended"
        return None


_USED = (
    "subscription_created",
    "subscription_updated",
    "subscription_expired",
    "subscription_payment_failed",
    "order_created",
)


def _person(meta: dict[str, Any]) -> int:
    """Our Discord user id, from the checkout's custom data."""
    custom = meta.get("custom_data")
    user_id = int(custom["user_id"]) if isinstance(custom, dict) else 0
    if user_id <= 0:
        raise ValueError("a payment event has no user_id in its custom data")
    return user_id


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _seconds(stamp: object) -> int:
    """Unix seconds from Lemon Squeezy's UTC time, e.g. 2027-01-15T09:45:53.000000Z."""
    if not isinstance(stamp, str):
        raise ValueError("expected a time")
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())


def _period(attributes: dict[str, Any]) -> tuple[int | None, int | None]:
    """This billing period: it ends when the subscription renews, and began one calendar
    month before (clamped to the month's last day, like the plan's own months). Lemon
    Squeezy sends no period start, and this is the same for every event in the period.
    Monthly plans only (all of plans.json): a yearly variant would need its own rule."""
    renews = attributes.get("renews_at")
    if not isinstance(renews, str):
        return None, None
    end = datetime.fromisoformat(renews.replace("Z", "+00:00")).astimezone(UTC)
    month = end.month - 1 or 12
    year = end.year - (1 if end.month == 1 else 0)
    day = min(end.day, calendar.monthrange(year, month)[1])
    start = end.replace(year=year, month=month, day=day)
    return int(start.timestamp()), int(end.timestamp())
