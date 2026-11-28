"""The website's Game calendar panel (#5364 slice 3a): the DM reads the calendar and previews one
being typed, worded by core; nobody else does. Real Postgres; invented names."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from dmbot.timebot import calendar as cal
from dmbot.timebot import calendar_setting as setting
from dmbot.timebot import layout as lay
from dmbot.web import calendar_setting
from dmbot.web.recaps import RecapError
from tests.test_scenes import Base
from tests.test_web_relationships import TheRoute

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
        self.assertEqual(got["line"], "Day 1 is Summer 2nd, 1201")
        self.assertIsNone(got["today"])
        self.assertEqual(got["note"], setting.NO_CLOCK_NOTE)  # (no game clock yet: still works)
        body["dayOne"] = {"year": 1201, "part": "Summer", "day": 91}
        got = await calendar_setting.preview(self.web, self.session, self.cref, body)
        self.assertEqual(list(got), ["problems"])
        self.assertEqual(len(got["problems"]), 1)
        got = await calendar_setting.preview(self.web, self.session, self.cref, None)
        self.assertEqual(list(got), ["problems"])

    async def test_with_a_game_clock_the_preview_also_says_today(self) -> None:
        await self.put_clock()
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE game_clocks SET minute = %s WHERE guild_id = %s", (10 * 1440, self.g)
            )
        body = {
            "kind": "custom",
            "def": SEASONS.to_json(),
            "dayOne": {"year": 1201, "part": "Summer", "day": 2},
        }
        got = await calendar_setting.preview(self.web, self.session, self.cref, body)
        self.assertEqual(got["line"], "Day 1 is Summer 2nd, 1201")
        self.assertEqual(got["today"], "Today is Summer 12th, 1201")
        self.assertNotIn("note", got)  # (a clock is there: it can be saved)

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


class TheCalendarRoute(TheRoute):
    async def test_the_dm_reads_and_previews_and_a_player_is_not_found(self) -> None:
        body = {
            "kind": "custom",
            "def": SEASONS.to_json(),
            "dayOne": {"year": 1201, "part": "Summer", "day": 2},
        }
        async with await self.client(self.session) as client:
            got = await client.get(f"/me/campaigns/{self.cref}/calendar")
            self.assertEqual(got.status_code, 200)
            self.assertEqual(got.json()["kind"], "days")
            said = await client.post(f"/me/campaigns/{self.cref}/calendar/preview", json=body)
            self.assertEqual(said.status_code, 200)
            self.assertEqual(said.json()["line"], "Day 1 is Summer 2nd, 1201")
            self.assertIsNone(said.json()["today"])  # (no game clock yet)
            self.assertEqual(said.json()["note"], setting.NO_CLOCK_NOTE)
        async with await self.client(self.player) as client:
            for call in (
                client.get(f"/me/campaigns/{self.cref}/calendar"),
                client.post(f"/me/campaigns/{self.cref}/calendar/preview", json=body),
            ):
                self.assertEqual((await call).status_code, 404)

    async def test_a_body_over_the_cap_is_refused_unread_and_a_broken_one_is_words(self) -> None:
        async with await self.client(self.session) as client:
            big = await client.post(
                f"/me/campaigns/{self.cref}/calendar/preview",
                content=b'{"kind": "' + b"x" * setting.MAX_BODY + b'"}',
                headers={"content-type": "application/json"},
            )
            self.assertEqual(big.json(), {"problems": [setting.PROBLEMS["big"]]})
            broken = await client.post(
                f"/me/campaigns/{self.cref}/calendar/preview",
                content=b"{not json",
                headers={"content-type": "application/json"},
            )
            self.assertEqual(broken.json(), {"problems": [setting.PROBLEMS["layout"]]})
