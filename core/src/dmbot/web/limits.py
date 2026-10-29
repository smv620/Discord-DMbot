"""Per-address limits for the web API's own routes (#1082, #498 item 1).

The proxy (Cloudflare or Caddy) is the first line; this is the second, so a flood can't use
up DMbot's Discord sign-in quota and lock real people out even if the proxy is set up wrong.
Everything is in memory: the API runs as one process, a restart forgetting the last few
minutes does no harm, and addresses are never stored in the database or logged.

One `RateLimit` is a sliding count: at most `count` turns per address in any `seconds`. The
"Say hello" form uses it with a count of one (one post per 10 minutes). The numbers for each
group of routes are in `LIMITS`, in one place, so dev1 can tune them.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

WAIT_SECONDS = 10 * 60
# More addresses than this held at once is a flood, not people: new ones are told "busy".
# Each limiter holds at most this many, so the API holds a bounded number in all.
MAX_WAITING = 20_000


class RateLimit:
    """At most `count` turns per address in any `seconds`, kept in memory."""

    def __init__(
        self,
        seconds: int = WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        most: int = MAX_WAITING,
        count: int = 1,
    ):
        if count < 1:
            raise ValueError("count must be at least 1")
        self._seconds = seconds
        self._clock = clock
        self._most = most
        self._count = count
        # Oldest address first (by its latest turn): moved to the end on every turn.
        self._turns: dict[str, deque[float]] = {}

    def __len__(self) -> int:
        return len(self._turns)

    @property
    def seconds(self) -> int:
        return self._seconds

    def take(self, address: str) -> Literal["ok", "wait", "busy"]:
        """Claim a turn: "wait" if this address has used its turns in the window, "busy" if
        too many addresses are held already (a flood)."""
        now = self._clock()
        # Forget addresses whose last turn is out of the window, from the oldest, so each
        # call is cheap.
        while self._turns:
            oldest = next(iter(self._turns))
            if now - self._turns[oldest][-1] < self._seconds:
                break
            del self._turns[oldest]
        turns = self._turns.get(address)
        if turns is None:
            if len(self._turns) >= self._most:
                return "busy"
            turns = self._turns[address] = deque()
        while turns and now - turns[0] >= self._seconds:
            turns.popleft()  # the window slides
        if len(turns) >= self._count:
            return "wait"
        turns.append(now)
        self._turns[address] = self._turns.pop(address)  # most recent last
        return "ok"

    def retry_after(self, address: str) -> int:
        """Whole seconds until this address gets a turn again (at least 1)."""
        turns = self._turns.get(address)
        if not turns or len(turns) < self._count:
            return 1
        return max(1, int(turns[0] + self._seconds - self._clock()) + 1)

    def give_back(self, address: str) -> None:
        """The post failed: let the person try again straight away. (The address stays where
        its last turn put it, so it may be held a little past its window: bounded, harmless.)"""
        turns = self._turns.get(address)
        if turns:
            turns.pop()
            if not turns:
                del self._turns[address]


# Free accounts (Try It, lapsed) may write to the team (#1435), but a free account costs nothing
# to make, so it gets a tighter guard than a paid one: this many new tickets in 24 hours and
# one waiting for an answer at a time. Counted from the database (support.recent), not memory,
# so a restart doesn't forget them. A paid plan keeps only the 10-minute limit.
FREE_TICKETS_PER_DAY = 3


def free_ticket_refusal(waiting: int, today: int) -> Literal["one_waiting", "daily"] | None:
    """Why a free account may not send another ticket now, or None. `waiting` is its tickets
    the team has not answered yet; `today` is those it sent in the last 24 hours."""
    if waiting >= 1:
        return "one_waiting"
    if today >= FREE_TICKETS_PER_DAY:
        return "daily"
    return None


@dataclass(frozen=True)
class Group:
    """One limit shared by several routes."""

    count: int
    seconds: int
    paths: tuple[str, ...]
    # Where a refused browser visit goes back to on the site: the `?name=slow` it shows.
    redirect_param: str | None = None


# Starting numbers, per address. A person signing in or adding DMbot needs a handful of
# visits (start, callback, and a retry or two), so 20 per 10 minutes is far above real use
# and far below a flood. Checkout and the billing page are one or two clicks, so 10. A
# delete request is rare and the step before a destructive action, so 5 per hour. The
# payment webhook is deliberately absent: the provider sends from a few addresses and
# retries, and its signature check is the gate (its body is capped in app.py).
LIMITS: dict[str, Group] = {
    "sign-in": Group(
        20,
        10 * 60,
        ("/auth/discord/start", "/auth/discord/callback"),
        redirect_param="signin",
    ),
    "install": Group(20, 10 * 60, ("/install", "/install/callback"), redirect_param="install"),
    "billing": Group(
        10,
        10 * 60,
        (
            "/billing/checkout",
            "/billing/portal",
            "/billing/renew",
            "/billing/extra-hours",
            "/billing/recap-audio",
        ),
    ),
    "delete": Group(5, 60 * 60, ("/account/delete/request",)),
    # Ending every sign-in is safe (signing in again is one click) but is still a write per
    # call, so a few a minute is far above real use (#1163). Like the other groups it counts
    # a call before the sign-in is checked, so a shared address shares the five.
    "sign-out-everywhere": Group(5, 60, ("/me/sign-out-everywhere",)),
    # Sign-in by emailed link (#1231). Asking for a link sends an email, so 10 per 10 minutes per
    # connection; the same refusal for everyone, whether or not the email has an account. The
    # per-email limit is separate (create_app). Using a link and opening a personal page are
    # cheap reads of a 256-bit secret or a signed code, so they are loose.
    "email-sign-in": Group(10, 10 * 60, ("/auth/email/request",)),
    "email-verify": Group(20, 10 * 60, ("/auth/email/verify",)),
    "personal": Group(30, 10 * 60, ("/personal",)),
    # A move code is an email to the payment address and starts a move: rare.
    "move-code": Group(5, 60 * 60, ("/me/move-code",)),
    # The unsubscribe link in an offer email (#1229): a person clicks it once; a token is
    # checked before anything is written.
    "unsubscribe": Group(20, 60, ("/email/unsubscribe",)),
}
