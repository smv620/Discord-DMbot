"""The website's Game calendar panel (#5364 slice 3a): the DM reads the calendar and previews one
being typed, worded by core; nobody else does. Real Postgres; invented names."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from dmbot.timebot import calendar as cal
from dmbot.timebot import layout as lay
from dmbot.web import calendar_setting
from dmbot.web.recaps import RecapError
from tests.test_scenes import Base

SEASONS = lay.four_seasons(90)


class OnThePanel(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{self.g}.{self.one.id}"

    async def put_clock(self) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest,"
                " updated_at) VALUES (%s, %s, 0, 0, 1)",
                (self.g, self.one.id),
            )

    async def test_a_campaign_with_no_clock_is_day_n_with_nowhere_to_save(self) -> None:
        got = await calendar_setting.read(self.web, self.session, self.cref)
        self.assertEqual(
            got, {"kind": "days", "def": None, "dayOne": None, "line": None, "hasClock": False}
        )

    async def test_the_dm_reads_each_kind_as_core_words_it(self) -> None:
        await self.put_clock()
        got = await calendar_setting.read(self.web, self.session, self.cref)
        self.assertEqual((got["kind"], got["hasClock"], got["line"]), ("days", True, None))
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE game_clocks SET calendar_kind = 'harptos', calendar_year = 1492,"
                " calendar_ordinal = 195 WHERE guild_id = %s",
                (self.g,),
            )
        got = await calendar_setting.read(self.web, self.session, self.cref)
        self.assertEqual(got["kind"], "harptos")
        self.assertIsNone(got["def"])
        self.assertEqual(got["line"], "Day 1 is Flamerule 14th, 1492 DR")
        self.assertEqual(
            got["dayOne"], {"year": 1492, "part": "Flamerule", "day": 14, "weekday": None}
        )
        mine = cal.Calendar(
            cal.CUSTOM, layout=SEASONS, layout_start=SEASONS.start_on(1201, "Summer", 2)
        )
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE game_clocks SET calendar_kind = 'custom', calendar_year = 1201,"
                " calendar_ordinal = 91, calendar_def = %s WHERE guild_id = %s",
                (Jsonb(cal.def_of(mine)), self.g),
            )
        got = await calendar_setting.read(self.web, self.session, self.cref)
        self.assertEqual(got["kind"], "custom")
        self.assertEqual(got["def"], SEASONS.to_json())
        self.assertEqual(got["line"], "Day 1 is Summer 2nd, 1201")
        self.assertEqual(got["dayOne"]["part"], "Summer")

    async def test_the_preview_says_the_line_or_why_not(self) -> None:
        body = {
            "kind": "custom",
            "def": SEASONS.to_json(),
            "dayOne": {"year": 1201, "part": "Summer", "day": 2},
        }
        got = await calendar_setting.preview(self.web, self.session, self.cref, body)
        self.assertEqual(got, {"line": "Day 1 is Summer 2nd, 1201"})
        body["dayOne"] = {"year": 1201, "part": "Summer", "day": 91}
        got = await calendar_setting.preview(self.web, self.session, self.cref, body)
        self.assertEqual(list(got), ["problems"])
        self.assertEqual(len(got["problems"]), 1)
        got = await calendar_setting.preview(self.web, self.session, self.cref, None)
        self.assertEqual(list(got), ["problems"])

    async def test_a_member_who_is_not_a_dm_gets_not_found(self) -> None:
        await self.put_clock()
        for call in (
            calendar_setting.read(self.web, self.player, self.cref),
            calendar_setting.preview(self.web, self.player, self.cref, {"kind": "days"}),
        ):
            with self.assertRaises(RecapError) as caught:
                await call
            self.assertEqual(caught.exception.code, "not_found")

    async def test_another_campaign_is_never_read(self) -> None:
        other = (await self.campaigns.create(self.g, "Elsewhere", 7)).id
        with self.assertRaises(RecapError) as caught:
            await calendar_setting.read(self.web, self.session, f"{self.g}.{other}")
        self.assertEqual(caught.exception.code, "not_found")
