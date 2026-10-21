"""The bot's side of the `installs` table (#2163): a row when DMbot joins a server, `left_at` when
it leaves.

The write contract is in migration 0014 (core/src/dmbot/schema.py): the bot inserts its own row
as a 'link' row with no installer, or clears `left_at` on a rejoin; the website fills in who added
it (`dmbot.web.accounts`) and its 'site' row is never overwritten here. Nothing wrote these rows
before, so "Link this server" on the website found no install and anything that asks whether the
bot has left a server (the recap voice door) saw `left_at` always empty.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from dmbot.db import Database

log = logging.getLogger(__name__)


class InstallStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def joined(self, guild_id: int) -> None:
        """DMbot is in this server: make the row, or clear `left_at` on a rejoin. An existing
        row keeps its installer, its way of arriving and its first date."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, NULL, %s, 'link')"
                " ON CONFLICT (guild_id) DO UPDATE SET left_at = NULL",
                (guild_id, int(self._now())),
            )

    async def left(self, guild_id: int) -> None:
        """DMbot was removed from this server. The row stays (who added it is kept); only
        `left_at` is set, and only the first time."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE installs SET left_at = %s WHERE guild_id = %s AND left_at IS NULL",
                (int(self._now()), guild_id),
            )

    async def present(self, guild_ids: Iterable[int]) -> int:
        """At start: every server the bot is in has a row that says so (servers it joined before
        this was recorded, and ones it left and came back to while it was down). One server's
        failure does not stop the others. Returns how many were written. Known limit (#2163):
        a server the bot left while it was down keeps an empty `left_at`, since rows of servers
        the bot is not in cannot be read here; one write per server, in turn, fine at today's
        size."""
        done = 0
        for guild_id in guild_ids:
            try:
                await self.joined(guild_id)
            except Exception as exc:  # the type only: a database error can quote a row
                log.warning("Couldn't record an install: %s", type(exc).__name__)
            else:
                done += 1
        return done
