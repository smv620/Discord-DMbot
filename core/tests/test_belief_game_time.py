"""The game time of a belief (#3091, part 3, slice 1): stored when a live writer gives one, empty
otherwise, kept by copies, Undo and backups. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.backup import MemorySection
from dmbot.memory.models import MemoryRuleError
from tests import test_lies as lies_t
from tests.test_memory_store import GUILD_A


class GameTime(lies_t.Lies):
    async def minute_of(self, belief_id: str) -> Any:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT game_time FROM memory_beliefs WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s",
                (GUILD_A, self.c, belief_id),
            )
            row = await cur.fetchone()
        assert row is not None
        return row["game_time"]

    async def test_a_live_belief_keeps_the_minute_and_any_other_has_none(self) -> None:
        view = await self.pc_view()
        live = await self.think(view, "theory", 0.5, [], game_time=12 * 1440 + 90)
        later = await self.think(view, "theory", 0.4, [])
        self.assertEqual(await self.minute_of(live.value.id), 12 * 1440 + 90)
        self.assertEqual(live.value.game_time, 12 * 1440 + 90)
        self.assertIsNone(await self.minute_of(later.value.id))  # (never the clock as it is now)

    async def test_a_game_time_below_zero_or_not_a_number_is_refused(self) -> None:
        view = await self.pc_view()
        for bad in (-1, True):
            with self.subTest(bad), self.assertRaises(MemoryRuleError):
                await self.think(view, "theory", 0.5, [], game_time=bad)

    async def test_a_pin_and_a_reword_keep_the_same_beliefs_minute(self) -> None:
        made = await self.think(self.truth, "revealed_fact", 1.0, [], game_time=5000)
        await self.memory.update_relation(GUILD_A, self.c, self.rel, source="dm", secret=False)
        await self.memory.pin(GUILD_A, self.c, self.rel, True, source="dm")
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT game_time FROM memory_beliefs WHERE guild_id = %s AND campaign_id = %s"
                " AND relation_id = %s AND perspective_id = %s ORDER BY seq",
                (GUILD_A, self.c, self.rel, self.truth),
            )
            minutes = [r["game_time"] for r in await cur.fetchall()]
        self.assertIn(5000, minutes)
        self.assertEqual(made.value.game_time, 5000)

    async def test_undo_and_redo_give_the_minute_back(self) -> None:
        view = await self.pc_view()
        made = await self.think(view, "theory", 0.5, [], game_time=777)
        assert made.batch is not None
        undone = await self.memory.undo(GUILD_A, self.c, made.batch, source="dm")
        assert undone.batch is not None
        await self.memory.undo(GUILD_A, self.c, undone.batch, source="dm")
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT game_time FROM memory_beliefs WHERE guild_id = %s AND campaign_id = %s"
                " AND perspective_id = %s AND status = 'theory'",
                (GUILD_A, self.c, view),
            )
            self.assertEqual([r["game_time"] for r in await cur.fetchall()], [777])

    async def test_a_backup_carries_it_and_a_file_from_before_loads_with_none(self) -> None:
        view = await self.pc_view()
        await self.think(view, "theory", 0.5, [], game_time=4242)
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        beliefs = [r for r in rows if r["table"] == "belief" and r["status"] == "theory"]
        self.assertEqual([b["game_time"] for b in beliefs], [4242])
        old = [
            {k: v for k, v in r.items() if k != "game_time"} if r["table"] == "belief" else r
            for r in rows
        ]
        section.check(old)  # (a file from before has no such key)
        bad = [{**r, "game_time": -5} if r["table"] == "belief" else r for r in rows]
        with self.assertRaises(Exception):  # noqa: B017 (the file is refused, whatever the class)
            section.check(bad)
