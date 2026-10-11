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
        """The post failed: let the person try again straight away."""
        turns = self._turns.get(address)
        if turns:
            turns.pop()
            if not turns:
                del self._turns[address]


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
    "billing": Group(10, 10 * 60, ("/billing/checkout", "/billing/portal")),
    "delete": Group(5, 60 * 60, ("/account/delete/request",)),
}
