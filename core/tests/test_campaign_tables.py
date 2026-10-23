"""Every table that belongs to a campaign is carried by a backup section or is on a written list
(#2552).

A table that is on neither is a campaign that loses data when it is restored, moved or deleted.
"Belongs to a campaign" is a `campaign_id` column, or a chain of foreign keys to a table that has
one (`transcript_lines` has none of its own). The list is `campaigns/tables.py`; the sections are
the production ones from `register_backup_sections`, so a new section or table cannot slip by.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock

from dmbot.bot import register_backup_sections
from dmbot.campaigns import CampaignStore
from dmbot.campaigns.tables import NOT_IN_BACKUP, OWED_TO_BACKUP, SECTION_TABLES, backed_up
from dmbot.memory.backup import _TAGS
from tests.pg import DatabaseTest

GUILD, DM = 1, 9

# Tables reached from a table with a `campaign_id` by a foreign key, found over and over until no
# new one turns up.
BELONGING = """
    WITH RECURSIVE belongs(name) AS (
        SELECT DISTINCT table_name::text FROM information_schema.columns
        WHERE table_schema = current_schema() AND column_name = 'campaign_id'
      UNION
        SELECT child.relname::text
        FROM pg_constraint c
        JOIN pg_class child ON child.oid = c.conrelid
        JOIN pg_class parent ON parent.oid = c.confrelid
        JOIN pg_namespace n ON n.oid = child.relnamespace
        JOIN belongs b ON b.name = parent.relname::text
        WHERE c.contype = 'f' AND n.nspname = current_schema()
    )
    SELECT name FROM belongs ORDER BY 1
"""


class CampaignTables(DatabaseTest):
    async def campaign_tables(self) -> set[str]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(BELONGING)
            return {str(r["name"]) for r in await cur.fetchall()}

    def store(self) -> CampaignStore:
        store = CampaignStore(self.db)
        register_backup_sections(
            store,
            forget_audio=lambda g, c: None,
            consenting=cast(Callable[[int], Awaitable[frozenset[int]]], AsyncMock()),
        )
        return store

    async def test_every_table_of_a_campaign_is_carried_or_on_a_written_list(self) -> None:
        listed = backed_up() | set(NOT_IN_BACKUP) | set(OWED_TO_BACKUP)
        missing = sorted(await self.campaign_tables() - listed)
        self.assertEqual(missing, [], "add each to a backup section or to NOT_IN_BACKUP, with why")

    async def test_a_table_with_no_campaign_id_of_its_own_is_found_by_its_foreign_key(self) -> None:
        tables = await self.campaign_tables()
        self.assertIn("transcript_lines", tables)
        self.assertIn("transcript_lines", OWED_TO_BACKUP)

    async def test_the_lists_name_real_tables_and_do_not_overlap(self) -> None:
        real = await self.campaign_tables()
        carried = backed_up()
        for name, tables in (
            ("carried", carried),
            ("left out", set(NOT_IN_BACKUP)),
            ("owed", set(OWED_TO_BACKUP)),
        ):
            self.assertEqual(sorted(tables - real), [], name)
        self.assertEqual(sorted(carried & set(NOT_IN_BACKUP)), [])
        self.assertEqual(sorted(carried & set(OWED_TO_BACKUP)), [])
        self.assertEqual(sorted(set(NOT_IN_BACKUP) & set(OWED_TO_BACKUP)), [])

    async def test_every_left_out_table_says_why(self) -> None:
        for table, reason in NOT_IN_BACKUP.items():
            self.assertGreater(len(reason), 20, table)

    async def test_the_list_names_exactly_the_production_sections(self) -> None:
        self.assertEqual(set(self.store()._sections), set(SECTION_TABLES))

    async def test_a_section_mentions_every_table_it_is_listed_as_carrying(self) -> None:
        """The hand list cannot claim a table the section's code never touches."""
        sections = self.store()._sections
        for name, tables in SECTION_TABLES.items():
            section = sections[name]
            if name == "memory":
                self.assertEqual(
                    sorted(set(tables) - {"memory_perspectives"}),
                    sorted({t.name for t in _TAGS.values()} - {"memory_perspectives"}),
                )
                continue
            source = inspect.getsource(type(section))
            for table in tables:
                self.assertIn(table, source, f"{name} does not touch {table}")

    async def test_private_notes_never_reach_a_backup(self) -> None:
        """Anyone in the server may download a backup, so a person's own notes stay out (PLAN)."""
        store = self.store()
        campaign = await store.create(GUILD, "Frostmaiden", DM)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO npc_notes (guild_id, campaign_id, entry_id, user_id, name, body,"
                " updated_at, orphaned_at)"
                " VALUES (%s, %s, 'e', %s, '', 'Secret thoughts', 5, NULL)",
                (GUILD, campaign.id, DM),
            )
        text = json.dumps(await store.export(GUILD, campaign.id))
        self.assertNotIn("Secret thoughts", text)
        self.assertNotIn("npc_notes", text)
