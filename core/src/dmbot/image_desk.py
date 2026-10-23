"""Describing the images posted in a table chat, one campaign's day at a time (#2450).

The bot hands this the attachment links of one message; it returns what `dmbot.images.read` made
of each, or None for one that was not described (past the campaign's limit for the day, not a
picture DMbot can open, or an answer that could not be used). Each image sent to the AI counts
toward the campaign's day, kept in the database (`image_day_use`: a count and nothing else) so the
limit holds across restarts. Nothing else is stored: the images are fetched, read once and
dropped, and the log gets counts only.

Consent is asked again before every download and every AI call (`still_ok`), because a picture
takes seconds and the poster may press Stop in that time (CLAUDE.md, "Consent").
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from dmbot import images
from dmbot.ai import Image
from dmbot.db import Database
from dmbot.images import ImageError, Read, Vision

log = logging.getLogger(__name__)

DAY_S = 86400
Fetcher = Callable[[str], Awaitable[Image]]
StillOk = Callable[[], bool]


class Stopped(Exception):
    """The poster's yes is gone (or the session ended): nothing more of the message is read."""


class DayCounter(Protocol):
    async def take(self, guild_id: int, campaign_id: str, day: int, limit: int) -> bool:
        """Count one more picture for this campaign's day if it has not used `limit`."""
        ...


class ImageUseStore:
    """The day's count in the database. One atomic statement, so two presses at once can't both
    take the last place, and a second copy of the bot shares the count."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def take(self, guild_id: int, campaign_id: str, day: int, limit: int) -> bool:
        if limit <= 0:
            return False
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO image_day_use AS u (guild_id, campaign_id, day, used)"
                " VALUES (%s, %s, %s, 1)"
                " ON CONFLICT (guild_id, campaign_id, day) DO UPDATE SET used = u.used + 1"
                " WHERE u.used < %s RETURNING used",
                (guild_id, campaign_id, day, limit),
            )
            taken = await cur.fetchone() is not None
            # Older days are of no use: cleared as the new one is counted.
            await conn.execute(
                "DELETE FROM image_day_use WHERE guild_id = %s AND campaign_id = %s AND day < %s",
                (guild_id, campaign_id, day),
            )
            return taken

    async def used(self, guild_id: int, campaign_id: str, day: int) -> int:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT used FROM image_day_use WHERE guild_id = %s AND campaign_id = %s"
                " AND day = %s",
                (guild_id, campaign_id, day),
            )
            row = await cur.fetchone()
            return int(row["used"]) if row else 0


class _Guarded:
    """A client that asks `still_ok` before each call it passes on."""

    def __init__(self, inner: Vision, still_ok: StillOk) -> None:
        self._inner, self._still_ok = inner, still_ok

    async def complete(self, system: str, text: str, **kwargs: Any) -> Any:
        if not self._still_ok():
            raise Stopped
        return await self._inner.complete(system, text, **kwargs)


class ImageDesk:
    def __init__(
        self,
        fast: Vision,
        careful: Vision,
        *,
        per_day: int,
        counter: DayCounter | None = None,
        clock: Callable[[], float] = time.time,
        fetcher: Fetcher = images.fetch_image,
    ) -> None:
        self._fast, self._careful = fast, careful
        self._per_day = per_day
        # Set where the database is known (like the other stores); without one nothing is read,
        # since the day's limit could not be kept.
        self.counter = counter
        self._clock = clock
        self._fetch = fetcher

    def day(self) -> int:
        return int(self._clock() // DAY_S)

    async def describe(
        self,
        guild_id: int,
        campaign_id: str,
        links: Sequence[str],
        still_ok: StillOk = lambda: True,
    ) -> list[Read | None]:
        """What was made of each link (at most `MAX_PER_MESSAGE`), in order; None where it was not
        described. `still_ok` is asked before each download and each AI call; once it says no the
        rest of the message is dropped (a shorter list: those pictures get no line at all, since
        the poster no longer agrees). Never raises for one image's trouble."""
        out: list[Read | None] = []
        if self.counter is None:
            return out
        fast, careful = _Guarded(self._fast, still_ok), _Guarded(self._careful, still_ok)
        for link in links[: images.MAX_PER_MESSAGE]:
            if not still_ok():
                break
            # The day's limit first, so past it nothing is downloaded. A link that then fails to
            # open costs a place in the day too: the rule stays simple.
            try:
                allowed = await self.counter.take(guild_id, campaign_id, self.day(), self._per_day)
            except Exception:
                log.exception("Table image: couldn't count the day's pictures")
                break  # the limit can't be kept, so nothing more is read
            if not allowed:
                out.append(None)
                continue
            try:
                if not still_ok():
                    raise Stopped
                image = await self._fetch(link)
                if not still_ok():
                    raise Stopped
                out.append(await images.read(image, fast, careful))
            except Stopped:
                break
            except ImageError:
                out.append(None)
            except Exception:  # one picture's trouble (even no funds) never loses the others' lines
                log.info("Table image: couldn't describe one")
                out.append(None)
        log.info(
            "Table images: %d link(s), %d described", len(out), sum(r is not None for r in out)
        )
        return out
