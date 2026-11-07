"""The build that made a row is stamped on it (#3739): the stamp, the trigger and the rules."""

from __future__ import annotations

import copy
import os
import unittest
from typing import Any
from unittest import mock

from dmbot import lineage
from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.memory._changes import ENTITIES
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import DM, PLAYER_CHARACTER
from dmbot.memory.store import MemoryStore
from dmbot.memory.vectors import ASSERTION, Item, VectorStore
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
        assert w.batch is not None
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

    async def test_undo_works_for_a_dmbot_write_under_a_set_build(self) -> None:
        """The stamp is never part of "changed since": DMbot writes, the DM undoes."""
        with mock.patch.dict(os.environ, {"GIT_COMMIT": OLD}):
            entity = await self._add("Mira", source="extractor")
        with mock.patch.dict(os.environ, {"GIT_COMMIT": NEW}):
            batch = await self._reader_rewrite(entity)
            await self.memory.undo(GUILD, self.campaign.id, batch)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT description AS d FROM memory_entities WHERE id = %s", (entity,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["d"], "")

    async def test_undo_ignores_a_stamp_in_a_logged_row_as_the_web_doors_log_it(self) -> None:
        """The website's SQL doors log `to_jsonb(row)`, whole rows, which carry the stamp."""
        with mock.patch.dict(os.environ, {"GIT_COMMIT": OLD}):
            entity = await self._add("Mira", source="extractor")
            batch = await self._reader_rewrite(entity)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_changes SET before = before || jsonb_build_object("
                "'made_by_commit', %s::text), after = after || jsonb_build_object("
                "'made_by_commit', %s::text) WHERE batch = %s",
                (OLD, OLD, batch),
            )
        await self.memory.undo(GUILD, self.campaign.id, batch)

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

    async def test_a_recomputed_vector_takes_the_new_stamp_through_an_upsert(self) -> None:
        """DMbot's own upserts (`ON CONFLICT DO UPDATE`) opt in with `by_dmbot`: a vector the new
        build recomputed must not still say the old build made it."""
        store = VectorStore(self.db)
        item = Item(ASSERTION, "e1", "h1", [0.5, 0.25])
        with mock.patch.dict(os.environ, {"GIT_COMMIT": OLD}):
            await store.save(GUILD, self.campaign.id, "m", [item])
        with mock.patch.dict(os.environ, {"GIT_COMMIT": NEW}):
            await store.save(
                GUILD, self.campaign.id, "m", [Item(ASSERTION, "e1", "h2", [0.5, 0.5])]
            )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT made_by_commit AS c FROM memory_vectors")
            self.assertEqual([r["c"] for r in await cur.fetchall()], [NEW])

    async def test_the_websites_transactions_name_the_build_too(self) -> None:
        """The web API is the same image and opens its transactions through the same `Database`
        (`as_person` for the campaign store, `user` for account doors), so a row a website door
        writes is stamped like the bot's."""
        with mock.patch.dict(os.environ, {"GIT_COMMIT": OLD}):
            scoped = self.db.as_person(USER, "s" * 16)
            for opener in (scoped.guild(GUILD), self.db.user(USER), self.db.anonymous()):
                async with opener as conn:
                    cur = await conn.execute("SELECT current_setting('dmbot.commit', true) AS c")
                    row = await cur.fetchone()
                    assert row is not None
                    self.assertEqual(row["c"], OLD)

    async def test_a_stamp_that_is_not_forty_hex_is_refused_by_the_table(self) -> None:
        import psycopg

        entity = await self._add("Mira")
        with self.assertRaises(psycopg.errors.CheckViolation):
            async with self.db.guild(GUILD, restoring=True) as conn:
                await conn.execute(
                    "UPDATE memory_entities SET made_by_commit = 'unknown' WHERE id = %s",
                    (entity,),
                )


class TheBackup(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "One", USER)

    async def _stamps(self, guild: int, campaign: str) -> dict[str, str | None]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT e.name_key AS n, e.made_by_commit AS c FROM ("
                " SELECT a.text AS name_key, en.made_by_commit FROM memory_entities en"
                " JOIN memory_aliases a ON a.guild_id = en.guild_id AND a.campaign_id ="
                " en.campaign_id AND a.entity_id = en.id WHERE en.campaign_id = %s) e",
                (campaign,),
            )
            return {r["n"]: r["c"] for r in await cur.fetchall()}

    async def _two(self) -> dict[str, Any]:
        with mock.patch.dict(os.environ, {"GIT_COMMIT": OLD}):
            await self.memory.add_entity(
                GUILD, self.campaign.id, type=PLAYER_CHARACTER, name="Mira", source=DM
            )
        async with self.db.guild(GUILD, restoring=True) as conn:
            await conn.execute(
                "UPDATE memory_entities SET made_by_commit = NULL WHERE campaign_id = %s",
                (self.campaign.id,),
            )
        with mock.patch.dict(os.environ, {"GIT_COMMIT": OLD}):
            await self.memory.add_entity(
                GUILD, self.campaign.id, type=PLAYER_CHARACTER, name="Rook", source=DM
            )
        return await self.campaigns.export(GUILD, self.campaign.id)

    async def test_a_round_trip_keeps_each_stamp_and_never_stamps_the_loading_build(self) -> None:
        backup = await self._two()
        self.assertEqual(backup["version"], 5)
        with mock.patch.dict(os.environ, {"GIT_COMMIT": NEW}):
            restored = await self.campaigns.import_backup(GUILD, backup, USER)
        stamps = await self._stamps(GUILD, restored.id)
        self.assertEqual(stamps["Rook"], OLD)  # a stamped row keeps its own
        self.assertIsNone(stamps["Mira"])  # an old row stays "before versions were recorded"
        # (the restore's other rows, such as the entity's first alias, are made by the backup)
        self.assertNotIn(NEW, stamps.values())

    async def test_a_file_from_before_stamps_loads_with_none(self) -> None:
        backup = await self._two()
        backup["version"] = 4
        for row in backup["sections"]["memory"]:
            row.pop("made_by", None)
        restored = await self.campaigns.import_backup(GUILD, backup, USER)
        self.assertEqual(set((await self._stamps(GUILD, restored.id)).values()), {None})

    async def test_a_damaged_stamp_in_a_file_is_refused_whole(self) -> None:
        backup = await self._two()
        for bad in ("unknown", OLD.upper(), 5, "a" * 39):
            damaged = copy.deepcopy(backup)
            row = next(r for r in damaged["sections"]["memory"] if "made_by" in r)
            row["made_by"] = bad
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD, damaged, USER)


if __name__ == "__main__":
    unittest.main()
