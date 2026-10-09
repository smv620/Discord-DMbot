"""The hours meter's rules, pure (#437 part 2; docs/PLAN.md, "Plans and pricing").

Hours are DMbot's listening time, start to stop, rounded up to the minute, pooled per
owner per plan month with no roll-over. This module only does the arithmetic: which month
a moment falls in, how many minutes a session used, and whether a start may go ahead. It
never reads the database or Discord, so every rule is testable alone; `entitlements` says
what a person may do, and the usage table (a later slice) holds what they have used.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from dmbot.entitlements import Access, Entitlement

WARN_AT = (80, 90)  # percent of the month's hours (PLAN: warnings at 80% and 90%)
GRACE_MINUTES = 2 * 60  # a session that hits the cap may finish, once a period

Verdict = Literal["ok", "no_plan", "out_of_hours"]


@dataclass(frozen=True, slots=True)
class Month:
    """One plan month: `start` up to (not including) `end`, Unix seconds."""

    start: int
    end: int


def minutes_used(started_at: int, ended_at: int) -> int:
    """Listening minutes for one session, rounded up (a session of 1 second is 1 minute;
    a clock that went backwards is 0)."""
    seconds = max(0, ended_at - started_at)
    return -(-seconds // 60)


def minutes_owed(started_at: int, now: int, recorded: int) -> int:
    """Minutes to add to a session's record now: the whole session so far, rounded up,
    less what is already recorded (never negative, so a late or repeated call adds nothing
    twice)."""
    return max(0, minutes_used(started_at, now) - recorded)


def _add_months(start: datetime, months: int) -> datetime:
    """`start` plus whole calendar months, keeping the time of day. A 29th to 31st start
    falls on the month's last day where that month is shorter, so it never skips a month."""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def month_from_anchor(anchor: int, now: int) -> Month:
    """The calendar-month window containing `now`, counting months from `anchor` (a
    grant's start day). Before the anchor it is the first month."""
    first = datetime.fromtimestamp(anchor, UTC)
    when = datetime.fromtimestamp(now, UTC)
    months = max(0, (when.year - first.year) * 12 + when.month - first.month)
    if _add_months(first, months) > when:  # not yet this calendar month's anchor day
        months = max(0, months - 1)
    return Month(
        int(_add_months(first, months).timestamp()),
        int(_add_months(first, months + 1).timestamp()),
    )


def month_for(
    access: Access, plan: Entitlement | None, grant_started_at: int | None, now: int
) -> Month | None:
    """The month whose hours count now, or None when the person has no meter (free list,
    "no limits" grant, or no working plan).

    A paid plan's month is its billing period (a late renewal event keeps the old period
    until it arrives, so a person is not given a fresh month early). A Guild-level grant
    runs from the day it started, unless a paid plan works too: then the paid plan's month
    (PLAN, #437 decision of 2026-10-08)."""
    if access.hours_cap is None or not access.works:
        return None
    if plan is not None and plan.usable(now):
        return Month(plan.period_start, plan.period_end)
    if access.kind == "grant" and grant_started_at is not None:
        return month_from_anchor(grant_started_at, now)
    return None


@dataclass(frozen=True, slots=True)
class Standing:
    """Where a person's hours stand this month. `cap_minutes` None means no meter."""

    cap_minutes: int | None
    used_minutes: int

    @property
    def left_minutes(self) -> int | None:
        return None if self.cap_minutes is None else max(0, self.cap_minutes - self.used_minutes)

    @property
    def percent_used(self) -> int:
        if not self.cap_minutes:
            return 0
        return min(100, self.used_minutes * 100 // self.cap_minutes)


def standing(access: Access, used_minutes: int) -> Standing:
    cap = None if access.hours_cap is None else access.hours_cap * 60
    return Standing(cap, used_minutes)


def start_verdict(access: Access, used_minutes: int) -> Verdict:
    """May a session begin? The plan has to work and some hours must be left. Never used on
    a session already running: that one is finished, not cut off, at the cap."""
    if not access.works:
        return "no_plan"
    left = standing(access, used_minutes).left_minutes
    if left is not None and left <= 0:
        return "out_of_hours"
    return "ok"


def warning_crossed(access: Access, used_before: int, used_after: int) -> int | None:
    """The highest warning mark (80 or 90) that this much listening just passed, or None.
    Each mark is reported once: asked with the minutes before and after a stretch of
    listening, it names a mark only when that stretch crossed it."""
    cap = standing(access, 0).cap_minutes
    if not cap:
        return None
    before = min(100, used_before * 100 // cap)
    after = min(100, used_after * 100 // cap)
    crossed = [mark for mark in WARN_AT if before < mark <= after]
    return crossed[-1] if crossed else None


def hours_left_words(left_minutes: int) -> str:
    """Plain words for the time left, rounded down to the half hour so it reads simply:
    "about 4 hours", "about 1½ hours", "less than half an hour"."""
    halves = left_minutes // 30
    if halves <= 0:
        return "less than half an hour"
    whole, half = divmod(halves, 2)
    if whole == 0:
        return "about half an hour"
    amount = f"{whole}½" if half else str(whole)
    return f"about {amount} {'hour' if halves == 2 else 'hours'}"


def ordinal(day: int) -> str:
    """14 -> "14th": the day a month ends, in the words used in messages."""
    if 10 <= day % 100 <= 20:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"


def account_link(site_url: str) -> str:
    """Where a message sends someone to pick or change a plan; plain words until the
    website's address is set (#437)."""
    return f"{site_url}/account" if site_url else "DMbot's website"


START_BLOCKED = (
    "DMbot can't start this campaign right now. Ask the campaign's owner to take a look."
)
NO_OWNER = (
    "This campaign has no owner yet, so DMbot doesn't know whose hours it uses. Open "
    "⚙️ Settings, press **Take it on**, then start again."
)


def refusal(
    verdict: Verdict, *, is_owner: bool, site_url: str = "", month_end: int | None = None
) -> str | None:
    """The plain words for a refused start, or None if it may go ahead. Only the owner is
    told why; anyone else starting the campaign is told to ask the owner, in words that fit
    every cause, so they never learn about the owner's plan or hours (#437). A link goes
    last so no full stop is glued onto it."""
    if verdict == "ok":
        return None
    if not is_owner:
        return START_BLOCKED
    where = f"here: {site_url}/account" if site_url else "on DMbot's website"
    if verdict == "no_plan":
        return f"Your plan has ended. Pick one {where}"
    back = (
        f" They come back on the {ordinal(datetime.fromtimestamp(month_end, UTC).day)}."
        if month_end is not None
        else ""
    )
    return (
        f"You've used all your hours this month.{back} "
        f"To play now, add 10 hours or change your plan {where}"
    )


def warning_text(left_minutes: int, mark: int = 80, site_url: str = "") -> str:
    """The DM-screen warning as hours run low: "About 4 hours left this month." The first
    one (80%) says what an hour is; the 90% one says where to add more."""
    words = hours_left_words(left_minutes)
    text = f"⏳ {words[0].upper()}{words[1:]} left this month."
    if mark < 90:
        return f"{text} (Hours are DMbot's listening time.)"
    where = f"{site_url}/account" if site_url else "DMbot's website"
    return f"{text} To add more, go to {where}"
