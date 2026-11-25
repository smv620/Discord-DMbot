"""What the paid calls cost, as business totals (#3672, migration 0625): each call adds to a
total by month, service, model, feature, plan and source; the plan is the campaign owner's at the
time; a sample run, a refused call and an unpriced model are told apart; the table holds no word
of anything; and a failing write never fails the call. Invented ids. Needs Postgres."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dmbot import entitlements, logs, service_spend
from dmbot.ai import Reply
from dmbot.memory.story_plan import micro_usd
from tests.test_access_grants import WORKS, AccessTest
from tests.test_web_api import THURSDAY

OWNER_ID, STRANGER = 71, 72
CAMPAIGN = "c" * 32
NOW = 1_700_000_000.0  # 2023-11-14


class Spend(AccessTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.owners: dict[str, int | None] = {CAMPAIGN: OWNER_ID}

        async def owner_of(_guild: int, campaign: str) -> int | None:
            return self.owners.get(campaign)

        service_spend.reset_for_tests()
        service_spend.configure(self.db, owner_of, lambda: NOW)
        self.addCleanup(service_spend.configure, None, None)
        self.addCleanup(service_spend.reset_for_tests)

    def answer(self, model: str = "claude-sonnet-5-5", **kw: Any) -> Reply:
        return Reply("words", cut=False, model=model, **{"input_tokens": 1000, **kw})

    async def rows(self) -> list[dict[str, Any]]:
        month = service_spend.month_start(NOW)
        return [dict(r) for r in await service_spend.read_month(self.db, month)]

    def in_campaign(self) -> Any:
        return logs.log_context(guild_id=THURSDAY.id, campaign_id=CAMPAIGN)


class Recording(Spend):
    async def test_an_ai_call_adds_its_tokens_and_cost_under_its_words(self) -> None:
        reply = self.answer(output_tokens=200, cache_write_tokens=400, cache_read_tokens=1000)
        with self.in_campaign():
            service_spend.record_ai(reply, "recap")
        self.assertEqual(await service_spend.flush(), 1)
        (row,) = await self.rows()
        self.assertEqual(
            (row["service"], row["model"], row["feature"], row["source"], row["calls"]),
            ("anthropic", "claude-sonnet-5-5", "recap", "play", 1),
        )
        self.assertEqual(row["units_in"], 1000 + 400 * 5 // 4 + 1000 // 10)  # (as the watch does)
        self.assertEqual(row["units_out"], 200)
        self.assertEqual(row["micro_usd"], micro_usd("claude-sonnet-5-5", 1000, 200, 1000, 400))
        self.assertEqual(row["unpriced"], 0)

    async def test_a_second_flush_adds_to_the_total_and_never_replaces_it(self) -> None:
        for _ in range(2):
            service_spend.record_ai(self.answer(), "cleaner")
            await service_spend.flush()
        service_spend.record_ai(self.answer(), "cleaner")
        await service_spend.flush()
        (row,) = await self.rows()
        self.assertEqual((row["calls"], row["units_in"]), (3, 3000))

    async def test_a_model_with_no_price_is_recorded_high_and_counted_unpriced(self) -> None:
        service_spend.record_ai(self.answer("gemini-9-test", output_tokens=10), "recap_write")
        await service_spend.flush()
        (row,) = await self.rows()
        self.assertEqual((row["service"], row["unpriced"]), ("gemini", 1))
        self.assertGreater(row["micro_usd"], 0)

    async def test_a_refused_call_is_counted_with_no_cost_and_no_call(self) -> None:
        service_spend.record_refused("sidebar", "fast")
        await service_spend.flush()
        (row,) = await self.rows()
        self.assertEqual(
            (row["feature"], row["refused"], row["calls"], row["micro_usd"]), ("sidebar", 1, 0, 0)
        )

    async def test_a_sample_runs_calls_are_their_own_source(self) -> None:
        service_spend.configure(self.db, None, lambda: NOW, source="sample")
        service_spend.record_ai(self.answer(), "names")
        await service_spend.flush()
        (row,) = await self.rows()
        self.assertEqual(row["source"], "sample")

    async def test_other_services_record_their_units(self) -> None:
        record = service_spend.record
        record("deepgram", "nova-3", service_spend.TRANSCRIBE, units_in=90, micro_usd=6450)
        record("elevenlabs", "v3", service_spend.RECAP_AUDIO, units_in=5000, micro_usd=1500)
        record("voyage", "voyage-3.5", service_spend.SEARCH, units_in=40, micro_usd=2)
        await service_spend.flush()
        got = {r["service"]: (r["units_in"], r["micro_usd"]) for r in await self.rows()}
        self.assertEqual(
            got, {"deepgram": (90, 6450), "elevenlabs": (5000, 1500), "voyage": (40, 2)}
        )

    async def test_an_unknown_service_is_ignored_not_an_error(self) -> None:
        service_spend.record("made-up", "m", "f", units_in=1)
        self.assertEqual(await service_spend.flush(), 0)


class Plans(Spend):
    async def test_the_plan_is_the_campaign_owners_at_the_time(self) -> None:
        await self.give_plan(OWNER_ID, plan="table", **WORKS)
        with self.in_campaign():
            service_spend.record_ai(self.answer(), "names")
        await service_spend.flush()
        (row,) = await self.rows()
        self.assertEqual(row["plan"], "table")

    async def test_an_owner_with_no_plan_is_free_and_a_listed_one_is_listed(self) -> None:
        with self.in_campaign():
            service_spend.record_ai(self.answer(), "names")
        await service_spend.flush()
        entitlements.configure_free_users([OWNER_ID])
        service_spend.reset_for_tests()
        with self.in_campaign():
            service_spend.record_ai(self.answer(), "cleaner")
        await service_spend.flush()
        plans = {r["feature"]: r["plan"] for r in await self.rows()}
        self.assertEqual(plans, {"names": "free", "cleaner": "listed"})

    async def test_a_call_with_no_campaign_or_no_owner_is_unknown(self) -> None:
        service_spend.record_ai(self.answer(), "support")
        self.owners[CAMPAIGN] = None
        with self.in_campaign():
            service_spend.record_ai(self.answer(), "recap")
        await service_spend.flush()
        self.assertEqual({r["plan"] for r in await self.rows()}, {"unknown"})

    async def test_totals_split_by_plan_so_the_page_can_add_them_up(self) -> None:
        await self.give_plan(OWNER_ID, plan="table", **WORKS)
        with self.in_campaign():
            service_spend.record_ai(self.answer(), "names")
        service_spend.record_ai(self.answer(), "names")  # (no campaign)
        await service_spend.flush()
        self.assertEqual({r["plan"] for r in await self.rows()}, {"table", "unknown"})


class Never(Spend):
    async def test_a_failing_write_keeps_the_counts_and_never_raises(self) -> None:
        service_spend.record_ai(self.answer(), "names")

        async def broken(*_a: Any, **_k: Any) -> int:
            raise OSError("down")

        with patch.object(service_spend, "_write", broken):
            self.assertEqual(await service_spend.flush(), 0)  # (no exception)
        service_spend.record_ai(self.answer(), "names")  # (counts added since stay too)
        self.assertEqual(await service_spend.flush(), 1)
        (row,) = await self.rows()
        self.assertEqual(row["calls"], 2)

    async def test_recording_with_no_database_does_nothing_and_does_not_fail(self) -> None:
        service_spend.configure(None, None)
        service_spend.record_ai(self.answer(), "names")
        self.assertEqual(await service_spend.flush(), 0)

    async def test_a_bad_reply_never_fails_the_call(self) -> None:
        service_spend.record_ai(None, "names")  # type: ignore[arg-type]


class NoWords(Spend):
    async def test_the_table_holds_only_counts_and_fixed_words(self) -> None:
        async with self.db.spend_admin() as conn:
            cur = await conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                ("service_spend",),
            )
            columns = {r["column_name"] for r in await cur.fetchall()}
        self.assertEqual(
            columns,
            {
                "month_start", "service", "model", "feature", "plan", "source", "calls",
                "units_in", "units_out", "micro_usd", "refused", "unpriced",
            },
        )  # fmt: skip
        self.assertTrue(
            not columns & {"guild_id", "campaign_id", "user_id", "text", "prompt", "reply", "name"}
        )

    async def test_nobody_but_the_two_doors_can_read_or_write_it(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM service_spend")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
            with self.assertRaises(Exception):  # noqa: B017 (row-level security refuses it)
                await conn.execute(
                    "INSERT INTO service_spend (month_start, service, model, feature, plan,"
                    " source) VALUES (0, 'voyage', 'm', 'f', 'free', 'play')"
                )


def test_only_the_cost_module_and_the_admin_page_open_the_doors() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "dmbot"
    writers = {
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if re.search(r"\.spend_writer\(", p.read_text())
    }
    admins = {
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if re.search(r"\.spend_admin\(", p.read_text())
    }
    assert writers == {"service_spend.py"}
    assert admins == {"service_spend.py"}  # (the admin route reads through `read_month`)
