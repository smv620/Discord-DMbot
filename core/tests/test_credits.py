"""AI credits (#4005, part 1): the ledger and the charge, with no limit. A call inside a job that
prepares material is charged exactly what the service reported; a call outside one is free; a
listed server is metered but never touches an owner's month; two campaigns of one owner charging at
once lose nothing; and every job that prepares material opens the scope. Needs Postgres for the
ledger tests (skipped without it, like the other store tests)."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import unittest
from unittest.mock import AsyncMock, patch

from dmbot import ai, credits, entitlements
from dmbot.ai import Reply
from dmbot.campaigns.store import CampaignStore
from dmbot.memory.story_plan import micro_usd
from tests.pg import DatabaseTest

OWNER, OTHER = 11, 22
GUILD_A, GUILD_B = 111, 222
NOW = 1_773_996_000.0  # (a day in March 2026)
MODEL = "claude-sonnet-5-5"


def reply(inp: int = 1000, out: int = 500, *, read: int = 0, write: int = 0) -> Reply:
    return Reply(
        "words that are never stored",
        cut=False,
        input_tokens=inp,
        output_tokens=out,
        model=MODEL,
        cache_read_tokens=read,
        cache_write_tokens=write,
    )


class Conversion(unittest.TestCase):
    def test_a_credit_is_a_thousand_millionths_of_a_dollar_rounded_up(self) -> None:
        self.assertEqual([credits.of_micro(n) for n in (0, 1, 1000, 1001, 2500)], [0, 1, 1, 2, 3])

    def test_a_negative_cost_is_nothing(self) -> None:
        self.assertEqual(credits.of_micro(-5), 0)


class EveryPreparingJobOpensTheScope(unittest.TestCase):
    """A new job that forgets `credits.preparing` would be free, so the list is checked."""

    def test_each_listed_job_opens_a_scope(self) -> None:
        self.assertGreaterEqual(len(credits.PREPARING_JOBS), 4)
        for module, dotted in credits.PREPARING_JOBS:
            target = importlib.import_module(module)
            for part in dotted.split("."):
                target = getattr(target, part)
            self.assertIn("credits.preparing(", inspect.getsource(target), f"{module}.{dotted}")


class Ledger(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        self.addCleanup(entitlements.configure_free_guilds, ())
        self.addCleanup(credits.configure, None, None)
        self.campaigns = CampaignStore(self.db)
        self.a = await self.campaigns.create(GUILD_A, "Frostmaiden", OWNER)
        self.a2 = await self.campaigns.create(GUILD_A, "Second", OWNER)
        self.b = await self.campaigns.create(GUILD_B, "Another", OTHER)
        owners = {self.a.id: OWNER, self.a2.id: OWNER, self.b.id: OTHER}

        async def owner_of(_guild: int, campaign_id: str) -> int | None:
            return owners.get(campaign_id)

        credits.configure(self.db, owner_of, lambda: NOW)

    async def rows(self, guild: int) -> list[dict[str, object]]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, owner_user_id, job_kind, feature, credits FROM ai_credit_spend"
                " ORDER BY at_ms, id"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def month_total(self, owner: int) -> int:
        async with self.db.user(owner) as conn:
            cur = await conn.execute("SELECT COALESCE(SUM(credits), 0) AS n FROM owner_credits")
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def test_a_call_outside_a_job_is_free(self) -> None:
        self.assertEqual(await credits.record(reply(), "names"), 0)
        self.assertEqual(await self.rows(GUILD_A), [])

    async def test_a_call_inside_a_job_is_charged_exactly_its_tokens(self) -> None:
        r = reply(20_000, 3_000, read=10_000, write=5_000)
        want = credits.of_micro(micro_usd(MODEL, 20_000, 3_000, 10_000, 5_000))
        with credits.preparing(GUILD_A, self.a.id, "lore"):
            self.assertEqual(await credits.record(r, "names"), want)
        (row,) = await self.rows(GUILD_A)
        self.assertEqual(
            row,
            {"campaign_id": self.a.id, "owner_user_id": OWNER, "job_kind": "lore",
             "feature": "names", "credits": want},
        )  # fmt: skip
        self.assertEqual(await self.month_total(OWNER), want)

    async def test_a_cut_answer_is_charged_for_the_tokens_it_used(self) -> None:
        cut = Reply("x", cut=True, input_tokens=5_000, output_tokens=16_000, model=MODEL)
        with credits.preparing(GUILD_A, self.a.id, "import"):
            got = await credits.record(cut, "extractor")
        self.assertEqual(got, credits.of_micro(micro_usd(MODEL, 5_000, 16_000)))
        self.assertGreater(got, 0)

    async def test_the_client_charges_each_answered_call_inside_a_job(self) -> None:
        client = ai.AnthropicClient("key-not-used")
        with patch.object(ai.AnthropicClient, "_complete", AsyncMock(return_value=reply())):
            await client.complete("s", "t", tier=ai.AIModelTier.FAST, feature="names")
            self.assertEqual(await self.rows(GUILD_A), [])  # outside a job: free
            with credits.preparing(GUILD_A, self.a.id, "lore"):
                await client.complete("s", "t", tier=ai.AIModelTier.FAST, feature="names")
        self.assertEqual(len(await self.rows(GUILD_A)), 1)

    async def test_a_listed_server_is_metered_but_not_added_to_the_owners_month(self) -> None:
        entitlements.configure_free_guilds([GUILD_A])
        with credits.preparing(GUILD_A, self.a.id, "lore"):
            await credits.record(reply(), "names")
        with credits.preparing(GUILD_B, self.b.id, "lore"):
            await credits.record(reply(), "names")  # (a different server, not listed)
        self.assertEqual(len(await self.rows(GUILD_A)), 1)
        self.assertEqual(await self.month_total(OWNER), 0)
        self.assertGreater(await self.month_total(OTHER), 0)

    async def test_a_listed_person_is_metered_but_not_added_to_their_month(self) -> None:
        entitlements.configure_free_users([OWNER])
        with credits.preparing(GUILD_A, self.a.id, "lore"):
            await credits.record(reply(), "names")
        self.assertEqual(len(await self.rows(GUILD_A)), 1)
        self.assertEqual(await self.month_total(OWNER), 0)

    async def test_two_campaigns_of_one_owner_charging_at_once_lose_nothing(self) -> None:
        async def charge(campaign_id: str) -> int:
            with credits.preparing(GUILD_A, campaign_id, "lore"):
                return await credits.record(reply(), "names")

        got = await asyncio.gather(*(charge(c) for c in [self.a.id, self.a2.id] * 5))
        self.assertGreater(min(got), 0)
        self.assertEqual(await self.month_total(OWNER), sum(got))
        self.assertEqual(len(await self.rows(GUILD_A)), 10)

    async def test_a_campaign_nobody_owns_is_kept_but_never_added_to_a_month(self) -> None:
        credits.configure(self.db, AsyncMock(return_value=None), lambda: NOW)
        with credits.preparing(GUILD_A, self.a.id, "lore"):
            self.assertGreater(await credits.record(reply(), None), 0)
        (row,) = await self.rows(GUILD_A)
        self.assertEqual((row["owner_user_id"], row["feature"]), (0, "-"))
        self.assertEqual(await self.month_total(OWNER), 0)

    async def test_a_failed_write_never_fails_the_work(self) -> None:
        credits.configure(self.db, AsyncMock(side_effect=OSError("down")), lambda: NOW)
        with (
            credits.preparing(GUILD_A, self.a.id, "lore"),
            self.assertLogs("dmbot.credits", level="WARNING") as logged,
        ):
            self.assertEqual(await credits.record(reply(), "names"), 0)
        self.assertEqual(
            [r.getMessage() for r in logged.records],
            ["AI credits: a charge could not be written (OSError)"],
        )

    async def test_a_hung_write_costs_one_warning_not_the_answer(self) -> None:
        async def hangs(_guild: int, _campaign: str) -> int | None:
            await asyncio.sleep(60)
            return OWNER

        credits.configure(self.db, hangs, lambda: NOW)
        with (
            patch.object(credits, "WRITE_TIMEOUT_S", 0.05),
            credits.preparing(GUILD_A, self.a.id, "lore"),
            self.assertLogs("dmbot.credits", level="WARNING") as logged,
        ):
            self.assertEqual(await credits.record(reply(), "names"), 0)
        self.assertEqual(
            [r.getMessage() for r in logged.records],
            ["AI credits: a charge could not be written (TimeoutError)"],
        )

    async def test_each_servers_rows_are_its_own(self) -> None:
        with credits.preparing(GUILD_B, self.b.id, "lore"):
            await credits.record(reply(), "names")
        self.assertEqual(await self.rows(GUILD_A), [])
        self.assertEqual(len(await self.rows(GUILD_B)), 1)

    async def test_the_log_line_has_counts_only(self) -> None:
        with (
            credits.preparing(GUILD_A, self.a.id, "lore"),
            self.assertLogs("dmbot.credits", level="INFO") as logged,
        ):
            n = await credits.record(reply(), "names")
        (line,) = [r.getMessage() for r in logged.records]
        self.assertEqual(line, f"AI credits: job=lore feature=names credits={n}")
        self.assertNotIn("words that are never stored", line)

    async def test_a_scope_ends_with_its_block(self) -> None:
        with credits.preparing(GUILD_A, self.a.id, "lore"):
            self.assertIsNotNone(credits.current())
        self.assertIsNone(credits.current())


if __name__ == "__main__":
    unittest.main()
