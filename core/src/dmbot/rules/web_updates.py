"""What the website's "Pick changes" on a followed house-rules set needs besides the store (#4841
part C, slice C2): the handle that lets its ↩️ Undo work later, and the bundle of things the bot
passes to the action applier. Discord keeps the result of a take (`rule_sets.Taken`) in the open
message's view; a website answer cannot hold it, so one row of `web_rule_takes` holds it for a
day (migration 0562): the campaign's own followed set and the rules a merge added, ids and version
numbers and rules, no story. The row names the DM who picked (only they may undo it; the pick's
own queue row is swept after an hour, so it cannot say), only the newest one for the campaign may
be undone, and the bot's sweep removes any older than a day."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from psycopg.types.json import Jsonb

from dmbot.db import Database
from dmbot.rules import rule_sets
from dmbot.rules.pack_updates import PackUpdateStore

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

TAKE_LIFE_S = 24 * 60 * 60  # an undo handle lasts a day; the page says so


@dataclass(frozen=True, slots=True)
class SavedTake:
    id: int
    user_id: int
    taken: rule_sets.Taken
    def_batch: int | None
    created_at: int


class TakeStore:
    """The day-long undo handles for the website's picks, by campaign (the table is the bot's own;
    no web door reads it)."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def save(
        self, guild_id: int, campaign_id: str, user_id: int, taken: rule_sets.Taken,
        def_batch: int | None,
    ) -> int:  # fmt: skip
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO web_rule_takes (guild_id, campaign_id, user_id, taken, def_batch,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    guild_id, campaign_id, user_id, Jsonb(rule_sets.taken_to_json(taken)),
                    def_batch, int(self._now()),
                ),
            )  # fmt: skip
            row = await cur.fetchone()
        assert row is not None
        return int(row["id"])

    async def get(self, guild_id: int, campaign_id: str, take_id: int) -> SavedTake | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, user_id, taken, def_batch, created_at FROM web_rule_takes"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, take_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return SavedTake(
            int(row["id"]), int(row["user_id"]), rule_sets.taken_from_json(row["taken"]),
            None if row["def_batch"] is None else int(row["def_batch"]), int(row["created_at"]),
        )  # fmt: skip

    async def newest_id(self, guild_id: int, campaign_id: str) -> int | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT max(id) AS id FROM web_rule_takes WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return None if row is None or row["id"] is None else int(row["id"])

    async def delete(self, guild_id: int, campaign_id: str, take_id: int) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM web_rule_takes WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, take_id),
            )

    async def sweep(self, guild_id: int) -> int:
        """Remove this server's handles older than a day; how many (counts only in the log)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_rule_takes WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - TAKE_LIFE_S),
            )
            return int(cur.rowcount)

    def fresh(self, saved: SavedTake) -> bool:
        """Still within the day (the sweep may not have run yet)."""
        return int(self._now()) - saved.created_at <= TAKE_LIFE_S


@dataclass(slots=True)
class RuleTools:
    """What the applier needs for a followed set's pick and its Undo: the followed-set store, the
    memory (for the relationship definitions the set carries), Discord's own `settle` (the
    borrowed definitions match the follow again) and the undo handles."""

    sets: rule_sets.RuleSetStore
    memory: MemoryStore
    takes: TakeStore
    settle: Callable[[int, str], Awaitable[None]]
    # Followed homebrew packs (#4841 packs slice): `sets` narrowed to a pack store (the same object
    # in production, so never pass a different one), None when the bot's store is not a
    # pack store) and what Discord does after a pack changes (a running session reads the
    # campaign's homebrew names again).
    packs: PackUpdateStore | None = None
    packs_changed: Callable[[int, str], None] | None = None
