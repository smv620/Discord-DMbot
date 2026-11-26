"""The bot's side of "Yes, read it" for a Shared story file given on the website (#3694 slice E):
the request is claimed, the plan is checked, and the read is begun with Discord's own calls
(`PlanStore.begin`, `start_plan_read`), which check the person, the switch, the confirmation and
the caps again. The row says only whether the read STARTED. Real Postgres, no website role;
invented names."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory import web_adventure_reads as bot
from dmbot.memory.story_plan import PlanStore
from tests.pg import DatabaseTest

GUILD, DM, OTHER = 5001, 7001, 7002
DOC = "a" * 32


class Reads(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaign = (await CampaignStore(self.db).create(GUILD, "Frost", DM)).id
        self.store = bot.ReadStore(self.db, clock=lambda: 1000.0)
        self.plan = PlanStore(self.db, clock=lambda: 1000.0)
        self.started: list[tuple[int, str, str, int]] = []
        self.reading: set[str] = set()
        self.refused: str | None = None
        await self.sql("UPDATE campaigns SET story_mode = 'watch' WHERE id = %s", self.campaign)
        await self.sql(
            "INSERT INTO shared_confirmations (guild_id, campaign_id, id, user_id, purpose,"
            " fingerprint, confirmed_at, restored)"
            " VALUES (%s, %s, 'c1', %s, 'shared_story', %s, 1, false)",
            GUILD, self.campaign, DM, "0" * 64,
        )  # fmt: skip
        await self.document()

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    async def document(self, doc: str = DOC, chunks: int = 3) -> None:
        await self.sql(
            "INSERT INTO adventure_documents (guild_id, campaign_id, id, file_name, fingerprint,"
            " characters, chunks, added_by, added_at) VALUES (%s, %s, %s, 'Book.pdf', %s, 10, %s,"
            " %s, 1)",
            GUILD, self.campaign, doc, doc * 2, chunks, DM,
        )  # fmt: skip

    async def queue(self, rid: str, *, user: int = DM, doc: str = DOC) -> bot.Item:
        await self.sql(
            "INSERT INTO web_adventure_reads (guild_id, campaign_id, id, user_id, document_id,"
            " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 1, 1)",
            GUILD, self.campaign, rid, user, doc,
        )  # fmt: skip
        item = await self.store.claim_next(GUILD)
        assert item is not None
        return item

    async def refusal(self, guild_id: int, campaign_id: str, user_id: int) -> str | None:
        return self.refused

    def start(self, guild_id: int, campaign_id: str, doc: str, chunk: int) -> None:
        self.started.append((guild_id, campaign_id, doc, chunk))

    async def go(self, item: bot.Item) -> str:
        return await bot.run_one(
            self.plan,
            self.store,
            self.refusal,
            self.start,
            lambda guild_id, doc: doc in self.reading,
            GUILD,
            item,
        )

    async def row(self, rid: str) -> tuple[str, str]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state, error FROM web_adventure_reads WHERE id = %s", (rid,)
            )
            row = await cur.fetchone()
        assert row is not None
        return row["state"], row["error"]

    async def test_a_read_is_begun_as_discords_button_does_it(self) -> None:
        item = await self.queue("1" * 32)
        self.assertEqual(await self.go(item), "")
        self.assertEqual(await self.row("1" * 32), ("done", ""))
        self.assertEqual(self.started, [(GUILD, self.campaign, DOC, 0)])
        state = (await self.plan.states(GUILD, self.campaign))[DOC]
        self.assertEqual((state.status, state.total_chunks), ("reading", 3))

    async def test_a_plan_that_does_not_allow_it_starts_nothing(self) -> None:
        self.refused = "Reading your adventure isn't available for this campaign."
        self.assertEqual(await self.go(await self.queue("2" * 32)), "plan")
        self.assertEqual(await self.row("2" * 32), ("failed", "plan"))
        self.assertEqual(self.started, [])
        self.assertEqual(await self.plan.states(GUILD, self.campaign), {})

    async def test_a_file_already_being_read_is_busy(self) -> None:
        self.reading.add(DOC)
        self.assertEqual(await self.go(await self.queue("3" * 32)), "busy")
        self.assertEqual(self.started, [])

    async def test_the_store_checks_the_person_and_the_switch_again(self) -> None:
        self.assertEqual(await self.go(await self.queue("4" * 32, user=OTHER)), "not_yours")
        await self.sql("UPDATE campaigns SET story_mode = 'off' WHERE id = %s", self.campaign)
        self.assertEqual(await self.go(await self.queue("5" * 32)), "not_on")
        self.assertEqual(self.started, [])

    async def test_a_file_that_is_gone_or_has_no_text_or_is_read_says_so(self) -> None:
        self.assertEqual(await self.go(await self.queue("6" * 32, doc="b" * 32)), "gone")
        await self.document("c" * 32, chunks=0)
        self.assertEqual(await self.go(await self.queue("7" * 32, doc="c" * 32)), "not_readable")
        await self.sql(
            "INSERT INTO adventure_reads (guild_id, campaign_id, document_id, status, next_chunk,"
            " total_chunks, started_by, started_at, updated_at)"
            " VALUES (%s, %s, %s, 'done', 3, 3, %s, 1, 1)",
            GUILD, self.campaign, DOC, DM,
        )  # fmt: skip
        self.assertEqual(await self.go(await self.queue("8" * 32)), "already_read")

    async def test_only_the_current_claim_finishes_a_row_and_the_sweep_drops_old_ones(
        self,
    ) -> None:
        item = await self.queue("9" * 32)
        stale = bot.Item(item.id, item.campaign_id, item.user_id, item.document_id, 1)
        self.assertFalse(await self.store.finish(GUILD, stale, ""))
        self.assertIsNone(await self.store.claim_next(GUILD))
        late = bot.ReadStore(self.db, clock=lambda: 10.0**9)
        self.assertEqual(await late.sweep(GUILD), 1)

    async def test_a_bug_ends_the_row_as_failed_without_quoting_anything(self) -> None:
        async def broken(guild_id: int, campaign_id: str, user_id: int) -> str | None:
            raise RuntimeError("the words of the book")

        item = await self.queue("a" * 32)
        with self.assertLogs("dmbot.memory.web_adventure_reads", "WARNING") as logged:
            error = await bot.run_one(
                self.plan, self.store, broken, self.start, lambda g, d: False, GUILD, item
            )
        self.assertEqual(error, "failed")
        self.assertNotIn("the words of the book", "".join(logged.output))
