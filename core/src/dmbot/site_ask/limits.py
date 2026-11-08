"""The limits and the cache for the site's short answer (#3856; owner, 2026-11-08).

Everything is in memory, like the other public routes' limits (`web/limits.py`): the API runs as
one process, a restart forgets the counts (which only ever lets a few more questions through), and
nothing about a visitor is stored anywhere that outlives the day.

Who is a visitor: a keyed hash of the client address. The key is made from the server's secret and
the UTC date, so it changes every day and yesterday's hashes match nobody today. The address is
never stored or logged; only the hash is a key in the counts, and the counts go when the day ends.

The limits: a visitor 100 questions an hour and 200 a day; the whole site 300 an hour, and a daily
ceiling on answers the service writes (1,500, so the free allowance is not passed). A cached answer
is free for the site (it does not count against the 300 or the ceiling) but still counts against the
visitor's own limits. Over a limit the route says which one ("hour" or "day").
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

VISITOR_HOUR = 100
VISITOR_DAY = 200
SITE_HOUR = 300
HOUR = 3600
DAY = 86400
# A flood of made-up addresses must not fill the memory: past this many visitors in a day, a new
# one is turned away as "day" (they have none of today's turns left to take).
MOST_VISITORS = 50_000
CACHE_MOST = 2000  # answers kept for a day; the oldest goes first
CACHE_SECONDS = DAY

Limit = Literal["hour", "day"]


def visitor_key(secret: bytes, address: str, now: float) -> str:
    """A day's hash of the address; the same address gives another hash tomorrow."""
    day = int(now // DAY)
    key = hmac.new(secret, f"site-ask:{day}".encode(), hashlib.sha256).digest()
    return hmac.new(key, address.encode("utf-8"), hashlib.sha256).hexdigest()


def normal(question: str) -> str:
    """A question as the cache sees it: words only, lower case, one space between them."""
    return " ".join("".join(c if c.isalnum() else " " for c in question.lower()).split())


@dataclass(slots=True)
class _Count:
    hour: deque[float]
    day: int  # questions in this UTC day


class Limits:
    def __init__(
        self,
        *,
        daily_answers: int = 1500,
        clock: Callable[[], float] = time.time,
        visitor_hour: int = VISITOR_HOUR,
        visitor_day: int = VISITOR_DAY,
        site_hour: int = SITE_HOUR,
    ) -> None:
        self._clock = clock
        self._daily_answers = daily_answers
        self._visitor_hour = visitor_hour
        self._visitor_day = visitor_day
        self._site_hour = site_hour
        self._day = int(clock() // DAY)
        self._visitors: dict[str, _Count] = {}
        self._site: deque[float] = deque()
        self._answers_today = 0
        self._cache: OrderedDict[str, tuple[float, str, str | None]] = OrderedDict()

    def _roll(self, now: float) -> None:
        today = int(now // DAY)
        if today != self._day:
            self._day = today
            self._visitors.clear()
            self._answers_today = 0
        while self._site and self._site[0] <= now - HOUR:
            self._site.popleft()

    def take_visitor(self, visitor: str) -> Limit | None:
        """Count one question against the visitor's own limits; the limit hit, or None."""
        now = self._clock()
        self._roll(now)
        count = self._visitors.get(visitor)
        if count is None:
            if len(self._visitors) >= MOST_VISITORS:
                return "day"
            count = self._visitors[visitor] = _Count(deque(), 0)
        while count.hour and count.hour[0] <= now - HOUR:
            count.hour.popleft()
        if count.day >= self._visitor_day:
            return "day"
        if len(count.hour) >= self._visitor_hour:
            return "hour"
        count.hour.append(now)
        count.day += 1
        return None

    def cached(self, question: str) -> tuple[str, str | None] | None:
        """A day-old-or-newer answer to the same question, as (answer, link)."""
        now = self._clock()
        found = self._cache.get(normal(question))
        if found is None:
            return None
        made, answer, link = found
        if made <= now - CACHE_SECONDS:
            del self._cache[normal(question)]
            return None
        return answer, link

    def take_site(self) -> Limit | None:
        """Count one answer the service must write (not a cached one) against the site's hour and
        the day's ceiling."""
        now = self._clock()
        self._roll(now)
        if self._answers_today >= self._daily_answers:
            return "day"
        if len(self._site) >= self._site_hour:
            return "hour"
        self._site.append(now)
        self._answers_today += 1
        return None

    def keep(self, question: str, answer: str, link: str | None) -> None:
        key = normal(question)
        self._cache[key] = (self._clock(), answer, link)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MOST:
            self._cache.popitem(last=False)

    def give_back_site(self) -> None:
        """A write that failed did not use the allowance: hand its turn back."""
        if self._site:
            self._site.pop()
        self._answers_today = max(0, self._answers_today - 1)

    @property
    def answers_today(self) -> int:
        return self._answers_today
