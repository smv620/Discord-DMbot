"""Retention (#964): the keep date for every plan and the lapse rule, the 14- and 3-day
warnings (each once), the daily job's deletion through the campaign store, the 10% guard,
the setting off (a dry run only), and isolation. Needs Postgres for the store tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dmbot import entitlements, plans, retention
from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.retention import DAY, RetentionJob, Standing
from dmbot.retention_channels import ChannelCounts
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL, DatabaseTest

GUILD_A, GUILD_B = 111, 222
ALICE, BOB, CAROL = 7, 8, 9
T0 = int(datetime(2026, 3, 1, 12, tzinfo=UTC).timestamp())


class KeepDates(unittest.TestCase):
    def test_every_plan_keeps_for_its_own_time_after_the_last_session(self) -> None:
        table = plans.load()
        for plan_id, plan in table.by_id.items():
            at = retention.delete_at(T0, Standing(plan_id, None, False))
            self.assertEqual(at, T0 + plan.keep_after_last_session.days * DAY, plan_id)
        self.assertEqual(table.by_id["try-it"].keep_after_last_session.days, 60)
        self.assertEqual(table.by_id["table"].keep_after_last_session.days, 180)
        self.assertEqual(table.by_id["pro"].keep_after_last_session.days, 365)

    def test_a_lapse_ends_it_120_days_after_if_that_comes_first(self) -> None:
        lapsed = T0 + 10 * DAY
        at = retention.delete_at(T0, Standing("pro", lapsed, True))
        self.assertEqual(at, lapsed + 120 * DAY)  # sooner than a year after the last session
        # A lapse long after the last session: the session rule comes first.
        late = T0 + 300 * DAY
        self.assertEqual(retention.delete_at(T0, Standing("pro", late, True)), T0 + 365 * DAY)

    def test_the_warning_stages(self) -> None:
        d = T0 + 100 * DAY
        self.assertEqual(retention.warning_stage(d, d - 15 * DAY), 0)
        self.assertEqual(retention.warning_stage(d, d - 14 * DAY), 1)
        self.assertEqual(retention.warning_stage(d, d - 4 * DAY), 1)
        self.assertEqual(retention.warning_stage(d, d - 3 * DAY), 2)
        self.assertEqual(retention.warning_stage(d, d - 1), 2)

    def test_it_runs_at_three_in_the_morning_utc(self) -> None:
        now = int(datetime(2026, 3, 1, 3, 0, tzinfo=UTC).timestamp())
        self.assertEqual(retention.next_run_after(now), now + 24 * 3600)
        before = now - 3600
        self.assertEqual(retention.next_run_after(before), now)


class CatchUp(unittest.TestCase):
    def test_overdue_only_when_the_last_run_was_over_a_day_ago(self) -> None:
        self.assertFalse(retention.catch_up_due(None, T0))  # never run: the 03:00 run is first
        self.assertFalse(retention.catch_up_due(T0 - DAY, T0))
        self.assertTrue(retention.catch_up_due(T0 - DAY - 1, T0))

    def test_the_last_run_is_kept_in_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "sub"
            self.assertIsNone(retention.read_last_run(state))
            retention.write_last_run(state, T0)
            self.assertEqual(retention.read_last_run(state), T0)
            (state / retention.LAST_RUN_FILE).write_text("junk")
            self.assertIsNone(retention.read_last_run(state))


class Job(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        entitlements.configure_free_users((BOB,))  # BOB can take a campaign on
        self.now = T0
        self.store = CampaignStore(self.db, clock=lambda: self.now)
        self.sent: list[tuple[int, str]] = []
        self.standings: dict[int, Standing] = {}
        self.running: set[str] = set()
        self.enforce = True
        self.retired: list[tuple[Any, bool]] = []
        self.retire_fails = False

    async def standing_of(self, guild_id: int, owner: int, now: int) -> Standing:
        return self.standings.get(owner, retention.UNKNOWN)

    async def send(self, user_id: int, text: str) -> bool:
        self.sent.append((user_id, text))
        return True

    async def retire(self, campaign: Any, now: int) -> ChannelCounts:
        """Stands in for the Discord work (#1598): records the campaign it was given and whether
        the campaign was already gone when it was called."""
        self.retired.append((campaign, await self.exists(campaign.guild_id, campaign.id)))
        if self.retire_fails:
            raise RuntimeError("discord is down")
        return ChannelCounts(deleted=2, closed=2, skipped=0)

    def job(self, guilds: tuple[int, ...] | None = None) -> RetentionJob:
        """`guilds` limits the servers asked about; None takes them from the database."""

        async def listed() -> list[int]:
            return list(guilds) if guilds is not None else await self.store.guilds_with_campaigns()

        return RetentionJob(
            campaigns=self.store,
            standing_of=self.standing_of,
            guild_ids=listed,
            forget_guild=self.store.forget_guild_if_empty,
            running=lambda: self.running,
            send=self.send,
            retire_channels=self.retire,
            enforce=self.enforce,
        )

    async def make(self, guild: int, name: str, owner: int | None, played: int) -> str:
        campaign = await self.store.create(guild, name, owner or ALICE)
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "UPDATE campaigns SET last_played_at = %s, created_at = %s,"
                " owner_user_id = %s WHERE guild_id = %s AND id = %s",
                (played, min(played, T0), owner, guild, campaign.id),
            )
        return campaign.id

    async def exists(self, guild: int, campaign_id: str) -> bool:
        return await self.store.get(guild, campaign_id) is not None

    async def test_warnings_at_14_and_3_days_each_sent_once(self) -> None:
        cid = await self.make(GUILD_A, "Old", ALICE, T0)
        for n in range(8):  # filler so one deletion never trips the guard
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        keep = plans.load().by_id["try-it"].keep_after_last_session.days
        delete = T0 + keep * DAY
        self.now = delete - 20 * DAY
        self.assertEqual((await self.job().run_once(self.now)).warnings, 0)
        self.now = delete - 14 * DAY
        first = await self.job().run_once(self.now)
        self.assertEqual(first.warnings, 1)
        again = await self.job().run_once(self.now + 3600)  # a restart, or a second run
        self.assertEqual(again.warnings, 0)
        self.now = delete - 3 * DAY
        last = await self.job().run_once(self.now)
        self.assertEqual(last.warnings, 1)
        self.assertEqual((await self.job().run_once(self.now + 3600)).warnings, 0)
        self.assertEqual([u for u, _ in self.sent], [ALICE, ALICE])
        self.assertIn("14 days", self.sent[0][1])
        self.assertIn("3 days", self.sent[1][1])
        self.assertIn("Last warning", self.sent[1][1])
        self.assertNotIn("Last warning", self.sent[0][1])
        self.assertIn("your server", self.sent[0][1])  # a private message names the server
        self.assertIn("play a session", self.sent[0][1])
        self.assertTrue(await self.exists(GUILD_A, cid))

    async def test_playing_a_session_starts_the_warnings_over(self) -> None:
        cid = await self.make(GUILD_A, "Old", ALICE, T0)
        for n in range(8):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        delete = T0 + 60 * DAY
        await self.job().run_once(delete - 3 * DAY)
        self.assertEqual(len(self.sent), 1)
        async with self.db.guild(GUILD_A) as conn:  # played on the 58th day
            await conn.execute(
                "UPDATE campaigns SET last_played_at = %s WHERE id = %s", (delete - DAY, cid)
            )
        out = await self.job().run_once(delete)
        self.assertEqual((out.warnings, out.deleted), (0, 0))  # far from deletion again
        later = (delete - DAY) + 60 * DAY - 14 * DAY
        self.assertEqual((await self.job().run_once(later)).warnings, 1)

    async def test_a_campaign_past_its_date_is_deleted_and_the_owner_told_once(self) -> None:
        old = await self.make(GUILD_A, "Old", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        await self.job().run_once(T0 + 57 * DAY)  # the 3-day warning
        now = T0 + 61 * DAY
        out = await self.job().run_once(now)
        self.assertEqual((out.to_delete, out.deleted), (1, 1))
        self.assertFalse(await self.exists(GUILD_A, old))
        self.assertEqual(len([m for u, m in self.sent if "was deleted" in m]), 1)
        async with self.db.user(ALICE) as conn:  # the owner row went with it (trigger)
            cur = await conn.execute("SELECT campaign_id FROM owner_campaigns")
            self.assertEqual(await cur.fetchall(), [])
        self.assertEqual((await self.job().run_once(now)).deleted, 0)  # twice is safe

    async def old_campaign_ready_to_go(self) -> str:
        old = await self.make(GUILD_A, "Old", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        await self.job().run_once(T0 + 57 * DAY)  # the 3-day warning
        return old

    async def test_the_channels_are_dealt_with_after_the_delete_and_counted(self) -> None:
        old = await self.old_campaign_ready_to_go()
        await self.store.set_dm_screen(GUILD_A, old, 777)
        before = await self.store.get(GUILD_A, old)
        assert before is not None
        with self.assertLogs("dmbot.retention", "INFO") as logged:
            out = await self.job().run_once(T0 + 61 * DAY)
        self.assertEqual(out.deleted, 1)
        ((got, existed),) = self.retired
        self.assertEqual((got.id, got.dm_screen_channel_id), (old, 777))  # read before the delete
        self.assertFalse(existed)  # the campaign was already gone: a failed delete touches nothing
        self.assertEqual((out.channels.deleted, out.channels.closed), (2, 2))
        self.assertIn("channels: 2 deleted, 2 closed, 0 skipped", logged.output[-1])

    async def test_nothing_is_done_to_channels_when_the_delete_does_not_happen(self) -> None:
        old = await self.old_campaign_ready_to_go()
        self.running = {old}  # a session is running: not deleted
        out = await self.job().run_once(T0 + 61 * DAY)
        self.assertEqual(out.deleted, 0)
        self.assertEqual(self.retired, [])

    async def test_a_dry_run_touches_no_channel(self) -> None:
        await self.old_campaign_ready_to_go()
        self.enforce = False
        await self.job().run_once(T0 + 61 * DAY)
        self.assertEqual(self.retired, [])

    async def test_a_channel_job_that_fails_never_stops_the_run_or_the_notice(self) -> None:
        old = await self.old_campaign_ready_to_go()
        self.retire_fails = True
        with self.assertLogs("dmbot.retention", "ERROR"):
            out = await self.job().run_once(T0 + 61 * DAY)
        self.assertEqual(out.deleted, 1)
        self.assertFalse(await self.exists(GUILD_A, old))
        self.assertEqual(len([m for _u, m in self.sent if "was deleted" in m]), 1)

    async def test_the_plan_decides_how_long(self) -> None:
        self.standings[ALICE] = Standing("pro", None, True)
        mine = await self.make(GUILD_A, "Mine", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 700 * DAY)
        await self.job().run_once(T0 + 100 * DAY)  # Try It would have deleted it by now
        self.assertTrue(await self.exists(GUILD_A, mine))
        self.assertTrue(any("download a backup" in m for _, m in self.sent) is False)

    async def test_a_lapsed_plan_deletes_120_days_after(self) -> None:
        self.standings[ALICE] = Standing("pro", T0 + 5 * DAY, True)
        mine = await self.make(GUILD_A, "Mine", ALICE, T0 + 4 * DAY)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 700 * DAY)
        await self.job().run_once(T0 + 5 * DAY + 119 * DAY)
        self.assertTrue(await self.exists(GUILD_A, mine))
        await self.job().run_once(T0 + 5 * DAY + 118 * DAY)  # inside the last 3 days: warned
        await self.job().run_once(T0 + 5 * DAY + 121 * DAY)
        self.assertFalse(await self.exists(GUILD_A, mine))

    async def test_a_campaign_with_no_owner_follows_try_it_and_its_dms_are_told(self) -> None:
        orphan = await self.make(GUILD_A, "Orphan", None, T0)
        await self.store.add_dm(GUILD_A, orphan, BOB)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", ALICE, T0 + 300 * DAY)
        await self.job().run_once(T0 + 50 * DAY)  # 10 days before Try It's 60
        self.assertIn(BOB, [u for u, _ in self.sent])
        await self.job().run_once(T0 + 58 * DAY)  # the last warning
        await self.job().run_once(T0 + 61 * DAY)
        self.assertFalse(await self.exists(GUILD_A, orphan))

    async def test_a_paused_campaign_follows_the_same_rules(self) -> None:
        cid = await self.make(GUILD_A, "Paused", ALICE, T0)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE campaigns SET paused = TRUE WHERE id = %s", (cid,))
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        await self.job().run_once(T0 + 58 * DAY)
        await self.job().run_once(T0 + 61 * DAY)
        self.assertFalse(await self.exists(GUILD_A, cid))

    async def test_a_running_session_is_left_alone(self) -> None:
        cid = await self.make(GUILD_A, "Live", ALICE, T0)
        self.running = {cid}
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        await self.job().run_once(T0 + 61 * DAY)
        self.assertTrue(await self.exists(GUILD_A, cid))

    async def test_the_guard_stops_a_run_that_would_delete_too_many(self) -> None:
        ids = [await self.make(GUILD_A, f"c{n}", ALICE, T0) for n in range(5)]
        await self.make(GUILD_B, "fresh", BOB, T0 + 300 * DAY)
        with self.assertLogs("dmbot.retention", "ERROR"):
            out = await self.job().run_once(T0 + 61 * DAY)
        self.assertTrue(out.guard_stopped)
        self.assertEqual(out.deleted, 0)
        for cid in ids:
            self.assertTrue(await self.exists(GUILD_A, cid))

    async def test_with_enforcement_off_it_only_counts(self) -> None:
        self.enforce = False
        cid = await self.make(GUILD_A, "Old", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        with self.assertLogs("dmbot.retention", "INFO") as logged:
            out = await self.job().run_once(T0 + 61 * DAY)
        self.assertTrue(out.dry_run)
        self.assertEqual((out.to_delete, out.deleted), (1, 0))
        self.assertTrue(await self.exists(GUILD_A, cid))
        self.assertEqual(self.sent, [])
        self.assertIn("dry run", logged.output[0])
        self.assertNotIn("Old", logged.output[0])  # counts only

    async def test_a_plan_that_cannot_be_read_never_deletes(self) -> None:
        cid = await self.make(GUILD_A, "Old", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)

        async def broken(guild_id: int, owner: int, now: int) -> Standing:
            raise RuntimeError("down")

        job = self.job()
        job._standing_of = broken
        with self.assertLogs("dmbot.retention", "ERROR"):
            out = await job.run_once(T0 + 61 * DAY)
        self.assertEqual((out.deleted, out.skipped), (0, 10))  # every owner failed
        self.assertTrue(await self.exists(GUILD_A, cid))

    async def test_only_the_servers_asked_for_are_touched(self) -> None:
        a = await self.make(GUILD_A, "A", ALICE, T0)
        b = await self.make(GUILD_B, "B", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_A, f"x{n}", BOB, T0 + 300 * DAY)
        await self.job(guilds=(GUILD_A,)).run_once(T0 + 58 * DAY)
        await self.job(guilds=(GUILD_A,)).run_once(T0 + 61 * DAY)
        self.assertFalse(await self.exists(GUILD_A, a))
        self.assertTrue(await self.exists(GUILD_B, b))

    async def test_nothing_is_deleted_that_was_never_warned(self) -> None:
        # The first run after deploy meets a campaign already well past its date: it is
        # warned now and deleted three days later, never at once.
        cid = await self.make(GUILD_A, "Ancient", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 700 * DAY)
        now = T0 + 200 * DAY
        first = await self.job().run_once(now)
        self.assertEqual((first.deleted, first.warnings), (0, 1))
        self.assertTrue(await self.exists(GUILD_A, cid))
        self.assertIn("will be deleted", self.sent[0][1])
        self.assertEqual((await self.job().run_once(now + DAY)).deleted, 0)
        self.assertTrue(await self.exists(GUILD_A, cid))
        self.assertEqual((await self.job().run_once(now + 3 * DAY)).deleted, 1)
        self.assertFalse(await self.exists(GUILD_A, cid))

    async def test_a_warning_nobody_received_does_not_license_a_deletion(self) -> None:
        cid = await self.make(GUILD_A, "Closed", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)

        async def nobody(user_id: int, text: str) -> bool:
            return False

        job = self.job()
        job._send = nobody
        for day in (57, 58, 61, 62):
            await job.run_once(T0 + day * DAY)
        self.assertTrue(await self.exists(GUILD_A, cid))

    async def test_a_session_after_the_warning_saves_the_campaign(self) -> None:
        cid = await self.make(GUILD_A, "Saved", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        now = T0 + 200 * DAY
        await self.job().run_once(now)  # warned, found past its date
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET last_played_at = %s WHERE id = %s", (now + DAY, cid)
            )
        out = await self.job().run_once(now + 4 * DAY)
        self.assertEqual(out.deleted, 0)
        self.assertTrue(await self.exists(GUILD_A, cid))

    async def test_a_session_starting_during_the_run_stops_the_delete(self) -> None:
        cid = await self.make(GUILD_A, "Racing", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        await self.job().run_once(T0 + 58 * DAY)
        job = self.job()
        original = job._running
        calls = {"n": 0}

        def running() -> set[str]:
            calls["n"] += 1
            return set() if calls["n"] == 1 else {cid}  # a session starts after the scan

        job._running = running
        out = await job.run_once(T0 + 61 * DAY)
        self.assertEqual(out.deleted, 0)
        self.assertTrue(await self.exists(GUILD_A, cid))
        del original

    async def test_a_wrong_clock_touches_nothing(self) -> None:
        cid = await self.make(GUILD_A, "Future", ALICE, T0 + 500 * DAY)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE campaigns SET created_at = %s", (T0 + 500 * DAY,))
        with self.assertLogs("dmbot.retention", "ERROR"):
            out = await self.job().run_once(T0)
        self.assertTrue(out.guard_stopped)
        self.assertTrue(await self.exists(GUILD_A, cid))

    async def test_a_campaign_restored_from_an_old_backup_starts_its_clock_now(self) -> None:
        cid = await self.make(GUILD_A, "Old", ALICE, T0)
        data = await self.store.export(GUILD_A, cid)
        self.now = T0 + 400 * DAY
        restored = await self.store.import_backup(GUILD_B, data, BOB, replace_campaign_id=None)
        got = await self.store.get(GUILD_B, restored.id)
        assert got is not None
        self.assertEqual(got.last_active_at, self.now)

    async def test_the_old_warning_cannot_license_a_later_deletion(self) -> None:
        cid = await self.make(GUILD_A, "Again", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 900 * DAY)
        await self.job().run_once(T0 + 58 * DAY)  # stage 2 for the first date
        async with self.db.guild(GUILD_A) as conn:  # played on day 59
            await conn.execute(
                "UPDATE campaigns SET last_played_at = %s WHERE id = %s", (T0 + 59 * DAY, cid)
            )
        await self.job().run_once(T0 + 60 * DAY)  # the job sees the date has moved: resets
        # Then nobody looks for a long time; when the new date passes there is no licence.
        out = await self.job().run_once(T0 + 59 * DAY + 61 * DAY)
        self.assertEqual(out.deleted, 0)
        self.assertTrue(await self.exists(GUILD_A, cid))

    async def test_a_server_the_bot_has_left_is_still_retained(self) -> None:
        # The list of servers comes from the database, not from the bot's own servers.
        a = await self.make(GUILD_A, "Left behind", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        self.assertEqual(await self.store.guilds_with_campaigns(), [GUILD_A, GUILD_B])
        await self.job().run_once(T0 + 58 * DAY)
        out = await self.job().run_once(T0 + 61 * DAY)
        self.assertEqual(out.deleted, 1)
        self.assertFalse(await self.exists(GUILD_A, a))

    async def test_an_empty_server_is_dropped_from_the_list(self) -> None:
        a = await self.make(GUILD_A, "Only", ALICE, T0)
        await self.make(GUILD_B, "Other", BOB, T0 + 300 * DAY)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM campaigns WHERE guild_id = %s AND id = %s", (GUILD_A, a)
            )
        await self.job().run_once(T0 + 10 * DAY)
        self.assertEqual(await self.store.guilds_with_campaigns(), [GUILD_B])
        self.assertFalse(await self.store.forget_guild_if_empty(GUILD_B))  # still has one

    async def test_an_owner_with_several_campaigns_gets_one_message(self) -> None:
        for n in range(3):
            await self.make(GUILD_A, f"Tale {n}", ALICE, T0)
        for n in range(8):  # filler so the guard stays out of it
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        keep = plans.load().by_id["try-it"].keep_after_last_session.days
        self.now = T0 + keep * DAY - 14 * DAY
        out = await self.job().run_once(self.now)
        self.assertEqual(out.warnings, 3)  # every campaign counts as warned
        self.assertEqual([u for u, _ in self.sent], [ALICE])
        for n in range(3):
            self.assertIn(f"Tale {n}", self.sent[0][1])
        self.assertEqual((await self.job().run_once(self.now + 3600)).warnings, 0)
        self.assertEqual(len(self.sent), 1)  # remembered: none repeated

    async def test_a_long_list_is_split_into_messages_of_a_few_campaigns(self) -> None:
        for n in range(7):
            await self.make(GUILD_A, f"Tale {n}", ALICE, T0)
        for n in range(80):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)
        keep = plans.load().by_id["try-it"].keep_after_last_session.days
        out = await self.job().run_once(T0 + keep * DAY - 14 * DAY)
        self.assertEqual(out.warnings, 7)
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all(len(text) < 1900 for _u, text in self.sent))

    async def test_a_left_server_nobody_can_reach_is_never_deleted(self) -> None:
        a = await self.make(GUILD_A, "Unreachable", ALICE, T0)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", BOB, T0 + 300 * DAY)

        async def nobody(user_id: int, text: str) -> bool:
            return False

        self.send = nobody  # type: ignore[method-assign]
        await self.job().run_once(T0 + 58 * DAY)
        out = await self.job().run_once(T0 + 70 * DAY)
        self.assertEqual(out.deleted, 0)
        self.assertTrue(await self.exists(GUILD_A, a))

    async def test_a_campaign_whose_owner_left_goes_120_days_after_and_its_dms_are_told(
        self,
    ) -> None:
        # Played yesterday on a plan with a year's keep: the countdown is still 120 days
        # from the deletion, and a session doesn't restart it (#1059).
        cid = await self.make(GUILD_A, "Left behind", ALICE, T0 + 100 * DAY)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL, owner_left_at = %s"
                " WHERE guild_id = %s AND id = %s",
                (T0 + 100 * DAY, GUILD_A, cid),
            )
        await self.store.add_dm(GUILD_A, cid, BOB)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", CAROL, T0 + 300 * DAY)
        left = T0 + 100 * DAY
        self.assertEqual((await self.job().run_once(left + 50 * DAY)).warnings, 0)
        first = await self.job().run_once(left + 106 * DAY)  # 14 days before
        self.assertEqual(first.warnings, 1)
        warned = [text for user, text in self.sent if user == BOB]
        self.assertTrue(warned and "deleted their account" in warned[0])
        self.assertIn("Take it on", warned[0])
        await self.job().run_once(left + 117 * DAY)  # the 3-day warning
        self.assertTrue(await self.exists(GUILD_A, cid))
        out = await self.job().run_once(left + 121 * DAY)
        self.assertEqual(out.deleted, 1)
        self.assertFalse(await self.exists(GUILD_A, cid))

    async def test_taking_it_on_before_the_date_keeps_the_campaign(self) -> None:
        cid = await self.make(GUILD_A, "Rescued", ALICE, T0)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL, owner_left_at = %s"
                " WHERE guild_id = %s AND id = %s",
                (T0, GUILD_A, cid),
            )
        await self.store.add_dm(GUILD_A, cid, BOB)
        for n in range(9):
            await self.make(GUILD_B, f"x{n}", CAROL, T0 + 300 * DAY)
        await self.job().run_once(T0 + 106 * DAY)
        self.assertEqual(
            await self.store.take_ownership(GUILD_A, cid, BOB, T0 + 107 * DAY), "taken"
        )
        self.standings[BOB] = Standing("pro", None, True)
        out = await self.job().run_once(T0 + 121 * DAY)
        self.assertEqual(out.deleted, 0)
        self.assertTrue(await self.exists(GUILD_A, cid))


class Migration0045(DatabaseTest):
    """Migrations run with no server set, so the backfill must open `campaigns` for itself,
    or the list of servers starts empty and the job reaches none of the old ones."""

    async def test_servers_that_already_hold_campaigns_are_listed(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0045"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        old = CampaignStore(self.db, clock=lambda: 1)
        await old.create(GUILD_A, "One", ALICE)
        await old.create(GUILD_A, "Two", ALICE)
        await old.create(GUILD_B, "Three", BOB)
        await self.db.close()
        self.db = await Database.open(TEST_URL, schema=self.schema)
        store = CampaignStore(self.db, clock=lambda: 1)
        self.assertEqual(await store.guilds_with_campaigns(), [GUILD_A, GUILD_B])
        await store.create(333, "New", ALICE)  # and the trigger keeps it going
        self.assertEqual(await store.guilds_with_campaigns(), [GUILD_A, GUILD_B, 333])
