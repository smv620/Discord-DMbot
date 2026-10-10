"""Where a campaign's game clock is kept (#965): one row per campaign, in that campaign's
own server scope, changed only by a DM of the campaign (checked in the same transaction as
the change), carried in backups and deleted with the campaign. See `timebot.clock` for the
arithmetic and docs/PLAN.md, "TimeBot".
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.timebot import clock as game
from dmbot.timebot.clock import Clock

NOT_A_DM = "Only this campaign's DMs can use the clock."
DAMAGED = "That backup file's game clock is damaged, so nothing was restored."


@dataclass(frozen=True, slots=True)
class Stored:
    """A campaign's clock and where its pinned message is (None until it is posted)."""

    clock: Clock
    channel_id: int | None
    message_id: int | None


def _stored(row: dict[str, Any]) -> Stored:
    tired = row["tired_told_for"]
    return Stored(
        Clock(
            int(row["minute"]), int(row["last_long_rest"]), None if tired is None else int(tired)
        ),
        None if row["channel_id"] is None else int(row["channel_id"]),
        None if row["message_id"] is None else int(row["message_id"]),
    )


class ClockStore:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def get(self, guild_id: int, campaign_id: str) -> Stored | None:
        async with self._db.guild(guild_id) as conn:
            return await self._read(conn, guild_id, campaign_id, lock=False)

    async def change(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        fn: Callable[[Clock | None], Clock | None],
    ) -> tuple[Clock | None, Clock | None]:
        """Apply `fn` to the campaign's clock (None if not set yet); it returns the new
        clock, or None to change nothing. Only a DM of the campaign may; the row is locked
        for the change, so two presses never lose one. Returns (before, after)."""
        async with self._db.guild(guild_id) as conn:
            if not await self._is_dm(conn, guild_id, campaign_id, user_id):
                raise CampaignError(NOT_A_DM)
            current = await self._read(conn, guild_id, campaign_id, lock=True)
            before = None if current is None else current.clock
            after = fn(before)
            if after is None:
                return before, before
            await conn.execute(
                "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest,"
                " tired_told_for, updated_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id) DO UPDATE SET minute = EXCLUDED.minute,"
                " last_long_rest = EXCLUDED.last_long_rest,"
                " tired_told_for = EXCLUDED.tired_told_for, updated_at = EXCLUDED.updated_at",
                (
                    guild_id,
                    campaign_id,
                    after.minute,
                    after.last_long_rest,
                    after.tired_told_for,
                    int(self._now()),
                ),
            )
            if before is not None and after.minute < before.minute:
                # The clock went back (Undo, or the time set earlier): a timer whose end is
                # ahead again will be said again when the clock passes it.
                await conn.execute(
                    "UPDATE game_effects SET told = FALSE WHERE guild_id = %s AND campaign_id = %s"
                    " AND ends_minute > %s AND told",
                    (guild_id, campaign_id, after.minute),
                )
            return before, after

    async def set_message(
        self, guild_id: int, campaign_id: str, channel_id: int, message_id: int
    ) -> None:
        """Remember the pinned clock message so it can be edited in place."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE game_clocks SET channel_id = %s, message_id = %s"
                " WHERE guild_id = %s AND campaign_id = %s",
                (channel_id, message_id, guild_id, campaign_id),
            )

    @staticmethod
    async def _is_dm(conn: Conn, guild_id: int, campaign_id: str, user_id: int) -> bool:
        cur = await conn.execute(
            "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
            (guild_id, campaign_id, user_id),
        )
        return await cur.fetchone() is not None

    @staticmethod
    async def _read(conn: Conn, guild_id: int, campaign_id: str, *, lock: bool) -> Stored | None:
        cur = await conn.execute(
            "SELECT minute, last_long_rest, tired_told_for, channel_id, message_id"
            " FROM game_clocks WHERE guild_id = %s AND campaign_id = %s"
            + (" FOR UPDATE" if lock else ""),
            (guild_id, campaign_id),
        )
        row = await cur.fetchone()
        return None if row is None else _stored(row)


class ClockSection:
    """The game clock in campaign backups (an `ExportSection`). No message ids: they mean
    nothing in another server. A backup from before the clock existed has no rows."""

    name = "game_clock"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        stored = await ClockStore._read(conn, guild_id, campaign_id, lock=False)
        if stored is None:
            return []
        c = stored.clock
        return [{"minute": c.minute, "last_long_rest": c.last_long_rest}]

    def check(self, rows: list[Any]) -> Clock | None:
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise CampaignError(DAMAGED)
        row = rows[0]
        if set(row) != {"minute", "last_long_rest"}:
            raise CampaignError(DAMAGED)
        minute, rest = row["minute"], row["last_long_rest"]
        for value in (minute, rest):
            if type(value) is not int or not 0 <= value <= game.MAX_MINUTE:
                raise CampaignError(DAMAGED)
        if rest > minute:  # a rest can't be in the future
            raise CampaignError(DAMAGED)
        return Clock(minute, rest, None)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        clock = rows if isinstance(rows, Clock) or rows is None else self.check(rows)
        if clock is None:
            return
        await conn.execute(
            "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest,"
            " tired_told_for, updated_at) VALUES (%s, %s, %s, %s, NULL, %s)"
            " ON CONFLICT (guild_id, campaign_id) DO UPDATE SET minute = EXCLUDED.minute,"
            " last_long_rest = EXCLUDED.last_long_rest, tired_told_for = NULL,"
            " channel_id = NULL, message_id = NULL, updated_at = EXCLUDED.updated_at",
            (guild_id, campaign_id, clock.minute, clock.last_long_rest, int(time.time())),
        )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM game_clocks WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
