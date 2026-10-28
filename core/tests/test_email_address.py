"""The email cleaner now lives in dmbot.email_address (#2547): one function under both names, and
the bot's hand-over form does not import from the website package."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import dmbot.handover_email as handover_email
from dmbot import email_address
from dmbot.web import email_signin


class TheMove(unittest.TestCase):
    def test_clean_email_is_one_function_under_both_names(self) -> None:
        self.assertIs(email_signin.clean_email, email_address.clean_email)

    def test_the_bots_hand_over_form_does_not_import_the_website(self) -> None:
        tree = ast.parse(Path(handover_email.__file__).read_text(encoding="utf-8"))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        # Exact package or its submodules only, so a future dmbot.webhooks is not a website import.
        self.assertEqual([m for m in modules if m == "dmbot.web" or m.startswith("dmbot.web.")], [])
