"""The website's World notes (#4285): what the queue functions refuse before anything reaches the
database. A door-and-bot test with Postgres is `test_web_world_notes.py` (slice 1c); these are the
pure checks of what the page may send. Invented ids only. No database."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.web import world_notes
from dmbot.web.recaps import RecapError

PLACE = "place:" + "a" * 32
LANGUAGE = "language:" + "b" * 32


async def _refused(call: Any) -> str:
    try:
        await call
    except RecapError as err:
        return err.code
    return ""


class WhatThePageMaySend(unittest.IsolatedAsyncioTestCase):
    async def keep(self, body: object) -> str:
        return await _refused(world_notes.queue_keep(None, None, "1.c", body))  # type: ignore[arg-type]

    async def test_keep_takes_only_the_shown_lines_ids_in_their_shape(self) -> None:
        bodies: tuple[object, ...] = (
            None,
            {},
            {"ids": []},
            {"ids": "place:" + "a" * 32},
            {"ids": [PLACE, PLACE]},  # (the same line twice)
            {"ids": ["place:xyz"]},
            {"ids": [PLACE.upper()]},
            {"ids": [f"place:{i:032x}" for i in range(world_notes.KEEP_AT_MOST + 1)]},
            {"ids": [123]},
        )
        for bad in bodies:
            with self.subTest(bad=bad):
                self.assertEqual(await self.keep(bad), "bad_action")

    async def test_not_takes_one_line_id(self) -> None:
        for bad in (
            None,
            {},
            {"id": ""},
            {"id": "place:zz"},
            {"id": [PLACE]},
            {"id": "x:" + "a" * 32},
        ):
            with self.subTest(bad=bad):
                code = await _refused(world_notes.queue_not(None, None, "1.c", bad))  # type: ignore[arg-type]
                self.assertEqual(code, "bad_action")

    async def test_undo_takes_only_a_change_number(self) -> None:
        for bad in ("", "abc", "1; drop", "²", "1" * 16, "-1", "1.5"):
            with self.subTest(bad=bad):
                code = await _refused(world_notes.queue_undo(None, None, "1.c", bad))  # type: ignore[arg-type]
                self.assertEqual(code, "not_found")

    def test_both_kinds_of_line_id_match(self) -> None:
        self.assertTrue(world_notes.LINE_ID.fullmatch(PLACE))
        self.assertTrue(world_notes.LINE_ID.fullmatch(LANGUAGE))
        self.assertEqual(world_notes.KEEP_AT_MOST, 25)
