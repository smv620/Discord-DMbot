"""Payments through a merchant-of-record hosted checkout (#435).

The payment company (Stripe: the owner's choice, 2026-10-13) is the seller and holds
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
    "subscription_cancelled",  # cancelled: works to the period's end (period_end), then ends
    "subscription_ended",  # stopped paying, or cancelled at the period's end
    "extra_hours_bought",  # +10 hours for this period
    "recap_audio_started",  # the recap audio add-on was bought (its first payment went through)
    "recap_audio_renewed",  # the add-on was paid for the next period
    "recap_audio_ended",  # the add-on's subscription ended (cancelled and run out, or stopped)
    "email_updated",  # the payer changed their address at the company (no plan change)
    # A plan change set for the end of the period (plan, and period_start = when it starts), or
    # undone. The company's schedule has no user of ours: the app finds them from the
    # subscription (user_id 0 until then, #2165).
    "plan_scheduled",
    "plan_unscheduled",
]


class PaymentError(RuntimeError):
    """The payment company refused or couldn't be reached. No personal data inside."""


class NotResumable(PaymentError):
    """The subscription is not one that can be resumed (it has ended, or its payment is
    overdue): Renew answers "not ending" (#2165)."""


class ResumeRefused(PaymentError):
    """The company would not undo a cancellation by a direct update (a plan change is scheduled
    for the period's end, say): the person renews on its billing page instead (#2165)."""


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
    # The address the company has for the payer (never logged): kept as the account's
    # payment email (#1242).
    email: str | None = None


class PaymentProvider(Protocol):
    name: str

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
        """The company's checkout page for this plan. `email` prefills it and is sent to
        the company server-side only, never put in the returned address. `customer_id`: the
        company's customer for this person if they have one; otherwise `dmbot_id` (their
        DMbot ID) is the prefix of the new customer's invoices (#1228)."""
        ...

    async def extra_hours_checkout_url(
        self,
        *,
        user_id: int,
        email: str | None,
        return_url: str,
        dmbot_id: str | None = None,
        customer_id: str | None = None,
    ) -> str:
        """The company's page to buy the extra hours (plans.json `extraHours`) for this
        month. Same rules as `checkout_url`: the email goes server-side only."""
        ...

    @property
    def recap_audio_for_sale(self) -> bool:
        """Whether the recap audio add-on can be bought now: its price is set. Until the owner
        sets it (#1385) the checkout is hidden."""
        ...

    async def recap_audio_checkout_url(
        self,
        *,
        user_id: int,
        email: str | None,
        return_url: str,
        dmbot_id: str | None = None,
        customer_id: str | None = None,
    ) -> str:
        """The company's page to subscribe to the recap audio add-on (#1389): a second,
        monthly subscription, marked so its events are never taken for the plan's."""
        ...

    async def billing_url(
        self, *, customer_id: str, return_url: str, subscription_id: str | None = None
    ) -> str:
        """The company's billing page. With a subscription id it opens straight on choosing a
        plan (#2165); the company may refuse that for an account, and then the ordinary page
        opens instead."""
        ...

    async def resume(self, *, subscription_id: str) -> None:
        """Undo a cancellation that has not taken effect yet (#2165): the plan renews on its date
        as before, with no new charge. Succeeds if it was not cancelled."""
        ...

    async def subscription_owner(self, *, subscription_id: str) -> int | None:
        """Our Discord user id for a subscription of the plan, or None for one that is not a
        plan's (the recap audio add-on, or one made outside DMbot). Only for a company event
        that does not name the person (a plan schedule, #2165)."""
        ...

    async def cancel(self, *, subscription_id: str) -> None:
        """Stop the subscription at the end of the period already paid for: the person is
        deleting their account and must never be charged again. Cancelling a subscription
        that is already cancelled must succeed, so a retried deletion works."""
        ...

    async def retarget(
        self, *, customer_id: str | None, subscription_id: str | None, user_id: int
    ) -> None:
        """After an account moved (#1230): point the customer's and the subscription's
        `user_id` at the new Discord ID, so later events name it. Raises PaymentError if
        the company refuses or can't be reached; safe to repeat."""
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
        self.refuse_resume = False  # a test can have the company refuse a direct resume
        self.cannot_resume = False  # ...or say the subscription has ended
        self.resumed: list[str] = []  # subscriptions whose cancellation was undone (#2165)
        self.owners: dict[str, int] = {}  # a subscription's person, for the events that lack one
        # What each checkout was given, so tests can see the DMbot ID and customer travel.
        self.checkouts: list[dict[str, object]] = []
        self.retargeted: list[tuple[str | None, str | None, int]] = []
        self.recap_audio_for_sale = True  # a test turns it off, as an unset price does

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

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
        self.checkouts.append(
            {"user_id": user_id, "plan": plan.id, "dmbot_id": dmbot_id, "customer_id": customer_id}
        )
        query: dict[str, str] = {"fake_checkout": plan.id}
        return f"{return_url}?{urlencode(query)}"

    async def extra_hours_checkout_url(
        self,
        *,
        user_id: int,
        email: str | None,
        return_url: str,
        dmbot_id: str | None = None,
        customer_id: str | None = None,
    ) -> str:
        self.checkouts.append(
            {"user_id": user_id, "extra": True, "dmbot_id": dmbot_id, "customer_id": customer_id}
        )
        return f"{return_url}?{urlencode({'fake_extra_hours': '1'})}"

    async def recap_audio_checkout_url(
        self,
        *,
        user_id: int,
        email: str | None,
        return_url: str,
        dmbot_id: str | None = None,
        customer_id: str | None = None,
    ) -> str:
        self.checkouts.append(
            {
                "user_id": user_id,
                "recap_audio": True,
                "dmbot_id": dmbot_id,
                "customer_id": customer_id,
            }
        )
        return f"{return_url}?{urlencode({'fake_recap_audio': '1'})}"

    async def billing_url(
        self, *, customer_id: str, return_url: str, subscription_id: str | None = None
    ) -> str:
        flow = {"fake_billing": "1"} | ({"flow": "plans"} if subscription_id else {})
        return f"{return_url}?{urlencode(flow)}"

    async def resume(self, *, subscription_id: str) -> None:
        if self.refuse_resume:
            raise ResumeRefused("the fake company will not resume")
        if self.cannot_resume:
            raise NotResumable("the fake subscription has ended")
        self.resumed.append(subscription_id)

    async def subscription_owner(self, *, subscription_id: str) -> int | None:
        return self.owners.get(subscription_id)

    async def cancel(self, *, subscription_id: str) -> None:
        self.cancelled.append(subscription_id)

    async def retarget(
        self, *, customer_id: str | None, subscription_id: str | None, user_id: int
    ) -> None:
        self.retargeted.append((customer_id, subscription_id, user_id))

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
            "subscription_cancelled",
            "subscription_ended",
            "plan_scheduled",
            "plan_unscheduled",
            "extra_hours_bought",
            "recap_audio_started",
            "recap_audio_renewed",
            "recap_audio_ended",
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
