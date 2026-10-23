"""Every button that must survive a restart is registered, and its id fits Discord (#1818).

A `discord.ui.DynamicItem` works after a restart only if `setup_hook` passes it to
`add_dynamic_items`; a class that is never registered works until the next deploy and then every
old message's button shows "This interaction failed". Discord also refuses a whole message whose
button has a `custom_id` over 100 characters. No Discord connection is needed: the registered list
is read from `bot.py` and the ids from each class's `template` pattern.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import unittest
from typing import Any

import discord

import dmbot
from dmbot import bot as bot_module
from dmbot.consent_dm import CONSENT_BUTTONS
from dmbot.discord_limits import CUSTOM_ID_MAX, UnboundedId, longest_custom_id
from dmbot.dm_screen import PeekButton
from dmbot.recaps import discord as recaps_discord

# Classes that are deliberately not registered, each with the reason. Empty today: every one is.
NOT_REGISTERED: dict[str, str] = {}


def all_dynamic_items(*modules: str) -> set[type]:
    """Every DynamicItem class defined in the `dmbot` package (and in `modules`, for a test's
    own stand-ins)."""
    for module in pkgutil.walk_packages(dmbot.__path__, "dmbot."):
        importlib.import_module(module.name)

    def subclasses(cls: type) -> set[type]:
        found: set[type] = set(cls.__subclasses__())
        for sub in list(found):
            found |= subclasses(sub)
        return found

    return {
        c
        for c in subclasses(discord.ui.DynamicItem)
        if c.__module__.startswith(("dmbot", *modules))
    }


def registered_items(source: str | None = None) -> set[type]:
    """The classes `setup_hook` (and the `setup` of each feature that registers its own buttons,
    #2129) passes to `add_dynamic_items`, read from the code (names are looked up in the
    module's own namespace, so `*CONSENT_BUTTONS` and `clock_screen.X` resolve)."""
    if source is None:
        return registered_items_in(bot_module) | registered_items_in(recaps_discord)
    return _registered(ast.parse(source), vars(bot_module))


def registered_items_in(module: Any) -> set[type]:
    return _registered(ast.parse(inspect.getsource(module)), vars(module))


def _registered(tree: ast.AST, namespace: dict[str, Any]) -> set[type]:
    found: set[type] = set()
    # Only `setup_hook` registers (a call anywhere else would not run at start-up); a test's
    # tiny source has a function of another name, so it is read whole.
    hooks: list[ast.stmt] = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and n.name in ("setup_hook", "setup")
    ]
    for node in ast.walk(ast.Module(body=hooks, type_ignores=[]) if hooks else tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_dynamic_items":
            for arg in node.args:
                starred = isinstance(arg, ast.Starred)
                expression = ast.Expression(arg.value if isinstance(arg, ast.Starred) else arg)
                value: Any = eval(compile(expression, "<bot.py>", "eval"), namespace)
                found |= set(value) if starred else {value}
    return found


def unregistered(items: set[type], registered: set[type]) -> list[str]:
    return sorted(
        f"{c.__module__}.{c.__qualname__}"
        for c in items - registered
        if f"{c.__module__}.{c.__qualname__}" not in NOT_REGISTERED
    )


def too_long(items: set[type]) -> list[str]:
    """The classes whose longest possible custom_id is over the limit, and those with no longest."""
    problems = []
    for cls in sorted(items, key=lambda c: c.__qualname__):
        pattern = cls.__discord_ui_compiled_template__.pattern  # type: ignore[attr-defined]
        try:
            longest = longest_custom_id(pattern)
        except UnboundedId:
            problems.append(f"{cls.__qualname__}: part of its id has no longest value ({pattern})")
            continue
        if longest > CUSTOM_ID_MAX:
            problems.append(
                f"{cls.__qualname__}: its id can be {longest} characters, over {CUSTOM_ID_MAX}"
            )
    return problems


class RegisteredTest(unittest.TestCase):
    def test_every_dynamic_item_is_registered(self) -> None:
        items = all_dynamic_items()
        self.assertGreater(len(items), 50)  # (the walk really found the many buttons: 101 today)
        self.assertEqual(
            unregistered(items, registered_items()),
            [],
            "not passed to add_dynamic_items in DMBot.setup_hook: after the next restart every "
            "old message's button shows 'This interaction failed'",
        )

    def test_a_stand_in_that_is_not_registered_is_named(self) -> None:
        class Stray(
            discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=r"dmbot:stray"
        ):
            pass

        names = unregistered(all_dynamic_items(__name__), registered_items())
        self.assertTrue(any(n.endswith("Stray") for n in names), names)

    def test_registrations_are_read_from_the_source(self) -> None:
        source = (
            "class B:\n    def setup_hook(self):\n"
            "        self.add_dynamic_items(PeekButton, *CONSENT_BUTTONS)\n"
        )
        found = registered_items(source)
        self.assertIn(PeekButton, found)
        self.assertTrue(set(CONSENT_BUTTONS) <= found)


class IdLengthTest(unittest.TestCase):
    def test_every_id_fits_discords_limit(self) -> None:
        self.assertEqual(too_long(all_dynamic_items()), [])

    def test_a_stand_in_over_the_limit_is_named(self) -> None:
        class Long(
            discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
            template=r"dmbot:long:" + "x" * 95,
        ):
            pass

        problems = too_long(all_dynamic_items(__name__))
        self.assertTrue(any("Long" in p and "over 100" in p for p in problems), problems)

    def test_a_stand_in_with_an_unbounded_part_is_named(self) -> None:
        class Open(
            discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
            template=r"dmbot:open:(?P<rest>.+)",
        ):
            pass

        problems = too_long(all_dynamic_items(__name__))
        self.assertTrue(any("Open" in p and "no longest" in p for p in problems), problems)

    def test_the_longest_id_counts_optional_parts_and_numbers(self) -> None:
        self.assertEqual(longest_custom_id(r"ab:(?P<n>\d+)(?::x)?"), 3 + 9 + 2)
        self.assertEqual(longest_custom_id(r"id:[0-9a-f]{32}"), 3 + 32)
        with self.assertRaises(UnboundedId):
            longest_custom_id(r"id:[^:]+")


if __name__ == "__main__":
    unittest.main()
