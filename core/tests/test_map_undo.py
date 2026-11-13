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

    async def add(self, picture_id: int, **cells: object) -> tuple[str, int]:
        """A map kept the way `MapStore.add_map` keeps it: the row, and its picture pinned."""
        ids, batch = await self.add_many([picture_id], **cells)
        return ids[0], batch

    async def add_many(self, picture_ids: list[int], **cells: object) -> tuple[list[str], int]:
        """Several maps in one write (one batch: the Undo's run path, not row by row)."""
        map_ids = [uuid.uuid4().hex for _ in picture_ids]
        async with self.memory.map_write(GUILD, self.camp) as w:
            for map_id, picture_id in zip(map_ids, picture_ids, strict=True):
                await self._insert(w, map_id, picture_id, **cells)
            assert w.batch is not None
            return map_ids, w.batch

    async def _insert(self, w: Any, map_id: str, picture_id: int, **cells: object) -> None:
        await w.insert(MAPS, {
                "id": map_id, "picture_id": picture_id, "title": "", "covers": None,
                "kind": "region", "width": None, "height": None, "scale_value": None,
                "scale_unit": None, "scale_from": None, "grid_kind": "none",
                "grid_cell_px": None, "grid_cell_distance": None, "north_angle": 0.0,
                "north_from": "assumed", "audience": "dm", "came_from": "website",
                "import_tool": "", "right_by": PLAYER, "right_at": 900, "use_ok": None,
                "use_by": None, "use_at": None, "as_of": None, "replaces": None,
                "created_at": 2000, **cells,
            })  # fmt: skip
        await w.conn.execute(
            "UPDATE campaign_pictures SET permanent = TRUE WHERE id = %s", (picture_id,)
        )

    async def change(self, map_id: str, **cells: object) -> int:
        async with self.memory.map_write(GUILD, self.camp) as w:
            await w.update(MAPS, map_id, dict(cells))
            assert w.batch is not None
            return w.batch

    async def remove(self, map_id: str, picture_id: int) -> int:
        async with self.memory.map_write(GUILD, self.camp) as w:
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
        # (a picture deleted while its map stood takes the map's log rows with it, #4350: its Undo
        # is refused as too late; a map removed earlier still has them, and says the picture went)
        with self.assertRaises(MemoryRuleError):
            await self.memory.undo(GUILD, self.camp, added)
        with self.assertRaises(MemoryRuleError) as caught:
            await self.memory.undo(GUILD, self.camp, removed)
        self.assertEqual(str(caught.exception), MAP_PICTURE_GONE)
        self.assertIsNone(await self.map_row(other_id))  # (never back on a deleted picture)
        self.assertFalse(await self.pinned(removed_pic))

    async def test_undo_of_two_maps_kept_at_once_removes_both(self) -> None:
        pics = [await self.picture(1), await self.picture(2)]
        map_ids, batch = await self.add_many(pics, scale_value=2.5, scale_unit="miles",
                                             scale_from="dm", north_angle=90.5)  # fmt: skip
        await self.memory.undo(GUILD, self.camp, batch)
        self.assertEqual([await self.map_row(m) for m in map_ids], [None, None])
        self.assertEqual([await self.pinned(p) for p in pics], [False, False])

    async def test_undo_of_two_maps_removed_at_once_brings_both_back(self) -> None:
        old_pic, new_pic = await self.picture(1), await self.picture(2)
        old_id, _ = await self.add(old_pic)
        new_id, _ = await self.add(new_pic, replaces=old_id)  # (the newer map replaces the older)
        async with self.memory.map_write(GUILD, self.camp) as w:
            await w.delete(MAPS, new_id)  # (first: it points at the older one)
            await w.delete(MAPS, old_id)
            await w.conn.execute(
                "UPDATE campaign_pictures SET permanent = FALSE WHERE id = ANY(%s)",
                ([old_pic, new_pic],),
            )
            batch = w.batch
        assert batch is not None
        await self.memory.undo(GUILD, self.camp, batch)
        self.assertIsNotNone(await self.map_row(old_id))
        self.assertIsNotNone(await self.map_row(new_id))
        self.assertEqual([await self.pinned(p) for p in (old_pic, new_pic)], [True, True])

    async def test_redo_puts_the_map_back_and_pins_its_picture(self) -> None:
        pic = await self.picture(1)
        map_id, batch = await self.add(pic)
        undone = await self.memory.undo(GUILD, self.camp, batch)
        assert undone.batch is not None
        await self.memory.undo(GUILD, self.camp, undone.batch)  # (undoing the Undo redoes it)
        self.assertIsNotNone(await self.map_row(map_id))
        self.assertTrue(await self.pinned(pic))

    async def test_a_place_a_map_covers_is_not_undone_from_under_it(self) -> None:
        place = await self.memory.add_entity(
            GUILD, self.camp, type="place", name="Oakhollow", source=DM, status="confirmed"
        )
        assert place.batch is not None
        map_id, _ = await self.add(await self.picture(1), covers=place.value.id)
        with self.assertRaises(MemoryRuleError):
            await self.memory.undo(GUILD, self.camp, place.batch)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT covers FROM campaign_maps WHERE guild_id = %s AND id = %s",
                (GUILD, map_id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["covers"], place.value.id)  # (never cleared outside the log)

    async def test_a_map_another_replaces_is_not_undone_from_under_it(self) -> None:
        old_id, added = await self.add(await self.picture(1))
        new_id, _ = await self.add(await self.picture(2), replaces=old_id)
        with self.assertRaises(MemoryRuleError):
            await self.memory.undo(GUILD, self.camp, added)
        self.assertIsNotNone(await self.map_row(old_id))
        self.assertIsNotNone(await self.map_row(new_id))

    def test_what_points_at_a_map_is_never_cleared_outside_the_log(self) -> None:
        # (both keys are ON DELETE SET NULL: the database would clear them unlogged)
        self.assertEqual(
            set(DEPENDENTS["campaign_maps"]),
            {("memory_map_facts", "map_id", False), ("campaign_maps", "replaces", False)},
        )
        self.assertIn(("campaign_maps", "covers", False), DEPENDENTS["memory_entities"])
