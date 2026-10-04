"""Per-server recording consent, stored in Postgres.

Consent is opt-in and per player per server. Core is the source of truth and pushes
the consenting set to ears, which refuses to capture anyone else. A cache keeps the
audio path's `has_consent` check instant; it is refreshed from the database on every
change and on first use per server.
"""

from __future__ import annotations

import time

from dmbot.db import Database


class ConsentStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: dict[int, frozenset[int]] = {}

    async def grant(self, guild_id: int, user_id: int) -> frozenset[int]:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (%s, %s, %s)"
                " ON CONFLICT (guild_id, user_id) DO UPDATE SET granted_at = EXCLUDED.granted_at",
                (guild_id, user_id, int(time.time())),
            )
        return await self._reload(guild_id)

    async def revoke(self, guild_id: int, user_id: int) -> frozenset[int]:
        # Drop the user from the cache first, so the audio path stops at once.
        self._cache[guild_id] = self._cache.get(guild_id, frozenset()) - {user_id}
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM consent WHERE guild_id = %s AND user_id = %s", (guild_id, user_id)
            )
        return await self._reload(guild_id)

    async def consenting(self, guild_id: int) -> frozenset[int]:
        if guild_id not in self._cache:
            await self._reload(guild_id)
        return self._cache[guild_id]

    def has_consent(self, guild_id: int, user_id: int) -> bool:
        """Fast, synchronous check for the audio path. Unknown servers deny."""
        return user_id in self._cache.get(guild_id, frozenset())

    async def _reload(self, guild_id: int) -> frozenset[int]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute("SELECT user_id FROM consent WHERE guild_id = %s", (guild_id,))
            users = frozenset(int(r["user_id"]) for r in await cur.fetchall())
        self._cache[guild_id] = users
        return users
