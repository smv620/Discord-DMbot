"""DMbot never pings (#1834): the client default is "nobody", and no module opts back in.

Campaign data is posted as typed, so an `@everyone` in a name must not notify anyone. One missed
`allowed_mentions=` is one surprise ping, so the client carries the default and this checks that
nobody builds an `AllowedMentions` that allows something.
"""

import ast
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import discord

from dmbot.bot import DMBot
from dmbot.config import Settings
from dmbot.mentions import NO_PINGS

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"


def allow_calls(source: str) -> list[int]:
    """Lines that build an `AllowedMentions` that is not `.none()`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            (isinstance(func, ast.Attribute) and func.attr == "AllowedMentions")
            or (isinstance(func, ast.Name) and func.id == "AllowedMentions")
            or (
                isinstance(func, ast.Attribute)
                and func.attr != "none"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "AllowedMentions"
            )
        ):
            found.append(node.lineno)
    return found


class NoPingsTest(unittest.TestCase):
    def test_the_client_default_pings_nobody(self) -> None:
        bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            cast(Any, MagicMock()),
            cast(Any, MagicMock()),
            cast(Any, MagicMock()),
        )
        mentions = bot.allowed_mentions
        assert mentions is not None
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)
        self.assertFalse(mentions.replied_user)

    def test_the_shared_constant_pings_nobody(self) -> None:
        self.assertEqual(NO_PINGS.to_dict(), discord.AllowedMentions.none().to_dict())

    def test_no_module_allows_a_ping(self) -> None:
        bad = [
            f"{path.relative_to(SRC)}:{line}"
            for path in SRC.rglob("*.py")
            for line in allow_calls(path.read_text())
        ]
        self.assertEqual(bad, [], "a deliberate ping needs a test change and a reason in review")

    def test_the_check_sees_a_ping(self) -> None:
        self.assertEqual(allow_calls("import discord\ndiscord.AllowedMentions(everyone=True)"), [2])
        self.assertEqual(allow_calls("import discord\ndiscord.AllowedMentions.all()"), [2])
        self.assertEqual(allow_calls("import discord\ndiscord.AllowedMentions.none()"), [])
