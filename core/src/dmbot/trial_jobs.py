"""The last day of Try It, and the automatic extension (#1229; docs/PLAN.md, "Accounts").

Two jobs the bot runs every hour (both safe to run again and again):

- **The last day:** a person whose Try It ends within a day gets a private message on Discord
  and, when DMbot has an email for them, an email. Once each, per Try It period.
- **The extension:** a person who has never paid, has used DMbot for under 4 hours in all, and
  whose Try It ended 3 days ago, gets 30 more days. It keeps every Try It limit, including the
  8 hours in all: the hours they get are what is left of the 8, not 8 more. It repeats each
  time one ends while the total stays under 4 hours. They get an email (when DMbot has an
  address) and are told privately at their next `/dmbot start`.

Nothing here runs while plans are not enforced (`DMBOT_ENFORCE_PLANS` off): before payment
works nobody's Try It ends. The rules are pure functions on `TrialPerson`; `TrialJobs` does
the sending, with the clock, the Discord message and the mailer passed in so tests fake them.
Personal links carry a signed token (dmbot.link_tokens) so the pages can greet the person.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from urllib.parse import urlencode

from dmbot.db import Database
from dmbot.link_tokens import make_link_token
from dmbot.mail import Mailer

log = logging.getLogger(__name__)

DAY_S = 86400
EXTENSION_DAYS = 30
EXTEND_AFTER_S = 3 * DAY_S  # Try It (or the last extension) ended this long ago
UNDER_MINUTES = 4 * 60  # "less than 4 hours in all"
TRY_IT_MINUTES_IN_ALL = 8 * 60  # Try It's 8 hours, in all, not per extension
LOOK_BACK_S = 40 * DAY_S  # how far back an ended Try It is still looked at


@dataclass(frozen=True, slots=True)
class TrialPerson:
    user_id: int
    dmbot_id: str
    email: str | None
    period_end: int
    minutes: int  # all DMbot minutes, all time
    paid: bool  # ever paid
    extensions: int  # how many times Try It was extended already


def on_last_day(p: TrialPerson, now: int) -> bool:
    """Try It ends within the next day (and it is not over yet)."""
    return not p.paid and now < p.period_end <= now + DAY_S


def extension_due(p: TrialPerson, now: int) -> bool:
    return not p.paid and p.minutes < UNDER_MINUTES and now >= p.period_end + EXTEND_AFTER_S


def extension_hours(minutes: int) -> int:
    """Whole hours left of Try It's 8 in all. At least 4, because only people under 4 hours
    are extended."""
    return max(0, (TRY_IT_MINUTES_IN_ALL - minutes) // 60)


def _page(site: str, path: str, dmbot_id: str, now: int, secret: bytes) -> str:
    if not site:
        return path
    if not secret:
        return f"{site}{path}"
    query = urlencode({"uid": dmbot_id, "t": make_link_token(dmbot_id, now, secret)})
    return f"{site}{path}?{query}"


def last_day_text(name: str, pricing: str, hello: str) -> str:
    """The owner's words (docs/PLAN.md, "Accounts")."""
    return (
        f"Hi {name}! Thanks for trying DMbot. We hope you love it. Free samples are nice, but "
        "we do have to pay other companies for the AI models DMbot uses. If you'd like to keep "
        f"using DMbot, please look at the plans at {pricing}. If you're still unsure, please "
        f"tell us how we can make DMbot better at {hello}."
    )


def extension_text(name: str, hours_left: int, pricing: str, hello: str) -> str:
    plural = "" if hours_left == 1 else "s"
    return (
        f"Hi {name}! We've given you 30 more days of Try It, because we want you to have the "
        "chance to really try DMbot at your table. It has the same limits as before: 8 hours "
        f"of DMbot time in all (you have {hours_left} hour{plural} left), one campaign, and no "
        f"backups or downloads. If you'd like to keep using DMbot after that, the plans are at "
        f"{pricing}. Tell us how we can make DMbot better at {hello}."
    )


EXTENSION_NOTICE = (
    "Good news: your Try It of DMbot has 30 more days, because we want you to have the "
    "chance to really try it at your table. It has the same limits: 8 hours in all, one "
    "campaign, and no backups or downloads."
)


async def take_extension_notice(db: Database, user_id: int, now: int) -> str | None:
    """The line to tell this person at their next `/dmbot start` if their Try It was
    extended and they haven't been told; None otherwise. Asking marks it told, so it shows
    once."""
    async with db.unscoped() as conn:
        cur = await conn.execute(
            "UPDATE trial_extensions SET told_at = %s WHERE user_id = %s AND told_at IS NULL"
            " RETURNING extended_at",
            (now, user_id),
        )
        return EXTENSION_NOTICE if await cur.fetchone() else None


SendDm = Callable[[int, str], Awaitable[bool]]
NameOf = Callable[[int], Awaitable[str]]


@dataclass(slots=True)
class Summary:
    last_day_dms: int = 0
    last_day_emails: int = 0
    extended: int = 0
    extension_emails: int = 0


async def people(db: Database, now: int) -> list[TrialPerson]:
    """Everyone in Try It whose period is recent enough to matter, through the one door the
    job may use (migration 0065)."""
    async with db.unscoped() as conn:
        cur = await conn.execute(
            "SELECT user_id, dmbot_id, email, period_end, minutes, paid, extensions"
            " FROM dmbot_trial_people(%s)",
            (now - LOOK_BACK_S,),
        )
        return [
            TrialPerson(
                int(r["user_id"]),
                str(r["dmbot_id"]),
                r["email"],
                int(r["period_end"]),
                int(r["minutes"]),
                bool(r["paid"]),
                int(r["extensions"]),
            )
            for r in await cur.fetchall()
        ]


async def claim(db: Database, user_id: int, kind: str, now: int) -> bool:
    """Take the once-only record for a message that is not an email (a private message)."""
    async with db.unscoped() as conn:
        cur = await conn.execute(
            "INSERT INTO sent_emails (user_id, kind, sent_at) VALUES (%s, %s, %s)"
            " ON CONFLICT DO NOTHING RETURNING user_id",
            (user_id, kind, now),
        )
        return await cur.fetchone() is not None


async def release(db: Database, user_id: int, kind: str) -> None:
    async with db.unscoped() as conn:
        await conn.execute(
            "DELETE FROM sent_emails WHERE user_id = %s AND kind = %s", (user_id, kind)
        )


class TrialJobs:
    def __init__(
        self,
        db: Database,
        mailer: Mailer,
        *,
        enforce_plans: bool,
        site_url: str,
        link_secret: bytes,
        send_dm: SendDm,
        name_of: NameOf,
        skip: Collection[int] = (),
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._mail = mailer
        self._enforce = enforce_plans
        self._site = site_url.rstrip("/")
        self._secret = link_secret
        self._send_dm = send_dm
        self._name_of = name_of
        self._skip = frozenset(skip)  # the owner's own accounts: free, no Try It to end
        self._clock = clock

    async def run(self) -> Summary:
        summary = Summary()
        if not self._enforce:  # before payment works, nobody's Try It ends
            return summary
        now = int(self._clock())
        for person in await people(self._db, now):
            if person.user_id in self._skip:
                continue
            try:
                if on_last_day(person, now):
                    await self._last_day(person, now, summary)
                elif extension_due(person, now):
                    await self._extend(person, now, summary)
            except Exception:  # one person's trouble never stops the others
                log.exception("A Try It job failed for one person")
        return summary

    def _links(self, person: TrialPerson, now: int) -> tuple[str, str]:
        return (
            _page(self._site, "/pricing", person.dmbot_id, now, self._secret),
            _page(self._site, "/hello", person.dmbot_id, now, self._secret),
        )

    async def _last_day(self, person: TrialPerson, now: int, summary: Summary) -> None:
        pricing, hello = self._links(person, now)
        name = await self._name_of(person.user_id)
        text = last_day_text(name, pricing, hello)
        kind = f"trial-end-dm:{person.period_end}"
        if await claim(self._db, person.user_id, kind, now):
            if await self._send_dm(person.user_id, text):
                summary.last_day_dms += 1
            else:  # closed messages or a hiccup: try again at the next run, within the day
                await release(self._db, person.user_id, kind)
        result = await self._mail.send(
            user_id=person.user_id,
            kind=f"trial-end:{person.period_end}",
            to=person.email,
            subject="Thanks for trying DMbot",
            body=text,
            offer=True,
        )
        if result == "sent":
            summary.last_day_emails += 1

    async def _extend(self, person: TrialPerson, now: int, summary: Summary) -> None:
        hours = extension_hours(person.minutes)
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT dmbot_extend_trial(%s, %s, %s, %s) AS ok",
                (person.user_id, now, EXTENSION_DAYS, hours),
            )
            row = await cur.fetchone()
        if not (row and row["ok"]):
            return
        summary.extended += 1
        pricing, hello = self._links(person, now)
        name = await self._name_of(person.user_id)
        result = await self._mail.send(
            user_id=person.user_id,
            kind=f"trial-extension:{person.extensions + 1}",
            to=person.email,
            subject="30 more days of DMbot",
            body=extension_text(name, hours, pricing, hello),
            offer=True,
        )
        if result == "sent":
            summary.extension_emails += 1
