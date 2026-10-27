"""The bot's side of "Delete my transcripts" asked for on the website (#2780, #2769): a queued row
is carried out with exactly what Discord's 🗑 button runs, for the person who asked and nobody
else, with the counts left on the row; a failure deletes nothing and ends `failed`. Invented
names throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore, web_line_delete
from dmbot.campaigns.web_line_delete import WebLineDeletions
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

GUILD, OTHER_GUILD = 1, 2
DM, ME, FRIEND = 7, 8, 9
START = 1_700_000_000

NOW = 1_800_000_000


def line(seconds: int, user: int, text: str) -> Line:
    return Line(START * 1000 + seconds * 1000, user, text, text)


class TheDeletion(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        campaigns = CampaignStore(self.db)
        self.store = TranscriptStore(self.db)
        one = await campaigns.create(GUILD, "Frostmaiden", DM)
        two = await campaigns.create(GUILD, "Sunken City", DM)
        far = await campaigns.create(OTHER_GUILD, "Far Away", DM)
        self.first = await self.store.open_session(GUILD, one.id, START)
        self.second = await self.store.open_session(GUILD, two.id, START + 10)
        self.elsewhere = await self.store.open_session(OTHER_GUILD, far.id, START)
        await self.store.add_lines(
            GUILD,
            self.first,
            [line(1, ME, "one"), line(2, FRIEND, "two"), line(3, ME, "three")],
        )
        await self.store.add_lines(GUILD, self.second, [line(1, ME, "four"), line(2, DM, "five")])
        await self.store.add_lines(OTHER_GUILD, self.elsewhere, [line(1, ME, "far words")])
        self.clock = [float(NOW)]
        self.rows = WebLineDeletions(self.db, lambda: self.clock[0])
        bot: Any = DMBot.__new__(DMBot)  # (a bare bot: only what delete_my_lines reads is set)
        bot.transcripts = self.store
        bot.memory = None
        bot.tables = {}
        bot._ending = {}
        bot.web_line_deletions = self.rows
        self.bot = bot

    async def users(self, guild: int, session: str) -> list[int]:
        return [x.user_id for x in await self.store.lines(guild, session)]

    async def ask(self, user: int, guild: int = GUILD, rid: str = "a" * 32, at: int = NOW) -> str:
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "INSERT INTO web_line_deletions (guild_id, id, user_id, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (guild, rid, user, at, at),
            )
        return rid

    async def row(self, guild: int, rid: str) -> dict[str, Any]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT state, stage, error, lines, sessions FROM web_line_deletions"
                " WHERE guild_id = %s AND id = %s",
                (guild, rid),
            )
            found = await cur.fetchone()
        assert found is not None
        return dict(found)

    async def carry(self, guild: int = GUILD) -> str | None:
        item = await self.rows.claim_next(guild)
        if item is None:
            return None
        return await web_line_delete.carry_out(self.bot, guild, item, self.rows)

    async def test_the_person_who_asked_loses_their_lines_and_the_row_holds_the_counts(
        self,
    ) -> None:
        before = await self.store.person_counts(GUILD, ME)
        rid = await self.ask(ME)
        self.assertEqual(await self.carry(), "")
        got = await self.row(GUILD, rid)
        self.assertEqual(
            (got["state"], got["stage"], got["error"], got["lines"], got["sessions"]),
            ("done", "done", "", before.lines, before.sessions),
        )
        self.assertEqual((before.lines, before.sessions), (3, 2))
        self.assertEqual(await self.users(GUILD, self.first), [FRIEND])

    async def test_nobody_elses_lines_and_no_other_servers_are_touched(self) -> None:
        await self.ask(ME)
        await self.carry()
        self.assertEqual(await self.users(GUILD, self.second), [DM])  # the DM's reply stays
        self.assertEqual(
            await self.users(OTHER_GUILD, self.elsewhere), [ME]
        )  # same id, other server
        self.assertEqual((await self.store.person_counts(GUILD, FRIEND)).lines, 1)

    async def test_another_servers_request_is_not_carried_out_by_this_servers_loop(self) -> None:
        rid = await self.ask(ME, OTHER_GUILD)
        self.assertIsNone(await self.carry(GUILD))  # nothing waits here
        self.assertEqual((await self.row(OTHER_GUILD, rid))["state"], "waiting")
        self.assertEqual(await self.users(OTHER_GUILD, self.elsewhere), [ME])

    async def test_a_failure_deletes_nothing_and_ends_failed(self) -> None:
        rid = await self.ask(ME)
        with patch.object(self.store, "delete_person", AsyncMock(side_effect=RuntimeError("x"))):
            self.assertEqual(await self.carry(), web_line_delete.FAILED)
        got = await self.row(GUILD, rid)
        self.assertEqual((got["state"], got["stage"], got["error"]), ("failed", "failed", "failed"))
        self.assertEqual((got["lines"], got["sessions"]), (0, 0))
        self.assertEqual((await self.store.person_counts(GUILD, ME)).lines, 3)

    async def test_a_working_row_cannot_be_taken_inside_ten_minutes_but_can_after(self) -> None:
        rid = await self.ask(ME)
        first = await self.rows.claim_next(GUILD)
        assert first is not None
        self.clock[0] += 599
        self.assertIsNone(await self.rows.claim_next(GUILD))
        self.clock[0] += 2
        again = await self.rows.claim_next(GUILD)
        assert again is not None
        self.assertEqual(again.id, rid)
        # The first worker's claim is stale now: it cannot write over the second's.
        self.assertFalse(await self.rows.done(GUILD, first, 9, 9))
        self.assertTrue(await self.rows.done(GUILD, again, 1, 1))

    async def test_a_person_with_no_lines_left_ends_done_with_nothing(self) -> None:
        await self.store.delete_person(GUILD, ME)  # deleted in Discord meanwhile
        rid = await self.ask(ME)
        self.assertEqual(await self.carry(), "")
        got = await self.row(GUILD, rid)
        self.assertEqual((got["state"], got["lines"], got["sessions"]), ("done", 0, 0))

    async def test_a_request_older_than_a_day_is_not_taken_and_the_sweep_removes_it(self) -> None:
        rid = await self.ask(ME, at=NOW - web_line_delete.KEEP_FOR_S - 1)
        self.assertIsNone(await self.rows.claim_next(GUILD))
        self.assertEqual(await self.rows.sweep(GUILD), 1)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT 1 FROM web_line_deletions WHERE id = %s", (rid,))
            self.assertIsNone(await cur.fetchone())
        self.assertEqual((await self.store.person_counts(GUILD, ME)).lines, 3)

    async def test_the_bots_loop_only_serves_servers_it_is_in(self) -> None:
        here = await self.ask(ME)
        far = await self.ask(ME, OTHER_GUILD, "b" * 32)
        with patch.object(
            DMBot, "guilds", new_callable=PropertyMock, return_value=[SimpleNamespace(id=GUILD)]
        ):
            await DMBot.run_web_line_deletions(self.bot)
        self.assertEqual((await self.row(GUILD, here))["state"], "done")
        self.assertEqual((await self.row(OTHER_GUILD, far))["state"], "waiting")

    async def test_nothing_of_the_person_is_logged(self) -> None:
        await self.ask(ME)
        with self.assertLogs("dmbot", "INFO") as logged:
            await self.carry()
            web_line_delete.log.info("x")  # (assertLogs needs one record even when all is quiet)
        text = "\n".join(logged.output)
        self.assertNotIn(
            str(ME), text.replace("Deleted past transcripts: 3 lines in 2 sessions", "")
        )
        self.assertNotIn("three", text)
