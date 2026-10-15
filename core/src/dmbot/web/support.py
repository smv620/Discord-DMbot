"""Private support tickets from paying people on the website (#1340; docs/PLAN.md, "QAbot and
support tickets").

Privacy: a ticket holds personal details, so it never becomes a public GitHub issue and its
words are never logged or emailed. It goes to the `support_tickets` table, which the signed-in
person can add to but not read (not even their own ticket), and which only the admin page's
door (`Database.support_admin`) can read or close. The email to the team says only that a ticket
is waiting, and from which account and plan, so the words stay in the database. Tickets older
than 12 months are deleted by the website's hourly sweep, and deleting an account deletes the
person's own.

Abuse: paying accounts only, one ticket per account per 10 minutes, 4,000 characters, and a
Cloudflare Turnstile check when its key is set (the route does the last two).
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


def is_paying(plan: Entitlement | None, now: int) -> bool:
    """A paid plan that still works. Not Try It (its own plan row), a grant or the free list.
    Unlike moving an account (`can_move`), the payment grace counts: someone whose payment
    just failed is exactly who may need to write to us."""
    return plan is not None and plan.plan in paid_plan_ids() and plan.usable(now)


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


async def newest(db: Database, *, limit: int = LIST_LIMIT) -> list[Row]:
    """The tickets, newest first, for the admin page."""
    async with db.support_admin() as conn:
        cur = await conn.execute(
            "SELECT id, created_at, user_id, plan, subject, message, source, status,"
            " dmbot_support_email(user_id) AS email"
            " FROM support_tickets ORDER BY created_at DESC, id DESC LIMIT %s",
            (limit,),
        )
        return [Row(**r) for r in await cur.fetchall()]


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
