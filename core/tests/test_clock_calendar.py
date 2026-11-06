"""The campaign's calendar setting (#3091, part 3, migration 0307): on the clock's row, DM-only,
read back as words, carried by backups. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.timebot import calendar as cal
from dmbot.timebot.calendar import Calendar
from dmbot.timebot.clock import MINUTES_PER_DAY, Clock
from dmbot.timebot.store import BAD_CALENDAR, NO_CLOCK, NOT_A_DM, ClockSection, ClockStore
from tests.pg import DatabaseTest
from tests.test_timebot import DM, GUILD_A, GUILD_B, PLAYER

FLAME = Calendar(cal.HARPTOS, cal.day_one(1492, "Flamerule", 14))


class ClockCase(DatabaseTest):
    """Two campaigns in two servers and a store for their clocks (the setup of `test_timebot`,
    without its tests, which a subclass would run again)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(ClockSection())
        self.clocks = ClockStore(self.db)
        self.a = await self.campaigns.create(GUILD_A, "Alpha", DM)
        self.b = await self.campaigns.create(GUILD_B, "Beta", DM)
        await self.campaigns.add_dm(GUILD_A, self.a.id, 9)

    async def set_flame(self, guild: int, campaign: str, user: int) -> None:
        """Day 1 is Flamerule 14th, 1492 DR (what a picker would send)."""
        await self.clocks.set_calendar(
            guild, campaign, user, "harptos", year=1492, name="Flamerule", day=14
        )


class Setting(ClockCase):
    async def test_no_setting_reads_as_day_n_with_or_without_a_clock(self) -> None:
        self.assertEqual(await self.clocks.calendar(GUILD_A, self.a.id), Calendar())
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(11 * MINUTES_PER_DAY, 0))
        got = await self.clocks.calendar(GUILD_A, self.a.id)
        self.assertEqual(got.words(11 * MINUTES_PER_DAY), "Day 12")

    async def test_a_dm_picks_harptos_and_the_clocks_days_read_as_its_dates(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        await self.set_flame(GUILD_A, self.a.id, DM)
        got = await self.clocks.calendar(GUILD_A, self.a.id)
        self.assertEqual(got, FLAME)
        self.assertEqual(got.words(0), "Flamerule 14th, 1492 DR")
        self.assertEqual(got.words(3 * MINUTES_PER_DAY), "Flamerule 17th, 1492 DR")
        await self.clocks.set_calendar(GUILD_A, self.a.id, DM, "days")  # back to Day N
        self.assertEqual((await self.clocks.calendar(GUILD_A, self.a.id)).words(0), "Day 1")

    async def test_only_a_dm_and_only_once_there_is_a_clock(self) -> None:
        with self.assertRaises(CampaignError) as no_clock:
            await self.set_flame(GUILD_A, self.a.id, DM)
        self.assertEqual(str(no_clock.exception), NO_CLOCK)
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        with self.assertRaises(CampaignError) as not_dm:
            await self.set_flame(GUILD_A, self.a.id, PLAYER)
        self.assertEqual(str(not_dm.exception), NOT_A_DM)
        self.assertEqual(await self.clocks.calendar(GUILD_A, self.a.id), Calendar())

    async def test_one_campaigns_calendar_is_not_anothers(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        await self.clocks.change(GUILD_B, self.b.id, DM, lambda _c: Clock(0, 0))
        await self.set_flame(GUILD_A, self.a.id, DM)
        self.assertEqual(await self.clocks.calendar(GUILD_B, self.b.id), Calendar())

    async def test_the_database_refuses_harptos_with_no_day_one(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        async with self.db.guild(GUILD_A) as conn:
            with self.assertRaises(Exception):  # noqa: B017 (a check violation, whatever the class)
                await conn.execute(
                    "UPDATE game_clocks SET calendar_kind = 'harptos' WHERE guild_id = %s",
                    (GUILD_A,),
                )

    async def test_a_leap_day_in_a_common_year_reads_as_day_n_not_a_crash(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE game_clocks SET calendar_kind = 'harptos', calendar_year = 1493,"
                " calendar_ordinal = 365 WHERE guild_id = %s",
                (GUILD_A,),
            )
        self.assertEqual(await self.clocks.calendar(GUILD_A, self.a.id), Calendar())


class Refusing(ClockCase):
    async def test_a_value_the_calendar_has_not_got_is_refused_and_leaves_the_row_alone(
        self,
    ) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        await self.set_flame(GUILD_A, self.a.id, DM)
        bad: tuple[dict[str, Any], ...] = (
            {"kind": "gregorian"},
            {"kind": "harptos"},  # no date for Harptos
            {"kind": "harptos", "year": 1492},  # no month
            {"kind": "harptos", "name": "Flamerule"},  # no year
            {"kind": "harptos", "year": 0, "name": "Flamerule", "day": 1},
            {"kind": "harptos", "year": 1492, "name": "Nope", "day": 1},
            {"kind": "harptos", "year": 1492, "name": "Flamerule", "day": 31},
            {"kind": "harptos", "year": 1492, "name": "Flamerule", "day": 1.5},  # no rounding
            {"kind": "harptos", "year": 1492, "name": "Flamerule", "day": True},
            {"kind": "harptos", "year": 1492.0, "name": "Flamerule", "day": 3},
            {"kind": "harptos", "year": 1492, "name": "Midsummer", "day": 3},  # a festival has none
            {"kind": "harptos", "year": 1493, "name": "Shieldmeet"},  # no leap day in 1493
            {"kind": "harptos", "year": 1492, "name": 5, "day": 1},
        )
        for kwargs in bad:
            with self.subTest(str(kwargs)):
                kind = str(kwargs.pop("kind"))
                with self.assertRaises(CampaignError) as why:
                    await self.clocks.set_calendar(GUILD_A, self.a.id, DM, kind, **kwargs)
                self.assertEqual(str(why.exception), BAD_CALENDAR)
                self.assertEqual(await self.clocks.calendar(GUILD_A, self.a.id), FLAME)


class InBackups(ClockCase):
    async def test_a_backup_carries_it_and_a_file_without_one_means_day_n(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        await self.set_flame(GUILD_A, self.a.id, DM)
        section = ClockSection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.a.id)
        self.assertEqual(rows[0]["calendar"], {"kind": "harptos", "year": 1492, "ordinal": 195})
        copy = await self.campaigns.create(GUILD_B, "Copy", DM)
        async with self.db.guild(GUILD_B) as conn:
            await section.load(conn, GUILD_B, copy.id, rows)
        self.assertEqual(await self.clocks.calendar(GUILD_B, copy.id), FLAME)
        old = [{k: v for k, v in rows[0].items() if k != "calendar"}]
        other = await self.campaigns.create(GUILD_B, "Old", DM)
        async with self.db.guild(GUILD_B) as conn:
            await section.load(conn, GUILD_B, other.id, old)
        self.assertEqual(await self.clocks.calendar(GUILD_B, other.id), Calendar())

    async def test_a_damaged_calendar_in_a_file_is_refused(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        section = ClockSection()
        async with self.db.guild(GUILD_A) as conn:
            good = (await section.dump(conn, GUILD_A, self.a.id))[0]
        fine = {"kind": "harptos", "year": 1492, "ordinal": 3}
        section.check([{**good, "calendar": fine}])
        for bad in (
            "harptos",
            {**fine, "kind": "gregorian"},
            {**fine, "year": 0},
            {**fine, "ordinal": 400},
            {**fine, "ordinal": 365, "year": 1493},  # (no Shieldmeet in a common year)
            {**fine, "year": "1492"},
            {**fine, "extra": 1},
            {"kind": "harptos", "year": 1492},
            {**fine, "year": True},
        ):
            with self.subTest(str(bad)[:50]), self.assertRaises(CampaignError):
                section.check([{**good, "calendar": bad}])
