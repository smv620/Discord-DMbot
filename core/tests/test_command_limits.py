"""The slash-command tree stays inside Discord's limits (#1797), checked with no Discord."""

from __future__ import annotations

import contextlib
import logging
import unittest
from typing import Any
from unittest import mock

import discord
from discord import app_commands

from dmbot.bot import DMBot, register_commands
from dmbot.discord_limits import (
    CHOICES_MAX,
    DESCRIPTION_MAX,
    OPTIONS_MAX,
    TOTAL_MAX,
    command_problems,
    tree_problems,
)


def real_payloads() -> list[dict[str, Any]]:
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    register_commands(tree)
    return [c.to_dict(tree) for c in tree.get_commands()]


def command(name: str = "ok", **extra: Any) -> dict[str, Any]:
    return {"type": 1, "name": name, "description": "Fine.", **extra}


class TodaysTreeTest(unittest.TestCase):
    def test_the_real_tree_is_inside_every_limit(self) -> None:
        payloads = real_payloads()
        self.assertTrue(payloads)
        self.assertEqual(tree_problems(payloads), [])


class BrokenTreeTest(unittest.TestCase):
    def one(self, payload: dict[str, Any]) -> str:
        problems = command_problems(payload)
        self.assertTrue(problems)
        return "\n".join(problems)

    def test_long_name_names_the_command_and_the_limit(self) -> None:
        text = self.one(command("x" * 33))
        self.assertIn("1 to 32 characters", text)
        self.assertIn("xxx", text)

    def test_capitals_and_odd_characters(self) -> None:
        self.assertIn("lowercase", self.one(command("Bad")))
        self.assertIn("letters, digits", self.one(command("a b")))

    def test_description_limit(self) -> None:
        text = self.one(command("long", description="d" * (DESCRIPTION_MAX + 1)))
        self.assertIn("/long", text)
        self.assertIn(f"1 to {DESCRIPTION_MAX} characters", text)
        self.assertIn("description", self.one(command("empty", description="")))

    def test_too_many_options(self) -> None:
        options = [{"type": 3, "name": f"o{i}", "description": "x"} for i in range(OPTIONS_MAX + 1)]
        text = self.one(command("many", options=options))
        self.assertIn("/many", text)
        self.assertIn(f"at most {OPTIONS_MAX}", text)

    def test_nesting_deeper_than_two_levels(self) -> None:
        inner = {"type": 1, "name": "leaf", "description": "x"}
        group = {"type": 2, "name": "g2", "description": "x", "options": [inner]}
        outer = {"type": 2, "name": "g1", "description": "x", "options": [group]}
        self.assertIn("only 2 levels", self.one(command("deep", options=[outer])))
        sub = {"type": 1, "name": "s", "description": "x", "options": [inner]}
        self.assertIn("may not hold subcommands", self.one(command("deep", options=[sub])))

    def test_choices(self) -> None:
        choices = [{"name": f"c{i}", "value": str(i)} for i in range(CHOICES_MAX + 1)]
        option = {"type": 3, "name": "pick", "description": "x", "choices": choices}
        text = self.one(command("pick", options=[option]))
        self.assertIn(f"at most {CHOICES_MAX}", text)
        long = {
            "type": 3,
            "name": "p",
            "description": "x",
            "choices": [{"name": "n" * 101, "value": "v"}],
        }
        self.assertIn("choice name", self.one(command("c", options=[long])))

    def test_combined_size(self) -> None:
        options = [{"type": 3, "name": f"o{i}", "description": "d" * 100} for i in range(25)]
        groups = [
            {"type": 1, "name": f"s{i}", "description": "d" * 100, "options": options}
            for i in range(2)
        ]
        text = self.one(command("big", options=groups))
        self.assertIn(f"at most {TOTAL_MAX}", text)

    def test_menus_allow_capitals_and_spaces(self) -> None:
        self.assertEqual(command_problems({"type": 3, "name": "Save Line"}), [])
        self.assertTrue(command_problems({"type": 3, "name": "n" * 33}))

    def test_menu_counts_are_per_kind(self) -> None:
        menus = [{"type": 2, "name": f"m{i}"} for i in range(6)]
        self.assertTrue(any("user context menus" in p for p in tree_problems(menus)))

    def test_tree_counts_and_duplicates(self) -> None:
        many = [command(f"c{i}") for i in range(101)]
        self.assertTrue(any("at most 100" in p for p in tree_problems(many)))
        self.assertTrue(any("used twice" in p for p in tree_problems([command("a"), command("a")])))


class StartupErrorTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_refused_sync_logs_status_code_and_text(self) -> None:
        response = mock.Mock(status=400, reason="Bad Request")
        error = discord.HTTPException(
            response, {"code": 50035, "message": "Invalid Form Body", "errors": {"0": "bad"}}
        )
        bot = mock.Mock()
        bot.tree.get_commands.return_value = []
        bot.tree.sync = mock.AsyncMock(side_effect=error)
        bot.settings.shards.ids = [0]
        bot.settings.dev_guild_id = None
        # only the log line matters; the rest of start-up is not under test
        with (
            self.assertLogs("dmbot.bot", level=logging.ERROR) as logs,
            contextlib.suppress(Exception),
        ):
            await DMBot._sync_commands(bot)
        text = "\n".join(logs.output)
        self.assertIn("HTTP 400", text)
        self.assertIn("50035", text)
        self.assertIn("Invalid Form Body", text)
        bot.tree.sync.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
