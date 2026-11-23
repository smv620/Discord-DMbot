"""The loosened scan (docs/design/session_names.md, section 3; slice 2d): beside the name reader, a
word also said in lower case may be a name, a name may hold "of" or "of the" and a possessive, and a
name made only of ordinary words goes on only when the reader found it too. The scan's own refusals
are listed for the trace. Rule 2's list of the functions that look at a letter's case in the name
paths, which may only shrink. Invented lines; the sample's place names are public."""

from __future__ import annotations

import ast
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory import name_trace as nt
from dmbot.memory import scan
from dmbot.memory.scan import Suggestion, find_new_names, group_alike

LINES = [
    "We ride to the Spine of the World tonight.",
    "The cold of the Spine of the World bites.",
    "We found the Heart of Chardalyn there.",
    "They want the Heart of Chardalyn back.",
    "It glows like Chardalyn does, and Chardalyn hums.",
    "We camp at Kelvin's Cairn and climb Kelvin's Cairn.",
    "Hrothgar's axe broke and Hrothgar laughed.",
]


def found(lines: list[str], *, loose: bool, **kw: Any) -> dict[str, Suggestion]:
    return {
        s.name: s for s in group_alike(find_new_names(lines, unlimited=True, loose=loose, **kw))
    }


class TheLooseRule(unittest.TestCase):
    def test_a_name_holding_of_or_of_the_is_one_name(self) -> None:
        got = found(LINES, loose=True)
        self.assertIn("Spine of the World", got)
        self.assertIn("Heart of Chardalyn", got)
        # its parts heard only inside it are never offered alone; a part heard alone still is
        for part in ("Spine", "World", "Heart"):
            self.assertNotIn(part, got)
        # (heard alone too, so offered with every sighting, as #5086 counts them; never folded in)
        self.assertEqual(got["Chardalyn"].times, 4)

    def test_a_possessive_inside_a_name_is_kept_and_one_at_its_end_is_not(self) -> None:
        got = found(LINES, loose=True)
        self.assertIn("Kelvin's Cairn", got)
        self.assertIn("Hrothgar", got)
        self.assertNotIn("Hrothgar's", got)

    def test_a_word_also_said_in_lower_case_may_be_a_name_but_is_marked_ordinary(self) -> None:
        lines = ["We call Hope home.", "Then Hope barks.", "I hope so."]
        self.assertEqual(found(lines, loose=False), {})  # (strict: "hope" rules it out)
        got = found(lines, loose=True)
        self.assertTrue(got["Hope"].ordinary)

    def test_a_name_with_one_word_never_said_in_lower_case_is_not_ordinary(self) -> None:
        lines = ["We reach Ten Towns.", "Ten Towns waits.", "We saw ten wolves in two towns."]
        self.assertTrue(found(lines, loose=True)["Ten Towns"].ordinary)
        lines = ["We reach Ten Vorns.", "Ten Vorns waits.", "We saw ten wolves."]
        self.assertFalse(found(lines, loose=True)["Ten Vorns"].ordinary)

    def test_joining_words_do_not_count_toward_the_limit(self) -> None:
        lines = ["We sail the Sea of Moving Ice.", "The Sea of Moving Ice is wide."]
        self.assertIn("Sea of Moving Ice", found(lines, loose=True))
        too_long = ["We see the Old Grey Cold Sea of Ice.", "The Old Grey Cold Sea of Ice."]
        self.assertNotIn("Old Grey Cold Sea of Ice", found(too_long, loose=True))

    def test_a_join_at_a_sentences_start_still_finds_the_name_after_the_first_word(self) -> None:
        lines = ["Ask Heart of Chardalyn keepers.", "We hold the Heart of Chardalyn."]
        self.assertEqual(found(lines, loose=True)["Heart of Chardalyn"].times, 2)

    def test_a_shorter_name_never_folds_into_a_name_joined_by_of(self) -> None:
        got = group_alike([Suggestion("Heart of Chardalyn", 2), Suggestion("Chardalyn", 3)])
        self.assertEqual(sorted(s.name for s in got), ["Chardalyn", "Heart of Chardalyn"])

    def test_grouping_keeps_a_name_plain_when_one_of_its_spellings_is_plain(self) -> None:
        got = group_alike(
            [Suggestion("Oskar Vane", 2, ordinary=True), Suggestion("Vane", 2, ordinary=False)]
        )
        self.assertEqual([(s.name, s.ordinary) for s in got], [("Oskar Vane", False)])

    def test_strict_is_as_before(self) -> None:
        got = found(LINES, loose=False)
        self.assertIn("Kelvin Cairn", got)
        self.assertNotIn("Spine of the World", got)
        self.assertFalse(any(s.ordinary for s in got.values()))


class TheRefusals(unittest.TestCase):
    def test_each_candidate_left_out_has_one_fixed_reason(self) -> None:
        lines = [
            *LINES,
            "Bryn walks. Bryn sits.",  # (only at a sentence's start)
            "We meet Vell once.",
            "The Old Grey Cold Sea of Ice.",
        ]
        left: list[tuple[str, str]] = []
        find_new_names(lines, unlimited=True, loose=True, refusals=left)
        why = dict(left)
        self.assertEqual(why["Spine"], scan.INSIDE)
        self.assertEqual(why["Bryn"], scan.AT_START)
        self.assertEqual(why["Vell"], scan.TOO_FEW)
        self.assertEqual(why["Old Grey Cold Sea of Ice"], scan.TOO_LONG)
        self.assertNotIn("Chardalyn", why)  # (offered)

    def test_a_skipped_word_is_never_listed(self) -> None:
        # (a person at the table or a known name: never a candidate, so never in the trace)
        left: list[tuple[str, str]] = []
        find_new_names(["We meet Mia once."], ["Mia"], unlimited=True, loose=True, refusals=left)
        self.assertEqual(left, [])


def stand_in(trace_dir: str, kinds: dict[str, str], *, on: bool = True) -> Any:
    """A bot whose name reader (mocked) found the names in `kinds`."""

    async def lines(*_: Any) -> list[Any]:
        return [SimpleNamespace(user_id=1, text=t, topic="game") for t in TEXTS]

    async def keys(*_: Any) -> set[str]:
        return set()

    added: list[str] = []

    async def add_entity(*_: Any, name: str, **__: Any) -> Any:
        added.append(name)
        return SimpleNamespace(value=SimpleNamespace(id=f"e-{name}"))

    async def read(_g: int, _c: str, _lines: Any, _people: Any, every: list[Suggestion]) -> Any:
        return every, dict(kinds), []

    async def campaign(*_: Any) -> Any:
        return SimpleNamespace(name="test of the frostmaiden (sample 41b.1)")

    return SimpleNamespace(
        added=added,
        settings=SimpleNamespace(session_names_on=on, name_trace_dir=trace_dir, name_trace_guild=5),
        name_pass_ai=object(),
        memory=SimpleNamespace(known_keys=keys, add_entity=add_entity, add_alias=AsyncMock()),
        transcripts=SimpleNamespace(lines=lines),
        campaigns=SimpleNamespace(get=campaign),
        import_job=None,
        add_short_names=AsyncMock(),
        drop_language_suggestions=AsyncMock(),
        _people_keys=AsyncMock(return_value=set()),
        _read_session_names=read,
        _take_name_asks=AsyncMock(return_value=[]),
        _scan_kinds=AsyncMock(return_value={}),
        _session_start=AsyncMock(return_value=None),
        _keep_places=AsyncMock(),
    )


TEXTS = ["We call Hope home.", "Then Hope barks at Vorna.", "I hope so, Vorna said."]


class TheBotStep(unittest.TestCase):
    def run_step(self, bot: Any) -> list[str]:
        from dmbot.bot import DMBot

        bot._write_name_trace = lambda *a: DMBot._write_name_trace(bot, *a)
        return asyncio.run(DMBot._propose_new_names(bot, 5, "c1", TEXTS, set(), "s1"))

    def test_an_ordinary_word_goes_on_only_when_the_reader_found_it_too(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_step(stand_in(d, {})), ["Vorna"])
            rows = nt.read(Path(d, "trace-c1.jsonl"))
            self.assertIn(
                (nt.SCAN, "Hope", scan.ORDINARY), [(r.source, r.name, r.reason) for r in rows]
            )
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                sorted(self.run_step(stand_in(d, {"Hope": "creature"}))), ["Hope", "Vorna"]
            )

    def test_with_the_switch_off_the_scan_is_as_before(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_step(stand_in(d, {"Hope": "creature"}, on=False)), ["Vorna"])


# Rule 2 (design section 11): the functions in the name paths that look at a letter's case. The
# list may only shrink: a new one fails, and one that no longer looks must leave the list.
NAME_PATHS = (
    "memory/scan.py",
    "memory/name_reader.py",
    "memory/name_longer.py",
    "memory/name_trace.py",
    "transcript/name_pass.py",
    "names/not_names.py",
)
CASE_CALLS = frozenset({"isupper", "islower", "istitle", "capitalize", "title"})
LOOKS_AT_CASE = frozenset(
    {
        "memory/scan.py:_is_name_word",  # (the strict scan; the loose one passes no lower words)
        "memory/scan.py:_candidates.leads",  # (#5086: a capitalized word leading a name)
        "memory/scan.py:find_new_names",  # (which words are said in lower case: `ordinary`)
        "memory/name_longer.py:widened",  # (#2570: a place's lower-case last word)
        "names/not_names.py:_clause",  # (a lower-case clause word: "so", "and")
        "names/not_names.py:shape_of",  # (a number or a time said with a capital)
    }
)


def case_functions() -> set[str]:
    root = Path(__file__).resolve().parents[1] / "src" / "dmbot"
    out: set[str] = set()

    def walk(rel: str, node: ast.AST, where: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                walk(rel, child, [*where, child.name])
                continue
            if (
                where
                and isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in CASE_CALLS
            ):
                out.add(f"{rel}:{'.'.join(where)}")
            walk(rel, child, where)

    for rel in NAME_PATHS:
        walk(rel, ast.parse((root / rel).read_text(encoding="utf-8")), [])
    return out


class RuleTwo(unittest.TestCase):
    def test_the_functions_that_look_at_case_in_the_name_paths_only_shrink(self) -> None:
        self.assertEqual(case_functions(), set(LOOKS_AT_CASE))
