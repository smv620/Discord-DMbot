"""The build that made a row is stamped on it (#3739): the stamp, the trigger and the rules."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from dmbot import lineage
from dmbot.campaigns import CampaignStore
from dmbot.memory.models import DM, PLAYER_CHARACTER
from dmbot.memory.store import ENTITIES, MemoryStore
from tests.pg import DatabaseTest

GUILD, USER = 91, 5
OLD = "a" * 40
NEW = "b" * 40


class TheStamp(unittest.TestCase):
    def test_only_a_full_hex_commit_counts(self) -> None:
        for value, expected in ((OLD, OLD), (OLD.upper(), OLD), ("abc123", None), ("", None)):
            with mock.patch.dict(os.environ, {"GIT_COMMIT": value}):
                self.assertEqual(lineage.running_commit(), expected)

    def test_an_untrusted_value_is_none_or_forty_hex(self) -> None:
        for good in (None, OLD):
            self.assertTrue(lineage.valid(good))
        for bad in ("unknown", OLD[:39], OLD.upper(), 5, ["a"], True):
            self.assertFalse(lineage.valid(bad))

    def test_the_words_for_an_old_row(self) -> None:
        self.assertEqual(lineage.short(None), "made before versions were recorded")
        self.assertEqual(lineage.short(OLD), "made by DMbot version aaaaaaa")


class TheTrigger(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.campaign = await CampaignStore(self.db).create(GUILD, "One", USER)

    async def _stamp(self, entity_id: str) -> str | None:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT made_by_commit FROM memory_entities WHERE id = %s", (entity_id,)
            )
            row = await cur.fetchone()
        assert row is not None
        return row["made_by_commit"]  # type: ignore[no-any-return]

    async def _add(self, name: str, source: str = DM) -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, type=PLAYER_CHARACTER, name=name, source=source
        )
        assert made.value is not None
        return str(made.value.id)

    async def test_a_new_row_carries_the_running_build(self) -> None:
        with mock.patch.dict(os.environ, {"GIT_COMMIT": OLD}):
            entity = await self._add("Mira")
        self.assertEqual(await self._stamp(entity), OLD)

    async def test_an_unknown_build_stamps_nothing_not_a_word(self) -> None:
        with mock.patch.dict(os.environ, {"GIT_COMMIT": ""}):
            entity = await self._add("Mira")
        self.assertIsNone(await self._stamp(entity))

    async def test_the_dms_own_edit_keeps_the_stamp(self) -> None:
        with mock.patch.dict(os.environ, {"GIT_COMMIT": OLD}):
            entity = await self._add("Mira")
        with mock.patch.dict(os.environ, {"GIT_COMMIT": NEW}):
            await self.memory.rename_entity(GUILD, self.campaign.id, entity, "Mirael", source=DM)
        self.assertEqual(await self._stamp(entity), OLD)

    async def _reader_rewrite(self, entity: str) -> int:
        """A reader (not the DM) updates the entry: DMbot rewriting its own work."""
        async with self.memory._write(GUILD, self.campaign.id, "extractor") as w:
            await w.update(ENTITIES, entity, {"description": "seen again"})
        return w.batch

    async def test_dmbot_rewriting_its_own_work_takes_the_new_stamp(self) -> None:
        with mock.patch.dict(os.environ, {"GIT_COMMIT": OLD}):
            entity = await self._add("Mira", source="extractor")
        with mock.patch.dict(os.environ, {"GIT_COMMIT": NEW}):
            await self._reader_rewrite(entity)
        self.assertEqual(await self._stamp(entity), NEW)

    async def test_an_undo_is_the_dms_act_and_does_not_restamp(self) -> None:
        """The change log holds named columns, not the stamp, so an Undo cannot hand the older
        stamp back; it must at least not claim the undoing build made the row."""
        with mock.patch.dict(os.environ, {"GIT_COMMIT": OLD}):
            entity = await self._add("Mira", source="extractor")
        with mock.patch.dict(os.environ, {"GIT_COMMIT": NEW}):
            batch = await self._reader_rewrite(entity)
        with mock.patch.dict(os.environ, {"GIT_COMMIT": "c" * 40}):
            await self.memory.undo(GUILD, self.campaign.id, batch)
        self.assertEqual(await self._stamp(entity), NEW)  # the rewrite's, not the undoing build's

    async def test_an_old_row_reads_fine_and_a_dm_edit_leaves_it_empty(self) -> None:
        entity = await self._add("Mira")
        async with self.db.guild(GUILD, restoring=True) as conn:
            await conn.execute("UPDATE memory_entities SET made_by_commit = NULL")
        self.assertIsNone(await self._stamp(entity))
        with mock.patch.dict(os.environ, {"GIT_COMMIT": NEW}):
            await self.memory.rename_entity(GUILD, self.campaign.id, entity, "Mirael", source=DM)
        self.assertIsNone(await self._stamp(entity))

    async def test_a_restore_keeps_what_the_row_carries_not_the_loading_build(self) -> None:
        entity = await self._add("Mira")
        with mock.patch.dict(os.environ, {"GIT_COMMIT": NEW}):
            async with self.db.guild(GUILD, restoring=True) as conn:
                await conn.execute(
                    "UPDATE memory_entities SET made_by_commit = %s, description = 'x'"
                    " WHERE id = %s",
                    (OLD, entity),
                )
        self.assertEqual(await self._stamp(entity), OLD)

    async def test_a_stamp_that_is_not_forty_hex_is_refused_by_the_table(self) -> None:
        import psycopg

        entity = await self._add("Mira")
        with self.assertRaises(psycopg.errors.CheckViolation):
            async with self.db.guild(GUILD, restoring=True) as conn:
                await conn.execute(
                    "UPDATE memory_entities SET made_by_commit = 'unknown' WHERE id = %s",
                    (entity,),
                )


if __name__ == "__main__":
    unittest.main()
