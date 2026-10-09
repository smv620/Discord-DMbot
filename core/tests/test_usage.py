"""Recording listening minutes (#437 part 2): both tables written together, one owner's
total across servers, hand-over mid-session, and each person and server seeing only theirs.
Needs Postgres (skipped without it, like the other store tests)."""

from __future__ import annotations

import ast
import asyncio
import json
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path

from psycopg import errors as pg_errors

from dmbot import entitlements, hours, usage
from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table
from dmbot.campaigns.store import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.sessions import SessionStore
from dmbot.web import grants
from tests.pg import DatabaseTest
from tests.test_web_accounts_db import INSERT_PLAN, PLAN_ROW

OWNER, NEW_OWNER, OTHER = 11, 22, 33
GUILD_A, GUILD_B = 111, 222
NOW = int(datetime(2026, 3, 20, 12, tzinfo=UTC).timestamp())
START = NOW - 600  # the session's start


class UsageTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        self.campaigns = CampaignStore(self.db)
        self.a = await self.campaigns.create(GUILD_A, "Frostmaiden", OWNER)
        self.b = await self.campaigns.create(GUILD_B, "Another", OWNER)

    async def add(
        self, guild: int, campaign_id: str, owner: int, minutes: int, started: int = START
    ) -> hours.Month:
        return (
            await usage.add_minutes(
                self.db,
                guild_id=guild,
                campaign_id=campaign_id,
                owner_user_id=owner,
                session_started_at=started,
                minutes=minutes,
                now=NOW,
            )
        ).month

    async def owner_minutes(self, owner: int, month: hours.Month) -> int:
        async with self.db.user(owner) as conn:
            return await usage.minutes_this_month(conn, owner, month)

    async def give_plan(self, owner: int, **values: object) -> None:
        async with self.db.user(owner) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, NULL, 0, 0)",
                (owner,),
            )
        async with self.db.plan_writer(owner) as conn:
            await conn.execute(INSERT_PLAN, {**PLAN_ROW, **values, "user_id": owner})


class Recording(UsageTest):
    async def test_minutes_go_into_both_tables_together(self) -> None:
        month = await self.add(GUILD_A, self.a.id, OWNER, 5)
        await self.add(GUILD_A, self.a.id, OWNER, 3)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 8)
        self.assertEqual(await self.owner_minutes(OWNER, month), 8)

    async def test_nothing_to_add_records_nothing(self) -> None:
        month = await self.add(GUILD_A, self.a.id, OWNER, 0)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 0)
        self.assertEqual(await self.owner_minutes(OWNER, month), 0)

    async def test_an_owner_without_a_plan_is_recorded_in_the_calendar_month(self) -> None:
        month = await self.add(GUILD_A, self.a.id, OWNER, 2)
        self.assertEqual(month, usage.calendar_month(NOW))
        self.assertEqual(month.start, int(datetime(2026, 3, 1, tzinfo=UTC).timestamp()))
        self.assertEqual(month.end, int(datetime(2026, 4, 1, tzinfo=UTC).timestamp()))

    async def test_december_rolls_into_next_year(self) -> None:
        dec = int(datetime(2026, 12, 31, 23, tzinfo=UTC).timestamp())
        self.assertEqual(
            usage.calendar_month(dec).end, int(datetime(2027, 1, 1, tzinfo=UTC).timestamp())
        )

    async def test_a_paid_plans_hours_are_in_its_billing_period(self) -> None:
        await self.give_plan(OWNER, period_start=NOW - 1000, period_end=NOW + 5000)
        month = await self.add(GUILD_A, self.a.id, OWNER, 4)
        self.assertEqual((month.start, month.end), (NOW - 1000, NOW + 5000))

    async def test_a_guild_grants_hours_run_from_the_day_it_started(self) -> None:
        began = int(datetime(2026, 1, 14, 10, tzinfo=UTC).timestamp())
        await grants.give(
            self.db, "admin@example.invalid", OWNER, "guild", ends_at=None, note="", now=began
        )
        month = await self.add(GUILD_A, self.a.id, OWNER, 4)
        self.assertEqual(month, hours.month_from_anchor(began, NOW))

    async def test_hours_from_an_earlier_period_do_not_count(self) -> None:
        old = await self.add(GUILD_A, self.a.id, OWNER, 9)
        later = hours.Month(old.end, old.end + 100)
        self.assertEqual(await self.owner_minutes(OWNER, later), 0)


class AcrossServers(UsageTest):
    async def test_one_owner_two_servers_one_total(self) -> None:
        month = await self.add(GUILD_A, self.a.id, OWNER, 5)
        await self.add(GUILD_B, self.b.id, OWNER, 7, started=START - 50)
        self.assertEqual(await self.owner_minutes(OWNER, month), 12)
        # Each server's own record is only its own.
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 5)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.b.id, START - 50), 0)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_B, self.b.id, START - 50), 7)

    async def test_a_handover_during_a_session_moves_the_later_minutes(self) -> None:
        month = await self.add(GUILD_A, self.a.id, OWNER, 5)
        await self.add(GUILD_A, self.a.id, NEW_OWNER, 4)  # after the hand-over
        self.assertEqual(await self.owner_minutes(OWNER, month), 5)
        self.assertEqual(await self.owner_minutes(NEW_OWNER, month), 4)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 9)


class Isolation(UsageTest):
    async def test_another_person_sees_no_hours(self) -> None:
        month = await self.add(GUILD_A, self.a.id, OWNER, 5)
        self.assertEqual(await self.owner_minutes(OTHER, month), 0)
        async with self.db.user(OTHER) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM owner_hours")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_another_server_sees_no_session_usage(self) -> None:
        await self.add(GUILD_A, self.a.id, OWNER, 5)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM session_usage")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_a_server_alone_sees_no_owner_hours(self) -> None:
        await self.add(GUILD_A, self.a.id, OWNER, 5)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM owner_hours")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_no_other_connection_sees_either_table(self) -> None:
        await self.add(GUILD_A, self.a.id, OWNER, 5)
        doors = {
            "another server": self.db.guild(GUILD_B),
            "another person": self.db.user(OTHER),
            "the owner alone": self.db.user(OWNER),
            "a meter for another server and person": self.db.meter(GUILD_B, OTHER),
        }
        for name, door in doors.items():
            async with door as conn:
                for table in ("session_usage", "owner_hours"):
                    cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
                    seen = (await cur.fetchone() or {})["n"]
                    expected = 1 if (name == "the owner alone" and table == "owner_hours") else 0
                    self.assertEqual(seen, expected, f"{name} / {table}")

    async def test_a_backup_holds_neither_usage_table(self) -> None:
        await self.add(GUILD_A, self.a.id, OWNER, 5)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        text = json.dumps(data)
        self.assertNotIn("owner_hours", text)
        self.assertNotIn("session_usage", text)

    async def test_only_the_meter_adds_hours(self) -> None:
        month = usage.calendar_month(NOW)
        doors = (self.db.user(OWNER), self.db.unscoped(), self.db.guild(GUILD_A))
        for door in doors:
            with self.assertRaises(pg_errors.InsufficientPrivilege):
                async with door as conn:
                    await conn.execute(
                        "INSERT INTO owner_hours (owner_user_id, month_start, minutes)"
                        " VALUES (%s, %s, 1)",
                        (OWNER, month.start),
                    )

    async def test_the_meter_cannot_write_for_a_different_person(self) -> None:
        month = usage.calendar_month(NOW)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.meter(GUILD_A, OWNER) as conn:
                await conn.execute(
                    "INSERT INTO owner_hours (owner_user_id, month_start, minutes)"
                    " VALUES (%s, %s, 1)",
                    (OTHER, month.start),
                )

    async def test_session_usage_goes_with_the_campaign(self) -> None:
        await self.add(GUILD_A, self.a.id, OWNER, 5)
        await self.campaigns.delete(GUILD_A, self.a.id)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 0)


class TheBotMeters(UsageTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
            meter=usage.Meter(self.db),
        )

    def table(
        self,
        started: int = START,
        guild: int = GUILD_A,
        campaign: str = "",
        listening_from: int = 0,
    ) -> Table:
        table = Table(
            guild_id=guild,
            voice_channel_id=2,
            screen_channel_id=3,
            dm_user_id=OWNER,
            segmenter=Segmenter(guild),
            campaign_id=campaign or self.a.id,
            campaign_name="Frostmaiden",
            started_at=started,
            listening_from=listening_from,
        )
        self.bot.tables[guild] = table  # running, as a tick expects
        return table

    async def test_a_tick_writes_the_minutes_so_far_rounded_up(self) -> None:
        table = self.table()  # 600 s ago
        await self.bot.meter_table(table, NOW)
        self.assertEqual(table.metered_minutes, 10)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 10)

    async def test_a_second_tick_adds_only_the_new_minutes(self) -> None:
        table = self.table()
        await self.bot.meter_table(table, NOW)
        await self.bot.meter_table(table, NOW)  # nothing new
        await self.bot.meter_table(table, NOW + 61)  # a minute and a bit later
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 12)

    async def test_the_end_rounds_a_short_session_up_to_a_whole_minute(self) -> None:
        table = self.table(started=NOW - 1)
        await self.bot.meter_table(table, NOW)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, NOW - 1), 1)

    async def test_a_restart_carries_on_without_counting_a_minute_twice(self) -> None:
        await self.bot.meter_table(self.table(), NOW)
        again = self.table(listening_from=NOW)  # the same session, a new process took it on
        await self.bot.meter_table(again, NOW)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 10)

    async def test_the_time_dmbot_was_down_is_never_billed(self) -> None:
        await self.bot.meter_table(self.table(), NOW)  # 10 minutes, then DMbot goes down
        back = NOW + 3 * 3600  # and is back three hours later
        again = self.table(listening_from=back)
        await self.bot.meter_table(again, back)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 10)
        await self.bot.meter_table(again, back + 150)  # two and a half minutes of listening
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 13)

    async def test_minutes_with_no_owner_are_never_billed_to_whoever_claims_it_later(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", (self.a.id,)
            )
        table = self.table()
        await self.bot.meter_table(table, NOW)  # 10 minutes with nobody to bill
        self.assertEqual(table.metered_minutes, 10)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE id = %s", (NEW_OWNER, self.a.id)
            )
        await self.bot.meter_table(table, NOW + 180)  # three minutes after they took it on
        month = usage.calendar_month(NOW)
        self.assertEqual(await self.owner_minutes(NEW_OWNER, month), 3)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT owner_user_id, minutes FROM session_usage ORDER BY owner_user_id"
            )
            rows = [(r["owner_user_id"], r["minutes"]) for r in await cur.fetchall()]
        self.assertEqual(rows, [(0, 10), (NEW_OWNER, 3)])

    async def test_a_restart_does_not_bill_the_no_owner_minutes_either(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", (self.a.id,)
            )
        await self.bot.meter_table(self.table(), NOW)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE id = %s", (NEW_OWNER, self.a.id)
            )
        again = self.table(listening_from=NOW)
        await self.bot.meter_table(again, NOW + 60)
        month = usage.calendar_month(NOW)
        self.assertEqual(await self.owner_minutes(NEW_OWNER, month), 1)

    async def test_a_tick_in_flight_at_the_stop_never_bills_past_the_end(self) -> None:
        table = self.table()
        table.ended_at = NOW - 300  # the session stopped five minutes before this tick ran
        await self.bot.meter_table(table, NOW, final=True)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 5)

    async def test_a_handover_during_the_session_moves_the_following_minutes(self) -> None:
        table = self.table()
        await self.bot.meter_table(table, NOW)  # 10 minutes, to OWNER
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE id = %s", (NEW_OWNER, self.a.id)
            )
        await self.bot.meter_table(table, NOW + 180)  # 3 more minutes, to NEW_OWNER
        month = usage.calendar_month(NOW)
        self.assertEqual(await self.owner_minutes(OWNER, month), 10)
        self.assertEqual(await self.owner_minutes(NEW_OWNER, month), 3)

    async def test_a_campaign_with_no_owner_spends_nobodys_hours_and_does_not_fail(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", (self.a.id,)
            )
        table = self.table()
        await self.bot.meter_table(table, NOW)
        self.assertEqual(table.metered_minutes, 10)  # moved on, so nobody is billed it later
        month = usage.calendar_month(NOW)
        self.assertEqual(await self.owner_minutes(OWNER, month), 0)

    async def test_no_meter_no_recording(self) -> None:
        bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
        )
        table = self.table()
        await bot.meter_table(table, NOW)
        self.assertIsNone(table.metered_minutes)

    async def test_a_tick_after_the_final_charge_bills_nothing(self) -> None:
        table = self.table()
        await self.bot.meter_table(table, NOW, final=True)
        await self.bot.meter_table(table, NOW + 90)  # a tick that was waiting its turn
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 10)

    async def test_a_tick_a_little_over_a_minute_in_charges_two_minutes(self) -> None:
        table = self.table(started=NOW - 61)
        await self.bot.meter_table(table, NOW)
        self.assertEqual(table.metered_minutes, 2)  # rounded up, never down

    async def test_two_ticks_at_once_charge_once(self) -> None:
        import asyncio

        table = self.table()
        await asyncio.gather(*(self.bot.meter_table(table, NOW) for _ in range(5)))
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 10)

    async def test_a_failed_read_after_a_restart_adds_nothing_and_is_retried(self) -> None:
        from unittest.mock import patch

        table = self.table()
        broken = patch.object(usage.Meter, "recorded", side_effect=RuntimeError("down"))
        with broken, self.assertLogs("dmbot.bot", "ERROR"):
            self.assertFalse(await self.bot.meter_table(table, NOW))
        self.assertIsNone(table.metered_minutes)  # not guessed as 0
        self.assertTrue(await self.bot.meter_table(table, NOW))
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 10)

    async def test_the_final_charge_is_retried_then_closes_the_meter(self) -> None:
        from unittest.mock import patch

        table = self.table()
        calls = []

        async def flaky(*args: object, **kwargs: object) -> bool:
            calls.append(1)
            return len(calls) == 3

        with (
            patch.object(self.bot, "meter_table", side_effect=flaky),
            patch("dmbot.bot.METER_FINAL_RETRY_S", 0),
        ):
            await self.bot._meter_final(table, NOW)
        self.assertEqual(len(calls), 3)

        calls.clear()
        with (
            patch.object(self.bot, "meter_table", side_effect=_false),
            patch("dmbot.bot.METER_FINAL_RETRY_S", 0),
        ):
            await self.bot._meter_final(table, NOW)
        self.assertTrue(table.metering_closed)

    async def test_a_stop_settles_the_session_in_its_own_task(self) -> None:
        from unittest.mock import AsyncMock, patch

        table = self.table()
        with (
            patch.object(self.bot, "_meter_final", new=AsyncMock()) as final,
            patch.object(self.bot, "wind_down", new=AsyncMock()),
            patch.object(self.bot.ears, "send", new=AsyncMock()),
        ):
            await self.bot.stop_table(GUILD_A, "test")
            await asyncio.gather(*self.bot._finishing)
        final.assert_awaited_once()
        self.assertIs(final.await_args.args[0], table)  # type: ignore[union-attr]

    async def test_a_tick_for_a_session_that_has_stopped_bills_nothing(self) -> None:
        table = self.table()
        self.bot.tables.pop(GUILD_A)  # it has been stopped
        await self.bot.meter_table(table, NOW)
        self.assertEqual(await usage.session_minutes(self.db, GUILD_A, self.a.id, START), 0)

    async def test_a_slow_database_cannot_hold_up_the_loop(self) -> None:
        from unittest.mock import patch

        async def forever(*args: object, **kwargs: object) -> bool:
            await asyncio.Event().wait()  # never finishes
            return True

        a = self.table()
        self.table(guild=GUILD_B, campaign=self.b.id)
        seen = []

        async def tick(table: Table, *args: object, **kwargs: object) -> bool:
            seen.append(table.guild_id)
            return await forever() if table is a else True

        async def one_round(_: float) -> None:
            if seen:
                raise asyncio.CancelledError

        with (
            patch.object(self.bot, "meter_table", side_effect=tick),
            patch("dmbot.bot.METER_CALL_TIMEOUT_S", 0.05),
            patch("dmbot.bot.asyncio.sleep", side_effect=one_round),
            self.assertLogs("dmbot.bot", "ERROR"),
            self.assertRaises(asyncio.CancelledError),
        ):
            await self.bot._meter_loop()
            await self.bot._meter_loop()
        self.assertEqual(seen, [GUILD_A, GUILD_B])

    async def test_a_database_failure_never_stops_the_game(self) -> None:
        from unittest.mock import patch

        down = patch.object(usage.Meter, "add", side_effect=RuntimeError("down"))
        with down, self.assertLogs("dmbot.bot", "ERROR"):
            await self.bot.meter_table(self.table(), NOW)


async def _false(*args: object, **kwargs: object) -> bool:
    return False


class PlanChecks(UsageTest):
    SITE = "https://dmbot.example"

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot = self.make_bot(enforce=True)

    def make_bot(self, *, enforce: bool) -> DMBot:
        return DMBot(
            Settings(discord_token="t", ears_secret="s", enforce_plans=enforce, site_url=self.SITE),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
            meter=usage.Meter(self.db),
        )

    async def campaign(self) -> object:
        return await self.campaigns.get(GUILD_A, self.a.id)

    async def refusal(self, starter: int = OWNER, bot: DMBot | None = None) -> str | None:
        campaign = await self.campaign()
        return await (bot or self.bot).plan_refusal(GUILD_A, campaign, starter)  # type: ignore[arg-type]

    async def test_off_by_default_nobody_is_refused(self) -> None:
        self.assertIsNone(await self.refusal(bot=self.make_bot(enforce=False)))

    async def test_an_owner_with_no_plan_is_told_to_pick_one(self) -> None:
        self.assertEqual(
            await self.refusal(), f"Your plan has ended. Pick one here: {self.SITE}/account"
        )

    async def test_a_co_dm_is_not_told_about_the_owners_plan(self) -> None:
        self.assertEqual(await self.refusal(starter=NEW_OWNER), hours.START_BLOCKED)

    async def test_a_plan_with_hours_left_may_start(self) -> None:
        await self.give_plan(OWNER, period_start=NOW - 1000, period_end=int(time.time()) + 5000)
        self.assertIsNone(await self.refusal())

    async def test_used_up_hours_are_refused_until_the_period_ends(self) -> None:
        # (The plan covers the fixed NOW the minutes are recorded at and the real now of the check.)
        await self.give_plan(
            OWNER, period_start=NOW - 1000, period_end=int(time.time()) + 5000, hours_cap=1
        )
        await self.add(GUILD_A, self.a.id, OWNER, 60)
        text = await self.refusal()
        self.assertIsNotNone(text)
        self.assertIn("You've used all your hours this month.", text or "")
        self.assertIn(f"{self.SITE}/account", text or "")

    async def test_a_free_account_has_no_limit(self) -> None:
        entitlements.configure_free_users([OWNER])
        await self.add(GUILD_A, self.a.id, OWNER, 10_000)
        self.assertIsNone(await self.refusal())

    async def test_a_campaign_with_no_owner_is_asked_to_be_taken_on(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", (self.a.id,)
            )
        self.assertEqual(await self.refusal(), hours.NO_OWNER)

    async def test_a_database_failure_lets_the_game_start(self) -> None:
        from unittest.mock import patch

        broken = patch.object(usage.Meter, "check", side_effect=RuntimeError("down"))
        with broken, self.assertLogs("dmbot.bot", "ERROR"):
            self.assertIsNone(await self.refusal())

    async def test_crossing_80_percent_warns_the_dm_screen_once(self) -> None:
        from unittest.mock import AsyncMock, patch

        await self.give_plan(OWNER, period_start=NOW - 1000, period_end=NOW + 5000, hours_cap=1)
        await self.add(GUILD_A, self.a.id, OWNER, 47, started=START - 9)  # 78% used
        table = Table(
            guild_id=GUILD_A,
            voice_channel_id=2,
            screen_channel_id=77,
            dm_user_id=OWNER,
            segmenter=Segmenter(GUILD_A),
            campaign_id=self.a.id,
            campaign_name="F",
            started_at=NOW - 120,
        )
        self.bot.tables[GUILD_A] = table
        with patch.object(self.bot, "post", new=AsyncMock()) as post:
            await self.bot.meter_table(table, NOW)  # +2 minutes: 81%
            await self.bot.meter_table(table, NOW + 60)  # +1 more: still past, said once
        post.assert_awaited_once()
        self.assertEqual(post.await_args.args[0], 77)  # type: ignore[union-attr]
        self.assertIn("left this month", post.await_args.args[1])  # type: ignore[union-attr]

    def running_table(self, *, started: int, screen: int = 77) -> Table:
        table = Table(
            guild_id=GUILD_A,
            voice_channel_id=2,
            screen_channel_id=screen,
            dm_user_id=OWNER,
            segmenter=Segmenter(GUILD_A),
            campaign_id=self.a.id,
            campaign_name="F",
            started_at=started,
        )
        self.bot.tables[GUILD_A] = table
        return table

    async def small_plan(self) -> None:
        await self.give_plan(OWNER, period_start=NOW - 1000, period_end=NOW + 5000, hours_cap=1)

    async def test_the_warning_text_is_the_80_percent_one(self) -> None:
        from unittest.mock import AsyncMock, patch

        await self.small_plan()
        await self.add(GUILD_A, self.a.id, OWNER, 47, started=START - 9)
        table = self.running_table(started=NOW - 120)
        with patch.object(self.bot, "post", new=AsyncMock()) as post:
            await self.bot.meter_table(table, NOW)
        # 60 - 49 = 11 minutes left: under half an hour
        self.assertEqual(
            post.await_args.args[1],  # type: ignore[union-attr]
            "⏳ Less than half an hour left this month. (Hours are DMbot's listening time.)",
        )

    async def test_crossing_90_percent_says_where_to_add_more(self) -> None:
        from unittest.mock import AsyncMock, patch

        await self.small_plan()
        await self.add(GUILD_A, self.a.id, OWNER, 53, started=START - 9)  # 88%
        table = self.running_table(started=NOW - 120)
        with patch.object(self.bot, "post", new=AsyncMock()) as post:
            await self.bot.meter_table(table, NOW)  # +2: 91.7%
        self.assertIn("To add more, go to https://dmbot.example/account", post.await_args.args[1])  # type: ignore[union-attr]

    async def test_crossing_both_marks_in_one_tick_warns_once_with_the_higher(self) -> None:
        from unittest.mock import AsyncMock, patch

        await self.small_plan()
        await self.add(GUILD_A, self.a.id, OWNER, 40, started=START - 9)  # 66%
        table = self.running_table(started=NOW - 20 * 60)
        with patch.object(self.bot, "post", new=AsyncMock()) as post:
            await self.bot.meter_table(table, NOW)  # +20: 100%
        post.assert_awaited_once()
        self.assertIn("To add more", post.await_args.args[1])  # type: ignore[union-attr]

    async def test_the_final_charge_never_warns(self) -> None:
        from unittest.mock import AsyncMock, patch

        await self.small_plan()
        await self.add(GUILD_A, self.a.id, OWNER, 47, started=START - 9)
        table = self.running_table(started=NOW - 120)
        with patch.object(self.bot, "post", new=AsyncMock()) as post:
            await self.bot.meter_table(table, NOW, final=True)
        post.assert_not_awaited()

    async def test_a_restart_does_not_say_the_same_mark_again(self) -> None:
        from unittest.mock import AsyncMock, patch

        await self.small_plan()
        await self.add(GUILD_A, self.a.id, OWNER, 47, started=START - 9)
        first = self.running_table(started=NOW - 120)
        with patch.object(self.bot, "post", new=AsyncMock()) as post:
            await self.bot.meter_table(first, NOW)
            again = self.running_table(started=NOW - 120)  # a new process, same session
            await self.bot.meter_table(again, NOW + 1)
        post.assert_awaited_once()

    async def test_a_plan_with_no_cap_never_warns(self) -> None:
        from unittest.mock import AsyncMock, patch

        entitlements.configure_free_users([OWNER])
        table = self.running_table(started=NOW - 5000 * 60)
        with patch.object(self.bot, "post", new=AsyncMock()) as post:
            await self.bot.meter_table(table, NOW)
        post.assert_not_awaited()

    async def test_two_campaigns_ticking_at_once_cross_a_mark_exactly_once(self) -> None:
        await self.small_plan()
        await self.add(GUILD_A, self.a.id, OWNER, 47, started=START - 9)  # 78%
        results = await asyncio.gather(
            usage.add_minutes(
                self.db,
                guild_id=GUILD_A,
                campaign_id=self.a.id,
                owner_user_id=OWNER,
                session_started_at=START,
                minutes=1,
                now=NOW,
            ),
            usage.add_minutes(
                self.db,
                guild_id=GUILD_B,
                campaign_id=self.b.id,
                owner_user_id=OWNER,
                session_started_at=START,
                minutes=1,
                now=NOW,
            ),
        )
        marks = [hours.warning_crossed(r.access, r.used_before, r.used_after) for r in results]
        self.assertEqual(sorted(m or 0 for m in marks), [0, 80])  # said by exactly one
        self.assertEqual({r.used_after for r in results}, {48, 49})

    async def test_a_slow_database_cannot_hold_up_a_start(self) -> None:
        from unittest.mock import patch

        async def forever(*args: object, **kwargs: object) -> object:
            await asyncio.Event().wait()

        with (
            patch.object(usage.Meter, "check", side_effect=forever),
            patch("dmbot.bot.METER_CALL_TIMEOUT_S", 0.05),
            self.assertLogs("dmbot.bot", "ERROR"),
        ):
            self.assertIsNone(await self.refusal())  # fails open

    async def test_no_warning_when_checks_are_off(self) -> None:
        from unittest.mock import AsyncMock, patch

        bot = self.make_bot(enforce=False)
        await self.give_plan(OWNER, period_start=NOW - 1000, period_end=NOW + 5000, hours_cap=1)
        table = Table(
            guild_id=GUILD_A,
            voice_channel_id=2,
            screen_channel_id=77,
            dm_user_id=OWNER,
            segmenter=Segmenter(GUILD_A),
            campaign_id=self.a.id,
            campaign_name="F",
            started_at=NOW - 55 * 60,
        )
        bot.tables[GUILD_A] = table
        with patch.object(bot, "post", new=AsyncMock()) as post:
            await bot.meter_table(table, NOW)
        post.assert_not_awaited()


class OnlyUsageOpensTheMeter(unittest.TestCase):
    def test_only_the_usage_module_opens_the_meter_door(self) -> None:
        src = Path(__file__).resolve().parents[1] / "src" / "dmbot"

        def opens(source: str) -> bool:
            return any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "meter"
                for node in ast.walk(ast.parse(source))
            )

        openers = {p.relative_to(src).as_posix() for p in src.rglob("*.py") if opens(p.read_text())}
        self.assertEqual(openers, {"usage.py"})


if __name__ == "__main__":
    unittest.main()
