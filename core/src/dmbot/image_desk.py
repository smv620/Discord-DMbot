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
from contextlib import AbstractContextManager
from typing import Any, Protocol

from dmbot import images
from dmbot.ai import AIOutOfFunds, Image
from dmbot.db import Database
from dmbot.images import ImageError, Read, Vision

log = logging.getLogger(__name__)

DAY_S = 86400
Fetcher = Callable[[str], Awaitable[Image]]
StillOk = Callable[[], bool]
# Called with a picture's place in the message, the picture and what was made of it, right after it
# was described, so it can be kept (#3274).
OnImage = Callable[[int, Image, Read | None], Awaitable[None]]


class DayLimit(Exception):
    """The day's picture limit is used (or can't be counted): a later sweep reads it."""


class Stopped(Exception):
    """The poster's yes is gone (or the session ended): nothing more of the message is read."""


class DayCounter(Protocol):
    async def take(self, guild_id: int, campaign_id: str, day: int, limit: int) -> bool:
        """Count one more picture for this campaign's day if it has not used `limit`."""
        ...

    async def give_back(self, guild_id: int, campaign_id: str, day: int) -> None: ...


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

    async def give_back(self, guild_id: int, campaign_id: str, day: int) -> None:
        """A place taken for a read that was put off (no AI credits, #4349): the table's own
        pictures that day keep it."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE image_day_use SET used = GREATEST(used - 1, 0)"
                " WHERE guild_id = %s AND campaign_id = %s AND day = %s",
                (guild_id, campaign_id, day),
            )

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

    async def read_closely(
        self,
        guild_id: int,
        campaign_id: str,
        image: Image,
        meter: Callable[[], AbstractContextManager[Any]],
        still_ok: StillOk = lambda: True,
    ) -> Read | None:
        """A kept map's close read (#4349, MapBot 6b-2): only once DMbot may use it, which the
        caller asked `maps.store.may_use_map`. One metered call: a kept map is a map, so there is
        no first look. Counts against the day's pictures like any read; raises DayLimit past it.
        `still_ok` is asked right before the call (Stopped when it says no). Out of AI credits
        gives the day's place back and raises AIOutOfFunds for the caller to put the read off."""
        counter = self.counter
        if counter is None:
            raise DayLimit
        day = self.day()
        if not await counter.take(guild_id, campaign_id, day, self._per_day):
            raise DayLimit
        try:
            if not still_ok():
                raise Stopped
            careful = _Guarded(self._careful, still_ok)
            return await images.read(
                image, self._fast, careful, close=True, meter=meter, known_map=True
            )
        except (AIOutOfFunds, Stopped):
            await counter.give_back(guild_id, campaign_id, day)
            raise

    async def describe(
        self,
        guild_id: int,
        campaign_id: str,
        links: Sequence[str],
        still_ok: StillOk = lambda: True,
        on_image: OnImage | None = None,
        *,
        meter: Callable[[], AbstractContextManager[Any]] | None = None,
    ) -> list[Read | None]:
        """What was made of each link (at most `MAX_PER_MESSAGE`), in order; None where it was not
        described. `still_ok` is asked before each download and each AI call; once it says no the
        rest of the message is dropped (a shorter list: those pictures get no line at all, since
        the poster no longer agrees). Never raises for one image's trouble. `meter`: a map read's
        AI credits (`images.read`). A map's close read is not made here: it waits for both yeses
        on the kept map (`read_closely`, #4349)."""
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
                read = await images.read(image, fast, careful, meter=meter)
                out.append(read)
                if on_image is not None and still_ok():
                    # Keeping a picture (#3274) must never cost the picture its line: its own
                    # trouble is logged by class and the next picture goes on.
                    try:
                        await on_image(len(out) - 1, image, read)
                    except Exception as exc:
                        log.info("Table image: couldn't keep one (%s)", type(exc).__name__)
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
