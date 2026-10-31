"""The game clock's log (#3091, part 3, migration 0305): every change to a clock is logged by the
database, a moment of real time reads the clock as it stood then, and the log follows backups,
splits and the campaign. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.timebot.clock import Clock
from dmbot.timebot.stamp import game_time_at
from dmbot.timebot.store import ClockSection
from tests import test_timebot as base
from tests.test_timebot import DM, GUILD_A, GUILD_B


class Logging(base.Clocks):
    async def rows(self, guild: int, campaign: str) -> list[tuple[Any, ...]]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT position, minute FROM game_clock_log WHERE guild_id = %s"
                " AND campaign_id = %s ORDER BY seq",
                (guild, campaign),
            )
            return [(r["position"], r["minute"]) for r in await cur.fetchall()]

    async def at(self, guild: int, campaign: str, seconds: int) -> None:
        """Say the newest log rows happened at this real time (the test sets the clock's time)."""
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "UPDATE game_clock_log SET at = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND seq = (SELECT max(seq) FROM game_clock_log WHERE guild_id = %s"
                " AND campaign_id = %s)",
                (seconds, guild, campaign, guild, campaign),
            )

    async def test_setting_and_moving_the_clock_each_write_a_row(self) -> None:
        self.assertEqual(await self.rows(GUILD_A, self.a.id), [])
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(1000, 0))
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda c: Clock(1060, 0))
        self.assertEqual(await self.rows(GUILD_A, self.a.id), [(None, 1000), (None, 1060)])

    async def test_a_change_that_leaves_the_minute_alone_writes_none(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(1000, 0))
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda c: Clock(1000, 0, None, None, 5))
        self.assertEqual(await self.rows(GUILD_A, self.a.id), [(None, 1000)])

    async def test_a_split_writes_a_row_for_each_group_and_a_rejoin_the_main_clock(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(2000, 0))
        await self.clocks.split(GUILD_A, self.a.id, DM, ["Tower", "Camp"])
        self.assertEqual(await self.rows(GUILD_A, self.a.id), [(None, 2000), (1, 2000), (2, 2000)])
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda c: Clock(2060, 0))
        self.assertEqual((await self.rows(GUILD_A, self.a.id))[-1], (1, 2060))

    async def minute(self, guild: int, campaign: str, at: int, group: int | None = None) -> Any:
        async with self.db.guild(guild) as conn:
            return await game_time_at(conn, guild, campaign, at, group)

    async def test_a_moment_of_real_time_reads_the_clock_as_it_stood_then(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(1000, 0))
        await self.at(GUILD_A, self.a.id, 5000)
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda c: Clock(1500, 0))
        await self.at(GUILD_A, self.a.id, 6000)
        m = self.minute
        self.assertIsNone(await m(GUILD_A, self.a.id, 4999))  # before the first row
        self.assertEqual(await m(GUILD_A, self.a.id, 5000), 1000)
        self.assertEqual(await m(GUILD_A, self.a.id, 5999), 1000)
        self.assertEqual(await m(GUILD_A, self.a.id, 6000), 1500)
        self.assertEqual(await m(GUILD_A, self.a.id, 10**9), 1500)

    async def test_no_clock_or_another_campaigns_clock_is_not_known(self) -> None:
        await self.clocks.change(GUILD_B, self.b.id, DM, lambda _c: Clock(7, 0))
        self.assertIsNone(await self.minute(GUILD_A, self.a.id, 10**9))
        self.assertIsNone(await self.minute(GUILD_A, self.b.id, 10**9))  # (wrong server too)

    async def test_a_speakers_group_gets_its_own_clock_and_the_main_one_is_not_known_while_split(
        self,
    ) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(2000, 0))
        await self.at(GUILD_A, self.a.id, 100)
        await self.clocks.split(GUILD_A, self.a.id, DM, ["Tower", "Camp"])
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE game_clock_log SET at = 200 WHERE guild_id = %s AND campaign_id = %s"
                " AND position IS NOT NULL",
                (GUILD_A, self.a.id),
            )
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda c: Clock(2090, 0))  # group 1
        await self.at(GUILD_A, self.a.id, 300)
        m = self.minute
        self.assertEqual(await m(GUILD_A, self.a.id, 150), 2000)  # before the split: the main clock
        self.assertEqual(await m(GUILD_A, self.a.id, 350, 1), 2090)  # group 1, its own clock
        self.assertEqual(await m(GUILD_A, self.a.id, 350, 2), 2000)  # group 2, still where it was
        self.assertIsNone(await m(GUILD_A, self.a.id, 350))  # no group given: not the stale main
        self.assertIsNone(await m(GUILD_A, self.a.id, 350, 3))  # a group that was never there

    async def test_the_log_goes_with_a_backup_and_a_file_from_before_has_none(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(1000, 0))
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda c: Clock(1500, 0))
        section = ClockSection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.a.id)
        self.assertEqual([r["minute"] for r in rows[0]["log"]], [1000, 1500])
        copy = await self.campaigns.create(GUILD_B, "Copy", DM)
        async with self.db.guild(GUILD_B) as conn:
            await section.load(conn, GUILD_B, copy.id, rows)
        self.assertEqual(await self.rows(GUILD_B, copy.id), [(None, 1000), (None, 1500)])
        old = [{k: v for k, v in rows[0].items() if k != "log"}]
        other = await self.campaigns.create(GUILD_B, "Old", DM)
        async with self.db.guild(GUILD_B) as conn:
            await section.load(conn, GUILD_B, other.id, old)
        self.assertEqual(await self.rows(GUILD_B, other.id), [])  # (nothing of "now" is left)

    async def test_a_damaged_log_in_a_file_is_refused(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(1000, 0))
        section = ClockSection()
        async with self.db.guild(GUILD_A) as conn:
            good = (await section.dump(conn, GUILD_A, self.a.id))[0]
        entry = {"position": None, "at": 5, "minute": 7}
        for bad in (
            [{**entry, "minute": -1}],
            [{**entry, "at": 0}],
            [{**entry, "position": 4}],
            [{**entry, "extra": 1}],
            [{"at": 5, "minute": 7}],
            "text",
            [{**entry, "minute": True}],
        ):
            with self.subTest(str(bad)[:40]), self.assertRaises(Exception):  # noqa: B017
                section.check([{**good, "log": bad}])
