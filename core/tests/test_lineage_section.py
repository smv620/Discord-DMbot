"""The build stamps carried by the `lineage` backup section (#3751, #3739)."""

from __future__ import annotations

import copy
import unittest
from typing import Any

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.lineage_section import (
    LineageSection,
    _key_part,
    _shape,
    carried_tables,
)
from tests.pg import DatabaseTest

GUILD, DM = 81, 5
OLD = "a" * 40


class TheSection(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.one = await self.campaigns.create(GUILD, "One", DM)
        self.two = await self.campaigns.create(GUILD, "Two", DM)
        self.section = LineageSection()

    async def _note(self, campaign: str, at: int, stamp: str | None) -> None:
        # (restoring keeps the stamp as given, so a test can make an old row or a stamped one)
        async with self.db.guild(GUILD, restoring=True) as conn:
            await conn.execute(
                "INSERT INTO session_notes (guild_id, campaign_id, session_at, beats, written_at,"
                " job_created_at, made_by_commit) VALUES (%s, %s, %s, '[]'::jsonb, 1, 1, %s)",
                (GUILD, campaign, at, stamp),
            )

    async def _stamps(self, campaign: str) -> dict[int, str | None]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT session_at AS at, made_by_commit AS c FROM session_notes"
                " WHERE campaign_id = %s",
                (campaign,),
            )
            return {int(r["at"]): r["c"] for r in await cur.fetchall()}

    async def _dump(self, campaign: str) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            return await self.section.dump(conn, GUILD, campaign)

    async def test_it_dumps_only_the_stamped_rows_keyed_by_their_primary_key(self) -> None:
        await self._note(self.one.id, 100, OLD)
        await self._note(self.one.id, 200, None)
        rows = [r for r in await self._dump(self.one.id) if r["table"] == "session_notes"]
        self.assertEqual(
            rows, [{"table": "session_notes", "key": [100], "stamps": {"made_by_commit": OLD}}]
        )

    async def test_it_puts_a_stamp_back_on_the_recreated_row_and_skips_a_missing_one(self) -> None:
        await self._note(self.one.id, 100, OLD)
        await self._note(self.one.id, 300, OLD)
        rows = await self._dump(self.one.id)
        await self._note(self.two.id, 100, None)  # recreated by the restore; 300 was not
        async with self.db.guild(GUILD, restoring=True) as conn:
            await self.section.load(conn, GUILD, self.two.id, rows)
        self.assertEqual(await self._stamps(self.two.id), {100: OLD})

    async def test_it_never_changes_another_campaigns_row(self) -> None:
        await self._note(self.one.id, 100, None)
        await self._note(self.two.id, 100, None)
        rows = [{"table": "session_notes", "key": [100], "stamps": {"made_by_commit": OLD}}]
        async with self.db.guild(GUILD, restoring=True) as conn:
            await self.section.load(conn, GUILD, self.two.id, rows)
        self.assertEqual(await self._stamps(self.one.id), {100: None})
        self.assertEqual(await self._stamps(self.two.id), {100: OLD})

    async def test_an_empty_or_missing_section_loads_nothing(self) -> None:
        async with self.db.guild(GUILD, restoring=True) as conn:
            await self.section.load(conn, GUILD, self.two.id, [])

    async def test_the_file_is_untrusted(self) -> None:
        good = {"table": "session_notes", "key": [1], "stamps": {"made_by_commit": OLD}}
        self.assertEqual(self.section.check([good]), [good])
        changes: list[dict[str, Any]] = [
            {"table": "memory_entities"},  # carried by the memory section itself
            {"table": "not_a_table"},
            {"key": "1"},
            {"key": [True]},
            {"key": [1.5]},
            {"key": ["x" * 301]},
            {"key": list(range(9))},
            {"stamps": {}},
            {"stamps": {"made_by_commit": OLD.upper()}},
            {"stamps": {"made_by_commit": "a" * 39}},
            {"stamps": {"made_by_commit": None}},
            {"stamps": {"something_else": OLD}},
            {"extra": 1},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(CampaignError):
                self.section.check([{**copy.deepcopy(good), **change}])
        with self.assertRaises(CampaignError):
            self.section.check("not a list")  # type: ignore[arg-type]

    async def test_a_key_of_the_wrong_length_is_skipped_not_applied(self) -> None:
        await self._note(self.two.id, 100, None)
        rows = [{"table": "session_notes", "key": [100, 2], "stamps": {"made_by_commit": OLD}}]
        async with self.db.guild(GUILD, restoring=True) as conn:
            await self.section.load(conn, GUILD, self.two.id, rows)
        self.assertEqual(await self._stamps(self.two.id), {100: None})

    async def test_a_key_of_the_wrong_type_refuses_the_backup(self) -> None:
        rows = [{"table": "session_notes", "key": ["x"], "stamps": {"made_by_commit": OLD}}]
        with self.assertRaises(CampaignError):
            async with self.db.guild(GUILD, restoring=True) as conn:
                await self.section.load(conn, GUILD, self.two.id, rows)

    async def test_a_backup_round_trip_keeps_the_stamp_of_a_section_that_does_not_carry_it(
        self,
    ) -> None:
        """End to end: the notes section knows no stamp, the lineage section carries it."""
        from dmbot.recaps.beats import Beat, as_json
        from dmbot.recaps.notes import SessionNotesSection

        beats = as_json([Beat(1, "Mira opened the door.", ("Mira",), (1,), "action")])
        store = CampaignStore(self.db)
        store.register_section(SessionNotesSection())
        store.register_section(LineageSection())
        async with self.db.guild(GUILD, restoring=True) as conn:
            await conn.execute(
                "INSERT INTO session_notes (guild_id, campaign_id, session_at, beats, written_at,"
                " job_created_at, made_by_commit) VALUES (%s, %s, 100, %s::jsonb, 1, 1, %s)",
                (GUILD, self.one.id, __import__("json").dumps(beats), OLD),
            )
        backup = await store.export(GUILD, self.one.id)
        restored = await store.import_backup(GUILD, backup, DM)
        self.assertEqual(await self._stamps(restored.id), {100: OLD})
        # the same file with the section removed (a backup from before it) restores with none
        older = copy.deepcopy(backup)
        del older["sections"]["lineage"]
        again = await store.import_backup(GUILD, older, DM)
        self.assertEqual(await self._stamps(again.id), {100: None})

    async def test_it_is_registered_last_so_it_runs_after_every_other_load(self) -> None:
        from dmbot.bot import register_backup_sections

        store = CampaignStore(self.db)
        register_backup_sections(
            store,
            forget_audio=lambda g, c: None,
            consenting=lambda g: None,  # type: ignore[arg-type,return-value]
        )
        self.assertEqual(list(store._sections)[-1], "lineage")

    async def test_every_carried_table_has_a_key_and_a_stamp_column_except_generated_keys(
        self,
    ) -> None:
        """A table that silently dropped out of the section would restore with no stamps. Only a
        table whose key is generated afresh at restore (identity or serial) is left out."""
        generated = {"map_notes", "memory_importance"}
        async with self.db.guild(GUILD) as conn:
            for table in sorted(carried_tables()):
                with self.subTest(table=table):
                    shape = await _shape(conn, table)
                    if table in generated:
                        self.assertIsNone(shape)
                    else:
                        self.assertIsNotNone(shape)

    async def test_a_long_text_key_is_carried_and_a_longer_one_is_not_dumped(self) -> None:
        self.assertTrue(_key_part("x" * 300))
        self.assertFalse(_key_part("x" * 301))
        row = {"table": "memory_kind_offers", "key": ["x" * 300], "stamps": {"made_by_commit": OLD}}
        self.assertEqual(self.section.check([row]), [row])

    async def test_it_loads_last_even_when_the_file_lists_it_first(self) -> None:
        from dmbot.recaps.beats import Beat, as_json
        from dmbot.recaps.notes import SessionNotesSection

        beats = as_json([Beat(1, "Mira opened the door.", ("Mira",), (1,), "action")])
        store = CampaignStore(self.db)
        store.register_section(SessionNotesSection())
        store.register_section(LineageSection())
        async with self.db.guild(GUILD, restoring=True) as conn:
            await conn.execute(
                "INSERT INTO session_notes (guild_id, campaign_id, session_at, beats, written_at,"
                " job_created_at, made_by_commit) VALUES (%s, %s, 100, %s::jsonb, 1, 1, %s)",
                (GUILD, self.one.id, __import__("json").dumps(beats), OLD),
            )
        backup = await store.export(GUILD, self.one.id)
        backup["sections"] = {
            "lineage": backup["sections"]["lineage"],
            **{k: v for k, v in backup["sections"].items() if k != "lineage"},
        }
        restored = await store.import_backup(GUILD, backup, DM)
        self.assertEqual(await self._stamps(restored.id), {100: OLD})

    async def test_recap_jobs_carry_the_voiceovers_stamp_too(self) -> None:
        async with self.db.guild(GUILD) as conn:
            shape = await _shape(conn, "recap_jobs")
        assert shape is not None
        self.assertEqual(shape, (["session_at"], ["made_by_commit", "audio_made_by_commit"]))


if __name__ == "__main__":
    unittest.main()
