"""Per-server recording consent, stored in Postgres.

Consent is opt-in and per player per server. Core is the source of truth and pushes
the consenting set to ears, which refuses to capture anyone else. A cache keeps the
audio path's `has_consent` check instant.

Revoking must never be undone by a race or a database failure:
- `stop_now` takes the player out of the cache at once, before any database work.
- Each change and the reload that follows it run in one transaction, under a lock per
  server, so an older reload can't overwrite a newer one.
- A revoked player is held back from every reload until the revoke is saved (or they
  give consent again), so a failed save can't quietly bring them back.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

from dmbot.db import Conn, Database


class ConsentStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: dict[int, frozenset[int]] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        # Revoked here but not (yet) saved: never trust the database for these.
        self._held_back: dict[int, set[int]] = {}

    async def grant(self, guild_id: int, user_id: int) -> frozenset[int]:
        async with self._lock(guild_id):
            async with self._db.guild(guild_id) as conn:
                await conn.execute(
                    "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (%s, %s, %s)"
                    " ON CONFLICT (guild_id, user_id)"
                    " DO UPDATE SET granted_at = EXCLUDED.granted_at",
                    (guild_id, user_id, int(time.time())),
                )
                users = await _select(conn, guild_id)
            self._held_back.get(guild_id, set()).discard(user_id)
            return self._store(guild_id, users)

    def stop_now(self, guild_id: int, user_id: int) -> None:
        """Stop trusting this player's consent immediately, without the database."""
        self._held_back.setdefault(guild_id, set()).add(user_id)
        if guild_id in self._cache:
            self._cache[guild_id] = self._cache[guild_id] - {user_id}

    async def revoke(self, guild_id: int, user_id: int) -> frozenset[int]:
        """Revoke and save. If saving fails, the player stays stopped in this process."""
        self.stop_now(guild_id, user_id)
        async with self._lock(guild_id):
            async with self._db.guild(guild_id) as conn:
                await conn.execute(
                    "DELETE FROM consent WHERE guild_id = %s AND user_id = %s",
                    (guild_id, user_id),
                )
                users = await _select(conn, guild_id)
            self._held_back[guild_id].discard(user_id)
            return self._store(guild_id, users)

    async def consenting(self, guild_id: int) -> frozenset[int]:
        if guild_id not in self._cache:
            async with self._lock(guild_id):
                if guild_id not in self._cache:
                    async with self._db.guild(guild_id) as conn:
                        users = await _select(conn, guild_id)
                    self._store(guild_id, users)
        return self._cache[guild_id]

    async def granted_times(self, guild_id: int, user_ids: Iterable[int]) -> dict[int, int]:
        """When each of these players consented here (Unix seconds); absent if they haven't.

        One query for a whole table. Players whose revoke isn't saved yet are absent.
        """
        ids = [u for u in set(user_ids) if u not in self._held_back.get(guild_id, set())]
        if not ids:
            return {}
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT user_id, granted_at FROM consent WHERE guild_id = %s AND user_id = ANY(%s)",
                (guild_id, ids),
            )
            rows = await cur.fetchall()
        return {int(r["user_id"]): int(r["granted_at"]) for r in rows}

    async def granted_at(self, guild_id: int, user_id: int) -> int | None:
        """When this player consented here (Unix seconds), or None if they haven't."""
        return (await self.granted_times(guild_id, [user_id])).get(user_id)

    def has_consent(self, guild_id: int, user_id: int) -> bool:
        """Fast, synchronous check for the audio path. Unknown servers deny."""
        return user_id in self._cache.get(guild_id, frozenset())

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

    def _store(self, guild_id: int, users: frozenset[int]) -> frozenset[int]:
        users = users - self._held_back.get(guild_id, set())
        self._cache[guild_id] = users
        return users


async def _select(conn: Conn, guild_id: int) -> frozenset[int]:
    cur = await conn.execute("SELECT user_id FROM consent WHERE guild_id = %s", (guild_id,))
    return frozenset(int(r["user_id"]) for r in await cur.fetchall())
