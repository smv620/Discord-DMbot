"""No schema we send to the AI service uses a keyword it refuses (#4652). The service answers
HTTP 400 to `minimum`/`maximum` on an integer (and does not support the other length and range
keywords), so a call carrying one never runs and reads as "nothing found". A range is an `enum`, or
is checked in code after the answer is parsed. No database, no network."""

from __future__ import annotations

import importlib
import pkgutil
import re
import unittest
from pathlib import Path
from typing import Any

import dmbot

REFUSED = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
)
SRC = Path(dmbot.__file__).parent


def _keys(node: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in REFUSED:
                found.append(f"{path}/{key}")
            found.extend(_keys(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_keys(value, f"{path}/{i}"))
    return found


class TheSchemasWeSend(unittest.TestCase):
    def test_no_source_file_spells_a_refused_keyword_as_a_schema_key(self) -> None:
        quoted = re.compile(r"""["'](""" + "|".join(REFUSED) + r""")["']\s*:""")
        bad = []
        for path in SRC.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if quoted.search(line):
                    bad.append(f"{path.relative_to(SRC)}:{number}")
        self.assertEqual(bad, [], "a schema key the AI service refuses (HTTP 400)")

    def test_no_module_level_schema_holds_one_either(self) -> None:
        bad: list[str] = []
        for info in pkgutil.walk_packages(dmbot.__path__, "dmbot."):
            if ".migrations" in info.name or ".devtools" in info.name:
                continue
            try:
                module = importlib.import_module(info.name)
            except (
                Exception
            ):  # (a module that needs a service to import: the source scan covers it)
                continue
            for name, value in vars(module).items():
                if "SCHEMA" in name and isinstance(value, dict):
                    bad.extend(f"{info.name}.{name}{key}" for key in _keys(value))
        self.assertEqual(bad, [])

    def test_the_source_scan_matches_either_quote_and_a_tight_colon(self) -> None:
        quoted = re.compile(r"""["'](""" + "|".join(REFUSED) + r""")["']\s*:""")
        self.assertTrue(quoted.search('"maximum": 5'))
        self.assertTrue(quoted.search("'minLength':1"))
        self.assertIsNone(quoted.search("the maximum is five"))

    def test_the_walker_finds_one_nested_in_a_list(self) -> None:
        schema = {"properties": {"a": {"items": [{"type": "integer", "maximum": 5}]}}}
        self.assertEqual(_keys(schema), ["/properties/a/items/0/maximum"])


if __name__ == "__main__":
    unittest.main()
