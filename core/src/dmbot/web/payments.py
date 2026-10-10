"""Payments through a merchant-of-record hosted checkout (#435).

The payment company (Paddle or Lemon Squeezy: the owner picks) is the seller and holds
all card details; DMbot never sees them. DMbot only:
- asks it for a checkout page for a plan, passing our Discord user id as custom data,
- asks it for the customer's billing page (change plan, fix a payment, stop),
- receives its signed events (the webhook), which are the only thing that changes plans.

Each company plugs in behind `PaymentProvider`, turning its own events into `PaymentEvent`.
`FakeProvider` is for tests and local work; a real one is added once the owner has chosen
and there is a sandbox account.

What every real adapter must do (#497):
- Compare signatures as bytes (`hmac.compare_digest` on bytes), never str against str: a
  header with non-ASCII text must be a refusal, not a crash. FakeProvider.verify shows how.
- Send `subscription_started` only once the first payment has gone through. A first payment
  that fails is sent as `payment_failed` (or not at all): the writer gives the 7-day grace
  only to someone with an earlier successful payment on record (#922).
- A change the company schedules for the next billing date (a downgrade at renewal) is
  sent as `subscription_started` only when it takes effect, at the renewal.
- `occurred_at` is when the change happened at the company, never when it was delivered:
  it orders late deliveries and becomes the plan's change time.
- The person's email goes to the company server-side (its API call), never in the
  checkout address the browser is sent to.
- `cancel` (account deletion) cancels at the end of the period already paid for, never
  immediately and with no refund or new charge (docs/PLAN.md, #435). The delete screen
  says (account.ts `deletePlanStops`): "You won't be charged again. Money you've already
  paid isn't paid back. If you want to ask about that, do it before you delete (see the
  Refunds page)." DMbot itself stops at once, so don't promise the rest of the month.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlencode

from dmbot import plans
from dmbot.plans import Plan, PlanId

log = logging.getLogger(__name__)

EventKind = Literal[
    "subscription_started",  # a new paid plan, or a plan change (a new plan id)
    "subscription_renewed",  # paid for the next period
    "payment_failed",  # the 7-day grace starts, if they have paid before (#922)
    "subscription_ended",  # stopped paying, or cancelled at the period's end
    "extra_hours_bought",  # +10 hours for this period
]


class PaymentError(RuntimeError):
    """The payment company refused or couldn't be reached. No personal data inside."""


@dataclass(frozen=True)
class PaymentEvent:
    provider: str
    event_id: str
    kind: EventKind
    user_id: int  # our Discord user id, from the checkout's custom data
    occurred_at: int  # Unix seconds, from the company (orders late deliveries)
    plan: str | None = None  # a plan id; checked against plans.json before use
    period_start: int | None = None
    period_end: int | None = None
    customer_id: str | None = None
    subscription_id: str | None = None


class PaymentProvider(Protocol):
    name: str

    async def checkout_url(
        self,
        *,
        user_id: int,
        email: str | None,
        plan: Plan,
        first_month_cents: int | None,
        return_url: str,
    ) -> str:
        """The company's checkout page for this plan. `email` prefills it and is sent to
        the company server-side only, never put in the returned address."""
        ...

    async def billing_url(self, *, customer_id: str, return_url: str) -> str: ...

    async def cancel(self, *, subscription_id: str) -> None:
        """Stop the subscription at the end of the period already paid for: the person is
        deleting their account and must never be charged again. Cancelling a subscription
        that is already cancelled must succeed, so a retried deletion works."""
        ...

    def verify(self, body: bytes, headers: Mapping[str, str]) -> bool:
        """The body really comes from the payment company (its signature checks out)."""
        ...

    def parse(self, body: bytes) -> PaymentEvent | None:
        """The event in our terms, or None for an event DMbot doesn't use."""
        ...


class FakeProvider:
    """A pretend payment company for tests and local work: no money moves. Its events
    are signed with HMAC-SHA256 like the real ones, so the webhook path is the same."""

    name = "fake"
    SIGNATURE_HEADER = "x-fake-signature"

    def __init__(self, secret: bytes, site_url: str) -> None:
        self._secret = secret
        self._site_url = site_url.rstrip("/")
        self.cancelled: list[str] = []

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    async def checkout_url(
        self,
        *,
        user_id: int,
        email: str | None,
        plan: Plan,
        first_month_cents: int | None,
        return_url: str,
    ) -> str:
        query: dict[str, str] = {"fake_checkout": plan.id}
        if first_month_cents is not None:
            query["first_month"] = str(first_month_cents)
        return f"{return_url}?{urlencode(query)}"

    async def billing_url(self, *, customer_id: str, return_url: str) -> str:
        return f"{return_url}?{urlencode({'fake_billing': '1'})}"

    async def cancel(self, *, subscription_id: str) -> None:
        self.cancelled.append(subscription_id)

    def verify(self, body: bytes, headers: Mapping[str, str]) -> bool:
        try:
            given = headers.get(self.SIGNATURE_HEADER, "").encode("latin-1")
        except UnicodeEncodeError:
            return False  # not a signature we could have made
        return hmac.compare_digest(self.sign(body).encode("ascii"), given)

    def parse(self, body: bytes) -> PaymentEvent | None:
        raw = json.loads(body)
        if not isinstance(raw, dict):
            raise ValueError("a payment event must be a JSON object")
        kind = raw.get("kind")
        if kind not in (
            "subscription_started",
            "subscription_renewed",
            "payment_failed",
            "subscription_ended",
            "extra_hours_bought",
        ):
            # Kinds are the company's own words, not personal data; cut short anyway.
            log.info("Payment event %.40r of a kind DMbot doesn't use: %.40r", raw.get("id"), kind)
            return None
        return PaymentEvent(
            provider=self.name,
            event_id=str(raw["id"]),
            kind=kind,
            user_id=int(raw["user_id"]),
            occurred_at=int(raw["occurred_at"]),
            plan=None if raw.get("plan") is None else str(raw["plan"]),
            period_start=_whole(raw.get("period_start")),
            period_end=_whole(raw.get("period_end")),
            customer_id=None if raw.get("customer_id") is None else str(raw["customer_id"]),
            subscription_id=(
                None if raw.get("subscription_id") is None else str(raw["subscription_id"])
            ),
        )


def _whole(value: object) -> int | None:
    """A whole number of seconds, or None; anything else is a malformed event."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected whole seconds")
    return value


def paid_plan_ids() -> tuple[PlanId, ...]:
    """Plans that can be bought now (a price, and not free)."""
    loaded = plans.load()
    return tuple(
        p for p in loaded.order if (price := loaded.by_id[p].price_cents) is not None and price > 0
    )
