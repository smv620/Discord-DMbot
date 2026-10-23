"""Wall-clock limits that follow how busy the machine is (#2490).

A fixed limit on a shared CI runner fails at random: the same code took 100 ms one run and 101 ms
the next because other jobs were using the machine. A test that guards against a slow query plan
or an N+1 should still fail when the code is slow, so its limit is the quiet-machine limit times
how much slower this machine is right now, measured with a small fixed piece of work beside the
test. The scale is never below 1 (a fast machine gets no extra room) and never above `MOST`, so a
runaway still fails: that cap is the backstop, ten times what a quiet machine allows."""

from __future__ import annotations

import hashlib
import time

# The fixed work takes about this long on a quiet machine (measured when this was written).
QUIET_S = 0.012
MOST = 10.0  # the most a limit is stretched


def _work() -> float:
    began = time.perf_counter()
    digest = b"dmbot"
    for _ in range(40_000):
        digest = hashlib.md5(digest).digest()
    return time.perf_counter() - began


def slowdown() -> float:
    """How many times slower than a quiet machine this one is right now (1.0 or more), taking the
    best of a few tries so one pause does not count."""
    return min(MOST, max(1.0, min(_work() for _ in range(5)) / QUIET_S))


def allowed(limit: float, *scales: float) -> float:
    """`limit`, stretched by the worst slowdown seen: the ones measured before the test (`scales`)
    and the one measured now, after it."""
    return limit * max(1.0, *scales, slowdown())
