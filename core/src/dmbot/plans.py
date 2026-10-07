"""The plans people can buy (#432, #437): prices, hours, campaign caps and how long data
is kept. Owner decisions; the numbers live in plans.json, a copy of the website's
web/src/content/plans.json (a test keeps the two equal), so the bot, the web API and the
website never disagree. Change both together, and only with the owner's say-so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Literal, cast

PlanId = Literal["try-it", "table", "two-tables", "guild", "pro"]
Unit = Literal["day", "month", "year"]

_DAYS_PER_UNIT: dict[Unit, int] = {"day": 1, "month": 30, "year": 365}


@dataclass(frozen=True)
class Period:
    count: int
    unit: Unit

    @property
    def days(self) -> int:
        """Whole days, counting a month as 30 and a year as 365."""
        return self.count * _DAYS_PER_UNIT[self.unit]


@dataclass(frozen=True)
class Plan:
    id: PlanId
    name: str
    price_cents: int | None  # None: not for sale yet
    hours_per_month: int
    campaigns: int
    trial_days: int | None
    backups: bool
    keep_after_last_session: Period
    first_month_after_trial_cents: int | None


@dataclass(frozen=True)
class Plans:
    order: tuple[PlanId, ...]
    by_id: dict[PlanId, Plan]
    extra_hours: int
    extra_hours_price_cents: int
    payment_grace_days: int
    keep_after_plan_stops_paying: Period
    deletion_warning_days_before: tuple[int, ...]

    def get(self, plan_id: str) -> Plan | None:
        """The plan with this id, or None for an unknown id (never trust a request's id)."""
        return self.by_id.get(cast(PlanId, plan_id))


def _period(raw: dict[str, object]) -> Period:
    return Period(count=int(cast(int, raw["count"])), unit=cast(Unit, raw["unit"]))


@cache
def load() -> Plans:
    raw = json.loads(resources.files("dmbot").joinpath("plans.json").read_text("utf-8"))
    by_id: dict[PlanId, Plan] = {}
    for plan_id in raw["order"]:
        p = raw["plans"][plan_id]
        by_id[plan_id] = Plan(
            id=plan_id,
            name=p["name"],
            price_cents=p["priceCents"],
            hours_per_month=p["hoursPerMonth"],
            campaigns=p["campaigns"],
            trial_days=p["trialDays"],
            backups=p["backups"],
            keep_after_last_session=_period(p["keepAfterLastSession"]),
            first_month_after_trial_cents=p["firstMonthAfterTrialCents"],
        )
    return Plans(
        order=tuple(raw["order"]),
        by_id=by_id,
        extra_hours=raw["extraHours"]["hours"],
        extra_hours_price_cents=raw["extraHours"]["priceCents"],
        payment_grace_days=raw["paymentGraceDays"],
        keep_after_plan_stops_paying=_period(raw["keepAfterPlanStopsPaying"]),
        deletion_warning_days_before=tuple(raw["deletionWarningDaysBefore"]),
    )
