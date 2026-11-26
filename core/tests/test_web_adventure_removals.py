"""The bot's side of removing a Shared story file from the website (#3694 slice C): the queue is
claimed in order, the removal is the call Discord's 🗑 makes, and a person who stopped being a DM is
refused. Real Postgres, no website role; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory import web_adventure_removals as bot
from dmbot.memory.adventure import AdventureStore
from tests.pg import DatabaseTest

GUILD, DM, OTHER = 5001, 7001, 7002
DOC = "a" * 32


class Removals(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaign = (await CampaignStore(self.db).create(GUILD, "Frost", DM)).id
        self.store = bot.RemovalStore(self.db, clock=lambda: 1000.0)
        self.adventure = AdventureStore(self.db)

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    async def document(self, doc: str = DOC) -> None:
        await self.sql(
            "INSERT INTO adventure_documents (guild_id, campaign_id, id, file_name, fingerprint,"
            " characters, chunks, added_by, added_at) VALUES (%s, %s, %s, 'Book.pdf', %s, 10, 1,"
            " %s, 1)",
            GUILD, self.campaign, doc, doc * 2, DM,
        )  # fmt: skip

    async def queue(self, rid: str, user: int = DM, doc: str = DOC) -> None:
        await self.sql(
            "INSERT INTO web_adventure_removals (guild_id, campaign_id, id, user_id, document_id,"
            " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 1, 1)",
            GUILD, self.campaign, rid, user, doc,
        )  # fmt: skip

    async def state(self, rid: str) -> tuple[str, str]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state, error FROM web_adventure_removals WHERE id = %s", (rid,)
            )
            row = await cur.fetchone()
        assert row is not None
        return row["state"], row["error"]

    async def test_a_dm_s_removal_is_done_and_the_file_is_gone(self) -> None:
        await self.document()
        await self.queue("1" * 32)
        item = await self.store.claim_next(GUILD)
        assert item is not None
        self.assertEqual(await bot.run_one(self.adventure, self.store, GUILD, item), "")
        self.assertEqual(await self.state("1" * 32), ("done", ""))
        self.assertEqual(await self.adventure.documents(GUILD, self.campaign), [])

    async def test_a_file_already_gone_counts_as_done(self) -> None:
        await self.queue("2" * 32)
        item = await self.store.claim_next(GUILD)
        assert item is not None
        self.assertEqual(await bot.run_one(self.adventure, self.store, GUILD, item), "")
        self.assertEqual(await self.state("2" * 32), ("done", ""))

    async def test_someone_who_is_not_a_dm_is_refused_and_the_file_stays(self) -> None:
        await self.document()
        await self.queue("3" * 32, user=OTHER)
        item = await self.store.claim_next(GUILD)
        assert item is not None
        self.assertEqual(await bot.run_one(self.adventure, self.store, GUILD, item), "not_yours")
        self.assertEqual(await self.state("3" * 32), ("failed", "not_yours"))
        self.assertEqual(len(await self.adventure.documents(GUILD, self.campaign)), 1)

    async def test_claims_go_in_order_and_a_taken_one_is_not_taken_twice(self) -> None:
        await self.queue("4" * 32, doc="b" * 32)
        await self.queue("5" * 32, doc="c" * 32)
        first = await self.store.claim_next(GUILD)
        second = await self.store.claim_next(GUILD)
        assert first is not None and second is not None
        self.assertEqual((first.id, second.id), ("4" * 32, "5" * 32))
        self.assertIsNone(await self.store.claim_next(GUILD))

    async def test_only_the_current_claim_can_finish_a_row(self) -> None:
        await self.queue("6" * 32)
        item = await self.store.claim_next(GUILD)
        assert item is not None
        stale = bot.Item(item.id, item.campaign_id, item.user_id, item.document_id, 1)
        self.assertFalse(await self.store.finish(GUILD, stale, ""))
        self.assertTrue(await self.store.finish(GUILD, item, ""))

    async def test_the_sweep_drops_a_day_old_row(self) -> None:
        await self.queue("7" * 32)
        late = bot.RemovalStore(self.db, clock=lambda: 10.0**9)
        self.assertEqual(await late.sweep(GUILD), 1)
