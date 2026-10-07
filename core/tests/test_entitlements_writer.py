"""Only the plan writer changes `entitlements` (#435). The database enforces it (writes
need Database.plan_writer()); this keeps that door in the web API's payment code."""

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"


def opens_writer(source: str) -> bool:
    """Any call of something's .plan_writer(...), however it's written (comments and
    docstrings that name it don't count)."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "plan_writer"
        for node in ast.walk(ast.parse(source))
    )


# The payment webhook and Try It live here (#435 parts 2 and 3).
ALLOWED_DIR = SRC / "web"


class OnlyTheWebApiWritesPlans(unittest.TestCase):
    def test_only_the_web_api_opens_the_plan_writer(self) -> None:
        openers = {
            path.relative_to(SRC).as_posix()
            for path in SRC.rglob("*.py")
            if opens_writer(path.read_text("utf-8")) and ALLOWED_DIR not in path.parents
        }
        self.assertEqual(openers, set(), "only dmbot.web may change a person's plan")

    def test_the_scan_finds_the_web_apis_own_calls(self) -> None:
        # Otherwise a broken pattern would pass the test above by finding nothing.
        found = [p for p in ALLOWED_DIR.rglob("*.py") if opens_writer(p.read_text("utf-8"))]
        self.assertTrue(found)
