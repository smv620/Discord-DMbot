"""Buttons, menus and forms stay inside Discord's component limits (#1819).

Discord refuses a whole message when one part is over a limit, and the person sees nothing or
"This interaction failed". Labels built from campaign data are the risky ones, so the views that
list campaigns and names are built with 30 items of 300 characters each. The limits live in
`dmbot.discord_limits`; no Discord connection is needed.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import unittest
from typing import Any

import discord

import dmbot
from dmbot.campaigns.models import Campaign
from dmbot.discord_limits import (
    BUTTON_LABEL_MAX,
    INPUT_LABEL_MAX,
    OPTION_LABEL_MAX,
    SELECT_OPTIONS_MAX,
    component_problems,
    modal_problems,
)
from dmbot.ui import (
    dmbot_commands,
    homebrew,
    house_rules,
    logic,
    names,
    optional_rules,
    rule_lookup,
    transcripts,
)

LONG = "Ünïcode Name " * 24  # 312 characters
CAMPAIGN_ARGS = (0, None, "2024", "2014", True, frozenset({7}), None, None, "peek")


def campaigns(count: int = 30) -> list[Campaign]:
    return [Campaign(f"{i:032x}", 1, f"{LONG}{i}", *CAMPAIGN_ARGS) for i in range(count)]


def options(count: int, label: str = "x") -> list[dict[str, Any]]:
    return [{"label": label, "value": str(i)} for i in range(count)]


LIMITS = {
    "label": {"Button": 80, "_Button": 80, "SelectOption": 100, "TextInput": INPUT_LABEL_MAX},
    "description": {"SelectOption": 100},
    "placeholder": {"TextInput": 100, "Select": 150, "_Select": 150, "ChannelSelect": 150},
    "title": {"Modal": 45},
}


class CheckerTest(unittest.TestCase):
    def test_a_good_view_has_no_problems(self) -> None:
        rows = [{"type": 1, "components": [{"type": 2, "label": "Fine"}]}]
        self.assertEqual(component_problems(rows), [])

    def test_a_long_button_label_is_named(self) -> None:
        rows = [{"type": 1, "components": [{"type": 2, "label": "b" * (BUTTON_LABEL_MAX + 1)}]}]
        (problem,) = component_problems(rows)
        self.assertIn("label is 81 characters, at most 80", problem)

    def test_too_many_rows_and_buttons(self) -> None:
        button = {"type": 2, "label": "x"}
        self.assertTrue(component_problems([{"type": 1, "components": [button] * 6}]))
        self.assertTrue(component_problems([{"type": 1, "components": [button]}] * 6))

    def test_a_menu_with_too_many_or_too_long_choices_is_named(self) -> None:
        many = {"type": 3, "placeholder": "p" * 151, "options": options(SELECT_OPTIONS_MAX + 1)}
        text = "\n".join(component_problems([{"type": 1, "components": [many]}]))
        self.assertIn("26 choices, at most 25", text)
        self.assertIn("placeholder is 151 characters", text)
        long = {"type": 3, "options": [{"label": "l" * 101, "description": "d" * 101}]}
        text = "\n".join(component_problems([{"type": 1, "components": [long]}]))
        self.assertIn("label is 101 characters", text)
        self.assertIn("description is 101 characters", text)

    def test_a_form_title_boxes_and_starting_text_are_checked(self) -> None:
        box = {
            "type": 4,
            "label": "L" * 46,
            "placeholder": "p" * 101,
            "max_length": 10,
            "value": "v" * 11,
        }
        rows = [{"type": 1, "components": [box]}]
        text = "\n".join(modal_problems("T" * 46, rows))
        for part in ("title is 46", "label is 46", "placeholder is 101", "starting text is 11"):
            self.assertIn(part, text)
        self.assertTrue(modal_problems("t", [{"type": 1, "components": [box]}] * 6))


class DataBuiltViewsTest(unittest.IsolatedAsyncioTestCase):
    """Lists of 30 campaigns named with 300 characters: every menu cuts both."""

    def assert_fits(self, view: discord.ui.View) -> None:
        self.assertEqual(component_problems(view.to_components()), [])
        for item in view.children:
            if isinstance(item, discord.ui.Select):
                self.assertLessEqual(len(item.options), SELECT_OPTIONS_MAX)

    async def test_every_campaign_menu_cuts_the_names_and_the_list(self) -> None:
        lots = campaigns()
        for build in (
            lambda: dmbot_commands.CampaignPicker(lots),
            lambda: dmbot_commands.BackupPicker(lots),
            lambda: dmbot_commands.RestoreChoice(object(), "copy", lots),
            lambda: transcripts.CampaignPicker(lots, {c.id: 3 for c in lots}),
            lambda: homebrew.CampaignChoice(lots),
            lambda: house_rules.CampaignChoice(lots),
            lambda: optional_rules.CampaignChoice(lots),
            lambda: rule_lookup.CampaignChoice(lots, "fireball"),
            lambda: names.CampaignChoice(lots),
        ):
            with self.subTest(view=build().__class__.__module__ + "." + build().__class__.__name__):
                self.assert_fits(build())

    async def test_the_label_helpers_cut_a_300_character_name(self) -> None:
        self.assertLessEqual(len(logic.name_label(LONG)), logic.NAME_LABEL_MAX)
        self.assertLessEqual(len(logic.shorten(LONG, OPTION_LABEL_MAX)), OPTION_LABEL_MAX)
        self.assertTrue(logic.shorten(LONG, 25).endswith("…"))


class StaticLabelsTest(unittest.TestCase):
    """Every label, placeholder, description and form title written as plain text or a named
    constant in the code is inside its limit. (Labels built from data are the views above.)"""

    def test_no_written_out_text_is_over_its_limit(self) -> None:
        root = pathlib.Path(dmbot.__file__).parent
        over = []
        for path in sorted(root.rglob("*.py")):
            parts = path.relative_to(root).with_suffix("").parts
            name = ".".join(("dmbot", *parts)).removesuffix(".__init__")
            try:
                namespace = vars(importlib.import_module(name))
            except ImportError:  # (a module that needs something not installed here)
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                over += self.problems(node, namespace, path.name)
        self.assertEqual(over, [])

    def problems(self, node: ast.AST, namespace: dict[str, Any], where: str) -> list[str]:
        if isinstance(node, ast.Call):
            callee = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            keywords = node.keywords
        elif isinstance(node, ast.ClassDef) and any(
            "Modal" in (b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", ""))
            for b in node.bases
        ):
            callee, keywords = "Modal", node.keywords
        else:
            return []
        out = []
        for keyword in keywords:
            limit = LIMITS.get(keyword.arg or "", {}).get(callee)
            if limit is None or not isinstance(
                keyword.value, (ast.Constant, ast.Name, ast.Attribute)
            ):
                continue
            try:
                value = eval(compile(ast.Expression(keyword.value), where, "eval"), namespace)
            except Exception:  # (a name that is not a plain constant: built from data)
                continue
            if isinstance(value, str) and len(value) > limit:
                out.append(
                    f"{where}:{node.lineno} {callee} {keyword.arg} is {len(value)}, at most {limit}"
                )
        return out


if __name__ == "__main__":
    unittest.main()
