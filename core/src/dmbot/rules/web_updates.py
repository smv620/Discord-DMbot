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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from dmbot.db import Database
from dmbot.rules import rule_sets
from dmbot.rules.pack_updates import PackTaken, PackUpdateStore

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


@dataclass(frozen=True, slots=True)
class SavedPackTake:
    id: int
    user_id: int
    taken: PackTaken
    created_at: int


def pack_taken_to_json(taken: PackTaken) -> dict[str, Any]:
    """A `PackTaken` as plain JSON for the day-long handle (pack entries only, never story)."""
    return {
        "merged": taken.merged,
        "before": {f.name: getattr(taken.before, f.name) for f in fields(rule_sets.Follow)},
        "place": taken.place,
        "after_version": taken.after_version,
        "added": list(taken.added),
        "offered": taken.offered,
        "skipped": taken.skipped,
    }


def pack_taken_from_json(data: Mapping[str, Any]) -> PackTaken:
    before = {
        f.name: data["before"][f.name] for f in fields(rule_sets.Follow) if f.name in data["before"]
    }
    return PackTaken(
        merged=bool(data["merged"]),
        before=rule_sets.Follow(**before),
        place=int(data["place"]),
        after_version=int(data["after_version"]),
        added=tuple(int(n) for n in data["added"]),
        offered=int(data["offered"]),
        skipped=int(data["skipped"]),
    )


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
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND kind = 'set'",
                (guild_id, campaign_id, take_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return SavedTake(
            int(row["id"]), int(row["user_id"]), rule_sets.taken_from_json(row["taken"]),
            None if row["def_batch"] is None else int(row["def_batch"]), int(row["created_at"]),
        )  # fmt: skip

    async def newest_id(self, guild_id: int, campaign_id: str, kind: str = "set") -> int | None:
        """The newest handle of its kind (a set's or a pack's) for the campaign."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT max(id) AS id FROM web_rule_takes"
                " WHERE guild_id = %s AND campaign_id = %s AND kind = %s",
                (guild_id, campaign_id, kind),
            )
            row = await cur.fetchone()
        return None if row is None or row["id"] is None else int(row["id"])

    async def save_pack(
        self, guild_id: int, campaign_id: str, user_id: int, taken: PackTaken
    ) -> int:
        """The handle of a pack pick (kind 'pack'), for this DM's Undo."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO web_rule_takes (guild_id, campaign_id, user_id, taken, kind,"
                " created_at) VALUES (%s, %s, %s, %s, 'pack', %s) RETURNING id",
                (guild_id, campaign_id, user_id, Jsonb(pack_taken_to_json(taken)),
                 int(self._now())),
            )  # fmt: skip
            row = await cur.fetchone()
        assert row is not None
        return int(row["id"])

    async def get_pack(self, guild_id: int, campaign_id: str, take_id: int) -> SavedPackTake | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, user_id, taken, created_at FROM web_rule_takes"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND kind = 'pack'",
                (guild_id, campaign_id, take_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return SavedPackTake(
            int(row["id"]), int(row["user_id"]), pack_taken_from_json(row["taken"]),
            int(row["created_at"]),
        )  # fmt: skip

    async def newest_pack_id(self, guild_id: int, campaign_id: str, set_id: str) -> int | None:
        """The newest pack handle for that one pack (a pick of another pack does not block it)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT max(id) AS id FROM web_rule_takes WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = 'pack' AND taken -> 'before' ->> 'set_id' = %s",
                (guild_id, campaign_id, set_id),
            )
            row = await cur.fetchone()
        return None if row is None or row["id"] is None else int(row["id"])

    def fresh_pack(self, saved: SavedPackTake) -> bool:
        return int(self._now()) - saved.created_at <= TAKE_LIFE_S

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
