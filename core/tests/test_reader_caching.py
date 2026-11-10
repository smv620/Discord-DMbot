"""The document readers cache their instructions (#4054): they send the same system text for
every piece of a document, so the service is asked to remember it. Read from bot.py's own source,
so a reader added or rebuilt without the flag is seen here."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import dmbot.bot

READERS = {
    "NAMES", "NAMES_BACKGROUND", "NAMES_BACKGROUND_READ", "PLAN_READER", "NAME_PASS",
    "HOUSE_RULES", "THEATRE",
}  # fmt: skip


def tier_calls() -> dict[str, bool]:
    """Feature name -> whether every `.tier(...)` call for it in bot.py sets cache_system=True."""
    tree = ast.parse(Path(dmbot.bot.__file__).read_text(encoding="utf-8"))
    found: dict[str, bool] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "tier" or len(node.args) < 2:
            continue
        feature = node.args[1]
        if not (isinstance(feature, ast.Attribute) and isinstance(feature.value, ast.Name)):
            continue
        if feature.value.id != "Feature":
            continue
        on = any(
            k.arg == "cache_system" and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in node.keywords
        )
        found[feature.attr] = found.get(feature.attr, True) and on  # (every call site)
    return found


class TheReaders(unittest.TestCase):
    def test_every_document_reader_asks_for_its_instructions_to_be_cached(self) -> None:
        found = tier_calls()
        for name in sorted(READERS):
            self.assertIn(name, found, name)
            self.assertTrue(found[name], f"{name} is built without cache_system=True")

    def test_a_job_with_a_different_prompt_each_call_is_not_cached_by_this_change(self) -> None:
        found = tier_calls()
        for name in ("SIDEBAR", "FIGHTBOT", "TIME_SUGGEST", "SHEET"):
            self.assertIn(name, found, name)  # (so a rename can't make this pass for nothing)
            self.assertFalse(found[name], name)
