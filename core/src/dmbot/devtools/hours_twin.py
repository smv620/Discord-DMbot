"""The hours rules, replayed offline (#437; docs/PLAN.md, "Plans and pricing").

    python -m dmbot.devtools.replay --session FOLDER --hours-only --plan table \\
        --hours-used 14 [--grace] [--session-minutes 240]

This is the offline proof of the hours rules before `DMBOT_ENFORCE_PLANS` is turned on
(#498): it puts a made-up owner on a plan with some hours already used, in a **throwaway
database schema** (made here, dropped at the end; the live schema is never touched), and
runs a session minute by minute through the same pure rules and the same hours meter the bot
uses. It prints what the DM screen would say at 80% and 90%, at the cap (the 2-hour grace,
once a month), when the session stops, and what the owner is told at the next start, with the
bot's exact words.

What it shares with the bot: `hours.warning_crossed` / `warning_text`, `hours.cap_action`,
`grace_started_text`, `stop_soon_text`, `stopped_text`, `hours.refusal`, and the meter's
database writes (`usage.add_minutes`, `start_grace`, `check_start`). What it does not: the
bot's loop around them (`Bot._meter_write`, `_enforce_cap`) needs Discord, so the order of the
steps here follows those methods and a test pins that the words come from the same functions.
Enforcement is on for this replay only. Nothing here is a real person: the owner and server
numbers are made up, and no player's words are used.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from dmbot import hours, plans, usage
from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.web import entitlements_writer
from dmbot.web.payments import PaymentEvent

# Made-up numbers (not Discord-shaped: 17 digits or more would be refused elsewhere).
OWNER = 9_001
GUILD = 1_001
# 2026-10-15 12:00 UTC: a fixed moment, so a replay gives the same answer every time.
NOW = 1_792_065_600
DAY = 86400
PLAN_IDS: tuple[str, ...] = ("try-it", "table", "two-tables", "guild")


class TwinError(ValueError):
    """The options don't make a scenario (a plain message for the person running it)."""


@dataclass(frozen=True, slots=True)
class Scenario:
    plan: str
    hours_used: float = 0.0
    grace: bool = False  # the plan is in its 7-day payment grace
    session_minutes: int = 60

    def check(self) -> None:
        if self.plan not in PLAN_IDS:
            raise TwinError(f"--plan must be one of {', '.join(PLAN_IDS)}")
        if self.hours_used < 0:
            raise TwinError("--hours-used can't be negative")
        if self.grace and self.plan == "try-it":
            raise TwinError("--grace is for a paid plan: Try It has no payment")
        if self.session_minutes < 1:
            raise TwinError("--session-minutes must be at least 1")


@dataclass(slots=True)
class Result:
    scenario: Scenario
    cap_hours: int | None = None
    lines: list[str] = field(default_factory=list)  # what to print, in order
    warnings: list[tuple[int, str]] = field(default_factory=list)  # (minute, DM-screen text)
    grace: list[tuple[int, str]] = field(default_factory=list)  # started / stop-soon notices
    stopped_at: int | None = None  # the minute the session stopped for hours
    stopped_text: str | None = None
    start_refusal: str | None = None  # at the first start (already at the cap)
    next_start_refusal: str | None = None  # at the start after the session


def _screen(minute: int, text: str) -> str:
    return f"  after {minute} min  DM screen: {text}"


async def _set_up(db: Database, scenario: Scenario) -> str:
    """The owner, their plan and one campaign, in the throwaway schema. Returns the campaign."""
    async with db.user(OWNER) as conn:
        await conn.execute(
            "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
            " VALUES (%s, NULL, %s, %s)",
            (OWNER, NOW - 40 * DAY, NOW - DAY),
        )
    if scenario.plan == "try-it":
        await entitlements_writer.start_try_it(db, OWNER, now=NOW - 5 * DAY)
    else:
        start = NOW - 10 * DAY
        started = PaymentEvent(
            provider="twin",
            event_id="twin-start",
            kind="subscription_started",
            user_id=OWNER,
            occurred_at=start,
            plan=scenario.plan,
            period_start=start,
            period_end=start + 30 * DAY,
            customer_id="twin-customer",
            subscription_id="twin-subscription",
        )
        await entitlements_writer.apply_event(db, started, now=start)
        if scenario.grace:  # a failed renewal payment: the plan still works for 7 days
            failed = PaymentEvent(
                provider="twin",
                event_id="twin-failed",
                kind="payment_failed",
                user_id=OWNER,
                occurred_at=NOW - DAY,
                subscription_id="twin-subscription",
            )
            await entitlements_writer.apply_event(db, failed, now=NOW - DAY)
    campaign = await CampaignStore(db, clock=lambda: NOW).create(GUILD, "Twin campaign", OWNER)
    used = round(scenario.hours_used * 60)
    if used:  # an earlier session, so this one starts with the hours already spent
        await usage.add_minutes(
            db,
            guild_id=GUILD,
            campaign_id=campaign.id,
            owner_user_id=OWNER,
            session_started_at=NOW - 2 * DAY,
            minutes=used,
            now=NOW - 2 * DAY,
        )
    return campaign.id


async def _refusal(db: Database, now: int, site_url: str) -> str | None:
    """What the owner is told at a start: the bot's own call (`Bot._plan_refusal`)."""
    check = await usage.check_start(db, GUILD, OWNER, now)
    return hours.refusal(
        check.verdict,
        is_owner=True,
        site_url=site_url,
        can_change_plan=check.can_change_plan,
        renews=check.renews,
        extra_hours=check.extra_hours,
    )


async def play(db: Database, scenario: Scenario, *, site_url: str = "") -> Result:
    """Run the scenario in `db` (a throwaway schema the caller made)."""
    scenario.check()
    campaign_id = await _set_up(db, scenario)
    result = Result(scenario)
    plan = plans.load().get(scenario.plan)
    result.cap_hours = plan.hours_per_month if plan is not None else None
    result.start_refusal = await _refusal(db, NOW, site_url)
    if result.start_refusal is not None:
        return result  # refused before listening starts: no session to run
    started_at = NOW
    for minute in range(1, scenario.session_minutes + 1):
        now = started_at + minute * 60
        standing = await usage.add_minutes(
            db,
            guild_id=GUILD,
            campaign_id=campaign_id,
            owner_user_id=OWNER,
            session_started_at=started_at,
            minutes=1,
            now=now,
        )
        action = hours.cap_action(
            standing.access, standing.used_after, standing.grace_session, started_at
        )
        if action == "none":  # a tick that passes 90% and the cap together says only the cap
            mark = hours.warning_crossed(standing.access, standing.used_before, standing.used_after)
            left = hours.standing(standing.access, standing.used_after).left_minutes
            if mark is not None and left is not None:
                text = hours.warning_text(
                    left, mark, site_url, standing.buys_hours, standing.renews
                )
                result.warnings.append((minute, text))
        if action == "start_grace":
            got = await usage.start_grace(db, GUILD, OWNER, standing.month, started_at)
            action = "in_grace" if got else "stop"
            if got:
                ends = hours.grace_ends_at(standing.access, standing.used_after, now)
                text = hours.grace_started_text(
                    ends, site_url, standing.buys_hours, standing.renews
                )
                result.grace.append((minute, text))
        elif action == "in_grace" and hours.stop_warning_due(
            standing.access,
            standing.grace_session,
            started_at,
            standing.used_before,
            standing.used_after,
        ):
            text = hours.stop_soon_text(site_url, standing.buys_hours, standing.renews)
            result.grace.append((minute, text))
        if action == "stop":
            result.stopped_at = minute
            result.stopped_text = hours.stopped_text(site_url, standing.buys_hours, standing.renews)
            break
    end = started_at + (result.stopped_at or scenario.session_minutes) * 60
    result.next_start_refusal = await _refusal(db, end + 60, site_url)
    return result


def report(result: Result) -> list[str]:
    """The lines to print: the scenario, the DM-screen notices in order, the stop, and the
    next start, in the bot's exact words."""
    s = result.scenario
    cap = f"{result.cap_hours} h a month" if result.cap_hours else "no cap"
    out = [
        f"hours twin: plan {s.plan} ({cap}){', in its payment grace' if s.grace else ''}, "
        f"{s.hours_used:g} h already used, a {s.session_minutes} min session; "
        "enforcement is on for this replay only"
    ]
    if result.start_refusal is not None:
        out.append("  start refused:")
        out.append(f"    {result.start_refusal}")
        return out
    notices = sorted(
        [(m, t) for m, t in result.warnings] + [(m, t) for m, t in result.grace],
        key=lambda x: x[0],
    )
    out += [_screen(m, t) for m, t in notices]
    if not notices and result.stopped_at is None:
        out.append("  no warning: the session ended before 80% of the hours")
    if result.stopped_at is not None and result.stopped_text is not None:
        out.append(_screen(result.stopped_at, result.stopped_text))
        out.append(f"  the session stopped after {result.stopped_at} min")
    out.append("  next /dmbot start (to the owner):")
    out.append(f"    {result.next_start_refusal or 'allowed: hours are left'}")
    return out


async def run(url: str, scenario: Scenario, *, site_url: str = "") -> Result:
    """`play` in a throwaway schema of the database at `url`, dropped afterwards."""
    schema = f"twin_{uuid.uuid4().hex[:12]}"
    db = await Database.open(url, schema=schema, max_size=2)
    try:
        return await play(db, scenario, site_url=site_url)
    finally:
        await db.close()
        await drop_schema(url, schema)
