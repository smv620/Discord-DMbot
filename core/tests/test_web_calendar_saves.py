"""The bot's side of saving a calendar from the website (#5364 slice 3b, migration 0654): claimed in
order, saved with Discord's own store calls, ended with an error word, retaken when a claim goes
quiet, swept after a day. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from dmbot.memory import web_calendar_saves as saves
from dmbot.timebot import calendar as cal
from dmbot.timebot import layout as lay
from dmbot.timebot.calendar import Calendar
from dmbot.timebot.clock import Clock
from tests.test_clock_calendar import ClockCase
from tests.test_timebot import DM, GUILD_A, PLAYER

SEASONS = lay.four_seasons(90)
BODY: dict[str, Any] = {
    "kind": "custom",
    "def": SEASONS.to_json(),
    "dayOne": {"year": 1201, "part": "Summer", "day": 2},
}


class Saving(ClockCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = saves.SaveStore(self.db)
        await self.clocks.change(GUILD_A, self.a.id, DM, lambda _c: Clock(0, 0))

    async def queue(self, body: Any, user: int = DM, campaign: str | None = None) -> str:
        rid = "ab" * 16
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM web_calendar_saves WHERE guild_id = %s AND id = %s", (GUILD_A, rid)
            )
            await conn.execute(
                "INSERT INTO web_calendar_saves (guild_id, campaign_id, id, user_id, body,"
                " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 1, 1)",
                (GUILD_A, campaign or self.a.id, rid, user, Jsonb(body)),
            )
        return rid

    async def work(self) -> tuple[saves.Item, str]:
        item = await self.store.claim_next(GUILD_A)
        assert item is not None
        return item, await saves.run_one(self.clocks, self.store, GUILD_A, item)

    async def row(self, rid: str) -> tuple[str, str]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT state, error FROM web_calendar_saves WHERE id = %s", (rid,)
            )
            r = await cur.fetchone()
            assert r is not None
            return str(r["state"]), str(r["error"])

    async def test_a_calendar_of_its_own_is_saved_as_discords_setting_saves_it(self) -> None:
        rid = await self.queue(BODY)
        _, error = await self.work()
        self.assertEqual((error, await self.row(rid)), ("", ("done", "")))
        got = await self.clocks.calendar(GUILD_A, self.a.id)
        self.assertEqual(got.kind, cal.CUSTOM)
        self.assertEqual(got.words(0), "Summer 2nd, 1201")

    async def test_harptos_and_days_are_saved_and_a_custom_one_is_taken_away(self) -> None:
        await self.queue(BODY)
        await self.work()
        rid = await self.queue(
            {"kind": "harptos", "dayOne": {"year": 1492, "part": "Flamerule", "day": 14}}
        )
        await self.work()
        self.assertEqual(
            await self.clocks.calendar(GUILD_A, self.a.id),
            Calendar(cal.HARPTOS, cal.day_one(1492, "Flamerule", 14)),
        )
        self.assertEqual(await self.row(rid), ("done", ""))
        await self.queue(BODY)
        await self.work()
        rid = await self.queue({"kind": "days"})
        await self.work()
        self.assertEqual(await self.clocks.calendar(GUILD_A, self.a.id), Calendar())
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT calendar_def FROM game_clocks")
            self.assertEqual([r["calendar_def"] for r in await cur.fetchall()], [None])

    async def test_the_weekday_of_day_one_is_saved(self) -> None:
        week = lay.Layout("Vale", SEASONS.parts, weekdays=("Sunday", "Moonday", "Fireday"))
        body = {
            "kind": "custom",
            "def": week.to_json(),
            "dayOne": {"year": 1201, "part": "Spring", "day": 1, "weekday": "Fireday"},
        }
        await self.queue(body)
        _, error = await self.work()
        self.assertEqual(error, "")
        got = await self.clocks.calendar(GUILD_A, self.a.id)
        assert got.layout is not None and got.layout_start is not None
        self.assertEqual(got.layout.weekday_of(1, got.layout_start), "Fireday")

    async def test_each_refusal_ends_the_row_with_its_word(self) -> None:
        bad = {**BODY, "dayOne": {"year": 1201, "part": "Nope", "day": 1}}
        rid = await self.queue(bad)
        self.assertEqual((await self.work())[1], saves.BAD_CALENDAR_ERROR)
        self.assertEqual(await self.row(rid), ("failed", "bad_calendar"))
        await self.queue(BODY, user=PLAYER)  # (no longer, or never, a DM of the campaign)
        self.assertEqual((await self.work())[1], saves.NOT_ALLOWED)
        self.assertEqual(await self.clocks.calendar(GUILD_A, self.a.id), Calendar())  # (untouched)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM game_clocks WHERE campaign_id = %s", (self.a.id,))
        await self.queue(BODY)
        self.assertEqual((await self.work())[1], saves.NO_CLOCK_ERROR)

    async def test_a_body_nobody_checked_is_never_saved(self) -> None:
        for odd in ({}, {"kind": "gregorian"}, {"kind": "custom", "def": {}}):
            await self.queue(odd)
            self.assertEqual((await self.work())[1], saves.BAD_CALENDAR_ERROR, str(odd))
        self.assertEqual(await self.clocks.calendar(GUILD_A, self.a.id), Calendar())

    async def test_one_save_is_open_at_a_time_and_a_quiet_claim_is_retaken(self) -> None:
        rid = await self.queue(BODY)
        with self.assertRaises(Exception):  # noqa: B017 (the one-open index)
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO web_calendar_saves (guild_id, campaign_id, id, user_id, body,"
                    " created_at, updated_at) VALUES (%s, %s, %s, %s, '{}'::jsonb, 1, 1)",
                    (GUILD_A, self.a.id, "cd" * 16, DM),
                )
        first = await self.store.claim_next(GUILD_A)
        assert first is not None
        self.assertIsNone(await self.store.claim_next(GUILD_A))  # (it is taken)
        async with self.db.guild(GUILD_A) as conn:  # the claim went quiet for ten minutes
            await conn.execute("UPDATE web_calendar_saves SET claimed_at = 1 WHERE id = %s", (rid,))
        again = await self.store.claim_next(GUILD_A)
        assert again is not None
        self.assertFalse(await self.store.finish(GUILD_A, first, ""))  # (the old claim is stale)
        self.assertTrue(await self.store.finish(GUILD_A, again, ""))

    async def test_a_day_old_row_is_swept(self) -> None:
        rid = await self.queue(BODY)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE web_calendar_saves SET created_at = 1 WHERE id = %s", (rid,))
        self.assertEqual(await self.store.sweep(GUILD_A), 1)
