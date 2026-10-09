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
STOP_WARNING_MINUTES = 15  # the grace session is told this long before it stops

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


def account_link(site_url: str) -> str:
    """Where a message sends someone to pick or change a plan; plain words until the
    website's address is set (#437)."""
    return f"{site_url}/account" if site_url else "DMbot's website"


START_BLOCKED = (
    "DMbot can't start this campaign right now. Ask the campaign's owner to take a look."
)
# A campaign with no owner can't start, because DMbot wouldn't know whose hours to use. A DM
# of the campaign gets a Take it on button under this (it may be the first time the card
# for ⚙️ Settings exists, so the words don't send them there); anyone else is pointed at the
# DMs.
NO_OWNER_ASK = (
    "This campaign has no owner yet, so DMbot doesn't know whose hours it uses. Press "
    "**Take it on** to use your plan for it, then press Start again."
)
NO_OWNER = (
    "This campaign has no owner yet, so DMbot doesn't know whose hours it uses. One of its "
    "DMs needs to take it on first."
)


def refusal(
    verdict: Verdict,
    *,
    is_owner: bool,
    site_url: str = "",
    can_change_plan: bool = False,
    renews: bool = False,
    extra_hours: int = 0,
) -> str | None:
    """The plain words for a refused start, or None if it may go ahead. Only the owner is
    told why; anyone else starting the campaign is told to ask the owner, in words that fit
    every cause, so they never learn about the owner's plan or hours (#437). A link goes
    last so no full stop is glued onto it.

    What the owner is offered follows from their plan: `can_change_plan` (a paid plan or Try
    It), `extra_hours` (how many hours one purchase adds, 0 when their plan can't buy any,
    which is the free list and grants), and `renews` (a paid plan that does renew). No date
    is given for the hours coming back: Try It's period ends rather than renews, a renewal
    can be a few days late, and which day it is depends on the owner's time zone."""
    if verdict == "ok":
        return None
    if not is_owner:
        return START_BLOCKED
    where = f"here: {site_url}/account" if site_url else "on DMbot's website"
    if verdict == "no_plan":
        return f"Your plan has ended. Pick one {where}"
    text = "You've used all your hours this month."
    if renews:
        text += " They start again when your plan renews."
    if extra_hours:
        return f"{text} To play now, add {extra_hours} hours or change your plan {where}"
    if can_change_plan:
        return f"{text} To play now, change your plan {where}"
    return text


def more_hours(site_url: str, buys_hours: bool = True, renews: bool = False) -> str:
    """How more hours can be had, for a message everyone at the table reads, so it never
    says whose plan it is. Only an owner who can buy hours is sent to the website: a Try It
    owner can't, so they are told when the hours start again, or to ask the owner. The link
    comes last so no full stop is glued onto it."""
    if buys_hours:
        return f"The campaign's owner can add more hours at {account_link(site_url)}"
    if renews:
        return "Hours start again when the plan renews."
    return "Ask the campaign's owner what to do next."


PAUSE_WAY = (
    "pause one of your other campaigns first "
    "(open it, then ⚙️ Settings and ⏸️ **Pause this campaign**)"
)
PAUSED_OWNER = (
    "This campaign is paused. Open ⚙️ Settings and press ▶️ **Unpause**. Everything in it is kept."
)
PAUSED_OTHER = "This campaign is paused. Ask its owner to unpause it, then press Start again."


def campaigns_refusal(
    cap: int,
    owned: int,
    *,
    is_owner: bool,
    site_url: str = "",
    can_change_plan: bool = False,
    creating: bool = False,
    unpausing: bool = False,
) -> str | None:
    """The plain words for a start (or, with `creating`, a new campaign; with `unpausing`,
    an unpause) refused because the owner has as many campaigns as their plan covers (#437
    part 2c, #957), or what anyone else is told. Like `refusal`: only the owner hears why,
    with both numbers; the change-your-plan offer only goes to a plan that can change; the
    link goes last. The way out names the real button (pausing, #957)."""
    if not is_owner:
        return START_BLOCKED
    noun = "campaign" if cap == 1 else "campaigns"
    text = f"Your plan covers {cap} {noun}, and you have {owned}."
    action = (
        "To make a new one"
        if creating
        else "To unpause this one"
        if unpausing
        else "To start this one"
    )
    if can_change_plan:
        where = f"here: {site_url}/account" if site_url else "on DMbot's website"
        return f"{text} {action}, {PAUSE_WAY} or change your plan {where}"
    return f"{text} {action}, {PAUSE_WAY}."


PAUSED_TOLD = (
    "DMbot paused {what} because your plan now covers {cap} {noun}. A paused campaign keeps "
    "everything but can't start. To use one again, pause one you play less (open it, then "
    "⚙️ Settings and ⏸️ **Pause this campaign**), then open the paused one and press "
    "▶️ **Unpause**.{change}"
)


def paused_told(names: list[str], cap: int, *, site_url: str = "", can_change_plan: bool) -> str:
    """The one private note to an owner whose campaigns were paused because their plan
    shrank (#957): which, why, and how to change it."""
    noun = "campaign" if cap == 1 else "campaigns"
    shown = ", ".join(f"**{n}**" for n in names[:10])
    if len(names) > 10:
        shown += f" and {len(names) - 10} more"
    what = shown
    where = f"here: {site_url}/account" if site_url else "on DMbot's website"
    change = f" Or change your plan {where}" if can_change_plan else ""
    return PAUSED_TOLD.format(cap=cap, noun=noun, what=what, change=change)


def warning_text(
    left_minutes: int,
    mark: int = 80,
    site_url: str = "",
    buys_hours: bool = True,
    renews: bool = False,
) -> str:
    """The DM-screen warning as hours run low: "About 4 hours left this month." The first
    one (80%) says what an hour is; the 90% one says how more can be had. It is read by
    everyone who can see the DM screen, so it never says whose plan it is or anything about
    payment."""
    words = hours_left_words(left_minutes)
    text = f"⏳ {words[0].upper()}{words[1:]} left this month."
    if mark < 90:
        return f"{text} (Hours are DMbot's listening time.)"
    return f"{text} {more_hours(site_url, buys_hours, renews)}"


CapAction = Literal["none", "start_grace", "in_grace", "stop"]


def cap_action(
    access: Access, used: int, grace_session: int | None, session_started_at: int
) -> CapAction:
    """What to do about a running session when the owner's hours are at or past the cap
    (#437). Under the cap, nothing. At the cap a session that has not had the month's
    grace is given it ("start_grace": it may run up to GRACE_MINUTES more); the session
    that was given it carries on until the grace is spent ("in_grace", then "stop"); any
    other session stops at once, because the grace is once a month."""
    cap = standing(access, used).cap_minutes
    # A session is told apart by its start second: two campaigns of one owner starting in
    # the very same second would share the grace (so rare it is accepted).
    if cap is None or used < cap:
        return "none"
    if grace_session is None:
        return "start_grace" if used < cap + GRACE_MINUTES else "stop"
    if grace_session == session_started_at and used < cap + GRACE_MINUTES:
        return "in_grace"
    return "stop"


def grace_ends_at(access: Access, used: int, now: int) -> int | None:
    """When the grace runs out if listening carries on (Unix seconds), from the owner's
    minutes used so far. Approximate: another campaign of the same owner listening at the
    same time spends the same hours, so the real end can only come sooner."""
    cap = standing(access, 0).cap_minutes
    if cap is None:
        return None
    return now + max(0, cap + GRACE_MINUTES - used) * 60


def stop_warning_due(
    access: Access,
    grace_session: int | None,
    session_started_at: int,
    used_before: int,
    used_after: int,
) -> bool:
    """Is this the tick that brings the grace session within STOP_WARNING_MINUTES of its
    stop? True once: found, like the 80% and 90% marks, by comparing the minutes before and
    after. Only the session that holds the grace is warned; any other already stopped."""
    cap = standing(access, 0).cap_minutes
    if cap is None or grace_session != session_started_at:
        return False
    mark = cap + GRACE_MINUTES - STOP_WARNING_MINUTES
    return used_before < mark <= used_after


# The grace and stop notices are read by everyone who can see the DM screen, so they never
# say whose plan it is (see more_hours).
def grace_started_text(
    ends_at: int | None = None,
    site_url: str = "",
    buys_hours: bool = True,
    renews: bool = False,
) -> str:
    # The end time says how long it runs; the "2 hours" is only for when it isn't known.
    until = (
        f" until <t:{ends_at}:t>"
        if ends_at is not None
        else f" (up to {GRACE_MINUTES // 60} hours)"
    )
    return (
        "⏳ This month's listening hours are used up. This session can finish: DMbot keeps "
        f"listening{until}, then stops. {more_hours(site_url, buys_hours, renews)}"
    )


def stop_soon_text(site_url: str = "", buys_hours: bool = True, renews: bool = False) -> str:
    return (
        f"⏳ DMbot will stop listening in about {STOP_WARNING_MINUTES} minutes. This month's "
        f"listening hours are used up. {more_hours(site_url, buys_hours, renews)}"
    )


def stopped_text(site_url: str = "", buys_hours: bool = True, renews: bool = False) -> str:
    return (
        "⏳ DMbot has stopped listening. This month's listening hours are used up. "
        f"{more_hours(site_url, buys_hours, renews)}"
    )
