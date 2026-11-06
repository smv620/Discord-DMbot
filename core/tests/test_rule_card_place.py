"""The rule card's words live in the rules package, and the sidebar answer imports nothing from the
UI for them (#2827)."""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

import dmbot.sidebar.answer


class TheRuleCardPlace(unittest.TestCase):
    def test_the_rule_card_lives_in_rules_and_the_sidebar_answer_imports_no_ui(self) -> None:
        self.assertIsNone(importlib.util.find_spec("dmbot.ui.rule_card"))
        assert dmbot.sidebar.answer.__file__ is not None
        tree = ast.parse(Path(dmbot.sidebar.answer.__file__).read_text(encoding="utf-8"))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
                modules += [f"{node.module}.{alias.name}" for alias in node.names]
        self.assertEqual([m for m in modules if m == "dmbot.ui" or m.startswith("dmbot.ui.")], [])
