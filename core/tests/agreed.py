"""A consent row for each test speaker (#4153): the after-session writers read who is recorded again
inside their own transaction, from `consent`, so a test of them has its speakers agree first, as
every real caller's list comes from that table."""

from __future__ import annotations

from dmbot.consent import TERMS_VERSION
from dmbot.db import Database


async def agree(db: Database, guild_id: int, *users: int) -> None:
    """Each of `users` has agreed in this server, under the current wording."""
    async with db.guild(guild_id) as conn:
        for user in users:
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
                " VALUES (%s, %s, 0, %s) ON CONFLICT DO NOTHING",
                (guild_id, user, TERMS_VERSION),
            )
