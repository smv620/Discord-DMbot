"""The calendar's own words are never offered as new names (#5364 slice 4b): its months, seasons,
festivals and weekdays join what the name finders leave out. Needs Postgres for the store."""

from __future__ import annotations

import unittest

from psycopg.types.json import Jsonb

from dmbot.timebot import calendar as cal
from dmbot.timebot import layout as lay
from tests.test_memory_store import GUILD_A, MemoryTest

VALE = lay.Layout(
    "Vale",
    (
        lay.Part("Spring", 90, lay.SEASON),
        lay.Part("Summer", 90, lay.SEASON),
        lay.Part("Longnight", 1, lay.FESTIVAL),
    ),
    lay.Leap(4, "Extra Day", "Summer"),
    ("Moonday", "Fireday"),
)
OWN = cal.Calendar(cal.CUSTOM, layout=VALE, layout_start=VALE.start_on(1201, "Summer", 2))


class TheNames(unittest.TestCase):
    def test_a_calendar_of_its_own_gives_every_name_it_has(self) -> None:
        got = cal.names_of(cal.CUSTOM, cal.def_of(OWN))
        self.assertEqual(
            set(got), {"Spring", "Summer", "Longnight", "Extra Day", "Moonday", "Fireday"}
        )

    def test_harptos_gives_its_months_and_festivals_and_days_none(self) -> None:
        got = cal.names_of(cal.HARPTOS, None)
        self.assertIn("Flamerule", got)
        self.assertIn("Shieldmeet", got)
        self.assertEqual(cal.names_of(cal.DAYS, None), ())

    def test_a_definition_that_no_longer_reads_gives_none_and_never_raises(self) -> None:
        for bad in (None, {}, {"layout": {}, "weekday": 0}, "x", 5, []):
            self.assertEqual(cal.names_of(cal.CUSTOM, bad), (), str(bad))


class TheStore(MemoryTest):
    async def put_calendar(
        self, kind: str, year: int | None, ordinal: int | None, d: object
    ) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM game_clocks WHERE guild_id = %s", (GUILD_A,))
            await conn.execute(
                "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest,"
                " updated_at, calendar_kind, calendar_year, calendar_ordinal, calendar_def)"
                " VALUES (%s, %s, 0, 0, 1, %s, %s, %s, %s)",
                (GUILD_A, self.c, kind, year, ordinal, None if d is None else Jsonb(d)),
            )

    async def test_the_names_of_the_calendar_are_never_new_names(self) -> None:
        before = await self.memory.known_keys(GUILD_A, self.c)
        self.assertNotIn("summer", before)
        await self.put_calendar("custom", 1201, 91, cal.def_of(OWN))
        keys = await self.memory.known_keys(GUILD_A, self.c)
        for name in ("spring", "summer", "longnight", "extra day", "moonday", "fireday"):
            self.assertIn(name, keys)
        self.assertTrue(before <= keys)  # (the names it already knew are all still there)

    async def test_harptos_and_days_and_a_broken_calendar(self) -> None:
        await self.put_calendar("harptos", 1492, 195, None)
        self.assertIn("flamerule", await self.memory.known_keys(GUILD_A, self.c))
        await self.put_calendar("days", None, None, None)
        self.assertNotIn("flamerule", await self.memory.known_keys(GUILD_A, self.c))
        await self.put_calendar("custom", 1201, 91, {"layout": {}, "weekday": 0})
        self.assertNotIn("summer", await self.memory.known_keys(GUILD_A, self.c))  # (never raises)

    async def test_no_game_clock_is_no_names(self) -> None:
        self.assertNotIn("summer", await self.memory.known_keys(GUILD_A, self.c))
