"""The reminder timers of "Questions for you" in the database (#3435): `question_notices`, one row
for each DM of each campaign. Times, counts and message ids only."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.questions.timer import Notice


@dataclass(frozen=True, slots=True)
class Stored:
    notice: Notice
    reminder_id: int | None = None  # the current notice or reminder in their private chat
    message_id: int | None = None  # the Questions message (part 3)


class NoticeStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def get(self, guild_id: int, campaign_id: str, user_id: int) -> Stored:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT step, due_at, muted, last_total, reminder_id, message_id"
                " FROM question_notices WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
                (guild_id, campaign_id, user_id),
            )
            row = await cur.fetchone()
        if row is None:
            return Stored(Notice())
        return Stored(
            Notice(
                step=int(row["step"]),
                due_at=None if row["due_at"] is None else int(row["due_at"]),
                muted=bool(row["muted"]),
                last_total=int(row["last_total"]),
            ),
            None if row["reminder_id"] is None else int(row["reminder_id"]),
            None if row["message_id"] is None else int(row["message_id"]),
        )

    async def change(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        before: Notice,
        after: Notice,
    ) -> bool:
        """Move the timer from `before` to `after`, once: True only for the call that found the row
        still as `before` (a second round, or a second copy of the bot, loses and sends nothing)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO question_notices (guild_id, campaign_id, user_id) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (guild_id, campaign_id, user_id),
            )
            cur = await conn.execute(
                "UPDATE question_notices SET step = %s, due_at = %s, muted = %s, last_total = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND user_id = %s"
                " AND step = %s AND due_at IS NOT DISTINCT FROM %s AND muted = %s"
                " AND last_total = %s",
                (
                    after.step,
                    after.due_at,
                    after.muted,
                    after.last_total,
                    guild_id,
                    campaign_id,
                    user_id,
                    before.step,
                    before.due_at,
                    before.muted,
                    before.last_total,
                ),
            )
            return cur.rowcount == 1

    async def set_reminder(
        self, guild_id: int, campaign_id: str, user_id: int, message_id: int | None
    ) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE question_notices SET reminder_id = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
                (message_id, guild_id, campaign_id, user_id),
            )

    async def mute_all(self, user_id: int, guild_ids: Sequence[int]) -> int:
        """🔕 Not now: no reminders for this DM in any of their campaigns until a new question.
        Returns how many campaigns were muted."""
        muted = 0
        for guild_id in guild_ids:
            async with self._db.guild(guild_id) as conn:
                cur = await conn.execute(
                    "UPDATE question_notices SET muted = TRUE"
                    " WHERE guild_id = %s AND user_id = %s AND NOT muted AND last_total > 0",
                    (guild_id, user_id),
                )
                muted += cur.rowcount
        return muted

    async def set_message(
        self, guild_id: int, campaign_id: str, user_id: int, message_id: int | None
    ) -> None:
        """Remember the Questions message in this DM's private chat (or forget it)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO question_notices (guild_id, campaign_id, user_id, message_id)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, user_id) DO UPDATE SET"
                " message_id = EXCLUDED.message_id",
                (guild_id, campaign_id, user_id, message_id),
            )
