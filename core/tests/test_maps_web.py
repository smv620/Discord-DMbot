"""The website's Maps page (#4347): what the queue functions refuse before anything reaches the
database. The door's player rule and the bot's applier are database tests (`test_web_maps.py`, CI's
core job). Invented ids only. No database."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.web_actions import MAPS_PAGE_ACTIONS
from dmbot.web import maps
from dmbot.web.app import create_app  # noqa: F401  (the routes import cleanly)
from dmbot.web.recaps import RecapError

MAP = "a" * 32


async def _refused(call: Any) -> str:
    try:
        await call
    except RecapError as err:
        return err.code
    return ""


class WhatThePageMaySend(unittest.IsolatedAsyncioTestCase):
    async def test_use_needs_a_yes_or_no_and_a_real_map_id(self) -> None:
        bodies: tuple[object, ...] = (None, {}, {"yes": "yes"}, {"yes": 1}, [])
        for body in bodies:
            with self.subTest(body=body):
                code = await _refused(maps.queue_use(None, None, "1.c", MAP, body))  # type: ignore[arg-type]
                self.assertEqual(code, "bad_action")
        for bad in ("", "xyz", "A" * 32, MAP + "0", "../etc"):
            with self.subTest(map_id=bad):
                code = await _refused(maps.queue_use(None, None, "1.c", bad, {"yes": True}))  # type: ignore[arg-type]
                self.assertEqual(code, "not_found")

    async def test_show_needs_a_true_or_false(self) -> None:
        bodies: tuple[object, ...] = (None, {}, {"shown": "true"}, {"shown": 0})
        for body in bodies:
            with self.subTest(body=body):
                code = await _refused(maps.queue_show(None, None, "1.c", MAP, body))  # type: ignore[arg-type]
                self.assertEqual(code, "bad_action")

    async def test_remove_and_take_back_need_a_real_map_id(self) -> None:
        for call in (maps.queue_remove, maps.queue_takeback):
            with self.subTest(call=call.__name__):
                code = await _refused(call(None, None, "1.c", "nope"))  # type: ignore[arg-type]
                self.assertEqual(code, "not_found")

    async def test_undo_takes_only_a_change_number(self) -> None:
        for bad in ("", "abc", "1; drop", "²", "1" * 16, "-1", "1.5"):
            with self.subTest(bad=bad):
                code = await _refused(maps.queue_undo(None, None, "1.c", bad))  # type: ignore[arg-type]
                self.assertEqual(code, "not_found")

    def test_the_page_and_the_door_name_the_same_actions(self) -> None:
        import importlib

        door = importlib.import_module("dmbot.migrations.0487_maps_doors")
        self.assertEqual(set(door.ACTIONS), set(MAPS_PAGE_ACTIONS))
