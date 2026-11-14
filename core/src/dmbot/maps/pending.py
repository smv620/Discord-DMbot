"""A map picture waiting for its owner's yes (#4372): held in memory, never on disk.

A map sent to DMbot in a private chat, or with `/dmbot map`, has no transcript line to keep, so
nothing may be stored before the sender says they may use and save it (design rule 3). The bytes
wait here until the answer. A yes stores them; a no, a time-out (`TTL_S`) or a restart drops them,
so a picture nobody said yes to is never written anywhere. Bounded as Supervisor ruled on #4372:
one pending picture per person (a new one replaces the old) and a total cap across the bot, beyond
which the sender is told DMbot is busy. Counts-only logs: never a name or the picture's words.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

TTL_S = 15 * 60
TOTAL_CAP_BYTES = 100 * 1024 * 1024


class Busy(Exception):
    """The held pictures already fill the cap: try again in a few minutes."""


@dataclass(frozen=True, slots=True)
class Held:
    code: str
    user_id: int
    data: bytes
    title: str
    expires_at: float


class PendingMaps:
    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        *,
        cap: int = TOTAL_CAP_BYTES,
        ttl: float = TTL_S,
    ) -> None:
        self._clock = clock
        self._cap = cap
        self._ttl = ttl
        self._held: dict[int, Held] = {}

    def _sweep(self) -> None:
        now = self._clock()
        for user_id in [u for u, h in self._held.items() if h.expires_at <= now]:
            del self._held[user_id]

    def total(self) -> int:
        self._sweep()
        return sum(len(h.data) for h in self._held.values())

    def put(self, user_id: int, data: bytes, title: str = "") -> Held:
        """Hold this person's picture, replacing the one they had. `Busy` when it would not fit."""
        self._sweep()
        others = sum(len(h.data) for u, h in self._held.items() if u != user_id)
        if others + len(data) > self._cap:
            raise Busy
        held = Held(secrets.token_hex(6), user_id, data, title, self._clock() + self._ttl)
        self._held[user_id] = held
        return held

    def get(self, user_id: int, code: str) -> Held | None:
        """The person's own held picture, only with its code (a forwarded card finds nothing)."""
        self._sweep()
        held = self._held.get(user_id)
        return held if held is not None and held.code == code else None

    def drop(self, user_id: int, code: str) -> bool:
        held = self.get(user_id, code)
        if held is None:
            return False
        del self._held[user_id]
        return True
