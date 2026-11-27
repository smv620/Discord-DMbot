"""The campaign's calendar setting (#3091, part 3, migration 0307): on the clock's row, DM-only,
read back as words, carried by backups. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.timebot import calendar as cal
from dmbot.timebot import layout as lay
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


SEASONS = lay.four_seasons(90)
VALE = Calendar(cal.CUSTOM, layout=SEASONS, layout_start=SEASONS.start_on(1201, "Summer", 2))


class OwnCalendar(ClockCase):
    """A campaign's own calendar (#5364 slice 2, migration 0653): on the clock's row, DM-only,
    carried by a backup, and never touching the game minute."""

    async def set_vale(self, guild: int, campaign: str, user: int) -> None:
        """Day 1 is the 2nd of Summer, 1201."""
        await self.clocks.set_custom_calendar(
            guild, campaign, user, SEASONS, year=1201, name="Summer", day=2
        )

    async def test_a_dm_sets_it_and_the_clocks_days_read_as_its_dates(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(3 * MINUTES_PER_DAY, 0))
        await self.set_vale(GUILD_A, self.a.id, DM)
        got = await self.clocks.calendar(GUILD_A, self.a.id)
        self.assertEqual(got, VALE)
        self.assertEqual(got.words(0), "Summer 2nd, 1201")
        self.assertEqual(got.words(3 * MINUTES_PER_DAY), "Summer 5th, 1201")
        stored = await self.clocks.get(GUILD_A, self.a.id)  # (game time is untouched)
        assert stored is not None
        self.assertEqual(stored.clock.minute, 3 * MINUTES_PER_DAY)

    async def test_another_calendar_replaces_it_and_takes_the_definition_with_it(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        await self.set_vale(GUILD_A, self.a.id, DM)
        await self.set_flame(GUILD_A, self.a.id, DM)
        self.assertEqual(await self.clocks.calendar(GUILD_A, self.a.id), FLAME)
        await self.set_vale(GUILD_A, self.a.id, DM)
        await self.clocks.set_calendar(GUILD_A, self.a.id, DM, "days")
        self.assertEqual(await self.clocks.calendar(GUILD_A, self.a.id), Calendar())
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT calendar_def FROM game_clocks")
            self.assertEqual([r["calendar_def"] for r in await cur.fetchall()], [None])

    async def test_only_a_dm_only_with_a_clock_and_only_a_date_it_has(self) -> None:
        with self.assertRaises(CampaignError) as no_clock:
            await self.set_vale(GUILD_A, self.a.id, DM)
        self.assertEqual(str(no_clock.exception), NO_CLOCK)
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        with self.assertRaises(CampaignError) as not_dm:
            await self.set_vale(GUILD_A, self.a.id, PLAYER)
        self.assertEqual(str(not_dm.exception), NOT_A_DM)
        await self.set_vale(GUILD_A, self.a.id, DM)
        bad: tuple[dict[str, Any], ...] = (
            {"year": 1201, "name": "Nope", "day": 1},
            {"year": 1201, "name": "Summer", "day": 91},
            {"year": 0, "name": "Summer", "day": 1},
            {"year": 1201, "name": "Summer", "day": True},
            {"year": 1201, "name": "Summer", "day": 1, "weekday": "Moonday"},  # (it has none)
        )
        for kwargs in bad:
            with self.subTest(str(kwargs)), self.assertRaises(CampaignError) as why:
                await self.clocks.set_custom_calendar(GUILD_A, self.a.id, DM, SEASONS, **kwargs)
            self.assertEqual(str(why.exception), BAD_CALENDAR)
        with self.assertRaises(CampaignError):
            await self.clocks.set_custom_calendar(
                GUILD_A,
                self.a.id,
                DM,
                "Vale",  # type: ignore[arg-type]
                year=1201,
                name="Summer",
            )
        self.assertEqual(await self.clocks.calendar(GUILD_A, self.a.id), VALE)  # (left alone)

    async def test_one_campaigns_calendar_is_not_anothers(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        await self.clocks.change(GUILD_B, self.b.id, DM, lambda _c: Clock(0, 0))
        await self.set_vale(GUILD_A, self.a.id, DM)
        self.assertEqual(await self.clocks.calendar(GUILD_B, self.b.id), Calendar())

    async def test_the_weekday_of_day_one_is_kept(self) -> None:
        week = lay.Layout("Vale", SEASONS.parts, weekdays=("Sunday", "Moonday", "Fireday"))
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        await self.clocks.set_custom_calendar(
            GUILD_A, self.a.id, DM, week, year=1201, name="Spring", day=1, weekday="Fireday"
        )
        got = await self.clocks.calendar(GUILD_A, self.a.id)
        assert got.layout is not None and got.layout_start is not None
        self.assertEqual(got.layout.weekday_of(1, got.layout_start), "Fireday")
        self.assertEqual(got.layout.weekday_of(2, got.layout_start), "Sunday")

    async def test_the_database_keeps_kind_and_definition_together(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        for statement in (
            "UPDATE game_clocks SET calendar_kind = 'custom', calendar_year = 1201,"
            " calendar_ordinal = 5",  # (a custom calendar with no definition)
            "UPDATE game_clocks SET calendar_def = '{}'::jsonb",  # (a definition with kind days)
            "UPDATE game_clocks SET calendar_kind = 'custom', calendar_year = 1201,"
            " calendar_ordinal = 1000, calendar_def = '{}'::jsonb",  # (a day past the longest year)
            "UPDATE game_clocks SET calendar_kind = 'custom', calendar_year = 1201,"
            " calendar_ordinal = 5, calendar_def = '[]'::jsonb",  # (not an object)
        ):
            with self.subTest(statement[:70]):
                async with self.db.guild(GUILD_A) as conn:
                    with self.assertRaises(Exception):  # noqa: B017 (a check violation)
                        await conn.execute(statement)

    async def test_a_clock_row_from_before_keeps_its_calendar(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        await self.set_flame(GUILD_A, self.a.id, DM)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT calendar_kind, calendar_ordinal, calendar_def FROM game_clocks"
            )
            (row,) = await cur.fetchall()
        self.assertEqual((row["calendar_kind"], row["calendar_ordinal"], row["calendar_def"]),
                         ("harptos", 195, None))  # fmt: skip

    async def test_a_backup_carries_it_and_an_older_one_loads_as_it_did(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        await self.set_vale(GUILD_A, self.a.id, DM)
        section = ClockSection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.a.id)
        saved = rows[0]["calendar"]
        self.assertEqual((saved["kind"], saved["year"], saved["ordinal"]), ("custom", 1201, 91))
        self.assertEqual(saved["def"], cal.def_of(VALE))
        copy = await self.campaigns.create(GUILD_B, "Copy", DM)
        async with self.db.guild(GUILD_B) as conn:
            await section.load(conn, GUILD_B, copy.id, rows)
        self.assertEqual(await self.clocks.calendar(GUILD_B, copy.id), VALE)
        # a campaign restored over one that had a calendar loses it, as any section's data does
        old = [{k: v for k, v in rows[0].items() if k != "calendar"}]
        async with self.db.guild(GUILD_B) as conn:
            await section.load(conn, GUILD_B, copy.id, old)
        self.assertEqual(await self.clocks.calendar(GUILD_B, copy.id), Calendar())

    async def test_a_damaged_own_calendar_in_a_file_is_refused(self) -> None:
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))
        section = ClockSection()
        async with self.db.guild(GUILD_A) as conn:
            good = (await section.dump(conn, GUILD_A, self.a.id))[0]
        fine = {"kind": "custom", "year": 1201, "ordinal": 91, "def": cal.def_of(VALE)}
        section.check([{**good, "calendar": fine}])
        parts: Any = cal.def_of(VALE)["layout"]
        for bad in (
            {k: v for k, v in fine.items() if k != "def"},  # (no definition)
            {**fine, "def": None},
            {**fine, "ordinal": 360},  # (a day the year has not got)
            {**fine, "year": True},
            {**fine, "extra": 1},
            {**fine, "def": {"layout": {**parts, "extra": 1}, "weekday": 0}},
            {**fine, "def": {"layout": parts, "weekday": 3}},  # (no weekdays)
            {**fine, "kind": "days"},  # (a plain kind with a definition)
        ):
            with self.subTest(str(bad)[:60]), self.assertRaises(CampaignError):
                section.check([{**good, "calendar": bad}])
