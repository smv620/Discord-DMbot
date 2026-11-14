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


class WhatTheDoorsLinesBecome(unittest.TestCase):
    """`_line` is given the door's literal rows (migration 0481): a place's line, a ruler's
    line, and a language's, whose word the door calls `value`."""

    def test_a_land_line(self) -> None:
        row = {
            "id": "place:" + "a" * 32,
            "kind": "place",
            "entityId": "e1",
            "name": "Saltmarsh",
            "layer": "terrain",
            "value": "swamp",
            "words": "",
            "part": "",
            "how": "partly",
            "ruler": None,
            "fromSession": 1700000000,
            "from": "session",
        }
        got = world_notes._line("place", row)
        self.assertEqual((got["layer"], got["value"], got["how"]), ("terrain", "swamp", "partly"))
        self.assertEqual(
            (got["fromSession"], got["from"], got["ruler"]), (1700000000, "session", None)
        )

    def test_a_rulers_line(self) -> None:
        row = {
            "id": "place:" + "c" * 32,
            "kind": "place",
            "entityId": "e1",
            "name": "Greywatch",
            "layer": "control",
            "value": "f" * 32,
            "words": "",
            "how": "rules",
            "ruler": "The Guard",
            "fromSession": None,
            "from": "lore",
        }
        got = world_notes._line("place", row)
        self.assertEqual(
            (got["how"], got["ruler"], got["fromSession"]), ("rules", "The Guard", None)
        )

    def test_a_language_line_reads_the_doors_value(self) -> None:
        row = {
            "id": "language:" + "b" * 32,
            "kind": "language",
            "entityId": "e2",
            "name": "Brother Venn",
            "value": "Giant",
            "fromSession": None,
            "from": "lore",
        }
        got = world_notes._line("language", row)
        self.assertEqual(got["language"], "Giant")
        self.assertNotIn("layer", got)
