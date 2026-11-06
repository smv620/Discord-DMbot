"""The phone's label limit is one number, defined beside the other Discord limits (#2827)."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from dmbot import discord_limits
from dmbot.ui import logic


class ThePhoneLabelLimit(unittest.TestCase):
    def test_the_phone_label_limit_is_one_number_under_both_names(self) -> None:
        self.assertEqual(logic.PHONE_LABEL_MAX, discord_limits.PHONE_LABEL_MAX)
        self.assertEqual(discord_limits.PHONE_LABEL_MAX, 25)
        # Equal values alone would not prove a re-export: the UI module must not assign its own.
        assert logic.__file__ is not None
        tree = ast.parse(Path(logic.__file__).read_text(encoding="utf-8"))
        assigned = [
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
        self.assertNotIn("PHONE_LABEL_MAX", assigned)
