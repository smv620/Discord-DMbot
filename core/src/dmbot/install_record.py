"""The bot's side of the `installs` table (#2163): a row when DMbot joins a server, `left_at` when
it leaves.

The write contract is in migration 0014 (core/src/dmbot/schema.py): the bot inserts its own row
as a 'link' row with no installer, or clears `left_at` on a rejoin; the website fills in who added
it (`dmbot.accounts`) and its 'site' row is never overwritten here. Nothing wrote these rows
before, so "Link this server" on the website found no install and anything that asks whether the
bot has left a server (the recap voice door) saw `left_at` always empty.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from functools import partial

from dmbot.db import Database

log = logging.getLogger(__name__)


class InstallStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock
        # One server's joins and leaves go one at a time, in the order they get here, so a quick
        # join and kick (or kick and re-add) cannot land out of order (#2166).
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

    async def joined(
        self,
        guild_id: int,
        at: int | None = None,
        *,
        gone: Callable[[], bool] | None = None,
    ) -> None:
        """DMbot is in this server: make the row, or clear `left_at` on a rejoin. An existing
        row keeps its installer, its way of arriving and its first date. `at` is when the bot
        really joined (Discord knows it), else now. `gone` is asked once the server's turn comes:
        if the bot was removed meanwhile (a join and a quick kick), nothing is written."""
        async with self._lock(guild_id):
            if gone is not None and gone():
                return
            async with self._db.guild(guild_id) as conn:
                await conn.execute(
                    "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                    " VALUES (%s, NULL, %s, 'link')"
                    " ON CONFLICT (guild_id) DO UPDATE SET left_at = NULL",
                    (guild_id, int(at if at is not None else self._now())),
                )

    async def left(self, guild_id: int, *, back: Callable[[], bool] | None = None) -> None:
        """DMbot was removed from this server. The row stays (who added it is kept); only
        `left_at` is set, and only the first time. `back` is asked once the server's turn comes:
        if the bot is in the server again (a kick and a quick re-add), nothing is written."""
        async with self._lock(guild_id):
            if back is not None and back():
                return
            async with self._db.guild(guild_id) as conn:
                await conn.execute(
                    "UPDATE installs SET left_at = %s WHERE guild_id = %s AND left_at IS NULL",
                    (int(self._now()), guild_id),
                )

    async def present(
        self,
        servers: Iterable[tuple[int, int | None]],
        gone: Callable[[int], bool] | None = None,
    ) -> int:
        """Every server the bot is in has a row that says so, each with the date the bot really
        joined (servers it joined before this was recorded, and ones it left and came back to
        while it was down). Run at every connect, since a reconnect after a long outage can skip
        join events. One server's failure does not stop the others. Returns how many were
        written; one write per server, in turn, fine at today's size. `gone(guild_id)` is asked at
        each server's turn: a server the bot was removed from meanwhile is not written back."""
        done = 0
        for guild_id, joined_at in servers:
            try:
                await self.joined(
                    guild_id, joined_at, gone=partial(gone, guild_id) if gone else None
                )
            except Exception as exc:  # the type only: a database error can quote a row
                log.warning("Couldn't record an install: %s", type(exc).__name__)
            else:
                done += 1
        return done

    async def left_except(self, guild_ids: Iterable[int]) -> int:
        """At start: every server that still shows the bot as there but is not in `guild_ids` was
        left while the bot was down. One narrow database function does it across servers
        (`dmbot_installs_left_except`, migration 0160); it only sets `left_at`. An empty list marks
        nothing (it would mean the bot could not see its servers). Returns how many rows it
        marked."""
        ids = list(guild_ids)
        if not ids:
            return 0
        async with self._db.unscoped() as conn:
            cur = await conn.execute("SELECT dmbot_installs_left_except(%s) AS n", (ids,))
            row = await cur.fetchone()
        return int(row["n"]) if row else 0
