"""Who said yes to "Save my voice for tests" (#1019): the second consent, per server.

Core keeps the people who agreed in memory (`has`), so the audio path asks in no time, and
the database remembers them across restarts. Taking a yes back (`stop`) leaves the memory
first, before any database work, so nothing more is saved even if the database is slow.
"""

from __future__ import annotations

import time

from dmbot.db import Database


class TestVoiceStore:
    __test__ = False

    def __init__(self, db: Database) -> None:
        self._db = db
        self._yes: dict[int, set[int]] = {}  # server -> people who agreed
        self._loaded: set[int] = set()

    def has(self, guild_id: int, user_id: int) -> bool:
        return user_id in self._yes.get(guild_id, ())

    async def load(self, guild_id: int) -> None:
        """Read a server's yeses (when its session starts)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute("SELECT user_id FROM test_voice_consent")
            rows = await cur.fetchall()
        self._yes[guild_id] = {int(r["user_id"]) for r in rows} | self._yes.get(guild_id, set())
        self._loaded.add(guild_id)

    async def grant(self, guild_id: int, user_id: int) -> int:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO test_voice_consent (guild_id, user_id, granted_at)"
                " VALUES (%s, %s, %s)"
                " ON CONFLICT (guild_id, user_id) DO UPDATE SET granted_at = EXCLUDED.granted_at"
                " RETURNING granted_at",
                (guild_id, user_id, int(time.time())),
            )
            row = await cur.fetchone()
        assert row is not None
        self._yes.setdefault(guild_id, set()).add(user_id)
        return int(row["granted_at"])

    def stop_now(self, guild_id: int, user_id: int) -> None:
        """Nothing more is saved from this person, at once (no database)."""
        self._yes.get(guild_id, set()).discard(user_id)

    async def revoke(self, guild_id: int, user_id: int) -> None:
        self.stop_now(guild_id, user_id)
        async with self._db.guild(guild_id) as conn:
            await conn.execute("DELETE FROM test_voice_consent WHERE user_id = %s", (user_id,))
        self.stop_now(guild_id, user_id)  # a grant that raced the revoke doesn't survive it
