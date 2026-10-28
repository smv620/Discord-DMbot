"""The fixed-spelling result class is one class under both names, and the transcript module that
reads it imports nothing from recaps (#2827)."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from dmbot.recaps import store
from dmbot.transcript import respell


class TheResultClass(unittest.TestCase):
    def test_the_result_class_is_one_class_under_both_names(self) -> None:
        self.assertIs(store.RecapRespelled, respell.RecapRespelled)

    def test_the_respell_module_imports_nothing_from_recaps(self) -> None:
        # Every import, including those inside an `if` block, is checked.
        tree = ast.parse(Path(respell.__file__).read_text(encoding="utf-8"))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        self.assertEqual(
            [m for m in modules if m == "dmbot.recaps" or m.startswith("dmbot.recaps.")], []
        )
