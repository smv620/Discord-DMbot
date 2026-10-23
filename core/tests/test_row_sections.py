"""The story-notes backup section (#2552): rows come back whole in a restored campaign, from the
production sections, and a damaged file is refused in plain words."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.bot import register_backup_sections
from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.row_sections import STORY_NOTES
from dmbot.memory.models import new_id
from tests.pg import DatabaseTest

GUILD, OTHER, DM, SPEAKER = 111, 222, 9, 31


class StoryNotes(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        register_backup_sections(
            self.store,
            forget_audio=lambda g, c: None,
            consenting=cast(Callable[[int], Awaitable[frozenset[int]]], AsyncMock()),
        )
        self.campaign = await self.store.create(GUILD, "Frostmaiden", DM)

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    async def seed(self) -> None:
        g, c = GUILD, self.campaign.id
        a, b = sorted((new_id(), new_id()))
        for key, name in ((a, "Aaa"), (b, "Bbb")):
            await self.sql(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at) VALUES (%s, %s, %s, 'npc', %s, '', 'confirmed',"
                " 'dm', 1)",
                g, c, key, name,
            )  # fmt: skip
        await self.sql(
            "INSERT INTO npc_notes (guild_id, campaign_id, entry_id, user_id, name, body,"
            " updated_at, orphaned_at) VALUES (%s, %s, %s, %s, '', 'Likes cheese', 5, NULL)",
            g, c, a, DM,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO npc_roster (guild_id, campaign_id, entry_id, view, updated_at)"
            " VALUES (%s, %s, %s, '{\"x\": 1}', 1)",
            g, c, a,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO backstory_links (guild_id, campaign_id, subject_id, predicate, object_id,"
            " quote, secret, detail, truth, created_at)"
            " VALUES (%s, %s, %s, 'knows', %s, 'q', false, '', true, 3)",
            g, c, a, b,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO name_later (guild_id, campaign_id, entity_id, created_at)"
            " VALUES (%s, %s, %s, 4)",
            g, c, a,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO name_not_same (guild_id, campaign_id, a_id, b_id, decided_by, created_at)"
            " VALUES (%s, %s, %s, %s, %s, 4)",
            g, c, a, b, DM,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO said_as_suggestions (guild_id, campaign_id, entity_id, heard, times,"
            " created_at) VALUES (%s, %s, %s, 'Aah', 2, 4)",
            g, c, a,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO recap_name_checks (guild_id, campaign_id, session_at, entity_id)"
            " VALUES (%s, %s, 100, %s)",
            g, c, a,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO secret_exposures (guild_id, campaign_id, session_started_at, kind,"
            " spelling, times, created_at) VALUES (%s, %s, 100, 'name', 'Aaa', 2, 4)",
            g, c,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO house_rules_file (guild_id, campaign_id, link, ignored, set_by, set_at)"
            " VALUES (%s, %s, 'https://example.org/rules', NULL, %s, 5)",
            g, c, DM,
        )  # fmt: skip

    async def rows(self, guild: int, campaign_id: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        async with self.db.guild(guild) as conn:
            for table in STORY_NOTES.tables:
                cur = await conn.execute(
                    f"SELECT (to_jsonb(t) - 'guild_id' - 'campaign_id' - 'id')::text AS r"
                    f" FROM {table.name} t WHERE campaign_id = %s ORDER BY 1",
                    (campaign_id,),
                )
                out[table.name] = [str(r["r"]) for r in await cur.fetchall()]
        return out

    async def test_every_row_comes_back_in_a_restored_campaign(self) -> None:
        await self.seed()
        before = await self.rows(GUILD, self.campaign.id)
        self.assertTrue(all(before[t.name] for t in STORY_NOTES.tables), before)
        data = await self.store.export(GUILD, self.campaign.id)
        restored = await self.store.import_backup(OTHER, data, DM)
        self.assertEqual(await self.rows(OTHER, restored.id), before)

    async def test_a_backup_from_before_the_section_still_loads_with_none(self) -> None:
        data = await self.store.export(GUILD, self.campaign.id)
        del data["sections"][STORY_NOTES.name]
        restored = await self.store.import_backup(OTHER, data, DM)
        self.assertTrue(all(not r for r in (await self.rows(OTHER, restored.id)).values()))

    async def test_deleting_the_campaign_removes_them(self) -> None:
        await self.seed()
        await self.store.delete(GUILD, self.campaign.id)
        self.assertTrue(all(not r for r in (await self.rows(GUILD, self.campaign.id)).values()))

    async def test_a_damaged_file_is_refused_in_plain_words(self) -> None:
        await self.seed()
        data = await self.store.export(GUILD, self.campaign.id)
        good = data["sections"][STORY_NOTES.name]
        for bad in (
            [{"table": "campaigns", "name": "x"}],  # not one of this section's tables
            [{"table": "npc_notes", "guild_id": 5}],  # the restore's column, not the file's
            [{"table": "npc_notes", "nothing": 1}],  # a column the table does not have
            [{"table": "npc_notes", "entry_id": "x"}],  # a required column missing
            [dict(good[0], user_id="not a number")],  # the wrong type
            "text",
        ):
            data["sections"][STORY_NOTES.name] = bad
            with self.assertRaisesRegex(CampaignError, "damaged"):
                await self.store.import_backup(OTHER, data, DM)
        self.assertEqual(await self.campaigns_in(OTHER), [])

    async def campaigns_in(self, guild: int) -> list[Any]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute("SELECT id FROM campaigns")
            return list(await cur.fetchall())

    async def test_a_table_the_section_does_not_name_is_refused(self) -> None:
        data = await self.store.export(GUILD, self.campaign.id)
        data["sections"][STORY_NOTES.name] = [
            {"table": "memory_entities", "id": new_id(), "name": "Sneaky"}
        ]
        with self.assertRaisesRegex(CampaignError, "damaged"):
            await self.store.import_backup(OTHER, data, DM)
