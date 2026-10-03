"""Per-server recording consent, persisted in SQLite.

Consent is opt-in and per player per server. Core is the source of truth and pushes
the consenting set to ears, which refuses to capture anyone else.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consent (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    granted_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
)
"""


class ConsentStore:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._db.execute(_SCHEMA)
        self._db.commit()
        self._lock = asyncio.Lock()
        self._cache: dict[int, frozenset[int]] = {}

    def close(self) -> None:
        self._db.close()

    async def grant(self, guild_id: int, user_id: int) -> frozenset[int]:
        async with self._lock:
            await asyncio.to_thread(self._grant, guild_id, user_id)
            return await self._reload(guild_id)

    async def revoke(self, guild_id: int, user_id: int) -> frozenset[int]:
        async with self._lock:
            await asyncio.to_thread(self._revoke, guild_id, user_id)
            return await self._reload(guild_id)

    async def consenting(self, guild_id: int) -> frozenset[int]:
        if guild_id not in self._cache:
            async with self._lock:
                await self._reload(guild_id)
        return self._cache[guild_id]

    def has_consent(self, guild_id: int, user_id: int) -> bool:
        """Fast, synchronous check for the audio path. Unknown guilds deny."""
        return user_id in self._cache.get(guild_id, frozenset())

    async def _reload(self, guild_id: int) -> frozenset[int]:
        users = await asyncio.to_thread(self._select, guild_id)
        self._cache[guild_id] = users
        return users

    def _grant(self, guild_id: int, user_id: int) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO consent (guild_id, user_id, granted_at) VALUES (?, ?, ?)",
            (guild_id, user_id, int(time.time())),
        )
        self._db.commit()

    def _revoke(self, guild_id: int, user_id: int) -> None:
        self._db.execute(
            "DELETE FROM consent WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        self._db.commit()

    def _select(self, guild_id: int) -> frozenset[int]:
        rows = self._db.execute(
            "SELECT user_id FROM consent WHERE guild_id = ?", (guild_id,)
        ).fetchall()
        return frozenset(int(r[0]) for r in rows)
