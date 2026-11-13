"""A map in the change log (#4346, design rule 8): each write to `campaign_maps` made through
`Changes` is one Undo; the picture's permanent flag follows the map back and forth in the same step;
a picture deleted since makes the Undo say so; and what points at a map is never cleared outside
the log. The map store's own writes are CloudDev-lead's (#4346); these tests write the rows the
way it will, through `Changes`. Invented ids only. Needs Postgres."""

from __future__ import annotations

import uuid
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory._changes import DEPENDENTS, MAP_PICTURE_GONE, MAPS
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.memory.store import MemoryStore
from dmbot.pictures.store import MB, PictureStore
from tests.pg import DatabaseTest

GUILD = 1
DM_ID, PLAYER = 7, 8


class MapUndo(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.camp = (await CampaignStore(self.db).create(GUILD, "One", DM_ID)).id

    async def picture(self, n: int) -> int:
        added = await self.pictures.add(
            GUILD, self.camp, poster_id=PLAYER, session_id="", line_id=None, message_id=n,
            description="", consent_version=7, right_granted_at=900, sha256=f"{n:064x}",
            object_key=f"{GUILD}/{self.camp}/{n:064x}.png", preview_key="",
            size_bytes=MB, preview_bytes=0, storage_mb=10,
        )  # fmt: skip
        assert added.picture is not None
        return added.picture.id

    async def pin(self, picture_id: int, on: bool) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE campaign_pictures SET permanent = %s WHERE id = %s", (on, picture_id)
            )

    async def pinned(self, picture_id: int) -> bool:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT permanent FROM campaign_pictures WHERE id = %s", (picture_id,)
            )
            row = await cur.fetchone()
        assert row is not None
        return bool(row["permanent"])

    async def map_row(self, map_id: str) -> dict[str, Any] | None:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT audience, use_ok, use_by FROM campaign_maps"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (GUILD, self.camp, map_id),
            )
            row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def add(self, picture_id: int) -> tuple[str, int]:
        """A map kept the way `MapStore.add_map` keeps it: the row, and its picture pinned."""
        map_id = uuid.uuid4().hex
        async with self.memory._write(GUILD, self.camp, DM) as w:
            await w.insert(MAPS, {
                "id": map_id, "picture_id": picture_id, "title": "", "covers": None,
                "kind": "region", "width": None, "height": None, "scale_value": None,
                "scale_unit": None, "scale_from": None, "grid_kind": "none",
                "grid_cell_px": None, "grid_cell_distance": None, "north_angle": 0.0,
                "north_from": "assumed", "audience": "dm", "came_from": "website",
                "import_tool": "", "right_by": PLAYER, "right_at": 900, "use_ok": None,
                "use_by": None, "use_at": None, "as_of": None, "replaces": None,
                "created_at": 2000,
            })  # fmt: skip
            await w.conn.execute(
                "UPDATE campaign_pictures SET permanent = TRUE WHERE id = %s", (picture_id,)
            )
            assert w.batch is not None
            return map_id, w.batch

    async def change(self, map_id: str, **cells: object) -> int:
        async with self.memory._write(GUILD, self.camp, DM) as w:
            await w.update(MAPS, map_id, dict(cells))
            assert w.batch is not None
            return w.batch

    async def remove(self, map_id: str, picture_id: int) -> int:
        async with self.memory._write(GUILD, self.camp, DM) as w:
            await w.delete(MAPS, map_id)
            await w.conn.execute(
                "UPDATE campaign_pictures SET permanent = FALSE WHERE id = %s", (picture_id,)
            )
            assert w.batch is not None
            return w.batch

    async def test_undo_of_an_add_removes_the_map_and_unpins_its_picture(self) -> None:
        pic = await self.picture(1)
        map_id, batch = await self.add(pic)
        self.assertTrue(await self.pinned(pic))
        await self.memory.undo(GUILD, self.camp, batch)
        self.assertIsNone(await self.map_row(map_id))
        self.assertFalse(await self.pinned(pic))

    async def test_undo_of_a_remove_brings_the_map_back_and_pins_its_picture(self) -> None:
        pic = await self.picture(1)
        map_id, _ = await self.add(pic)
        batch = await self.remove(map_id, pic)
        self.assertFalse(await self.pinned(pic))
        await self.memory.undo(GUILD, self.camp, batch)
        self.assertIsNotNone(await self.map_row(map_id))
        self.assertTrue(await self.pinned(pic))

    async def test_undo_of_a_dms_yes_puts_the_map_back_to_waiting(self) -> None:
        pic = await self.picture(1)
        map_id, _ = await self.add(pic)
        batch = await self.change(map_id, use_ok=True, use_by=DM_ID, use_at=2100)
        await self.memory.undo(GUILD, self.camp, batch)
        self.assertEqual(await self.map_row(map_id), {"audience": "dm", "use_ok": None,
                                                      "use_by": None})  # fmt: skip
        self.assertTrue(await self.pinned(pic))  # (still a map, so still pinned)

    async def test_undo_of_show_to_the_table_hides_it_again(self) -> None:
        pic = await self.picture(1)
        map_id, _ = await self.add(pic)
        batch = await self.change(map_id, audience="table")
        await self.memory.undo(GUILD, self.camp, batch)
        row = await self.map_row(map_id)
        assert row is not None
        self.assertEqual(row["audience"], "dm")

    async def test_a_picture_deleted_since_makes_the_undo_say_so(self) -> None:
        pic = await self.picture(1)
        _, added = await self.add(pic)
        removed_pic = await self.picture(2)
        other_id, _ = await self.add(removed_pic)
        removed = await self.remove(other_id, removed_pic)
        await self.pictures.delete(GUILD, self.camp, pic)  # (takes its map, outside the log)
        await self.pictures.delete(GUILD, self.camp, removed_pic)  # (its row stays, deleted)
        for batch in (added, removed):
            with self.assertRaises(MemoryRuleError) as caught:
                await self.memory.undo(GUILD, self.camp, batch)
            self.assertEqual(str(caught.exception), MAP_PICTURE_GONE)
        self.assertIsNone(await self.map_row(other_id))  # (never back on a deleted picture)
        self.assertFalse(await self.pinned(removed_pic))

    def test_what_points_at_a_map_is_never_cleared_outside_the_log(self) -> None:
        # (both keys are ON DELETE SET NULL: the database would clear them unlogged)
        self.assertEqual(
            set(DEPENDENTS["campaign_maps"]),
            {("memory_map_facts", "map_id", False), ("campaign_maps", "replaces", False)},
        )
        self.assertIn(("campaign_maps", "covers", False), DEPENDENTS["memory_entities"])
