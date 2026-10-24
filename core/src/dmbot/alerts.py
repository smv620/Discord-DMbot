"""Telling the owner by email when a service DMbot depends on fails (#1425, item 4; #1907).

The sentences are fixed: a caller names the problem (`Problem`) and never passes text, so a key,
a token, a ticket or a player's words can't reach an alert. Each problem is sent at most once an
hour, and at most `ALERT_DAILY_MAX` a day in all (the slots `mail.py` keeps back from other
mail, so a flood of sign-in links can't starve an alert). With no `OWNER_ALERT_EMAIL` nothing is
sent and the log says so once.

When mail itself is what failed (or the email can't go), the owner is told by a private Discord
message instead, and the log. The address is the owner's own and lives in the server's `.env`
only: it is never logged.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Awaitable, Callable
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # dmbot.mail imports this module, and ai.py/deepgram.py stay light
    from dmbot.mail import MailSettings, Transport

log = logging.getLogger(__name__)

ALERT_EVERY_S = 3600  # one email per problem per hour
ALERT_WINDOW_S = 24 * 3600
# Alerts never send more than this many emails a day from one process. The bot and the website each
# count for themselves, so mail.py keeps back twice this many slots from MAIL_DAILY_CAP.
ALERT_DAILY_MAX = 5
DEEPGRAM_DOWN_AFTER_S = 10 * 60  # "not answering for N minutes"


class Problem(enum.Enum):
    """What can be reported. The value is the fixed sentence the owner reads."""

    AI_OUT_OF_FUNDS = (
        "The AI service says DMbot is out of credit. Top up at https://console.anthropic.com/ "
        "then nothing else is needed."
    )
    AI_KEY_REFUSED = (
        "The AI service didn't accept DMbot's key. Check ANTHROPIC_API_KEY at "
        "https://console.anthropic.com/settings/keys and in the server's .env."
    )
    GEMINI_OUT_OF_FUNDS = (
        "Google's Gemini says DMbot has no credit or has reached its spend cap, so the recap "
        "writer is paused. Check billing at https://aistudio.google.com/ (the key must be on a "
        "paid plan)."
    )
    GEMINI_KEY_REFUSED = (
        "Google's Gemini didn't accept DMbot's key. Check GEMINI_API_KEY at "
        "https://aistudio.google.com/apikey and in the server's .env."
    )
    DEEPGRAM_KEY_REFUSED = (
        "Deepgram didn't accept DMbot's key, so nothing is being transcribed. Check "
        "DEEPGRAM_API_KEY at https://console.deepgram.com/ and in the server's .env."
    )
    DEEPGRAM_DOWN = (
        "Deepgram hasn't answered for 10 minutes, so nothing is being transcribed. Check "
        "https://status.deepgram.com/ and the server's network."
    )
    STRIPE_KEY_REFUSED = (
        "Stripe didn't accept DMbot's key, so payments are not working. Check the Stripe key at "
        "https://dashboard.stripe.com/apikeys and in the server's .env."
    )
    MAIL_FAILED = (
        "DMbot couldn't send an email (the mail service refused it). Check the mail settings in "
        "the server's .env and the mail provider's dashboard."
    )
    DATABASE_DOWN = (
        "DMbot couldn't start its database (it wouldn't connect, or its role or tables were "
        "wrong). Check that Postgres is running and that DATABASE_URL in .env is right."
    )


# Mail is what failed, so these never go by email.
BY_DISCORD_ONLY = frozenset({Problem.MAIL_FAILED})

SendEmail = Callable[[EmailMessage], Awaitable[None]]
TellOwner = Callable[[str], Awaitable[None]]


class Alerter:
    """`transport` is the real SMTP send (faked in tests); `tell_owner` is a private Discord
    message to the application's owner (None where there is no Discord, such as the website)."""

    def __init__(
        self,
        settings: MailSettings,
        *,
        transport: Transport | None = None,
        tell_owner: TellOwner | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._s = settings
        self._transport = transport
        self._tell_owner = tell_owner
        self._clock = clock
        self._last: dict[Problem, float] = {}
        self._sent_at: list[float] = []
        self._said_off = False

    def set_tell_owner(self, tell_owner: TellOwner | None) -> None:
        """The bot gives this once it is logged in to Discord (the database alert at start-up
        comes before that and goes by email only)."""
        self._tell_owner = tell_owner

    @property
    def on(self) -> bool:
        from dmbot.mail import one_address  # here, not at the top: see TYPE_CHECKING above

        return bool(self._s.configured and one_address(self._s.owner_alert_email))

    def say_if_off(self) -> None:
        """One line at start-up, so a missing setting is not a silent one."""
        if not self.on and not self._said_off:
            self._said_off = True
            log.info("Owner alerts are off (OWNER_ALERT_EMAIL is empty or mail isn't set up)")

    async def alert(self, problem: Problem) -> bool:
        """Tell the owner about `problem` unless it was told in the last hour. Never raises;
        True means a message went out (email or Discord)."""
        now = self._clock()
        last = self._last.get(problem)
        if last is not None and now - last < ALERT_EVERY_S:
            return False
        self._last[problem] = now
        log.error("Owner alert: %s", problem.name)  # the name only: a fixed word, no values
        if problem not in BY_DISCORD_ONLY and self.on and self._room(now):
            self._sent_at.append(now)
            if await self._email(problem):
                return True
            # The email didn't go: that is a mail failure, told the other way, and only as
            # often as any other problem (so seven failing emails are one message an hour).
            self._sent_at.pop()  # a send that failed doesn't use up a slot
            last_failed = self._last.get(Problem.MAIL_FAILED)
            if last_failed is not None and now - last_failed < ALERT_EVERY_S:
                return False
            self._last[Problem.MAIL_FAILED] = now
            return await self._discord(Problem.MAIL_FAILED.value)
        return await self._discord(problem.value)

    def _room(self, now: float) -> bool:
        self._sent_at = [t for t in self._sent_at if now - t < ALERT_WINDOW_S]
        return len(self._sent_at) < ALERT_DAILY_MAX

    async def _email(self, problem: Problem) -> bool:
        from dmbot.mail import SmtpTransport, one_address

        s = self._s
        message = EmailMessage()
        message["From"] = s.alerts_sender
        message["To"] = s.owner_alert_email
        if s.alerts_reply_to and one_address(s.alerts_reply_to):
            message["Reply-To"] = s.alerts_reply_to
        message["Subject"] = "DMbot needs attention"
        message["Date"] = formatdate(self._clock(), localtime=False)
        message["Message-ID"] = make_msgid(domain=parseaddr(s.alerts_sender)[1].rpartition("@")[2])
        message.set_content(problem.value)
        try:
            await (self._transport or SmtpTransport(s))(message)
        except Exception:  # an SMTP error can carry the address: say only that it failed
            log.error("Couldn't send an owner alert email", exc_info=False)
            return False
        return True

    async def _discord(self, text: str) -> bool:
        if self._tell_owner is None:
            return False
        try:
            await self._tell_owner(f"⚠️ {text}")
        except Exception:  # private messages may be off; the log above has the problem's name
            log.warning("Couldn't send the owner alert on Discord")
            return False
        return True


# --- The one alerter a process uses, so the places that catch errors need no wiring. ---

_current: Alerter | None = None
_tasks: set[asyncio.Task[bool]] = set()


def install(alerter: Alerter | None) -> None:
    global _current
    _current = alerter


def current() -> Alerter | None:
    return _current


def raise_alert(problem: Problem) -> None:
    """Fire and forget, from where the error is already caught. Without an alerter (a test, a
    tool) or a running loop it only logs."""
    if _current is None:
        log.error("Owner alert (no alerter in this process): %s", problem.name)
        return
    try:
        task = asyncio.get_running_loop().create_task(_current.alert(problem))
    except RuntimeError:
        log.error("Owner alert (no running loop): %s", problem.name)
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


class Outage:
    """A service that fails in a row: `failed()` returns True once the failures have gone on
    for `after_s`, `worked()` starts the count again. Used for "Deepgram hasn't answered"."""

    def __init__(self, after_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._after = after_s
        self._clock = clock
        self._since: float | None = None
        self._last: float | None = None

    def worked(self) -> None:
        self._since = None

    def failed(self) -> bool:
        now = self._clock()
        # A failure long after the last one (nobody was playing) starts a new count: the service
        # was not known to be down all that time.
        if self._since is None or (self._last is not None and now - self._last > self._after):
            self._since = now
        self._last = now
        return now - self._since >= self._after
