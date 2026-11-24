"""The name reader's stage on the import card (#5091): `ImportStore.set_names` keeps where the
reader is (part n of m, when it started, when it last moved, how many stretches went unread), the
import progress door sends it beside the story read's, and an imported session's name step feeds it
from the reader's own progress while a live session's does not (#5124 is that half). Postgres for
the store; a stand-in bot for the step. Invented names."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.memory.scan import Suggestion
from tests.test_import_recaps import GUILD, WithImports
from tests.test_scan_loose import TEXTS, stand_in


async def names_of(db: Any, sid: str) -> tuple[str, int, int, int, int, int]:
    async with db.guild(GUILD) as conn:
        cur = await conn.execute(
            "SELECT names_state, names_part, names_parts, names_started_at, names_moved_at,"
            " names_unread FROM session_imports WHERE session_id = %s",
            (sid,),
        )
        r: Any = await cur.fetchone()
    return (
        r["names_state"], r["names_part"], r["names_parts"], r["names_started_at"],
        r["names_moved_at"], r["names_unread"],
    )  # fmt: skip


class TheStore(WithImports):
    async def test_the_reader_notes_its_part_when_it_started_and_what_went_unread(self) -> None:
        sid = await self.imported(1)
        self.assertEqual(await names_of(self.db, sid), ("", 0, 0, 0, 0, 0))
        await self.store.set_names(GUILD, sid, "reading", 100, part=1, parts=3)
        await self.store.set_names(GUILD, sid, "reading", 140, part=2, parts=3)
        self.assertEqual(await names_of(self.db, sid), ("reading", 2, 3, 100, 140, 0))
        await self.store.set_names(GUILD, sid, "reading", 900, part=1, parts=3)  # (a restart)
        self.assertEqual((await names_of(self.db, sid))[3], 900)
        await self.store.set_names(GUILD, sid, "read", 950, unread=1)
        self.assertEqual(await names_of(self.db, sid), ("read", 1, 3, 900, 950, 1))

    async def test_a_read_started_again_after_dying_in_its_first_part_starts_afresh(self) -> None:
        sid = await self.imported(1)
        await self.store.set_names(GUILD, sid, "reading", 100, part=1, parts=3)
        await self.store.set_names(GUILD, sid, "reading", 700, part=1, parts=3)  # (a restart)
        self.assertEqual((await names_of(self.db, sid))[3], 700)
        await self.store.set_names(GUILD, sid, "stopped", 720)
        self.assertEqual(await names_of(self.db, sid), ("stopped", 1, 3, 700, 720, 0))

    async def test_the_import_door_sends_the_names_stage_beside_the_story_s(self) -> None:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT pg_get_functiondef(p.oid) AS d FROM pg_proc p"
                " WHERE p.pronamespace = current_schema()::regnamespace"
                " AND p.proname = 'dmbot_import_progress'"
            )
            row: Any = await cur.fetchone()
        self.assertIn("'names', jsonb_build_object(", row["d"])
        self.assertIn("'unread', i.names_unread", row["d"])


class TheStep(unittest.TestCase):
    def run_step(self, bot: Any, *, import_scan: bool) -> None:
        bot._write_name_trace = AsyncMock()
        bot._add_scan_names = lambda *a, **k: DMBot._add_scan_names(bot, *a, **k)  # (#5055 B)
        bot._still_recorded = lambda *a: DMBot._still_recorded(bot, *a)  # (#5187)
        asyncio.run(
            DMBot._propose_new_names(bot, 5, "c1", TEXTS, set(), "s1", import_scan=import_scan)
        )

    def bot(self, unread: int | None, *, moves: bool = True) -> Any:
        bot = stand_in("", {}, unread=unread)
        bot.import_job = type("Job", (), {})()
        bot.import_job.store = AsyncMock()
        given: list[Any] = []

        async def read(_g: int, _c: str, _lines: Any, _people: Any, every: list[Suggestion],
                       *, progress: Any = None, **_kw: Any) -> Any:  # fmt: skip
            given.append(progress)
            if progress is not None and moves:
                await progress(1, 2)
                await progress(2, 2)
            return [], [], unread

        bot._read_session_names = read
        bot.given = given
        return bot

    def test_an_import_s_card_follows_the_reader_part_by_part_then_says_read(self) -> None:
        bot = self.bot(unread=1)
        self.run_step(bot, import_scan=True)
        calls = [(c.args[2], c.kwargs) for c in bot.import_job.store.set_names.await_args_list]
        self.assertEqual(
            calls,
            [
                ("reading", {"part": 1, "parts": 2}),
                ("reading", {"part": 2, "parts": 2}),
                ("read", {"unread": 1}),
            ],
        )

    def test_a_live_session_gives_the_reader_no_progress_and_the_card_is_untouched(self) -> None:
        bot = self.bot(unread=0)
        self.run_step(bot, import_scan=False)
        self.assertEqual(bot.given, [None])
        bot.import_job.store.set_names.assert_not_awaited()

    def states(self, bot: Any) -> list[str]:
        return [c.args[2] for c in bot.import_job.store.set_names.await_args_list]

    def test_a_reader_that_fails_after_it_started_leaves_stopped_never_reading(self) -> None:
        bot = self.bot(unread=None)
        self.run_step(bot, import_scan=True)
        self.assertEqual(self.states(bot), ["reading", "reading", "stopped"])

    def test_a_reader_that_never_started_leaves_the_card_untouched(self) -> None:
        bot = self.bot(unread=None, moves=False)
        self.run_step(bot, import_scan=True)
        self.assertEqual(self.states(bot), [])

    def test_a_card_that_cannot_be_written_never_stops_the_names(self) -> None:
        bot = self.bot(unread=0, moves=False)
        bot.import_job.store.set_names.side_effect = RuntimeError("down")
        self.run_step(bot, import_scan=True)  # (raises if the card's failure escaped)
        self.assertEqual(self.states(bot), ["read"])
