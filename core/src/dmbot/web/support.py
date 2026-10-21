"""Private support tickets from signed-in people on the website (#1340; docs/PLAN.md, "QAbot and
support tickets").

Privacy: a ticket holds personal details, so it never becomes a public GitHub issue and its
words are never logged or emailed. It goes to the `support_tickets` table. The signed-in person
can add to it and read back only their own list (when, subject, status; #1373), never the words,
which no one but the admin page's door (`Database.support_admin`) can read, and only that door
can close a ticket. The email to the team says only that a ticket
is waiting, and from which account and plan, so the words stay in the database. Tickets older
than 12 months are deleted by the website's hourly sweep, and deleting an account deletes the
person's own.

Abuse: every signed-in account with a plan row may write (Try It too, #1435), one ticket per
account per 10 minutes, 4,000 characters, and a Cloudflare Turnstile check when its key is set
(the route does the last two). An account without a working paid plan (Try It, lapsed) also
gets the free-account guard in `limits.py`: three new tickets a day and one waiting at a time.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from dmbot.db import Database
from dmbot.entitlements import Entitlement
from dmbot.web.feedback import clean_text
from dmbot.web.payments import paid_plan_ids

log = logging.getLogger(__name__)

MAX_SUBJECT = 120
MAX_MESSAGE = 4000
PAGE_SIZE = 20  # tickets on one page of the admin list (#2211)

# "new" is a ticket waiting for an answer (the helper's, or the team's after an escalation
# reply from the person goes back to it); "escalated" is waiting for a person, and the helper
# leaves it alone (#1431).
Status = Literal["new", "answered", "closed", "escalated"]
# Why a ticket was handed to a person (#1459): the sentiment check, the person asking for one, the
# helper's five answers used, no address to write to, the helper down for a day.
Reason = Literal["unhappy", "asked", "limit", "no-email", "helper-down"]
REASONS: tuple[Reason, ...] = ("unhappy", "asked", "limit", "no-email", "helper-down")
STATUSES: tuple[Status, ...] = ("new", "answered", "closed", "escalated")


def may_write(plan: Entitlement | None) -> bool:
    """Any account with a plan row: Try It, paid (even lapsed), a grant or the free list. A
    person whose paid plan has lapsed is exactly who most needs to write to us (#1435). Only
    an account with no plan row yet may not."""
    return plan is not None


def is_free(plan: Entitlement, now: int) -> bool:
    """No working paid plan: Try It, a lapsed plan, a grant or the free list. These accounts
    cost nothing to make, so they get the free-account guard (`limits.free_ticket_refusal`).
    The payment grace counts as working: someone whose payment just failed is not a fresh,
    free account."""
    return not (plan.plan in paid_plan_ids() and plan.usable(now))


@dataclass(frozen=True)
class Ticket:
    subject: str
    message: str


def read_ticket(raw: object) -> Ticket | str:
    """The form's fields, checked; or the error code to answer with. The site words each
    code (web/src/content/account.ts)."""
    if not isinstance(raw, dict):
        return "bad_request"
    subject, message = raw.get("subject"), raw.get("message")
    if not isinstance(subject, str) or not isinstance(message, str):
        return "bad_request"
    if raw.get("turnstile") is not None and not isinstance(raw.get("turnstile"), str):
        return "bad_request"
    # A subject is one line: a line break in it is a space.
    subject = " ".join(clean_text(subject).split())
    message = clean_text(message)
    if not subject or not message:
        return "empty"
    if len(subject) > MAX_SUBJECT:
        return "subject_too_long"
    if len(message) > MAX_MESSAGE:
        return "too_long"
    return Ticket(subject=subject, message=message)


async def recent(db: Database, user_id: int, *, now: int) -> tuple[int, int]:
    """(tickets still waiting, tickets sent in the last 24 hours) for this person, for the
    free-account guard. Counts only their own rows: the table's `read_own` policy holds the
    session to them, and the WHERE says it again."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT count(*) FILTER (WHERE status = 'new') AS waiting,"
            " count(*) FILTER (WHERE created_at > %s) AS today"
            " FROM support_tickets WHERE user_id = %s",
            (now - 86400, user_id),
        )
        row = await cur.fetchone()
    return (int(row["waiting"]), int(row["today"])) if row else (0, 0)


async def add(db: Database, user_id: int, plan: str, ticket: Ticket, *, now: int) -> int:
    """Keep a ticket for this signed-in person; returns its number. The table's policy lets them
    add a row for their own account and nothing else, which is why the person is set
    (`db.user`); the number comes back through the same person's `read_own` right."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "INSERT INTO support_tickets (created_at, user_id, plan, subject, message)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (now, user_id, plan, ticket.subject, ticket.message),
        )
        row = await cur.fetchone()
    assert row is not None
    return int(row["id"])


@dataclass(frozen=True)
class Row:
    id: int
    created_at: int
    user_id: int
    plan: str
    subject: str
    message: str
    source: str
    status: Status
    # Why it went to a person (#1459): one of REASONS, null when it hasn't (or has since been
    # answered).
    reason: str | None
    # When the helper last handed it to a person (Unix seconds); null if it never was. Set with
    # the hand-over and never cleared (#1487), unlike `reason`.
    handed_over_at: int | None
    # The account's address (the one the team replies to), read through the admin door and never
    # stored in the ticket; None when the account has none. Never logged.
    email: str | None


async def page(
    db: Database, *, status: Status | None, number: int, size: int = PAGE_SIZE
) -> tuple[list[Row], int]:
    """Page `number` (from 1) of the tickets with an optional status filter, and
    the number of pages in that filtered list (at least one). Newest first, except "escalated":
    that list is read longest-waiting first (since it was handed to a person, #1485), so the page
    that matters is page 1. The count and the rows come from one statement, so they share a
    snapshot; the count is joined to the page, so it comes back even for a page past the end
    (#2211)."""
    where = "" if status is None else " WHERE status = %s"
    # The status goes in twice when there is one: for the count and for the page.
    filter_args: tuple[object, ...] = () if status is None else (status,)
    params = (*filter_args, *filter_args, size, (number - 1) * size)
    if status == "escalated":
        order = "COALESCE(handed_over_at, created_at), id"
        outer = "COALESCE(t.handed_over_at, t.created_at) NULLS LAST, t.id NULLS LAST"
    else:
        order = "created_at DESC, id DESC"
        outer = "t.created_at DESC NULLS LAST, t.id DESC NULLS LAST"
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "SELECT c.n, t.id, t.created_at, t.user_id, t.plan, t.subject, t.message, t.source,"
            " t.status, t.reason, t.handed_over_at, t.email"
            f" FROM (SELECT count(*) AS n FROM support_tickets{where}) c"
            " LEFT JOIN LATERAL (SELECT id, created_at, user_id, plan, subject,"
            # The words come through a door function: the website's role has no right to the column.
            " COALESCE(dmbot_support_message(id), '') AS message, source, status,"
            " escalated_reason AS reason, handed_over_at, dmbot_support_email(user_id) AS email"
            f" FROM support_tickets{where} ORDER BY {order}"
            " LIMIT %s OFFSET %s) t ON true"
            f" ORDER BY {outer}",
            params,
        )
        found = await cur.fetchall()
    pages = max(1, -(-(int(found[0]["n"]) if found else 0) // size))
    return [
        Row(**{k: v for k, v in r.items() if k != "n"}) for r in found if r["id"] is not None
    ], pages


async def counts(db: Database) -> dict[Status, int]:
    """How many tickets have each status, for the filter buttons (#1375). Every status is there,
    with 0 for none, and the counts cover all tickets, not just the page shown."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "SELECT status, count(*) AS n FROM support_tickets GROUP BY status"
        )
        found = {r["status"]: int(r["n"]) for r in await cur.fetchall()}
    return {status: found.get(status, 0) for status in STATUSES}


WEEK_S = 7 * 86400


@dataclass(frozen=True)
class Week:
    """How support went in the last seven days (#1480): numbers only, never a word of a ticket.
    `helper` and `team` count the tickets that are answered or closed whose latest answer was
    DMbot's helper's or the team's; `waiting` the ones still new or escalated; `reasons` how many
    tickets came to a person for each code, from `handed_over_reason`, which stays after a person
    answers (#1487), so every hand-over the helper makes is counted, whatever happened since,
    among the tickets opened this week (like the other numbers here: a ticket counts once, under
    its latest reason). Tickets handed over before that column existed have none, and a ticket a
    customer reopens after the team's answer comes back to the team with no new reason. The
    admin list's `reason` is a different thing: why a ticket is waiting now."""

    questions: int
    helper: int
    team: int
    waiting: int
    reasons: dict[Reason, int]


async def week(db: Database, now: int) -> Week:
    """The last seven days from the tickets and who wrote each one's latest answer. Only the
    columns the admin door may read are used (`support_replies`'s words are not among them)."""
    since = now - WEEK_S
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "SELECT count(*) AS questions,"
            " count(*) FILTER (WHERE t.status IN ('answered', 'closed') AND a.who = 'ai')"
            "   AS helper,"
            " count(*) FILTER (WHERE t.status IN ('answered', 'closed') AND a.who = 'person')"
            "   AS team,"
            " count(*) FILTER (WHERE t.status IN ('new', 'escalated')) AS waiting"
            " FROM support_tickets t LEFT JOIN LATERAL ("
            "   SELECT r.who FROM support_replies r"
            "   WHERE r.ticket_id = t.id AND r.who IN ('ai', 'person')"
            "   ORDER BY r.id DESC LIMIT 1) a ON true"
            " WHERE t.created_at >= %s",
            (since,),
        )
        row = await cur.fetchone()
        assert row is not None
        cur = await conn.execute(
            "SELECT handed_over_reason AS reason, count(*) AS n FROM support_tickets"
            " WHERE created_at >= %s AND handed_over_reason IS NOT NULL"
            " GROUP BY handed_over_reason",
            (since,),
        )
        found = {r["reason"]: int(r["n"]) for r in await cur.fetchall()}
    return Week(
        questions=int(row["questions"]),
        helper=int(row["helper"]),
        team=int(row["team"]),
        waiting=int(row["waiting"]),
        reasons={reason: found.get(reason, 0) for reason in REASONS},
    )


def week_line(week: Week) -> str:
    """The week in one sentence: the same words as the admin page's `weekLine` (web/src/content/
    admin.ts), which tests/test_support_week_mail.py keeps in step. Counts only, never a word of a
    ticket."""
    if week.questions <= 0:
        return "No questions this week."
    noun = "question" if week.questions == 1 else "questions"
    line = (
        f"This week: {week.questions} {noun}, {week.helper} answered by DMbot's helper, "
        f"{week.team} by the team, {week.waiting} waiting."
    )
    handed = sum(week.reasons.values())
    if handed > 0:
        parts = [
            f"{REASON_WORDS[code]} {n}"
            for code, n in week.reasons.items()
            if n > 0 and code in REASON_WORDS
        ]
        detail = f" ({', '.join(parts)})" if parts else ""
        line += f" Helper handed over {handed}{detail}."
    return line


# The weekly email goes out in the first sweep after this time on a Monday (UTC) (#1514).
WEEKLY_MAIL_HOUR = 8


def mail_week(now: int) -> int:
    """The week the email is due for: the day number (date ordinal) of the latest Monday 08:00 UTC
    at or before `now`. It changes once a week, so it is the once-only mark."""
    when = datetime.fromtimestamp(now, UTC)
    monday = when.date() - timedelta(days=when.weekday())
    if when < datetime(monday.year, monday.month, monday.day, WEEKLY_MAIL_HOUR, tzinfo=UTC):
        monday -= timedelta(days=7)
    return monday.toordinal()


async def claim_week(db: Database, week: int) -> bool:
    """Take the weekly email for this week: True for the one call that moves the mark forward, so
    a restart or a second copy never sends it twice. Claimed before sending, like the account
    emails: a crash loses one email rather than doubling it."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "INSERT INTO support_week_mail (id, week) VALUES (1, %s)"
            " ON CONFLICT (id) DO UPDATE SET week = EXCLUDED.week"
            " WHERE support_week_mail.week < EXCLUDED.week RETURNING week",
            (week,),
        )
        return await cur.fetchone() is not None


async def release_week(db: Database, week: int) -> None:
    """Give the claim back after a send that failed, so the next sweep tries again."""
    async with db.support_admin() as conn:
        await conn.execute(
            "UPDATE support_week_mail SET week = week - 1 WHERE id = 1 AND week = %s", (week,)
        )


# The words for each hand-over code (the same as the admin page's weekReasonWords).
REASON_WORDS: dict[str, str] = {
    "unhappy": "unhappy",
    "asked": "asked",
    "limit": "five answers used",
    "no-email": "no email",
    "helper-down": "helper down",
}

# How many of their own questions a person sees at once (#1373).
MINE_LIMIT = 20


@dataclass(frozen=True)
class Mine:
    """One of a person's own questions as the account page lists it: when, the subject and where
    it stands. Not the words, and not the ticket's number (it would show how many other people
    write)."""

    created_at: int
    subject: str
    status: Status
    # Who wrote the latest answer ("ai": DMbot's helper, "person": the team) and its first line
    # (#1432); None before any answer. The person's own words never come back.
    answered_by: Literal["ai", "person"] | None = None
    latest: str | None = None


ANSWER_LINE_MAX = 100  # characters of an answer's first line the account page shows


_GREETING = re.compile(
    r"^(hi|hello|hey|dear|thanks|thank you)\b[^.!?:]{0,40}[,!.:]?$", re.IGNORECASE
)


def first_line(text: str) -> str:
    """The first line of an answer that says something: not an empty line, and not a short
    greeting ("Hi Alice,"), cut to `ANSWER_LINE_MAX` (at a word's end if it can)."""
    lines = [" ".join(raw.split()) for raw in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) > 1 and _GREETING.match(lines[0]):
        lines = lines[1:]
    for line in lines[:1]:
        if line:
            if len(line) <= ANSWER_LINE_MAX:
                return line
            cut = line[:ANSWER_LINE_MAX].rsplit(" ", 1)[0] or line[:ANSWER_LINE_MAX]
            return cut.rstrip(".,;:!? ") + "…"
    return ""


async def mine(db: Database, user_id: int) -> list[Mine]:
    """The signed-in person's own tickets, newest first. The table's `read_own` policy already
    holds a person's session to their own rows; the WHERE says it again, so one wrong line in
    either place does not show anyone else's."""
    async with db.user(user_id) as conn:
        # The latest answer comes through its own door (the role has no right on the words); one
        # call for each of at most MINE_LIMIT tickets.
        cur = await conn.execute(
            "SELECT t.created_at, t.subject, t.status, a.who AS answered_by, a.text AS latest"
            " FROM support_tickets t LEFT JOIN LATERAL dmbot_support_latest_answer(t.id) a ON true"
            " WHERE t.user_id = %s ORDER BY t.created_at DESC, t.id DESC LIMIT %s",
            (user_id, MINE_LIMIT),
        )
        found = await cur.fetchall()
    return [
        Mine(
            r["created_at"],
            r["subject"],
            r["status"],
            r["answered_by"],
            # None before an answer (or one with no words in it), else its first line.
            (first_line(r["latest"]) or None) if r["latest"] is not None else None,
        )
        for r in found
    ]


async def set_status(db: Database, ticket_id: int, status: Status) -> bool:
    """Mark a ticket answered or closed. False: there is no such ticket (or it was deleted)."""
    async with db.support_admin() as conn:
        # The reason is for an escalated ticket only: any other status clears it.
        cur = await conn.execute(
            "UPDATE support_tickets SET status = %s, escalated_reason ="
            " CASE WHEN %s = 'escalated' THEN escalated_reason END WHERE id = %s",
            (status, status, ticket_id),
        )
        return cur.rowcount == 1


# An answered ticket the person has not written to for this long is finished (#1505): otherwise
# the admin list keeps every answered ticket until someone presses Close, for ever. Not a setting.
SUPPORT_AUTO_CLOSE_DAYS = 14


async def close_stale(db: Database, now: int) -> int:
    """Close answered tickets with no activity for `SUPPORT_AUTO_CLOSE_DAYS` (the newest reply,
    or the ticket's own date when it has none). New and escalated tickets are never touched: they
    are waiting for an answer. A later emailed reply from the person reopens it (`add_reply`
    takes any status, so a closed ticket goes back to waiting). How many were closed."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "UPDATE support_tickets t SET status = 'closed', escalated_reason = NULL"
            " WHERE t.status = 'answered' AND greatest(t.created_at, coalesce("
            "   (SELECT max(r.created_at) FROM support_replies r WHERE r.ticket_id = t.id), 0))"
            "   < %s",
            (now - SUPPORT_AUTO_CLOSE_DAYS * 86400,),
        )
        return cur.rowcount


async def forget_old(db: Database) -> int:
    """Delete tickets kept for over 12 months (privacy page, #1340). The database decides
    which are old: this role may delete them, never read them."""
    async with db.cleanup("old-tickets") as conn:
        cur = await conn.execute("DELETE FROM support_tickets")
        return cur.rowcount


# --- the thread: the helper's answers, the person's emailed replies, the team's replies (#1431)

# The most emailed replies one ticket keeps: a flood is dropped, not stored.
MAX_CUSTOMER_REPLIES = 30
Who = Literal["customer", "ai", "person"]


@dataclass(frozen=True)
class Reply:
    id: int
    who: Who
    text: str
    created_at: int


async def thread(db: Database, ticket_id: int) -> list[Reply]:
    """The ticket's replies, oldest first (the first message is the ticket's own). The words come
    through a door that answers only while the admin door is open."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "SELECT id, who, text, created_at FROM dmbot_support_thread(%s)", (ticket_id,)
        )
        return [Reply(**r) for r in await cur.fetchall()]


@dataclass(frozen=True)
class Open:
    """A ticket the helper has to look at: the words are read through their doors, and the
    address is the account's, never copied into the ticket. Never logged."""

    id: int
    user_id: int
    plan: str
    subject: str
    message: str
    created_at: int
    status: Status
    ai_failed_at: int | None
    email: str | None


LEASE_S = (
    600  # a ticket the helper has taken is left alone this long (an AI call is under 5 minutes)
)


async def waiting(db: Database, now: int, limit: int = 5) -> list[Open]:
    """Tickets waiting for the helper whose turn has come, oldest first. Each is taken for
    `LEASE_S` seconds in the same statement, so a second copy of the website (a restart that
    overlaps) or the next pass never answers the same ticket twice; an answer clears the lease."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "UPDATE support_tickets SET ai_next_at = %s WHERE id IN ("
            " SELECT id FROM support_tickets WHERE status = 'new' AND ai_next_at <= %s"
            " ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING id",
            (now + LEASE_S, now, limit),
        )
        ids = sorted(int(r["id"]) for r in await cur.fetchall())
    found = [await one(db, i) for i in ids]
    return [t for t in found if t is not None]


async def one(db: Database, ticket_id: int) -> Open | None:
    """One ticket as the helper and the team's reply door see it, whatever its status."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "SELECT id, user_id, plan, subject, COALESCE(dmbot_support_message(id), '') AS message,"
            " created_at, status, ai_failed_at, dmbot_support_email(user_id) AS email"
            " FROM support_tickets WHERE id = %s",
            (ticket_id,),
        )
        row = await cur.fetchone()
    return None if row is None else Open(**row)


@dataclass(frozen=True)
class Added:
    id: int
    before: Status  # the ticket's status when the reply came in
    after: Status


async def add_reply(
    db: Database, ticket_id: int, who: Who, text: str, *, now: int, after_id: int | None = None
) -> Added | None:
    """Keep a reply and move the ticket's status in the same step, with the ticket's row locked,
    so two writers (the helper's answer, a person's emailed reply, the team) can't undo each other.
    None: nothing was kept (no such ticket; too many replies from the person; or, for the helper,
    the ticket is no longer waiting or has a reply the helper hasn't read, `after_id` being the
    last reply it read, 0 for none).
    - helper (`ai`): the ticket becomes answered.
    - the team (`person`): the ticket becomes answered.
    - the person (`customer`): the ticket goes back to waiting, except that one a person has
      already had a hand in (escalated, or answered by the team) goes to the team, not the
      helper."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "SELECT status FROM support_tickets WHERE id = %s FOR UPDATE", (ticket_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        before: Status = row["status"]
        cur = await conn.execute(
            "SELECT COALESCE(max(id), 0) AS last,"
            " count(*) FILTER (WHERE who = 'customer') AS customers,"
            " count(*) FILTER (WHERE who = 'person') AS team"
            " FROM dmbot_support_thread(%s)",
            (ticket_id,),
        )
        seen = await cur.fetchone()
        assert seen is not None
        if who == "ai" and (before != "new" or int(seen["last"]) != (after_id or 0)):
            return None
        if who == "customer" and int(seen["customers"]) >= MAX_CUSTOMER_REPLIES:
            return None
        after: Status
        if who == "customer":
            handled = before == "escalated" or int(seen["team"]) > 0
            after = "escalated" if handled else "new"
        else:
            after = "answered"
        cur = await conn.execute(
            "INSERT INTO support_replies (ticket_id, who, text, created_at)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (ticket_id, who, text, now),
        )
        inserted = await cur.fetchone()
        assert inserted is not None
        await conn.execute(
            "UPDATE support_tickets SET status = %s, ai_next_at = 0, ai_failed_at = NULL,"
            " escalated_reason = CASE WHEN %s = 'escalated' THEN escalated_reason END"
            " WHERE id = %s",
            (after, after, ticket_id),
        )
        return Added(int(inserted["id"]), before, after)


async def mail_ids(db: Database, ticket_id: int) -> list[str]:
    """The Message-IDs of the emails already sent for the ticket, oldest first (#2192)."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "SELECT message_id FROM support_mail_ids WHERE ticket_id = %s ORDER BY id",
            (ticket_id,),
        )
        return [str(r["message_id"]) for r in await cur.fetchall()]


async def keep_mail_id(db: Database, ticket_id: int, message_id: str, *, now: int) -> None:
    """Keep the Message-ID of an email just sent for the ticket. Nothing else about the email."""
    async with db.support_admin() as conn:
        await conn.execute(
            "INSERT INTO support_mail_ids (ticket_id, message_id, created_at) VALUES (%s, %s, %s)",
            (ticket_id, message_id, now),
        )


async def release(db: Database, ticket_id: int) -> None:
    """Give a ticket back to the helper at once (what it read was out of date)."""
    async with db.support_admin() as conn:
        await conn.execute(
            "UPDATE support_tickets SET ai_next_at = 0 WHERE id = %s AND status = 'new'",
            (ticket_id,),
        )


async def defer(db: Database, ticket_id: int, *, now: int, wait: int) -> int:
    """The helper's call failed: try this ticket again in `wait` seconds. Returns when the calls
    first began to fail (now, if this is the first time)."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "UPDATE support_tickets SET ai_next_at = %s, ai_failed_at = COALESCE(ai_failed_at, %s)"
            " WHERE id = %s RETURNING ai_failed_at",
            (now + wait, now, ticket_id),
        )
        row = await cur.fetchone()
    return now if row is None else int(row["ai_failed_at"])


async def escalate(db: Database, ticket_id: int, reason: Reason, now: int) -> bool:
    """Hand a ticket to a person, saying why. True only the first time (so the team is told
    once). The reason and the time are also kept in `handed_over_*`, which a person's answer
    does not clear (#1487): a ticket handed over again after an answer keeps the latest of
    each."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "UPDATE support_tickets SET status = 'escalated', escalated_reason = %s,"
            " handed_over_reason = %s, handed_over_at = %s"
            " WHERE id = %s AND status <> 'escalated'",
            (reason, reason, now, ticket_id),
        )
        return cur.rowcount == 1
