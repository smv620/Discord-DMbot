"""The website's "Let me choose" answers (#4799): what the bot worked out for one DM, kept for the
page to read.

`MemoryStore.join_choices` is the graph's own logic (which sessions and story threads have
something to give each character, and how much), so it is worked out here in Python and kept as one
row of `web_join_choices` (migration 0569) for the DM who asked, because the answer is too big for
a queue answer. The row holds ids, counts and the story threads' names (what the DM reads on the
Names page) and no transcript or card word. A new read replaces the row; the bot's sweep removes
any older than an hour. The page reads it through the DM-only door `dmbot_web_join_choices`."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from dmbot.db import Database

if TYPE_CHECKING:
    from dmbot.memory.store import JoinChoices

CHOICES_LIFE_S = 60 * 60  # the page asks again after an hour; "Look again" at any time


def payload_of(choices: JoinChoices) -> dict[str, Any]:
    """What is kept: the characters (ids, in the question's order), each session (its start,
    number and how many things each character would be given), each story thread (id, name and the
    same counts) and how many there are in all."""
    return {
        "members": list(choices.members),
        "sessions": [
            {"at": at, "number": number, "counts": dict(counts)}
            for at, number, counts in choices.sessions
        ],
        "threads": [
            {"id": tid, "name": name, "counts": dict(counts)}
            for tid, name, counts in choices.threads
        ],
        "sessionsInAll": choices.sessions_in_all,
        "threadsInAll": choices.threads_in_all,
        "nobody": choices.nobody_caught_up,
    }


class JoinChoicesStore:
    """The kept "Let me choose" answers, one row for each campaign, question and DM."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def save(
        self, guild_id: int, campaign_id: str, group_id: str, user_id: int, choices: JoinChoices
    ) -> None:
        """Replace this DM's row for the question with a fresh one."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO web_join_choices (guild_id, campaign_id, group_id, user_id, payload,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, group_id, user_id)"
                " DO UPDATE SET payload = EXCLUDED.payload, created_at = EXCLUDED.created_at",
                (
                    guild_id, campaign_id, group_id, user_id, Jsonb(payload_of(choices)),
                    int(self._now()),
                ),
            )  # fmt: skip

    async def sweep(self, guild_id: int) -> int:
        """Remove this server's answers older than an hour; how many (counts only in the log)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_join_choices WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - CHOICES_LIFE_S),
            )
            return int(cur.rowcount)
