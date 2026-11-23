"""The background round that keeps every character's card sentence and first/last mention fresh
(#5050 slice b3b). It decides only *whether* to work a campaign now and how much: the work itself
is `card_job.refresh_who` and `card_mentions.refresh_mentions`.

Why it is careful: it makes AI calls nobody asked for in that moment, so a round has a cap on
calls, a campaign whose story and sessions have not changed since the last complete round is
skipped without a query, and a campaign whose calls failed waits before the next try (in memory:
a restart simply tries again). Counts only in the log, never a name or a word of the story.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

from dmbot.memory.card_mentions import Budget

log = logging.getLogger(__name__)
T = TypeVar("T")

CALLS_PER_PASS = 20  # AI calls one pass may make across all campaigns, scenes included
BACKOFF_S = 900  # a campaign with a failed call waits this long
MENTION_BATCH = 20  # characters whose mentions are looked up together

# What `refresh_who` returns when it made an AI call (so it counts against the pass's cap).
_CALLED = frozenset({"written", "unknown", "failed"})


@dataclass(slots=True)
class Pass:
    """What one pass did, for the log: counts only. `budget` is the pass's AI calls, spent by the
    sentences and by the scenes alike."""

    budget: Budget = field(default_factory=lambda: Budget(CALLS_PER_PASS))
    skipped: int = 0  # campaigns not worked (unchanged, waiting or out of calls)
    worked: int = 0
    failed: int = 0
    results: dict[str, int] = field(default_factory=dict)

    @property
    def calls(self) -> int:
        return self.budget.used

    def count(self, word: str) -> None:
        self.results[word] = self.results.get(word, 0) + 1


class CardRunner:
    """Remembers, in memory only, which (version, sessions, who agreed) of each campaign was
    finished and when a failing campaign may be tried again."""

    def __init__(
        self,
        *,
        backoff_s: int = BACKOFF_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backoff_s = backoff_s
        self._clock = clock
        self._done: dict[tuple[int, str], tuple[int, int, int]] = {}
        self._wait_until: dict[tuple[int, str], float] = {}
        self.turn = 0  # (campaigns are started from a different one each pass: see `rotated`)

    def fresh(self, gid: int, cid: str, version: int, sessions: int, agreed: int) -> bool:
        """Is there nothing to do for this campaign now: it was finished at this version, with these
        sessions and these people agreeing (a person who stops changes what a card may say), or it
        is waiting after a failure. Asked before anything else is read."""
        key = (gid, cid)
        return self._done.get(key) == (version, sessions, agreed) or self._clock() < (
            self._wait_until.get(key, 0.0)
        )

    def rotated(self, items: Sequence[T]) -> list[T]:
        """The items starting from a different one each pass, so a campaign that always uses up the
        calls cannot keep the ones after it from ever being worked."""
        self.turn += 1
        if not items:
            return []
        start = self.turn % len(items)
        return [*items[start:], *items[:start]]

    async def work(
        self,
        pass_: Pass,
        gid: int,
        cid: str,
        *,
        version: int,
        sessions: int,
        agreed: int,
        entity_ids: Sequence[str],
        who: Callable[[str], Awaitable[str]],
        mentions: Callable[[Sequence[str], Budget], Awaitable[dict[str, str]]],
    ) -> None:
        """One campaign: each character's sentence (until the pass's calls run out), then the
        mentions. It is marked finished only when every step completed, so what was left over is
        done next pass. A step that failed, or whose scene could not be made, makes the campaign
        wait before its next try; an exception does the same and goes no further."""
        key = (gid, cid)
        if self.fresh(gid, cid, version, sessions, agreed):
            pass_.skipped += 1
            return
        budget = pass_.budget
        complete = True
        trouble = False
        pass_.worked += 1
        try:
            for entity_id in entity_ids:
                if budget.left <= 0:
                    complete = False
                    break
                word = await who(entity_id)
                pass_.count(word)
                if word in _CALLED:
                    budget.take()
                if word == "failed":
                    trouble = True
                    complete = False
            for start in range(0, len(entity_ids), MENTION_BATCH):
                if budget.left <= 0:
                    complete = False
                    break
                found = await mentions(entity_ids[start : start + MENTION_BATCH], budget)
                for word in found.values():
                    pass_.count(word)
                    if word == "scene-later":
                        trouble = True
                        complete = False
        except Exception as exc:  # the type only: an error can quote a row
            log.warning("Card round: a campaign failed (%s)", type(exc).__name__)
            trouble = True
            complete = False
        if trouble:
            pass_.failed += 1
            self._wait_until[key] = self._clock() + self._backoff_s
        if complete:
            self._done[key] = (version, sessions, agreed)
            self._wait_until.pop(key, None)
