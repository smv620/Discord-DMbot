"""An import's name scan and story read run inside its AI feed job (#3710): their calls are filed
under the import and the session, with the import page's words for the step, and the job is gone
after. No database, no Discord."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dmbot import ai_feed
from dmbot.bot import IMPORT_NAMES, IMPORT_READING, DMBot

READY = SimpleNamespace(campaign_id="c1", session_id="s" * 32)


class ImportSteps(unittest.IsolatedAsyncioTestCase):
    async def test_the_scan_and_the_story_read_are_filed_under_this_sessions_import(self) -> None:
        seen: dict[str, Any] = {}

        async def scan(gid: int, ready: Any) -> list[str]:
            seen["scan"] = ai_feed._current.get()
            return ["e1"]

        async def story(gid: int, ready: Any) -> str:
            seen["story"] = ai_feed._current.get()
            return "done"

        me = SimpleNamespace()
        with (
            patch.object(DMBot, "_scan_import_lines", lambda _s, g, r: scan(g, r)),
            patch.object(DMBot, "_read_import_story", lambda _s, g, r: story(g, r)),
        ):
            self.assertEqual(await DMBot._scan_import(me, 7, READY), ["e1"])  # type: ignore[arg-type]
            self.assertEqual(await DMBot._extract_import(me, 7, READY), "done")  # type: ignore[arg-type]
        for key, step in (("scan", IMPORT_NAMES), ("story", IMPORT_READING)):
            ctx = seen[key]
            self.assertEqual(
                (ctx.guild_id, ctx.campaign_id, ctx.kind, ctx.job_id, ctx.step),
                (7, "c1", "import", "s" * 32, step),
            )
        self.assertIsNone(ai_feed._current.get())

    async def test_a_failure_still_ends_the_job(self) -> None:
        async def broken(gid: int, ready: Any) -> list[str]:
            raise RuntimeError("down")

        with (
            patch.object(DMBot, "_scan_import_lines", lambda _s, g, r: broken(g, r)),
            self.assertRaises(RuntimeError),
        ):
            await DMBot._scan_import(SimpleNamespace(), 7, READY)  # type: ignore[arg-type]
        self.assertIsNone(ai_feed._current.get())
