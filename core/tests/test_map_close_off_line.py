"""The map close-read sweep says once that it is off (#4743): a kept map that is never read must
leave a line in the bot's log, not silence. Stand-ins, no database."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from dmbot.bot import DMBot


def bot(desk: Any) -> Any:
    b: Any = DMBot.__new__(DMBot)
    b.map_store, b.image_desk, b.map_notes = object(), desk, object()
    b.picture_store, b.picture_bucket = object(), object()
    b._map_close_off_told = False
    b._map_close_task = None
    b._map_close_swept = 0.0
    return b


class TheOffLine(unittest.IsolatedAsyncioTestCase):
    async def test_without_the_picture_reader_it_is_said_once_with_counts_only(self) -> None:
        b = bot(None)
        with self.assertLogs("dmbot.bot", level="INFO") as logged:
            await b.run_map_close_reads()
            await b.run_map_close_reads()
        lines = [x for x in logged.output if "Map close reads: off" in x]
        self.assertEqual(len(lines), 1)
        self.assertIn("picture reader off", lines[0])
        self.assertIn("kept pictures on", lines[0])

    async def test_a_server_without_maps_logs_nothing(self) -> None:
        b = bot(None)
        b.map_store = None
        with self.assertNoLogs("dmbot.bot", level="INFO"):
            await b.run_map_close_reads()

    async def test_with_everything_set_up_it_says_nothing_and_starts_the_sweep(self) -> None:
        b = bot(object())
        b._track = mock.Mock(side_effect=lambda coro, name: coro.close())
        await b.run_map_close_reads()
        b._track.assert_called_once()
        self.assertFalse(b._map_close_off_told)


class TheEveryRunLine(unittest.IsolatedAsyncioTestCase):
    async def test_an_empty_sweep_still_leaves_one_counts_only_line(self) -> None:
        b = bot(object())
        b._closing = False
        # (no guilds, so nothing is listed)
        with (
            mock.patch.object(DMBot, "guilds", new=[]),
            self.assertLogs("dmbot.bot", level="INFO") as logged,
        ):
            await b._map_close_reads()
        self.assertTrue(logged.output[-1].endswith("Map close reads: none waiting (0 servers)"))
