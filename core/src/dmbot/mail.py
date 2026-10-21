"""Sending email (#1229; docs/PLAN.md, "Accounts"): one small module, plain SMTP with STARTTLS,
through Resend (an API key in the server's `.env` as the SMTP password; a Google app password
works the same way), never in the repository. No SDK.

Two kinds of email:
- **Account emails** (sign-in links, move codes): wanted, sent when asked, no unsubscribe.
- **Offer emails** (the trial-end and extension emails): each carries a signed unsubscribe
  link and the postal address (US CAN-SPAM). They are refused while `MAIL_POSTAL_ADDRESS` is
  empty, and never go to a person who unsubscribed.

`sent_emails` records who got which kind and when, never the text, so a one-off email goes
once. A daily cap (default 400; keep it at or under the provider's limit) stops a bug from
sending a flood.
No address or body is ever logged: the log says only what kind went and what happened.
Tests use example.com addresses and a fake transport.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Literal

from dmbot.account_id import to_dmbot_id
from dmbot.alerts import Problem, raise_alert
from dmbot.db import Database
from dmbot.link_tokens import make_unsubscribe_token

log = logging.getLogger(__name__)

KIND_MAX = 60  # the sent_emails column's limit
DAILY_CAP_DEFAULT = 400
# Addresses that are a person's own live replies and never an automated sender (#1425: the owner
# answers from admin@ by hand, so a robot sending as it would look like a person's word).
RESERVED_SENDERS = ("admin@getdmbot.com",)
# Daily slots kept back for owner alerts: other mail stops this many short of the cap, so a flood
# of sign-in links can't starve an alert. Twice dmbot.alerts.ALERT_DAILY_MAX because the bot and
# the website each send up to that many.
ALERT_RESERVE = 10
DAY_S = 86400
# A Try It's sent notes (the last-day and 30-more-days emails and their private-message
# records) have no job left after a year: a Try It can't be taken twice, because the DMbot ID
# marks it used. So they are deleted then, not kept for ever (#1287).
TRIAL_EMAIL_KEEP_S = 365 * DAY_S

Result = Literal[
    "sent", "already_sent", "unsubscribed", "capped", "not_ready", "no_address", "failed"
]


@dataclass(frozen=True, slots=True)
class MailSettings:
    """Everything from the environment (names in .env.example). Never logged."""

    host: str = ""
    port: int = 587
    user: str = ""
    app_password: str = field(default="", repr=False)
    sender: str = ""  # the From address
    # The support flow's two other senders (#1431): what a person gets from DMbot's helper and the
    # team (`info@`), and the alerts to the team (the alerts sender, the `<dmbot private>` email,
    # docs/EMAIL.md). Empty: `sender` stands in.
    support_from: str = ""
    alerts_from: str = ""
    # Where owner alerts go (#1907): the owner's private address, from the server's .env only,
    # never logged. Empty: no alert emails. `alerts_reply_to` is the Reply-To on them (optional).
    owner_alert_email: str = field(default="", repr=False)
    alerts_reply_to: str = ""
    postal_address: str = ""  # required in offer emails; the owner's call (needs: owner)
    daily_cap: int = DAILY_CAP_DEFAULT

    @property
    def mail_cap(self) -> int:
        """What other mail may use of the day: the cap less the slots kept for alerts."""
        return max(1, self.daily_cap - ALERT_RESERVE)

    @property
    def offer_cap(self) -> int:
        """Offer emails stop here; account emails go on up to `mail_cap`."""
        return min(self.mail_cap * 3 // 4, 300)  # the database door's own ceiling is 300

    @property
    def support_sender(self) -> str:
        return self.support_from or self.sender

    @property
    def alerts_sender(self) -> str:
        return self.alerts_from or self.sender

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.app_password and self.sender)


class MailConfigError(ValueError):
    """A mail setting that can't be right; the message says which."""


def load_mail_settings(env: Mapping[str, str]) -> MailSettings:
    def get(name: str) -> str:
        return env.get(name, "").strip()

    port_raw = get("SMTP_PORT") or "587"
    cap_raw = get("MAIL_DAILY_CAP") or str(DAILY_CAP_DEFAULT)
    if not (port_raw.isascii() and port_raw.isdigit() and 0 < int(port_raw) < 65536):
        raise MailConfigError("SMTP_PORT must be a port number, such as 587.")
    if not (cap_raw.isascii() and cap_raw.isdigit() and ALERT_RESERVE < int(cap_raw) <= 10_000):
        raise MailConfigError(
            f"MAIL_DAILY_CAP must be a number from {ALERT_RESERVE + 1} to 10000 "
            f"({ALERT_RESERVE} are kept back for owner alerts)."
        )
    address = ", ".join(
        " ".join(part.split()) for part in get("MAIL_POSTAL_ADDRESS").split("|") if part.strip()
    )
    senders: dict[str, str] = {}
    for name in ("MAIL_FROM", "MAIL_SUPPORT_FROM", "MAIL_ALERTS_FROM"):
        raw = get(name)
        # "Name <a@b.example>" or a bare address; anything else would send from nowhere.
        if raw and not one_address(parseaddr(raw)[1]):
            raise MailConfigError(f"{name} must be an email address, such as info@example.org.")
        if parseaddr(raw)[1].lower() in RESERVED_SENDERS:
            raise MailConfigError(
                f"{name} can't be {parseaddr(raw)[1]}: that address is for a person's own "
                f"replies, never for DMbot's automatic emails."
            )
        senders[name] = raw
    owner_alert_email = get("OWNER_ALERT_EMAIL")
    if owner_alert_email and not one_address(owner_alert_email):
        raise MailConfigError("OWNER_ALERT_EMAIL must be one email address (or empty).")
    alerts_reply_to = get("MAIL_ALERTS_REPLY_TO")
    if alerts_reply_to and not one_address(alerts_reply_to):
        raise MailConfigError("MAIL_ALERTS_REPLY_TO must be one email address (or empty).")
    return MailSettings(
        host=get("SMTP_HOST"),
        port=int(port_raw),
        user=get("SMTP_USER"),
        app_password=get("SMTP_APP_PASSWORD"),
        sender=senders["MAIL_FROM"],
        support_from=senders["MAIL_SUPPORT_FROM"],
        alerts_from=senders["MAIL_ALERTS_FROM"],
        owner_alert_email=owner_alert_email,
        alerts_reply_to=alerts_reply_to,
        postal_address=address,
        daily_cap=int(cap_raw),
    )


# One message in, nothing out; raises on any failure. Faked in tests.
Transport = Callable[[EmailMessage], Awaitable[None]]


class SmtpTransport:
    """The real send: SMTP over STARTTLS (port 587) or SSL (port 465), in a thread so the
    bot's event loop never waits on it."""

    def __init__(self, settings: MailSettings) -> None:
        self._s = settings

    def _send(self, message: EmailMessage) -> None:
        s = self._s
        context = ssl.create_default_context()
        if s.port == 465:
            with smtplib.SMTP_SSL(s.host, s.port, timeout=20, context=context) as smtp:
                smtp.login(s.user, s.app_password)
                smtp.send_message(message)
            return
        with smtplib.SMTP(s.host, s.port, timeout=20) as smtp:
            smtp.starttls(context=context)
            smtp.login(s.user, s.app_password)
            smtp.send_message(message)

    async def __call__(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send, message)


def one_address(to: str | None) -> bool:
    """Exactly one plain address and nothing else: no list, no display name, no line break,
    so one send can never reach a second person or add a header."""
    if not to or len(to) > 254 or not to.isprintable() or not to.isascii():
        return False
    if any(c in to for c in ' ,;<>()[]"\\'):
        return False
    local, at, domain = to.partition("@")
    return bool(local and at and "." in domain and "@" not in domain and not domain.endswith("."))


def _tell_owner_mail_failed() -> None:
    """The owner hears (on Discord: email is what broke) that a send failed."""
    raise_alert(Problem.MAIL_FAILED)


class Mailer:
    def __init__(
        self,
        db: Database,
        settings: MailSettings,
        *,
        transport: Transport | None = None,
        site_url: str = "",
        link_secret: bytes = b"",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._s = settings
        self._send: Transport = transport or SmtpTransport(settings)
        self._site = site_url.rstrip("/")
        self._secret = link_secret
        self._clock = clock

    @property
    def settings(self) -> MailSettings:
        return self._s

    @property
    def ready_for_offers(self) -> bool:
        """Offer emails need a way out of the mailbox, a postal address, and a place for the
        unsubscribe link to point."""
        return bool(self._s.configured and self._s.postal_address and self._site and self._secret)

    async def is_unsubscribed(self, user_id: int) -> bool:
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT 1 AS ok FROM email_unsubscribed WHERE user_id = %s", (user_id,)
            )
            return await cur.fetchone() is not None

    async def already_sent(self, user_id: int, kind: str) -> bool:
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT 1 AS ok FROM sent_emails WHERE user_id = %s AND kind = %s",
                (user_id, kind),
            )
            return await cur.fetchone() is not None

    async def send(
        self,
        *,
        user_id: int,
        kind: str,
        to: str | None,
        subject: str,
        body: str,
        offer: bool = False,
        sender: str | None = None,
        reply_to: str | None = None,
    ) -> Result:
        """Send one email to a person, once per `kind` (put a round number in `kind` for
        something that may repeat). Never raises: the result says what happened. An offer
        is refused without a postal address, to someone who unsubscribed, or when the daily
        cap is spent."""
        # "dm:" kinds are the private-message records the Try It jobs keep in the same table.
        if (
            not one_address(to)
            or not 0 < len(kind) <= KIND_MAX
            or kind.startswith("dm:")
            or user_id <= 0
        ):
            return "no_address"
        if not self._s.configured:
            return "not_ready"
        if offer:
            if not self.ready_for_offers:
                return "not_ready"
            if await self.is_unsubscribed(user_id):
                return "unsubscribed"
        # Built before anything is recorded: a message that can't be built must not leave the
        # once-only row behind. The unsubscribe link is made for this person, from their id.
        try:
            message = self._build(
                to or "", to_dmbot_id(user_id), subject, body, offer, sender, reply_to
            )
        except ValueError:
            log.error("Couldn't build a %s email", kind)
            return "failed"
        # The database's door counts the day, applies the caps (offers stop short of the
        # cap, so the sign-in links and move codes people ask for always have room) and
        # writes the row under one lock, so two runs at once can't both send it. The website
        # uses the same door. The row stays if the send then fails (a crash or an SMTP error
        # loses that email rather than risk sending it twice), except for an offer: that is
        # given back below so the next run retries it.
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT dmbot_claim_email(%s, %s, %s, %s, %s) AS status",
                (user_id, kind, offer, self._s.mail_cap, self._s.offer_cap),
            )
            row = await cur.fetchone()
        status = row["status"] if row else "refused"
        if status == "capped":
            log.warning("The daily email cap is spent; %s waits", kind)
            return "capped"
        if status == "duplicate":
            return "already_sent"
        if status != "sent":
            return "no_address"
        try:
            await self._send(message)
        except Exception as exc:  # an SMTP error can carry the address: log only the kind
            log.error("Couldn't send a %s email", kind, exc_info=False)
            # One person's mistyped address is not the mail service failing.
            if not isinstance(exc, smtplib.SMTPRecipientsRefused):
                _tell_owner_mail_failed()
            if offer:
                await self._release(user_id, kind)
            return "failed"
        log.info("Sent a %s email", kind)
        return "sent"

    async def notify_team(
        self, to: str, subject: str, body: str, *, sender: str | None = None
    ) -> bool:
        """One plain email to the team's own address (a new support ticket, #1340). It is not
        to a person, so it has no once-only record, no unsubscribe link and no share of the
        daily cap (its pace is the ticket form's one per account per 10 minutes). Never
        raises; False means it didn't go. Callers keep private words out of it."""
        if not self._s.configured or not one_address(to):
            return False
        try:
            message = self._build(to, "", subject, body, False, sender)
            await self._send(message)
        except Exception as exc:  # an SMTP error can carry the address: say only that it failed
            log.error("Couldn't send a team email", exc_info=False)
            if not isinstance(exc, smtplib.SMTPRecipientsRefused):
                _tell_owner_mail_failed()
            return False
        return True

    async def _release(self, user_id: int, kind: str) -> None:
        """Give back the claim of an offer that didn't go, so the next run retries it."""
        try:
            async with self._db.unscoped() as conn:
                await conn.execute("SELECT dmbot_release_email(%s, %s)", (user_id, kind))
        except Exception:
            log.error("Couldn't give back a %s email's claim", kind)

    def _build(
        self,
        to: str,
        dmbot_id: str,
        subject: str,
        body: str,
        offer: bool,
        sender: str | None = None,
        reply_to: str | None = None,
    ) -> EmailMessage:
        from_address = sender or self._s.sender
        message = EmailMessage()
        message["From"] = from_address
        if reply_to:
            if not one_address(reply_to):
                raise ValueError("bad reply-to")
            message["Reply-To"] = reply_to
        message["To"] = to
        message["Subject"] = " ".join(subject.split())
        message["Date"] = formatdate(self._clock(), localtime=False)
        domain = parseaddr(from_address)[1].rpartition("@")[
            2
        ]  # from "Name <a@b>", not the raw text
        message["Message-ID"] = make_msgid(domain=domain or None)
        text = body.strip()
        if offer:
            link = self.unsubscribe_link(dmbot_id)
            text += (
                f"\n\n--\nYou get this email because you tried DMbot. To stop emails like this, "
                f"open:\n{link}\n{self._s.postal_address}"
            )
            message["List-Unsubscribe"] = f"<{link}>"
        message.set_content(text)
        return message

    def unsubscribe_link(self, dmbot_id: str) -> str:
        token = make_unsubscribe_token(dmbot_id, self._secret)
        return f"{self._site}/unsubscribe?uid={dmbot_id}&t={token}"


async def unsubscribe(db: Database, user_id: int, *, now: int) -> None:
    """Record that this person wants no offer emails (again is the same). Without RETURNING:
    the website's role may insert here but not read the list (migration 0064)."""
    async with db.unscoped() as conn:
        await conn.execute(
            "INSERT INTO email_unsubscribed (user_id, at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, now),
        )


async def prune_account_emails(db: Database, now: int) -> int:
    """Forget old records of emails that repeat or have done their job, so the table doesn't
    grow for ever: sign-in, move-code and recap-email records after 90 days (a recap email goes
    once for each session, so after 90 days nothing could send it again; #1400), and a Try It's
    notes after a year (TRIAL_EMAIL_KEEP_S). Other one-off offers stay (that is what keeps them
    from being sent twice)."""
    async with db.unscoped() as conn:
        cur = await conn.execute(
            "DELETE FROM sent_emails WHERE"
            " (sent_at < %s AND (kind LIKE 'sign-in:%%' OR kind LIKE 'move-code:%%'"
            "   OR kind LIKE 'recap-%%'))"
            " OR (sent_at < %s AND (kind LIKE 'trial-%%' OR kind LIKE 'dm:trial-%%'))",
            (now - 90 * DAY_S, now - TRIAL_EMAIL_KEEP_S),
        )
        return int(cur.rowcount)
