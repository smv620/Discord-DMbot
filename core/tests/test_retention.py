"""Retention (#964): the keep date for every plan and the lapse rule, the 14- and 3-day
warnings (each once), the daily job's deletion through the campaign store, the 10% guard,
the setting off (a dry run only), and isolation. Needs Postgres for the store tests."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from dmbot import entitlements, plans, retention
from dmbot.campaigns import CampaignStore
from dmbot.retention import DAY, RetentionJob, Standing
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 111, 222
ALICE, BOB = 7, 8
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


class Job(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        self.now = T0
        self.store = CampaignStore(self.db, clock=lambda: self.now)
        self.sent: list[tuple[int, str]] = []
        self.standings: dict[int, Standing] = {}
        self.running: set[str] = set()
        self.enforce = True

    async def standing_of(self, guild_id: int, owner: int, now: int) -> Standing:
        return self.standings.get(owner, retention.UNKNOWN)

    async def send(self, user_id: int, text: str) -> bool:
        self.sent.append((user_id, text))
        return True

    def job(self, guilds: tuple[int, ...] = (GUILD_A, GUILD_B)) -> RetentionJob:
        return RetentionJob(
            campaigns=self.store,
            standing_of=self.standing_of,
            guild_ids=lambda: list(guilds),
            running=lambda: self.running,
            send=self.send,
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
