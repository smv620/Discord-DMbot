"""Describing the images posted in a table chat, one campaign's day at a time (#2450).

The bot hands this the attachment links of one message; it returns what `dmbot.images.read` made
of each, or None for one that was not described (past the campaign's limit for the day, not a
picture DMbot can open, or an answer that could not be used). Each image sent to the AI counts
toward the campaign's day, kept in memory (a restart starts the day's count again, which can only
let a few more through). Nothing is stored: the images are fetched, read once and dropped, and the
log gets counts only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence

from dmbot import images
from dmbot.ai import Image
from dmbot.images import ImageError, Read, Vision

log = logging.getLogger(__name__)

DAY_S = 86400
Fetcher = Callable[[str], Awaitable[Image]]


class ImageDesk:
    def __init__(
        self,
        fast: Vision,
        careful: Vision,
        *,
        per_day: int,
        clock: Callable[[], float] = time.time,
        fetcher: Fetcher = images.fetch_image,
    ) -> None:
        self._fast, self._careful = fast, careful
        self._per_day = per_day
        self._clock = clock
        self._fetch = fetcher
        self._used: dict[tuple[int, str], tuple[int, int]] = {}  # campaign → (day, images)

    def _take(self, guild_id: int, campaign_id: str) -> bool:
        """One more image for this campaign today, if it has not used its day's limit."""
        day = int(self._clock() // DAY_S)
        seen_day, count = self._used.get((guild_id, campaign_id), (day, 0))
        if seen_day != day:
            count = 0
        if count >= self._per_day:
            self._used[(guild_id, campaign_id)] = (day, count)
            return False
        self._used[(guild_id, campaign_id)] = (day, count + 1)
        return True

    def used_today(self, guild_id: int, campaign_id: str) -> int:
        day = int(self._clock() // DAY_S)
        seen_day, count = self._used.get((guild_id, campaign_id), (day, 0))
        return count if seen_day == day else 0

    async def describe(
        self, guild_id: int, campaign_id: str, links: Sequence[str]
    ) -> list[Read | None]:
        """What was made of each link (at most `MAX_PER_MESSAGE`), in order; None where it was not
        described. Never raises for one image's trouble: the others still get their turn."""
        out: list[Read | None] = []
        for link in links[: images.MAX_PER_MESSAGE]:
            # The day's limit first, so past it nothing is downloaded. A link that then fails to
            # open costs a place in the day too: the rule stays simple.
            if not self._take(guild_id, campaign_id):
                out.append(None)
                continue
            try:
                out.append(await images.read(await self._fetch(link), self._fast, self._careful))
            except ImageError:
                out.append(None)
            except Exception:  # one picture's trouble (even no funds) never loses the others' lines
                log.info("Table image: couldn't describe one")
                out.append(None)
        log.info(
            "Table images: %d link(s), %d described", len(out), sum(r is not None for r in out)
        )
        return out
