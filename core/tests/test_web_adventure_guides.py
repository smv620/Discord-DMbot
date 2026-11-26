"""The bot's side of Guide me on the website (#3694 slice F): the queue is claimed, every job is
checked again, the answer is made by the function the bot passes in (Discord's own), a pick or a
clear is written first, and the book's names are kept only on a finished row and never logged. Real
Postgres, no website role; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory import web_adventure_guides as bot
from dmbot.memory.models import MemoryRuleError
from tests.pg import DatabaseTest

GUILD, DM = 5001, 7001
DOC, CHAPTER = "a" * 32, "b" * 32
ANSWER = {
    "text": "🧭 Probably **The Harbour**",
    "picked": False,
    "chapter": {"id": CHAPTER, "name": "The Harbour"},
    "chapters": [{"id": CHAPTER, "name": "The Harbour"}],
}


class Guides(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaign = (await CampaignStore(self.db).create(GUILD, "Frost", DM)).id
        self.store = bot.GuideStore(self.db, clock=lambda: 1000.0)
        self.calls: list[tuple[Any, ...]] = []
        self.checked: str | None = None
        self.composed: dict[str, Any] | None = ANSWER

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    def host(self, *, pick_error: Exception | None = None) -> bot.Host:
        async def check(guild_id: int, campaign_id: str, user_id: int) -> str | None:
            return self.checked

        async def compose(guild_id: int, campaign_id: str, doc: str) -> dict[str, Any] | None:
            self.calls.append(("compose", doc))
            return self.composed

        async def pick(
            guild_id: int, campaign_id: str, doc: str, chapter: str, user_id: int
        ) -> object:
            if pick_error is not None:
                raise pick_error
            self.calls.append(("pick", doc, chapter, user_id))
            return 1

        async def clear(guild_id: int, campaign_id: str, doc: str) -> object:
            self.calls.append(("clear", doc))
            return 1

        return bot.Host(check, compose, pick, clear)

    async def queue(self, rid: str, job: str = "read", chapter: str = "") -> bot.Item:
        await self.sql(
            "INSERT INTO web_adventure_guides (guild_id, campaign_id, id, user_id, job,"
            " document_id, chapter_id, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, 1, 1)",
            GUILD, self.campaign, rid, DM, job, DOC, chapter,
        )  # fmt: skip
        item = await self.store.claim_next(GUILD)
        assert item is not None
        return item

    async def row(self, rid: str) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state, error, answer FROM web_adventure_guides WHERE id = %s", (rid,)
            )
            row = await cur.fetchone()
        assert row is not None
        return dict(row)

    async def test_a_read_keeps_the_answer_on_the_finished_row(self) -> None:
        item = await self.queue("1" * 32)
        self.assertEqual(await bot.run_one(self.host(), self.store, GUILD, item), "")
        got = await self.row("1" * 32)
        self.assertEqual((got["state"], got["error"], got["answer"]), ("done", "", ANSWER))
        self.assertEqual(self.calls, [("compose", DOC)])

    async def test_a_pick_is_written_first_and_then_answered(self) -> None:
        item = await self.queue("2" * 32, "pick", CHAPTER)
        self.assertEqual(await bot.run_one(self.host(), self.store, GUILD, item), "")
        self.assertEqual(self.calls, [("pick", DOC, CHAPTER, DM), ("compose", DOC)])

    async def test_a_clear_goes_back_to_the_guess_and_answers(self) -> None:
        item = await self.queue("3" * 32, "clear")
        self.assertEqual(await bot.run_one(self.host(), self.store, GUILD, item), "")
        self.assertEqual(self.calls, [("clear", DOC), ("compose", DOC)])

    async def test_a_refused_check_writes_and_keeps_nothing(self) -> None:
        for n, why in enumerate(("not_yours", "not_guide", "plan")):
            self.calls.clear()
            self.checked = why
            rid = f"{n + 8:x}" * 32
            item = await self.queue(rid, "pick", CHAPTER)
            self.assertEqual(await bot.run_one(self.host(), self.store, GUILD, item), why)
            got = await self.row(rid)
            self.assertEqual((got["state"], got["answer"]), ("failed", {}))
            self.assertEqual(self.calls, [])

    async def test_a_book_with_no_chapters_says_so(self) -> None:
        self.composed = None
        item = await self.queue("4" * 32)
        self.assertEqual(await bot.run_one(self.host(), self.store, GUILD, item), "no_chapters")
        self.assertEqual((await self.row("4" * 32))["answer"], {})

    async def test_a_chapter_that_left_the_book_is_gone(self) -> None:
        item = await self.queue("5" * 32, "pick", CHAPTER)
        host = self.host(pick_error=MemoryRuleError("That chapter isn't in your adventure."))
        self.assertEqual(await bot.run_one(host, self.store, GUILD, item), "gone")

    async def test_a_bug_ends_the_row_as_failed_without_quoting_the_book(self) -> None:
        item = await self.queue("6" * 32, "pick", CHAPTER)
        host = self.host(pick_error=RuntimeError("the silver key is under the step"))
        with self.assertLogs("dmbot.memory.web_adventure_guides", "WARNING") as logged:
            self.assertEqual(await bot.run_one(host, self.store, GUILD, item), "failed")
        self.assertNotIn("silver key", "".join(logged.output))

    async def test_only_the_current_claim_finishes_a_row_and_the_sweep_drops_old_ones(self) -> None:
        item = await self.queue("7" * 32)
        stale = bot.Item(item.id, item.campaign_id, item.user_id, "read", DOC, "", 1)
        self.assertFalse(await self.store.finish(GUILD, stale, "", ANSWER))
        self.assertIsNone(await self.store.claim_next(GUILD))
        late = bot.GuideStore(self.db, clock=lambda: 10.0**9)
        self.assertEqual(await late.sweep(GUILD), 1)
