"""Only the plan writer changes `entitlements` (#435). The database enforces it (writes
need Database.plan_writer()); this keeps that door in the web API's payment code."""

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
OPENS_WRITER = re.compile(r"\b_?db\.plan_writer\(")
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
