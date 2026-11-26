"""The bot's side of Match to files on the website (#3694 slice G1): the queue is claimed, every job
is checked again, a decide is one call for each pair and stops at the first refusal, an undo takes
the batches back newest first, and the names are kept only on a finished row and never logged.
Real Postgres, no website role; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory import web_adventure_matches as bot
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from tests.pg import DatabaseTest

GUILD, DM = 5001, 7001
PLAN, ENTITY = "a" * 32, "b" * 32
PAIR = {
    "planId": PLAN,
    "planName": "Captain Varn",
    "planKind": "person",
    "entityId": ENTITY,
    "entityName": "Varn",
}


class Matches(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaign = (await CampaignStore(self.db).create(GUILD, "Frost", DM)).id
        self.store = bot.MatchStore(self.db, clock=lambda: 1000.0)
        self.calls: list[tuple[Any, ...]] = []
        self.checked: str | None = None
        self.refuse_at: int | None = None  # the pick (by index) the store will not take
        self.late_at: set[int] = set()  # the batches that are too late to undo
        self.changed_at: set[int] = set()

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    def host(self) -> bot.Host:
        async def check(guild_id: int, campaign_id: str, user_id: int) -> str | None:
            return self.checked

        async def suggest(guild_id: int, campaign_id: str) -> list[dict[str, str]]:
            self.calls.append(("suggest",))
            return [PAIR]

        async def decide(
            guild_id: int, campaign_id: str, pick: tuple[str, str, str], user_id: int
        ) -> int | None:
            number = sum(1 for c in self.calls if c[0] == "decide")
            if self.refuse_at == number:
                raise MemoryRuleError("That name is already linked to another.")
            self.calls.append(("decide", pick, user_id))
            return 100 + number

        async def undo(guild_id: int, campaign_id: str, batch: int) -> object:
            if batch in self.late_at:
                raise TooLateToUndo("too late")
            if batch in self.changed_at:
                raise MemoryRuleError("changed")
            self.calls.append(("undo", batch))
            return None

        return bot.Host(check, suggest, decide, undo)

    async def queue(
        self,
        rid: str,
        job: str,
        picks: list[list[str]] | None = None,
        batches: list[int] | None = None,
    ) -> bot.Item:
        from psycopg.types.json import Jsonb

        await self.sql(
            "INSERT INTO web_adventure_matches (guild_id, campaign_id, id, user_id, job, picks,"
            " source_id, batches, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, 1)",
            GUILD, self.campaign, rid, DM, job, Jsonb(picks or []),
            "f" * 32 if job == "undo" else "", batches or [],
        )  # fmt: skip
        item = await self.store.claim_next(GUILD)
        assert item is not None
        return item

    async def row(self, rid: str) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state, error, answer, batches FROM web_adventure_matches WHERE id = %s",
                (rid,),
            )
            row = await cur.fetchone()
        assert row is not None
        return dict(row)

    def picks(self, n: int) -> list[list[str]]:
        return [[f"{i:032x}", ENTITY, "same" if i % 2 else "not_same"] for i in range(n)]

    async def test_a_suggest_keeps_the_pairs_on_the_finished_row(self) -> None:
        item = await self.queue("1" * 32, "suggest")
        self.assertEqual(await bot.run_one(self.host(), self.store, GUILD, item), "")
        got = await self.row("1" * 32)
        self.assertEqual((got["state"], got["answer"]), ("done", {"pairs": [PAIR]}))

    async def test_a_decide_is_one_call_for_each_pair_and_keeps_each_batch(self) -> None:
        item = await self.queue("2" * 32, "decide", self.picks(3))
        self.assertEqual(await bot.run_one(self.host(), self.store, GUILD, item), "")
        got = await self.row("2" * 32)
        self.assertEqual(got["answer"], {"written": 3, "refusal": ""})
        self.assertEqual(got["batches"], [100, 101, 102])
        self.assertEqual([c[0] for c in self.calls], ["decide"] * 3)
        self.assertEqual(self.calls[0][2], DM)

    async def test_a_decide_stops_at_the_first_refusal_and_says_what_was_written(self) -> None:
        self.refuse_at = 1
        item = await self.queue("3" * 32, "decide", self.picks(3))
        self.assertEqual(await bot.run_one(self.host(), self.store, GUILD, item), "")
        got = await self.row("3" * 32)
        self.assertEqual(
            got["answer"], {"written": 1, "refusal": "That name is already linked to another."}
        )
        self.assertEqual(got["batches"], [100])  # (the one written can still be undone)
        self.assertEqual(len(self.calls), 1)

    async def test_an_undo_takes_the_batches_back_newest_first(self) -> None:
        item = await self.queue("4" * 32, "undo", batches=[100, 101, 102])
        self.assertEqual(await bot.run_one(self.host(), self.store, GUILD, item), "")
        self.assertEqual(self.calls, [("undo", 102), ("undo", 101), ("undo", 100)])
        self.assertEqual((await self.row("4" * 32))["answer"], {"undone": 3, "note": ""})

    async def test_an_undo_stops_at_one_that_is_too_late_or_changed_since(self) -> None:
        self.late_at = {101}
        item = await self.queue("5" * 32, "undo", batches=[100, 101, 102])
        await bot.run_one(self.host(), self.store, GUILD, item)
        self.assertEqual((await self.row("5" * 32))["answer"], {"undone": 1, "note": "too_late"})
        self.late_at, self.changed_at = set(), {200}
        item = await self.queue("6" * 32, "undo", batches=[200])
        await bot.run_one(self.host(), self.store, GUILD, item)
        self.assertEqual(
            (await self.row("6" * 32))["answer"], {"undone": 0, "note": "changed_since"}
        )

    async def test_a_refused_check_does_nothing_and_keeps_no_answer(self) -> None:
        self.checked = "not_on"
        item = await self.queue("7" * 32, "decide", self.picks(2))
        self.assertEqual(await bot.run_one(self.host(), self.store, GUILD, item), "not_on")
        got = await self.row("7" * 32)
        self.assertEqual((got["state"], got["answer"], got["batches"]), ("failed", {}, []))
        self.assertEqual(self.calls, [])

    async def test_a_bug_ends_the_row_as_failed_without_quoting_a_name(self) -> None:
        async def broken(guild_id: int, campaign_id: str) -> list[dict[str, str]]:
            raise RuntimeError("Captain Varn hides the silver key")

        host = self.host()
        host = bot.Host(host.check, broken, host.decide, host.undo)
        item = await self.queue("8" * 32, "suggest")
        with self.assertLogs("dmbot.memory.web_adventure_matches", "WARNING") as logged:
            self.assertEqual(await bot.run_one(host, self.store, GUILD, item), "failed")
        self.assertNotIn("Varn", "".join(logged.output))

    async def test_only_the_current_claim_finishes_a_row_and_the_sweep_drops_old_ones(self) -> None:
        item = await self.queue("9" * 32, "suggest")
        stale = bot.Item(item.id, item.campaign_id, item.user_id, "suggest", (), (), 1)
        self.assertFalse(await self.store.finish(GUILD, stale, ""))
        self.assertIsNone(await self.store.claim_next(GUILD))
        late = bot.MatchStore(self.db, clock=lambda: 10.0**9)
        self.assertEqual(await late.sweep(GUILD), 1)
