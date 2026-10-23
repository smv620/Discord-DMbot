"""Every table with a `campaign_id` is carried by a backup section or on a written list (#2552).

A table that is on neither is a campaign that loses data when it is restored, moved or deleted.
The list is `campaigns/tables.py`; the sections are the production ones from
`register_backup_sections`, so a new section or a new table cannot slip past this.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock

from dmbot.bot import register_backup_sections
from dmbot.campaigns import CampaignStore
from dmbot.campaigns.tables import NOT_IN_BACKUP, OWED_TO_BACKUP, SECTION_TABLES, backed_up
from tests.pg import DatabaseTest


class CampaignTables(DatabaseTest):
    async def campaign_tables(self) -> set[str]:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT table_name FROM information_schema.columns"
                " WHERE table_schema = current_schema() AND column_name = 'campaign_id'"
            )
            return {str(r["table_name"]) for r in await cur.fetchall()}

    async def test_every_campaign_table_is_carried_or_on_the_written_list(self) -> None:
        listed = backed_up() | set(NOT_IN_BACKUP) | set(OWED_TO_BACKUP)
        missing = sorted(await self.campaign_tables() - listed)
        self.assertEqual(missing, [], "add each to a backup section or to NOT_IN_BACKUP, with why")

    async def test_the_lists_name_real_tables_and_do_not_overlap(self) -> None:
        real = await self.campaign_tables()
        carried = backed_up()
        self.assertEqual(sorted(carried - real), [])
        self.assertEqual(sorted(set(NOT_IN_BACKUP) - real), [])
        self.assertEqual(sorted(set(OWED_TO_BACKUP) - real), [])
        self.assertEqual(sorted(carried & set(NOT_IN_BACKUP)), [])
        self.assertEqual(sorted(carried & set(OWED_TO_BACKUP)), [])
        self.assertEqual(sorted(set(NOT_IN_BACKUP) & set(OWED_TO_BACKUP)), [])

    async def test_the_list_names_exactly_the_production_sections(self) -> None:
        store = CampaignStore(self.db)
        register_backup_sections(
            store,
            forget_audio=lambda g, c: None,
            consenting=cast(Callable[[int], Awaitable[frozenset[int]]], AsyncMock()),
        )
        self.assertEqual(set(store._sections), set(SECTION_TABLES))
