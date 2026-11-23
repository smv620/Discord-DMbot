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

log = logging.getLogger(__name__)

CALLS_PER_PASS = 20  # AI calls one pass may make across all campaigns
BACKOFF_S = 900  # a campaign with a failed call waits this long
MENTION_BATCH = 20  # characters whose mentions are looked up together

# What `refresh_who` returns when it made an AI call (so it counts against the pass's cap).
_CALLED = frozenset({"written", "unknown", "failed"})


@dataclass(slots=True)
class Pass:
    """What one pass did, for the log: counts only."""

    calls: int = 0
    skipped: int = 0  # campaigns not worked (unchanged, waiting or out of calls)
    worked: int = 0
    failed: int = 0
    results: dict[str, int] = field(default_factory=dict)

    def count(self, word: str) -> None:
        self.results[word] = self.results.get(word, 0) + 1


class CardRunner:
    """Remembers, in memory only, which (version, sessions) of each campaign was finished and when a
    failing campaign may be tried again."""

    def __init__(
        self,
        *,
        calls_per_pass: int = CALLS_PER_PASS,
        backoff_s: int = BACKOFF_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._calls_per_pass = calls_per_pass
        self._backoff_s = backoff_s
        self._clock = clock
        self._done: dict[tuple[int, str], tuple[int, int]] = {}
        self._wait_until: dict[tuple[int, str], float] = {}

    async def work(
        self,
        pass_: Pass,
        gid: int,
        cid: str,
        *,
        version: int,
        sessions: int,
        entity_ids: Sequence[str],
        who: Callable[[str], Awaitable[str]],
        mentions: Callable[[Sequence[str]], Awaitable[dict[str, str]]],
    ) -> None:
        """One campaign: skipped when nothing changed or it is waiting after a failure; else each
        character's sentence (until the pass's calls run out), then the mentions. It is marked
        finished only when every step completed, so what was left over is done next pass."""
        key = (gid, cid)
        if self._done.get(key) == (version, sessions) or self._clock() < self._wait_until.get(
            key, 0.0
        ):
            pass_.skipped += 1
            return
        complete = True
        failed = False
        pass_.worked += 1
        for entity_id in entity_ids:
            if pass_.calls >= self._calls_per_pass:
                complete = False
                break
            word = await who(entity_id)
            pass_.count(word)
            if word in _CALLED:
                pass_.calls += 1
            if word == "failed":
                failed = True
                complete = False
        for start in range(0, len(entity_ids), MENTION_BATCH):
            if pass_.calls >= self._calls_per_pass:
                complete = False
                break
            batch = entity_ids[start : start + MENTION_BATCH]
            found = await mentions(batch)
            for word in found.values():
                pass_.count(word)
                if word == "scene-later":
                    pass_.calls += 1  # (a scene is an AI call, tried again next pass)
                    complete = False
        if failed:
            pass_.failed += 1
            self._wait_until[key] = self._clock() + self._backoff_s
        if complete:
            self._done[key] = (version, sessions)
            self._wait_until.pop(key, None)
