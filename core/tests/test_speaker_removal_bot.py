"""The bot's side of a DM's "Remove this speaker's lines" (#3305): a queued row is carried out for
the one campaign and speaker it names, with the counts left on the row; a failure removes nothing
and ends `failed`; nothing of the speaker is logged. Invented names throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore, speaker_removal
from dmbot.campaigns.speaker_removal import SpeakerRemovals
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

GUILD, OTHER_GUILD = 1, 2
DM, ME, FRIEND = 7, 8, 9
START = 1_700_000_000
NOW = 1_800_000_000


def line(seconds: int, user: int, text: str) -> Line:
    return Line(START * 1000 + seconds * 1000, user, text, text)


class TheRemoval(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        campaigns = CampaignStore(self.db)
        self.store = TranscriptStore(self.db)
        self.one = await campaigns.create(GUILD, "Frostmaiden", DM)
        self.two = await campaigns.create(GUILD, "Sunken City", DM)
        self.far = await campaigns.create(OTHER_GUILD, "Far Away", DM)
        self.first = await self.store.open_session(GUILD, self.one.id, START)
        self.second = await self.store.open_session(GUILD, self.two.id, START + 10)
        self.elsewhere = await self.store.open_session(OTHER_GUILD, self.far.id, START)
        await self.store.add_lines(
            GUILD, self.first, [line(1, ME, "one"), line(2, FRIEND, "two"), line(3, ME, "three")]
        )
        await self.store.add_lines(GUILD, self.second, [line(1, ME, "four"), line(2, DM, "five")])
        await self.store.add_lines(OTHER_GUILD, self.elsewhere, [line(1, ME, "far words")])
        self.clock = [float(NOW)]
        self.rows = SpeakerRemovals(self.db, lambda: self.clock[0])
        bot: Any = DMBot.__new__(DMBot)  # (a bare bot: only what remove_speaker reads is set)
        bot.transcripts = self.store
        bot.tables = {}
        bot._ending = {}
        bot.speaker_removals = self.rows
        self.bot = bot

    async def users(self, guild: int, session: str) -> list[int]:
        return [x.user_id for x in await self.store.lines(guild, session)]

    async def ask(
        self, speaker: int, campaign: str | None = None, guild: int = GUILD, rid: str = "a" * 32
    ) -> str:
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "INSERT INTO speaker_removals (guild_id, campaign_id, id, requested_by, speaker,"
                " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (guild, campaign or self.one.id, rid, DM, speaker, NOW, NOW),
            )
        return rid

    async def row(self, guild: int, rid: str) -> dict[str, Any]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT state, stage, error, lines, sessions FROM speaker_removals"
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
        return await speaker_removal.carry_out(self.bot, guild, item, self.rows)

    async def test_the_speaker_loses_their_lines_in_that_campaign_only(self) -> None:
        rid = await self.ask(ME)
        self.assertEqual(await self.carry(), "")
        got = await self.row(GUILD, rid)
        self.assertEqual(
            (got["state"], got["stage"], got["error"], got["lines"], got["sessions"]),
            ("done", "done", "", 2, 1),
        )
        self.assertEqual(await self.users(GUILD, self.first), [FRIEND])
        self.assertEqual(await self.users(GUILD, self.second), [ME, DM])  # the other campaign
        self.assertEqual(await self.users(OTHER_GUILD, self.elsewhere), [ME])  # the other server

    async def test_another_servers_request_is_not_carried_out_by_this_servers_loop(self) -> None:
        rid = await self.ask(ME, self.far.id, OTHER_GUILD)
        self.assertIsNone(await self.carry(GUILD))
        self.assertEqual((await self.row(OTHER_GUILD, rid))["state"], "waiting")
        self.assertEqual(await self.users(OTHER_GUILD, self.elsewhere), [ME])

    async def test_a_failure_removes_nothing_and_ends_failed(self) -> None:
        rid = await self.ask(ME)
        with patch.object(self.store, "remove_speaker", AsyncMock(side_effect=RuntimeError("x"))):
            self.assertEqual(await self.carry(), speaker_removal.FAILED)
        got = await self.row(GUILD, rid)
        self.assertEqual((got["state"], got["stage"], got["error"]), ("failed", "failed", "failed"))
        self.assertEqual((got["lines"], got["sessions"]), (0, 0))
        self.assertEqual(await self.users(GUILD, self.first), [ME, FRIEND, ME])

    async def test_a_working_row_is_taken_again_only_after_ten_minutes(self) -> None:
        rid = await self.ask(ME)
        first = await self.rows.claim_next(GUILD)
        assert first is not None
        self.clock[0] += 600
        self.assertIsNone(await self.rows.claim_next(GUILD))
        self.clock[0] += 1
        again = await self.rows.claim_next(GUILD)
        assert again is not None
        self.assertEqual(again.id, rid)
        self.assertFalse(await self.rows.done(GUILD, first, 9, 9))  # the stale claim writes nothing
        self.assertTrue(await self.rows.done(GUILD, again, 1, 1))

    async def test_a_speaker_with_nothing_left_ends_done_with_nothing(self) -> None:
        await self.store.remove_speaker(GUILD, self.one.id, ME)  # removed meanwhile
        rid = await self.ask(ME)
        self.assertEqual(await self.carry(), "")
        got = await self.row(GUILD, rid)
        self.assertEqual((got["state"], got["lines"], got["sessions"]), ("done", 0, 0))

    async def test_the_bots_loop_only_serves_servers_it_is_in(self) -> None:
        here = await self.ask(ME)
        far = await self.ask(ME, self.far.id, OTHER_GUILD, "b" * 32)
        with patch.object(
            DMBot, "guilds", new_callable=PropertyMock, return_value=[SimpleNamespace(id=GUILD)]
        ):
            await DMBot.run_speaker_removals(self.bot)
        self.assertEqual((await self.row(GUILD, here))["state"], "done")
        self.assertEqual((await self.row(OTHER_GUILD, far))["state"], "waiting")

    async def test_nothing_of_the_speaker_is_logged(self) -> None:
        big = 8_000_000_000_000_001
        await self.store.add_lines(GUILD, self.first, [line(9, big, "secret words")])
        await self.ask(big)
        boom = RuntimeError(f"user {big} said secret words")
        with self.assertLogs("dmbot", "INFO") as logged:
            await self.carry()
        await self.ask(big, rid="c" * 32)
        with (
            patch.object(self.store, "remove_speaker", AsyncMock(side_effect=boom)),
            self.assertLogs("dmbot", "INFO") as failed,
        ):
            await self.carry()
        text = "\n".join(logged.output + failed.output)
        for needle in (str(big), "secret words"):
            self.assertNotIn(needle, text)
        self.assertIn("RuntimeError", text)
