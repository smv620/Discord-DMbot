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
from dataclasses import dataclass
from typing import Literal

from dmbot.db import Database
from dmbot.entitlements import Entitlement
from dmbot.web.feedback import clean_text
from dmbot.web.payments import paid_plan_ids

log = logging.getLogger(__name__)

MAX_SUBJECT = 120
MAX_MESSAGE = 4000
# How many tickets the admin page lists at once, newest first. Kept short because the page looks
# up each person's name with one query per person.
LIST_LIMIT = 100

Status = Literal["new", "answered", "closed"]
STATUSES: tuple[Status, ...] = ("new", "answered", "closed")


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


async def add(db: Database, user_id: int, plan: str, ticket: Ticket, *, now: int) -> None:
    """Keep a ticket for this signed-in person. The table's policy lets them add a row for
    their own account and nothing else, which is why the person is set (`db.user`)."""
    async with db.user(user_id) as conn:
        await conn.execute(
            "INSERT INTO support_tickets (created_at, user_id, plan, subject, message)"
            " VALUES (%s, %s, %s, %s, %s)",
            (now, user_id, plan, ticket.subject, ticket.message),
        )


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
    # The account's address (the one the team replies to), read through the admin door and never
    # stored in the ticket; None when the account has none. Never logged.
    email: str | None


async def newest(
    db: Database, *, status: Status | None = None, limit: int = LIST_LIMIT
) -> list[Row]:
    """The tickets, newest first, for the admin page; only those with this status when one is
    given (#1375). `status` is one of STATUSES (the route checks it); it goes in as a parameter."""
    where, args = ("", (limit,)) if status is None else (" WHERE status = %s", (status, limit))
    async with db.support_admin() as conn:
        cur = await conn.execute(
            # The words come through a door function: the website's role has no right to the column.
            # One call per row (at most LIST_LIMIT): fine for an admin page read now and then.
            "SELECT id, created_at, user_id, plan, subject,"
            " COALESCE(dmbot_support_message(id), '') AS message, source, status,"
            " dmbot_support_email(user_id) AS email"
            f" FROM support_tickets{where} ORDER BY created_at DESC, id DESC LIMIT %s",
            args,
        )
        return [Row(**r) for r in await cur.fetchall()]


async def counts(db: Database) -> dict[Status, int]:
    """How many tickets have each status, for the filter buttons (#1375). Every status is there,
    with 0 for none, and the counts cover all tickets, not just the newest LIST_LIMIT."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "SELECT status, count(*) AS n FROM support_tickets GROUP BY status"
        )
        found = {r["status"]: int(r["n"]) for r in await cur.fetchall()}
    return {status: found.get(status, 0) for status in STATUSES}


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


async def mine(db: Database, user_id: int) -> list[Mine]:
    """The signed-in person's own tickets, newest first. The table's `read_own` policy already
    holds a person's session to their own rows; the WHERE says it again, so one wrong line in
    either place does not show anyone else's."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT created_at, subject, status FROM support_tickets WHERE user_id = %s"
            " ORDER BY created_at DESC, id DESC LIMIT %s",
            (user_id, MINE_LIMIT),
        )
        return [Mine(**r) for r in await cur.fetchall()]


async def set_status(db: Database, ticket_id: int, status: Status) -> bool:
    """Mark a ticket answered or closed. False: there is no such ticket (or it was deleted)."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "UPDATE support_tickets SET status = %s WHERE id = %s", (status, ticket_id)
        )
        return cur.rowcount == 1


async def forget_old(db: Database) -> int:
    """Delete tickets kept for over 12 months (privacy page, #1340). The database decides
    which are old: this role may delete them, never read them."""
    async with db.cleanup("old-tickets") as conn:
        cur = await conn.execute("DELETE FROM support_tickets")
        return cur.rowcount
