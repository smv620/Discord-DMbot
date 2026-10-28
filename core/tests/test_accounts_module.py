"""The account helpers live in dmbot.accounts and the website re-exports them, and the bot and its
migration screen do not import the website package (#2547)."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import dmbot.bot
import dmbot.ui.migrate
from dmbot import accounts
from dmbot.web import accounts as web_accounts

PUBLIC_NAMES = (
    "NAME_MAX",
    "account_email",
    "active_subscription",
    "customer_id",
    "delete_person",
    "dmbot_id",
    "link_install",
    "payment_email",
    "record_install",
    "remember_display_name",
    "user_of_dmbot_id",
)


class TheAccountHelpers(unittest.TestCase):
    def test_the_account_helpers_are_one_set_of_functions_under_both_names(self) -> None:
        for name in PUBLIC_NAMES:
            with self.subTest(name=name):
                self.assertIs(getattr(accounts, name), getattr(web_accounts, name))

    def test_the_bot_and_the_migrate_screen_do_not_import_the_website(self) -> None:
        for module in (dmbot.bot, dmbot.ui.migrate):
            assert module.__file__ is not None
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            names: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names += [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                    names += [f"{node.module}.{alias.name}" for alias in node.names]
            with self.subTest(module=module.__name__):
                self.assertEqual(
                    [n for n in names if n == "dmbot.web" or n.startswith("dmbot.web.")], []
                )
