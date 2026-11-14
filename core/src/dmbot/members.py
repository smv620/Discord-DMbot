"""The per-server directory of members who agreed to be recorded (#2141), for the website's import
search. Names only: the display name and Discord username the bot last saw. These people's names
already appear in the server's shared transcripts, so nothing new is kept about them; the
import search still shows a row only while their consent is current. Written only by the bot.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from dmbot.db import Database

AVATAR = re.compile(r"(?:a_)?[0-9a-f]{32}")  # a Discord avatar hash (the column checks it too)
NAME_MAX = 100  # a Discord name is at most 32 characters; the rest is a ceiling, not a target


class MemberDirectory:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def remember(
        self, guild_id: int, user_id: int, display_name: str | None, username: str | None
    ) -> None:
        """Add or refresh one member who has agreed to be recorded (nothing is kept for anyone
        without a consent row, whatever the caller thinks)."""
        shown = " ".join((display_name or "").split())[:NAME_MAX]
        handle = " ".join((username or "").split())[:NAME_MAX]
        if not (shown or handle):
            return
        async with self._db.guild(guild_id) as conn:
            # Only for someone who has a consent row now: a task that finishes after the person
            # stopped can't put their names back.
            await conn.execute(
                "INSERT INTO member_directory (guild_id, user_id, display_name, username,"
                " updated_at) SELECT %s, %s, %s, %s, %s WHERE EXISTS (SELECT 1 FROM consent"
                " WHERE guild_id = %s AND user_id = %s)"
                " ON CONFLICT (guild_id, user_id) DO UPDATE SET"
                " display_name = EXCLUDED.display_name, username = EXCLUDED.username,"
                " updated_at = EXCLUDED.updated_at",
                (guild_id, user_id, shown, handle, int(self._now()), guild_id, user_id),
            )

    async def set_avatar(self, guild_id: int, user_id: int, avatar: str | None) -> None:
        """Keep (or clear, with None: a default avatar, or a member who left) this member's Discord
        avatar hash for the Story Graph's icons (#3621). Only a member already in the directory,
        which means someone who agreed to be recorded; anything that is not a hash is cleared."""
        kept = avatar if avatar is not None and AVATAR.fullmatch(avatar) else None
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE member_directory SET avatar = %s WHERE guild_id = %s AND user_id = %s",
                (kept, guild_id, user_id),
            )

    async def linked_players(self, guild_id: int, *, limit: int = 200) -> list[int]:
        """The members of this server who play a confirmed player character and are in the
        directory (so have agreed): whose avatars the daily sweep refreshes."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT e.played_by FROM memory_entities e"
                " JOIN member_directory d ON d.guild_id = e.guild_id AND d.user_id = e.played_by"
                " WHERE e.guild_id = %s AND e.role = 'player_character'"
                " AND e.status = 'confirmed' AND e.merged_into IS NULL ORDER BY e.played_by"
                " LIMIT %s",
                (guild_id, limit),
            )
            return [int(r["played_by"]) for r in await cur.fetchall()]

    async def forget(self, guild_id: int, user_id: int) -> None:
        """Drop one member: they stopped being recorded. Also drops any row in this server whose
        person has no consent row any more (a stop made on the website while the bot was down),
        so the directory keeps no one who isn't recorded."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM member_directory d WHERE d.guild_id = %s AND (d.user_id = %s OR"
                " NOT EXISTS (SELECT 1 FROM consent x WHERE x.guild_id = d.guild_id"
                " AND x.user_id = d.user_id))",
                (guild_id, user_id),
            )
