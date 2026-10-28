"""Lines a DM puts back after their session are read once (#2587): the names scan and the story
extractor see just those lines, a speaker who stopped being recorded is skipped, an extractor that
stops early leaves them for the next try, and the poll loop does not wait for the read. Lines here
are made up."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
from dmbot.memory.extractor import Report
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

GUILD = 4141
DM, ALICE, BOB = 7, 11, 22
START = 1_700_000_000
BASE_MS = (START + 86_400) * 1000


def lines() -> list[Line]:
    return [
        Line(BASE_MS + 1_000, ALICE, "the pass is closed", "the pass is closed", 4000),
        Line(BASE_MS + 6_000, ALICE, "Kesh Varn waits", "Kesh Varn waits", 5000, "off_topic"),
        Line(BASE_MS + 12_000, BOB, "Orla Fenn knows", "Orla Fenn knows", 5000, "off_topic"),
        Line(BASE_MS + 20_000, BOB, "we climb", "we climb", 3000),
    ]


class Reading(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: START)
        self.cid = (await self.campaigns.create(GUILD, "Frozen Wastes", DM)).id
        self.store = TranscriptStore(self.db)
        self.sid = await self.store.open_session(GUILD, self.cid, START + 86_400)
        await self.store.add_lines(GUILD, self.sid, lines())
        await self.store.end_session(GUILD, self.sid, START + 90_000)
        async with self.db.guild(GUILD) as conn:  # the DM put the two off-topic lines back
            await conn.execute(
                "UPDATE transcript_lines SET topic = 'game', reread = TRUE, topic_by = %s"
                " WHERE session_id = %s AND heard IN ('Kesh Varn waits', 'Orla Fenn knows')",
                (DM, self.sid),
            )
        self.agreed = {ALICE, BOB}
        self.scanned: list[list[str]] = []
        dm: Any = DMBot.__new__(DMBot)
        self.dm = dm
        dm.transcripts = self.store
        dm.memory = object()
        dm.campaigns = self.campaigns
        dm.consent = SimpleNamespace(consenting=self.consenting)
        dm.settings = SimpleNamespace(
            story_extractor_on=False,
            story_extractor_max_calls=10,
            story_extractor_max_calls_day=50,
            story_readings_on=False,
        )
        dm.extractor_ai = None
        dm._put_back_tasks = {}

        async def propose(gid: int, cid: str, texts: list[str], *_: Any, **__: Any) -> list[str]:
            self.scanned.append(list(texts))
            return []

        dm._propose_new_names = propose

    async def consenting(self, gid: int) -> set[int]:
        return set(self.agreed)

    async def waiting(self) -> list[tuple[int, int]]:
        return await self.store.put_back_in(GUILD, self.sid)

    async def test_a_put_back_line_is_read_once_then_not_again(self) -> None:
        self.assertEqual(len(await self.waiting()), 2)
        self.assertEqual(await self.dm._read_put_back_all(GUILD), 2)
        self.assertEqual(self.scanned, [["Kesh Varn waits", "Orla Fenn knows"]])
        self.assertEqual(await self.waiting(), [])
        self.assertEqual(await self.dm._read_put_back_all(GUILD), 0)
        self.assertEqual(len(self.scanned), 1)  # nothing read twice

    async def test_a_speaker_who_stopped_being_recorded_is_skipped_and_not_asked_again(
        self,
    ) -> None:
        self.agreed = {ALICE}
        self.assertEqual(await self.dm._read_put_back_all(GUILD), 1)
        self.assertEqual(self.scanned, [["Kesh Varn waits"]])  # not Bob's
        self.assertEqual(await self.waiting(), [])  # Bob's line is done too

    async def test_the_extractor_reads_only_the_put_back_lines_numbered_in_place(self) -> None:
        self.dm.settings.story_extractor_on = True
        self.dm.extractor_ai = SimpleNamespace(model="m")
        self.dm.plan_allows = AsyncMock(return_value=True)
        self.dm.name_of = lambda gid, uid: f"<@{uid}>"
        seen: dict[str, Any] = {}

        async def run(client: Any, claims: Any, given: list[Line], **kw: Any) -> Report:
            seen["lines"], seen["after_ms"] = given, kw["after_ms"]
            return Report(calls=1)

        with (
            patch("dmbot.bot.extractor.run", new=run),
            patch("dmbot.bot.ClaimStore", return_value=object()),
        ):
            self.assertEqual(await self.dm._read_put_back_all(GUILD), 2)
        topics = [(x.heard, x.topic) for x in seen["lines"]]
        self.assertEqual(
            topics,
            [
                ("the pass is closed", "off_topic"),  # not new: skipped by `readable`, place kept
                ("Kesh Varn waits", "game"),
                ("Orla Fenn knows", "game"),
                ("we climb", "off_topic"),
            ],
        )
        self.assertEqual(seen["after_ms"], 0)
        self.assertEqual(await self.waiting(), [])

    async def test_an_extractor_that_stopped_early_leaves_the_lines_for_next_time(self) -> None:
        self.dm.settings.story_extractor_on = True
        self.dm.extractor_ai = SimpleNamespace(model="m")
        self.dm.plan_allows = AsyncMock(return_value=True)
        self.dm.name_of = lambda gid, uid: f"<@{uid}>"

        async def run(*_: Any, **__: Any) -> Report:
            return Report(calls=1, stopped="call limit")

        with patch("dmbot.bot.extractor.run", new=run), patch("dmbot.bot.ClaimStore"):
            self.assertEqual(await self.dm._read_put_back_all(GUILD), 0)
        self.assertEqual(len(await self.waiting()), 2)  # tried again later

    async def test_a_plan_without_the_extractor_still_reads_the_names(self) -> None:
        self.dm.settings.story_extractor_on = True
        self.dm.extractor_ai = SimpleNamespace(model="m")
        self.dm.plan_allows = AsyncMock(return_value=False)
        self.assertEqual(await self.dm._read_put_back_all(GUILD), 2)
        self.assertEqual(len(self.scanned), 1)
        self.assertEqual(await self.waiting(), [])

    async def test_leaned_lines_are_listed_for_the_name_scan(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE transcript_lines SET leaned = TRUE WHERE session_id = %s"
                " AND heard = 'we climb'",
                (self.sid,),
            )
        got = await self.store.leaned_lines(GUILD, self.sid)
        self.assertEqual([x.text for x in got], ["we climb"])

    async def test_the_sessions_waiting_are_found_without_their_words(self) -> None:
        self.assertEqual(await self.store.sessions_with_put_back(GUILD), [(self.cid, self.sid)])
        await self.dm._read_put_back_all(GUILD)
        self.assertEqual(await self.store.sessions_with_put_back(GUILD), [])


class TheTask(DatabaseTest):
    async def test_the_poll_loop_returns_while_a_read_runs_one_a_server(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(self: Any, guild_id: int) -> int:
            started.set()
            await release.wait()
            return 0

        b: Any = DMBot.__new__(DMBot)
        b.transcripts, b.memory = object(), object()
        b._put_back_tasks = {}
        b._closing = False
        b._asking = set()
        with patch.object(DMBot, "_read_put_back_all", slow):
            await asyncio.wait_for(b.read_put_back_lines(1), timeout=1)  # returns at once
            await started.wait()
            await asyncio.wait_for(b.read_put_back_lines(1), timeout=1)  # and again
            self.assertEqual(len(b._put_back_tasks), 1)  # one at a time for the server
            release.set()
            await asyncio.gather(*b._put_back_tasks.values())
        await asyncio.sleep(0)
        self.assertEqual(b._put_back_tasks, {})
