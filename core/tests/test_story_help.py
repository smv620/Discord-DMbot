"""The plain no for a request to invent the story, and the check for that request, are one object
under both names; the plot chat imports them without going through recaps (#2827)."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from dmbot.memory import plot_talk, story_help
from dmbot.recaps import feedback


class TheStoryHelp(unittest.TestCase):
    def test_the_reply_and_the_check_are_one_object_under_both_names(self) -> None:
        self.assertEqual(feedback.STORY_HELP_REPLY, story_help.STORY_HELP_REPLY)
        self.assertIs(feedback.asks_to_invent, story_help.asks_to_invent)

    def test_plot_talk_imports_nothing_from_recaps(self) -> None:
        tree = ast.parse(Path(plot_talk.__file__).read_text(encoding="utf-8"))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
                modules += [f"{node.module}.{alias.name}" for alias in node.names]
        self.assertEqual(
            [m for m in modules if m == "dmbot.recaps" or m.startswith("dmbot.recaps.")], []
        )
