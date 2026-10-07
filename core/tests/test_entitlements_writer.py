"""Only the plan writer changes `entitlements` (#435). The database enforces it (writes
need Database.plan_writer()); this keeps that door in the web API's payment code."""

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
# A real call (awaited or entered), whatever the variable is called; docstrings that
# name Database.plan_writer() don't count.
OPENS_WRITER = re.compile(r"(?:async with|await)\s+[\w.]*\.plan_writer\(")
# The payment webhook and Try It live here (#435 parts 2 and 3).
ALLOWED_DIR = SRC / "web"


class OnlyTheWebApiWritesPlans(unittest.TestCase):
    def test_only_the_web_api_opens_the_plan_writer(self) -> None:
        openers = {
            path.relative_to(SRC).as_posix()
            for path in SRC.rglob("*.py")
            if OPENS_WRITER.search(path.read_text("utf-8")) and ALLOWED_DIR not in path.parents
        }
        self.assertEqual(openers, set(), "only dmbot.web may change a person's plan")

    def test_the_scan_finds_the_web_apis_own_calls(self) -> None:
        # Otherwise a broken pattern would pass the test above by finding nothing.
        found = [p for p in ALLOWED_DIR.rglob("*.py") if OPENS_WRITER.search(p.read_text("utf-8"))]
        self.assertTrue(found)
