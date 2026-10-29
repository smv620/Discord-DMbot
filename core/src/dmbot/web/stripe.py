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
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

import aiohttp

from dmbot.alerts import Problem, raise_alert
from dmbot.plans import Plan
from dmbot.web.payments import EventKind, NotResumable, PaymentError, PaymentEvent, ResumeRefused

log = logging.getLogger(__name__)

API_BASE = "https://api.stripe.com"
PREFIX_MIN, PREFIX_MAX = 3, 12  # the characters Stripe allows in an invoice prefix
SIGNATURE_HEADER = "stripe-signature"
# How far a signed timestamp may be from now, either way (Stripe's own default).
TOLERANCE_SECONDS = 300

# The API version every call is made on, so the payload shapes we tested are the shapes we get
# (an account's default version can change under us in the dashboard). The webhook endpoint
# must be created on this same version: an endpoint has its own, and it decides the shape of
# the events. Checkout Sessions with Managed Payments are supported from this release.
# Moved from 2026-04-22.dahlia to 2026-09-30.endive (#1703) because Stripe's dashboard stopped
# offering dahlia when the owner created the test-mode webhook endpoint, so events arrive in
# endive's shape. The fields this adapter reads were checked against the stripe-python 15.x and
# 16.0.0 changelogs and none of them changed between the two (see the PR); the sandbox run
# (#1096) is what proves it end to end.
API_VERSION = "2026-09-30.endive"
# The owner's Checkout Studio parameters (#1446), sent with every Checkout Session. `ui_mode`
# is `hosted_page` on this API version and the one before it (older versions called it
# `hosted`); the owner's values are fixed, so a change is a decision, not a tweak.
# `payment_method_collection` is added only to subscriptions (see `_checkout`). Listed in
# STRIPE_INTEGRATION_TODO.md.
CHECKOUT_STUDIO: dict[str, str] = {
    "ui_mode": "hosted_page",
    "billing_address_collection": "auto",
    "phone_number_collection[enabled]": "false",
    "automatic_tax[enabled]": "true",
    "allow_promotion_codes": "true",
    "submit_type": "auto",
    "integration_identifier": "hosted_web_0001",
    "origin_context": "web",
}


# (method, path, form fields or None) -> (HTTP status, parsed JSON or None). Faked in tests.
# Stripe takes form-encoded bodies; nested fields are written the way it reads them
# ("line_items[0][price]").
class Transport(Protocol):
    """The calls to Stripe. A checkout also passes an idempotency key (see
    `StripeProvider._checkout`); other calls don't."""

    def __call__(
        self,
        method: str,
        path: str,
        form: dict[str, str] | None,
        idempotency_key: str | None = None,
    ) -> Awaitable[tuple[int, Any]]: ...


_CUSTOMER = re.compile(r"cus_[A-Za-z0-9]+")
_SUBSCRIPTION = re.compile(r"sub_[A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class StripeConfig:
    """Everything from the environment (names in .env.example). Never logged."""

    secret_key: str = field(repr=False)
    # A paid plan's id (plans.json) -> its recurring Price id in Stripe.
    prices: Mapping[str, str]
    extra_hours_price: str
    # The recap audio add-on's recurring Price id (#1389). Empty until the owner sets it
    # (#1385): the add-on is then not for sale and its checkout is hidden.
    recap_audio_price: str = ""
    # Stripe's Managed Payments (Stripe as the merchant of record, owner decision on #1096):
    # asks each Checkout Session to opt in. Off by default until the sandbox run (#1096) has
    # shown the parameter is accepted; the owner also accepts Stripe's terms in the dashboard.
    managed_payments: bool = False

    @property
    def live(self) -> bool:
        """Stripe's test keys start `sk_test_` (or `rk_test_`), so there is no separate test
        switch to get out of step with the key."""
        return not self.secret_key.startswith(("sk_test_", "rk_test_"))


class HttpTransport:
    """The real calls, with short timeouts: a person is waiting on the button."""

    def __init__(self, secret_key: str, base: str = API_BASE) -> None:
        self._headers = {"Authorization": f"Bearer {secret_key}", "Stripe-Version": API_VERSION}
        self._base = base
        self._http: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def __call__(
        self,
        method: str,
        path: str,
        form: dict[str, str] | None,
        idempotency_key: str | None = None,
    ) -> tuple[int, Any]:
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6, connect=3))
        headers = self._headers
        if idempotency_key is not None:
            headers = {**headers, "Idempotency-Key": idempotency_key}
        try:
            async with self._http.request(
                method, self._base + path, data=form, headers=headers, allow_redirects=False
            ) as response:
                try:
                    parsed = await response.json(content_type=None)
                except ValueError:
                    parsed = None
                if response.status == 401:  # Stripe's answer to a key it doesn't accept
                    raise_alert(Problem.STRIPE_KEY_REFUSED)
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
    if not pattern.fullmatch(value):  # fullmatch: a trailing newline must not pass
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

    async def _customer(self, user_id: int, dmbot_id: str, email: str | None) -> str:
        """A new Stripe customer whose invoices are numbered from the person's DMbot ID
        (#1228). Stripe's Checkout can't set an invoice prefix, so the customer is made first
        and the checkout is told to use it. The same request within Stripe's idempotency
        window gets the same customer back, so abandoned checkouts don't pile up customers."""
        # A customer made earlier for this person (they left a checkout and came back after
        # Stripe forgot the idempotency key) is reused, never a second one with the same prefix.
        # Stripe's search can lag a minute behind, which the idempotency key covers.
        query = urlencode({"query": f"metadata['user_id']:'{user_id}'", "limit": "1"})
        status, answer = await self._http("GET", f"/v1/customers/search?{query}", None)
        if status == 200 and isinstance(answer, dict):
            for old in answer.get("data") or []:
                if isinstance(old, dict) and isinstance(old.get("id"), str):
                    return _checked(old["id"], _CUSTOMER, "customer id")
        form = {"metadata[user_id]": str(user_id)}
        if PREFIX_MIN <= len(dmbot_id) <= PREFIX_MAX:  # Stripe takes 3 to 12 characters
            form["invoice_prefix"] = dmbot_id
        if email:
            form["email"] = email
        status, answer = await self._http(
            "POST", "/v1/customers", form, "dmbot-customer-" + _idempotency_key(form)[15:]
        )
        found = answer.get("id") if status == 200 and isinstance(answer, dict) else None
        if not isinstance(found, str):
            raise PaymentError(f"Stripe answered {status} to a new customer")
        return _checked(found, _CUSTOMER, "customer id")

    async def set_invoice_prefix(self, customer_id: str, dmbot_id: str) -> None:
        """Give an existing customer the DMbot ID as its invoice prefix."""
        if not PREFIX_MIN <= len(dmbot_id) <= PREFIX_MAX:
            raise PaymentError("A DMbot ID that short can't be an invoice prefix")
        path = f"/v1/customers/{_checked(customer_id, _CUSTOMER, 'customer id')}"
        status, _ = await self._http("POST", path, {"invoice_prefix": dmbot_id})
        if status != 200:
            raise PaymentError(f"Stripe answered {status} setting an invoice prefix")

    async def _checkout(
        self,
        form: dict[str, str],
        email: str | None,
        *,
        user_id: int,
        dmbot_id: str | None = None,
        customer_id: str | None = None,
    ) -> str:
        form.update(CHECKOUT_STUDIO)
        # A one-time payment (extra hours) has no card to keep for later: Stripe refuses this
        # on `payment` mode, so only the two subscriptions send it.
        if form["mode"] == "subscription":
            form["payment_method_collection"] = "always"
        if self._config.managed_payments:
            form["managed_payments[enabled]"] = "true"
        if customer_id:
            form["customer"] = _checked(customer_id, _CUSTOMER, "customer id")
        elif dmbot_id:
            form["customer"] = await self._customer(user_id, dmbot_id, email)
        elif email:
            form["customer_email"] = email  # server-side only: never in the address returned
        if "customer" in form:
            # Automatic tax needs the customer's address; this lets Checkout save the one the
            # person types onto the existing customer (Stripe refuses it without a customer).
            form["customer_update[address]"] = "auto"
        # A double-click, or a retry after a timeout, must not make a second live checkout
        # (two completed ones would be two subscriptions). Within one five-minute window the
        # same request carries the same idempotency key, so Stripe answers with the session it
        # already made. The expiry is the window's end plus Stripe's 30-minute minimum, so it
        # is the same for every call in the window (a different one under the same key would be
        # refused) and an abandoned page stops working soon after.
        # (Stripe keeps the first answer under a key, a failure too: a retry in the same window
        # gets the same failure, and one in the next window starts afresh.)
        window = int(self._clock() // IDEMPOTENCY_WINDOW_S)
        form["expires_at"] = str((window + 1) * IDEMPOTENCY_WINDOW_S + CHECKOUT_LIFETIME_S)
        key = _idempotency_key(form)
        status, answer = await self._http("POST", "/v1/checkout/sessions", form, key)
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
        return_url: str,
        dmbot_id: str | None = None,
        customer_id: str | None = None,
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
        return await self._checkout(
            form, email, user_id=user_id, dmbot_id=dmbot_id, customer_id=customer_id
        )

    async def extra_hours_checkout_url(
        self,
        *,
        user_id: int,
        email: str | None,
        return_url: str,
        dmbot_id: str | None = None,
        customer_id: str | None = None,
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
        return await self._checkout(
            form, email, user_id=user_id, dmbot_id=dmbot_id, customer_id=customer_id
        )

    @property
    def recap_audio_for_sale(self) -> bool:
        return bool(self._config.recap_audio_price)

    async def recap_audio_checkout_url(
        self,
        *,
        user_id: int,
        email: str | None,
        return_url: str,
        dmbot_id: str | None = None,
        customer_id: str | None = None,
    ) -> str:
        """The page to subscribe to the recap audio add-on (#1389): a second recurring
        subscription whose metadata carries `item=recap_audio`, so every event it makes is
        read as the add-on's and never as the plan's."""
        if not self._config.recap_audio_price:
            raise PaymentError("No Stripe price is set for the recap audio add-on")
        form = {
            "mode": "subscription",
            "line_items[0][price]": self._config.recap_audio_price,
            "line_items[0][quantity]": "1",
            "success_url": return_url,
            "cancel_url": return_url,
            "metadata[user_id]": str(user_id),
            "metadata[item]": RECAP_AUDIO_ITEM,
            "subscription_data[metadata][user_id]": str(user_id),
            "subscription_data[metadata][item]": RECAP_AUDIO_ITEM,
        }
        return await self._checkout(
            form, email, user_id=user_id, dmbot_id=dmbot_id, customer_id=customer_id
        )

    async def billing_url(
        self, *, customer_id: str, return_url: str, subscription_id: str | None = None
    ) -> str:
        """The customer portal (change plan, fix a payment, stop). Stripe makes a short-lived
        session per visit, so it is asked for each time. With a subscription id the portal opens
        straight on choosing a plan (`flow_data` `subscription_update`, #2165) instead of the
        overview, whose button says "Update subscription". If Stripe refuses that for this
        account's portal settings, the ordinary portal opens and the refusal is logged."""
        form = {
            "customer": _checked(customer_id, _CUSTOMER, "customer id"),
            "return_url": return_url,
        }
        if subscription_id is not None:
            flow = dict(form)
            flow["flow_data[type]"] = "subscription_update"
            flow["flow_data[subscription_update][subscription]"] = _checked(
                subscription_id, _SUBSCRIPTION, "subscription id"
            )
            status, answer = await self._http("POST", "/v1/billing_portal/sessions", flow)
            if status == 200:
                return _stripe_page(
                    answer.get("url") if isinstance(answer, dict) else None,
                    "billing.stripe.com",
                    "billing",
                )
            if status != 400:
                # An outage or a rate limit is not a reason to change the page: say so, once.
                raise PaymentError(f"Stripe answered {status} for a billing page")
            log.warning(
                "Stripe answered %s to a plan-choosing billing page: the ordinary one opens", status
            )
        status, answer = await self._http("POST", "/v1/billing_portal/sessions", form)
        if status != 200:
            raise PaymentError(f"Stripe answered {status} for a billing page")
        url = answer.get("url") if isinstance(answer, dict) else None
        return _stripe_page(url, "billing.stripe.com", "billing")

    async def resume(self, *, subscription_id: str) -> None:
        """Undo a cancellation that has not taken effect (#2165): the plan renews on its date,
        with no new charge. Both ways Stripe records a cancel are cleared. Nothing to do for a
        subscription that is not cancelled; one that has ended cannot be resumed."""
        path = f"/v1/subscriptions/{_checked(subscription_id, _SUBSCRIPTION, 'subscription id')}"
        status, answer = await self._http("GET", path, None)
        if status != 200:
            raise PaymentError(f"Stripe answered {status} for a subscription")
        current = answer if isinstance(answer, dict) else {}
        # Only a subscription that is paid up and running: an overdue one (past_due, unpaid) would
        # have its failed invoice retried, and one that has ended cannot be brought back.
        if current.get("status") not in ("active", "trialing"):
            raise NotResumable("the subscription is not active and cannot be resumed")
        if current.get("cancel_at_period_end") is not True and current.get("cancel_at") is None:
            return  # not cancelled: nothing to undo
        form = {"cancel_at_period_end": "false"}
        if current.get("cancel_at") is not None:
            form["cancel_at"] = ""  # an empty value clears a cancel date
        status, _ = await self._http("POST", path, form)
        if status != 200:
            # A subscription with a plan change scheduled for the period's end can refuse a
            # direct update (a 400): the person is sent to the billing page then. Any other
            # answer (401, 403, 429, 5xx) is an error, not that refusal.
            if status == 400:
                raise ResumeRefused(f"Stripe refused a resume ({status})")
            raise PaymentError(f"Stripe answered {status} to a resume")

    async def subscription_owner(self, *, subscription_id: str) -> int | None:
        path = f"/v1/subscriptions/{_checked(subscription_id, _SUBSCRIPTION, 'subscription id')}"
        status, answer = await self._http("GET", path, None)
        if status == 404:
            return None  # gone at the company: not ours to act on
        if status != 200 or not isinstance(answer, dict):
            raise PaymentError(f"Stripe answered {status} for a subscription")
        metadata = answer.get("metadata") or {}
        if _is_add_on(metadata):
            return None
        try:
            return _person(metadata)
        except ValueError:
            return None  # an unreadable user id: not a plan of ours, and a retry would not help

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

    async def retarget(
        self, *, customer_id: str | None, subscription_id: str | None, user_id: int
    ) -> None:
        """Point the customer's and the subscription's `user_id` metadata at the new Discord
        ID (an account move, #1230). Stripe merges metadata keys, so only this one changes;
        repeating it is harmless."""
        form = {"metadata[user_id]": str(int(user_id))}
        paths = []
        if customer_id:
            paths.append(f"/v1/customers/{_checked(customer_id, _CUSTOMER, 'customer id')}")
        if subscription_id:
            paths.append(
                f"/v1/subscriptions/{_checked(subscription_id, _SUBSCRIPTION, 'subscription id')}"
            )
        for path in paths:
            status, _ = await self._http("POST", path, form)
            if status == 404:
                continue  # gone at the company (deleted): nothing left to point
            if status != 200:
                raise PaymentError(f"Stripe answered {status} to a metadata update")

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
            # At most 12 digits: a longer one is no real time, and int() of a huge one would
            # overflow the float maths below into a 500 instead of a refusal.
            if not stamp.isascii() or not stamp.isdigit() or len(stamp) > 12 or not signatures:
                return False
            if abs(self._clock() - int(stamp)) > TOLERANCE_SECONDS:
                return False
        except (UnicodeEncodeError, ValueError, OverflowError):
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
        if name in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            return self._extra_hours(obj, event_id, when)
        if name == "customer.updated":
            # A Customer object has no user_id of ours: the account is found by the customer id
            # stored with its plan (user_id 0 here means "not known yet"). A customer DMbot
            # has no plan for is ignored by the writer.
            email, customer = _email(obj.get("email")), _text(obj.get("id"))
            if email is None or customer is None:
                return None
            return PaymentEvent(
                provider=self.name,
                event_id=event_id,
                kind="email_updated",
                user_id=0,
                occurred_at=when,
                customer_id=customer,
                email=email,
            )
        if name.startswith("subscription_schedule."):
            return self._schedule(name, obj, event_id, when)
        if name.startswith("invoice."):
            return self._invoice(name, obj, event_id, when)
        return self._subscription(name, obj, raw.get("data", {}), event_id, when)

    def _schedule(
        self, name: str, schedule: dict[str, Any], event_id: str, when: int
    ) -> PaymentEvent | None:
        """A subscription schedule (#2165). The person is not named on it: user_id 0 means "find
        them from the subscription" (the app asks the company). A schedule that is live and has a
        phase after the current one, on a different plan, is a plan change set for the period's
        end; any other schedule event (or a finished or undone one) clears it. A schedule on a
        price DMbot does not sell is not ours."""
        subscription = _text(schedule.get("subscription"))
        if subscription is None:
            return None
        common: dict[str, Any] = {
            "provider": self.name,
            "event_id": event_id,
            "user_id": 0,
            "occurred_at": when,
            "customer_id": _text(schedule.get("customer")),
            "subscription_id": subscription,
        }
        clear = PaymentEvent(kind="plan_unscheduled", **common)
        if name != "subscription_schedule.created" and name != "subscription_schedule.updated":
            return clear
        if schedule.get("status") not in ("not_started", "active"):
            return clear
        phases = schedule.get("phases")
        phases = [p for p in phases if isinstance(p, dict)] if isinstance(phases, list) else []
        current = schedule.get("current_phase")
        started = _int(current.get("start_date")) if isinstance(current, dict) else None
        # The phase after the current one: the one that starts later than it.
        later = [p for p in phases if (_int(p.get("start_date")) or 0) > (started or 0)]
        if not later:
            return clear
        upcoming = min(later, key=lambda p: _int(p.get("start_date")) or 0)
        # A phase's items are a plain list (an invoice's lines are a `data` list).
        items = upcoming.get("items")
        item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
        # A phase item names its price as a plain id, unlike an invoice line's object.
        price = item.get("price") if isinstance(item.get("price"), str) else _price_id(item)
        plan = self._plan_of_price.get(price or "")
        due = _int(upcoming.get("start_date"))
        if plan is None or due is None:
            return None  # not a plan DMbot sells
        return PaymentEvent(kind="plan_scheduled", plan=plan, period_start=due, **common)

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
        user_id = _person(metadata)
        if user_id is None:
            return None
        return PaymentEvent(
            provider=self.name,
            event_id=event_id,
            kind="extra_hours_bought",
            user_id=user_id,
            occurred_at=when,
            customer_id=_text(session.get("customer")),
            email=_email((session.get("customer_details") or {}).get("email")),
        )

    def _invoice(
        self, name: str, invoice: dict[str, Any], event_id: str, when: int
    ) -> PaymentEvent | None:
        details = _subscription_details(invoice)
        subscription_id = _text(details.get("subscription") or invoice.get("subscription"))
        if subscription_id is None:
            return None  # a one-off invoice, not a subscription's
        user_id = _person(details["metadata"])
        if user_id is None:
            log.info("Stripe event %.60s is for a subscription DMbot didn't make", event_id)
            return None
        if _is_add_on(details["metadata"]):
            return self._add_on_invoice(name, invoice, subscription_id, user_id, event_id, when)
        common: dict[str, Any] = {
            "provider": self.name,
            "event_id": event_id,
            "occurred_at": when,
            "customer_id": _text(invoice.get("customer")),
            "subscription_id": subscription_id,
            "email": _email(invoice.get("customer_email")),
        }
        if name == "invoice.payment_failed":
            return PaymentEvent(kind="payment_failed", user_id=user_id, **common)
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
        line = _subscription_line(invoice.get("lines"), self._plan_of_price)
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
            user_id=user_id,
            plan=plan,
            period_start=_int(period.get("start")),
            period_end=_int(period.get("end")),
            **common,
        )

    def _add_on_invoice(
        self,
        name: str,
        invoice: dict[str, Any],
        subscription_id: str,
        user_id: int,
        event_id: str,
        when: int,
    ) -> PaymentEvent | None:
        """The recap audio add-on's payments (#1389). Only a paid invoice for the add-on's own
        price counts; a failed payment starts no grace (the add-on simply runs out at the end
        of the period already paid for), and a proration or manual invoice changes nothing."""
        if name == "invoice.payment_failed" or invoice.get("status") != "paid":
            return None
        if not (_int(invoice.get("amount_paid")) or 0) > 0:
            return None  # a free invoice (a coupon) is no payment: it grants nothing
        reason = invoice.get("billing_reason")
        kind: EventKind
        if reason == "subscription_create":
            kind = "recap_audio_started"
        elif reason == "subscription_cycle":
            kind = "recap_audio_renewed"
        else:
            return None
        line = _subscription_line(invoice.get("lines"), {self._config.recap_audio_price: "add-on"})
        if not self._config.recap_audio_price or _price_id(line) != self._config.recap_audio_price:
            log.error(
                "Stripe event %.60s is an add-on invoice at a price DMbot doesn't sell", event_id
            )
            return None
        period = line.get("period") or {}
        return PaymentEvent(
            provider=self.name,
            event_id=event_id,
            kind=kind,
            user_id=user_id,
            occurred_at=when,
            period_start=_int(period.get("start")),
            period_end=_int(period.get("end")),
            customer_id=_text(invoice.get("customer")),
            subscription_id=subscription_id,
            email=_email(invoice.get("customer_email")),
        )

    def _add_on_subscription(
        self, name: str, subscription: dict[str, Any], user_id: int, event_id: str, when: int
    ) -> PaymentEvent | None:
        """Only the add-on subscription's end matters (its payments come as invoices). A
        cancel set for the period's end changes nothing: it works until the paid period ends."""
        if name != "customer.subscription.deleted":
            return None
        return PaymentEvent(
            provider=self.name,
            event_id=event_id,
            kind="recap_audio_ended",
            user_id=user_id,
            occurred_at=when,
            customer_id=_text(subscription.get("customer")),
            subscription_id=_text(subscription.get("id")),
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
        user_id = _person(metadata)
        if user_id is None:
            log.info("Stripe event %.60s is for a subscription DMbot didn't make", event_id)
            return None
        if _is_add_on(metadata):
            return self._add_on_subscription(name, subscription, user_id, event_id, when)
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
            return PaymentEvent(kind="subscription_ended", user_id=user_id, **common)
        if subscription.get("status") != "active":
            return None  # incomplete, past_due, unpaid: a failed payment arrives as its own event
        changed = data.get("previous_attributes") or {}
        if not isinstance(changed, dict):
            changed = {}
        cancel_at = _int(subscription.get("cancel_at"))
        if subscription.get("cancel_at_period_end") is True or cancel_at is not None:
            if _price_changed(changed, price):
                # Decided on #1122 (not two events): a plan change made while a cancel is
                # pending arrives as this one event, so only the end date is stored, and the
                # new plan applies if they resume or resubscribe. Said in the log so the
                # owner can see it happened; PLAN notes it too.
                log.info(
                    "Stripe event %.60s changes the plan of a subscription that is set to"
                    " cancel: only the end date is stored",
                    event_id,
                )
            # Cancelled on the payment page: the plan works to the end of the paid period and
            # then ends (stored so the page can say so, #824). The portal sets either
            # `cancel_at_period_end` or a `cancel_at` date, depending on the API version.
            return PaymentEvent(
                kind="subscription_cancelled",
                user_id=user_id,
                plan=plan,
                period_end=cancel_at or end,
                **common,
            )
        resumed = changed.get("cancel_at_period_end") is True or (
            changed.get("cancel_at") is not None
        )
        # A price change that has taken effect: Stripe's "previous" holds the old items.
        if resumed or _price_changed(changed, price):
            if plan is None:
                log.error(
                    "Stripe event %.60s has price %.40r, which no plan is set to", event_id, price
                )
            return PaymentEvent(
                kind="subscription_renewed",
                user_id=user_id,
                plan=plan,
                period_start=start,
                period_end=end,
                **common,
            )
        return None


EXTRA_HOURS_ITEM = "extra_hours"
RECAP_AUDIO_ITEM = "recap_audio"

IDEMPOTENCY_WINDOW_S = 300  # one checkout request, however many times it is sent in this long
# Stripe's minimum for a Checkout Session's expiry is 30 minutes from when it gets the request, by
# its clock: two minutes more, so a request in the last second of a window, network time and a
# little clock difference never land under it.
CHECKOUT_LIFETIME_S = 1800 + 120


def _idempotency_key(form: Mapping[str, str]) -> str:
    """The same request (same person, plan, address, expiry...) gives the same key. A hash, so
    nothing personal is in a header or a log, and under Stripe's 255 characters."""
    canonical = json.dumps(sorted(form.items()), separators=(",", ":"))
    return "dmbot-checkout-" + hashlib.sha256(canonical.encode()).hexdigest()


_USED = (
    "invoice.paid",
    "invoice.payment_failed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    # A plan change set for the end of the period (the billing page's downgrade, #2165): the
    # schedule holds the next plan and when it starts.
    "subscription_schedule.created",
    "subscription_schedule.updated",
    "subscription_schedule.released",
    "subscription_schedule.canceled",
    "subscription_schedule.completed",
    "subscription_schedule.aborted",
    "checkout.session.completed",
    # The payer changed their address (the billing page): kept as the payment email (#1242).
    "customer.updated",
    # A payment method that settles later (a bank debit): paid only when this arrives.
    "checkout.session.async_payment_succeeded",
)


def _is_add_on(metadata: object) -> bool:
    """The subscription is the recap audio add-on's (#1389), by the marker its checkout set."""
    return isinstance(metadata, dict) and metadata.get("item") == RECAP_AUDIO_ITEM


def _person(metadata: object) -> int | None:
    """Our Discord user id, from the checkout's (or subscription's) metadata. None when there
    is none: a subscription made outside DMbot (by hand in the dashboard, say) is not ours,
    and must be a quiet 200 rather than an error Stripe retries for days. A user_id that is
    there but is not a number is unreadable."""
    if not isinstance(metadata, dict) or metadata.get("user_id") in (None, ""):
        return None
    user_id = int(metadata["user_id"])
    if user_id <= 0:
        raise ValueError("a payment event has a bad user_id in its metadata")
    return user_id


def _price_changed(changed: dict[str, Any], current: str | None) -> bool:
    """Whether `previous_attributes` shows the subscription was on another price. A renewal
    also lists the items (their period moved) with the same price: that is the renewal
    invoice's business, and counting it here too would renew the period twice."""
    old = _price_id(_first(changed.get("items")))
    if old is None and isinstance(changed.get("plan"), dict):
        old = _text(changed["plan"].get("id"))  # older API versions
    return old is not None and old != current


def _subscription_details(invoice: dict[str, Any]) -> dict[str, Any]:
    """The subscription an invoice belongs to, as both API shapes put it: older versions on
    the invoice itself, newer ones under `parent`. Always has `metadata` (maybe empty)."""
    parent = invoice.get("parent")
    nested = parent.get("subscription_details") if isinstance(parent, dict) else None
    details = nested or invoice.get("subscription_details") or {}
    if not isinstance(details, dict):
        details = {}
    return {"metadata": {}, **details}


def _subscription_line(listing: object, plans_by_price: Mapping[str, str]) -> dict[str, Any]:
    """The invoice line for the plan itself. After a mid-period plan change the next renewal
    invoice also carries proration lines (credit for the old price, charge for the new) with
    the old dates; taking the first line could renew the wrong plan or period. So: not a
    proration, a price DMbot sells, and of those the one that runs longest."""
    rows = listing.get("data") if isinstance(listing, dict) else None
    best: dict[str, Any] = {}
    best_end = -1
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or _is_proration(row):
            continue
        if (_price_id(row) or "") not in plans_by_price:
            continue
        end = _int((row.get("period") or {}).get("end")) or 0
        if end > best_end:
            best, best_end = row, end
    return best or _first(listing)


def _is_proration(line: dict[str, Any]) -> bool:
    """A proration line, under either API shape."""
    parent = line.get("parent")
    details = parent.get("subscription_item_details") if isinstance(parent, dict) else None
    if isinstance(details, dict) and details.get("proration") is True:
        return True
    return line.get("proration") is True


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


def _email(value: object) -> str | None:
    """The payer's address as Stripe gives it, or None if it is missing or not one address.
    Kept as the account's payment email; never logged."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not 3 <= len(text) <= 254 or text.count("@") != 1 or any(c.isspace() for c in text):
        return None
    return text


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
