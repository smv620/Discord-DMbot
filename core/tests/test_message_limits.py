"""One message limit (#1835): the builders stay inside it, and no module carries its own 2000.

Discord refuses a message over 2,000 characters and the whole action fails, so every builder that
formats campaign names into one message is fed the worst case here, and a source check keeps new
copies of the number out.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from dmbot.discord_limits import MESSAGE_MAX
from dmbot.memory import story_watch as memory_story_watch
from dmbot.rules.homebrew import Homebrew
from dmbot.ui import guide, story_watch
from dmbot.ui import homebrew as brew_ui

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

# Files that use a number from 1500 to 2000 as a length that is not a message's, and why.
NOT_A_MESSAGE = {
    "ai.py": "the length of an error body kept for the log",
    "devtools/recheck.py": "characters of scene text fed to a test prompt",
    "memory/adventure.py": "characters per chunk of a book",
    "memory/story_plan.py": "the most nodes read from one document",
    "memory/who_ask.py": "characters of story text shown to the who-is-it check",
    "recaps/draft.py": "characters of the last recap given to the next draft's prompt",
    "recaps/inputs.py": "characters of one session's notes given to the recap writer's prompt",
    "recaps/critics.py": "characters of the earlier recap shown to the critics",
    "recaps/motives.py": "characters of the earlier recap shown to the motive check",
    "recaps/requests.py": "the longest transcribed voice note",
    "recaps/significance.py": "characters of the earlier recap shown to the significance check",
    "rules/homebrew.py": "the longest homebrew text stored",
    "rules/house_file_link.py": "the longest link",
    "rules/rule_sets.py": "the longest link",
    "rules/site_urls.py": "the longest address",
    "transcript/import_link.py": "the longest Google Doc link",
    "transcript/import_plan.py": "the longest stored line of an imported transcript",
    "sidebar/context.py": "characters of recent scene given to a prompt",
    "sidebar/knowledge.py": "characters in one chunk of the rules knowledge",
    "memory/web_sheets.py": "the longest list of names a typed sheet from the website holds",
    "web/imports.py": "the earliest year a session date may have",
    "web/lore.py": "the longest link an uploaded lore text may name",
    "web/sheets.py": "the longest list of names Discord's typed-sheet form holds",
    "ui/name_lists.py": "the longest link in a form field",
    "ui/rule_lookup.py": "entries kept in a map",
}

LENGTH_LOW, LENGTH_HIGH = 1500, 2000
LENGTH_NAMES = ("_MAX", "_LIMIT", "_CHARS", "_SHOWN")  # a constant that is a length


def _is_length_name(name: str) -> bool:
    return name.endswith(LENGTH_NAMES) or name in ("LIMIT", "MAX")


def _in_range(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Constant)
        and type(node.value) is int
        and LENGTH_LOW <= node.value <= LENGTH_HIGH
    )


def _numbers_in(node: ast.AST) -> list[int]:
    return [n.lineno for n in ast.walk(node) if isinstance(n, ast.Constant) and _in_range(n)]


def literals_of_2000(source: str) -> list[int]:
    """Lines with a number from 1500 to 2000 used as a length: a `*_MAX` / `*_LIMIT` constant, a
    comparison, a slice end, an argument to a `fit` or `cut` function, a `limit=`, a default for
    a `limit` parameter, or a budget such as `1900 - len(text)`. (The name is kept from when it
    looked only for 2000.)"""
    found: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if node.value is not None and any(_is_length_name(n) for n in names):
                found.update(_numbers_in(node.value))
        elif isinstance(node, ast.Slice | ast.Compare):
            found.update(_numbers_in(node))
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if "fit" in name or "cut" in name:
                for arg in node.args:
                    found.update(_numbers_in(arg))
            for keyword in node.keywords:
                if keyword.arg == "limit":
                    found.update(_numbers_in(keyword.value))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub) and _in_range(node.left):
            found.add(node.lineno)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = node.args
            names = [a.arg for a in args.args[len(args.args) - len(args.defaults) :]]
            for arg_name, default in zip(names, args.defaults, strict=True):
                if "limit" in arg_name:
                    found.update(_numbers_in(default))
    return sorted(found)


def names_of_25() -> list[str]:
    return ["N" * 100 for _ in range(25)]


class MessageLimitTest(unittest.TestCase):
    def test_no_module_writes_its_own_message_limit(self) -> None:
        bad = []
        for path in SRC.rglob("*.py"):
            rel = path.relative_to(SRC).as_posix()
            if rel == "discord_limits.py" or rel in NOT_A_MESSAGE:
                continue
            bad += [f"{rel}:{line}" for line in literals_of_2000(path.read_text())]
        self.assertEqual(bad, [], "use dmbot.discord_limits.MESSAGE_MAX / fit_message")

    def test_the_allow_list_has_no_stale_entries(self) -> None:
        for rel in NOT_A_MESSAGE:
            self.assertTrue(literals_of_2000((SRC / rel).read_text()), f"{rel} no longer needs it")

    def test_the_check_sees_a_copy(self) -> None:
        self.assertEqual(literals_of_2000("LIMIT = 2_000\nx = 1999"), [1])

    def test_guide_text_stays_inside_one_message(self) -> None:
        text = guide._fit([f"{n}. " + "**" + "x" * 100 + "**" for n in range(25)])
        self.assertLessEqual(len(text), MESSAGE_MAX)
        blocks = guide._fit_blocks(["**" + "y" * 300 + "**" for _ in range(25)])
        self.assertLessEqual(len(blocks), MESSAGE_MAX)

    def test_story_watch_message_stays_inside_one_message(self) -> None:
        line_pieces = names_of_25()
        clash = memory_story_watch.Clash(
            id="d" * 32,
            book_name=line_pieces[0],
            book_needs=line_pieces[1],
            need=line_pieces[2],
            chapter=line_pieces[3],
            table_name=line_pieces[4],
            condition="x" * 100,
            session_started_at=None,
        )
        found = memory_story_watch.Found(shown=(clash,) * 25, more=40)
        self.assertLessEqual(len(story_watch.message_text(found, {})), MESSAGE_MAX)

    def test_the_length_check_sees_each_way_of_writing_a_limit(self) -> None:
        for source in (
            "PANEL_MAX = 1900",
            "text[:1900]",
            "if len(text) > 1800:\n    pass",
            "_fit(text, 1900)",
            "fit_message(text, limit=1950)",
            "budget = 1900 - len(text)",
            "def f(text, limit=1990):\n    pass",
        ):
            self.assertTrue(literals_of_2000(source), source)
        self.assertEqual(literals_of_2000("CHUNK = 1900\nrounds = 1800"), [])

    def test_homebrew_lists_stay_inside_one_message(self) -> None:
        def entry(n: int) -> Homebrew:
            return Homebrew(
                id=n, campaign_id="c", kind="item", name="N" * 100, text="T " * 800,
                summary="S" * 100, created_by=7, created_at=1, updated_at=1,
            )  # fmt: skip

        entries = [entry(n) for n in range(25)]
        campaign = cast(Any, SimpleNamespace(name="C" * 100))
        for note in ("", "n" * 300):
            text = brew_ui.list_text(campaign, entries, 0, note=note, kind="item")
            self.assertLessEqual(len(text), MESSAGE_MAX)
            self.assertLessEqual(len(brew_ui.entry_text(entries[0], note=note)), MESSAGE_MAX)
