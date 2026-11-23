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

from dmbot.memory import name_groups as ng
from dmbot.memory import name_trace as nt
from dmbot.memory import scan
from dmbot.memory.name_reader import SPOKEN_OF, Found
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


class TheTypedVersionLosesNothing(unittest.TestCase):
    """The bar for the typed version (design section 10): with the reader returning nothing, every
    name the strict scan offers is still offered in loose mode, whole or inside a longer name
    loose mode offers (the Expert, on slice 2d). "Ordinary" names, which loose mode holds back,
    are by definition ones the strict scan never offered."""

    TRANSCRIPTS = (
        LINES,
        [
            "We reach Bryn Shander at dusk. The guards of Bryn Shander frown.",
            "Speaker Duvessa Shane waits. Duvessa Shane nods at Hlin.",
            "Hlin says the Dwarven Valley is close; the dwarven city is old.",
            "We cross the Dwarven Valley and meet Hlin's friend Torg.",
            "Torg of Easthaven grins. Torg of Easthaven fishes; Easthaven is cold.",
            "roll for it. Roll again, and Kelvin's Cairn looms. kelvin's cairn, said Torg.",
        ],
        [
            "we met Oskar Vane of Bryn Shander today.",
            "we met Oskar Vane of Bryn Shander again.",
            "we cheer Brave Hrothgar today.",
            "we met Hrothgar there.",
            "a brave man.",
            "Ask Heart of Chardalyn now. we saw Chardalyn there. a Chardalyn shard.",
        ],
    )

    @staticmethod
    def words(name: str) -> list[str]:
        return [w.casefold().removesuffix("'s") for w in name.split()]

    def test_every_strict_name_is_still_offered_in_loose_mode(self) -> None:
        for lines in self.TRANSCRIPTS:
            strict = find_new_names(lines, unlimited=True)
            loose = [s for s in find_new_names(lines, unlimited=True, loose=True) if not s.ordinary]
            for name in (s.name for s in strict):
                held = [
                    s.name
                    for s in loose
                    if " ".join(self.words(name)) in " ".join(self.words(s.name))
                ]
                self.assertTrue(held, f"{name!r} offered strict, not loose")

    def test_a_known_name_is_never_joined_across(self) -> None:
        lines = ["We meet Wulfgar of Icewind Dale.", "Then Wulfgar of Icewind Dale nods."]
        got = found(lines, loose=True, skip_keys=["Icewind Dale"])
        self.assertEqual(list(got), ["Wulfgar"])
        got = found(lines, loose=True, skip_keys=["Wulfgar"])
        self.assertEqual(list(got), ["Icewind Dale"])

    def test_split_joined_offers_the_parts_once_and_never_a_skipped_one(self) -> None:
        found_ = [Suggestion("Wulfgar of Icewind Dale", 3), Suggestion("Wulfgar", 2)]
        out, split = scan.split_joined(found_, {"wulfgar"}, {"icewind dale"})
        self.assertEqual([(s.name, s.times) for s in out], [("Wulfgar", 2)])
        self.assertEqual([s.name for s in split], ["Wulfgar of Icewind Dale"])
        out, split = scan.split_joined(found_, {"wulfgar of icewind dale", "wulfgar"})
        self.assertEqual(split, [])


class FromTheReview(unittest.TestCase):
    def test_one_hearing_at_a_sentences_start_is_counted_once(self) -> None:
        self.assertEqual(found(["Ask Heart of Chardalyn now."], loose=True), {})
        got = found(["Ask Heart of Chardalyn now.", "we saw Chardalyn there."], loose=True)
        self.assertEqual(got["Chardalyn"].times, 2)  # (two hearings, never three)
        got = found(
            ["Ask Heart of Chardalyn now.", "we saw Chardalyn there.", "a Chardalyn shard."],
            loose=True,
        )
        self.assertEqual(got["Chardalyn"].times, 3)

    def test_a_join_too_long_to_be_one_name_is_read_as_its_parts(self) -> None:
        lines = [
            "we met Oskar Vane of Bryn Shander today.",
            "we met Oskar Vane of Bryn Shander again.",
        ]
        self.assertEqual(sorted(found(lines, loose=True)), ["Bryn Shander", "Oskar Vane"])

    def test_a_word_said_in_lower_case_leading_a_name_keeps_the_name(self) -> None:
        lines = ["we cheer Brave Hrothgar today.", "we met Hrothgar there.", "a brave man."]
        self.assertEqual(found(lines, loose=False)["Hrothgar"].times, 2)
        self.assertEqual(found(lines, loose=True)["Hrothgar"].times, 2)

    def test_a_person_at_the_table_is_never_part_of_a_name_through_a_possessive(self) -> None:
        lines = ["I think Mia's Wolf ran off.", "Then Mia's Wolf came back."]
        skip = ["mia", "mia stone", "stone"]
        left: list[tuple[str, str]] = []
        got = found(lines, loose=True, skip_keys=skip, refusals=left)
        self.assertEqual(list(got), ["Wolf"])
        self.assertFalse(any("mia" in n.casefold() for n, _why in left))

    def test_a_known_name_with_a_possessive_inside_is_covered_whole(self) -> None:
        lines = ["We climb Kelvin's Cairn.", "Then Kelvin's Cairn looms."]
        self.assertEqual(found(lines, loose=True, skip_keys=["Kelvin's Cairn"]), {})


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

    def test_a_run_that_stopped_at_another_small_word_is_counted(self) -> None:
        lines = ["We meet Wulfgar the Mighty.", "Hrothgar in Bryn Shander waits."]
        left: list[tuple[str, str]] = []
        find_new_names(lines, unlimited=True, loose=True, refusals=left)
        self.assertIn(("Wulfgar the Mighty", scan.SMALL_WORD), left)
        self.assertIn(("Hrothgar in Bryn Shander", scan.SMALL_WORD), left)

    def test_a_skipped_word_is_never_listed(self) -> None:
        # (a person at the table or a known name: never a candidate, so never in the trace)
        left: list[tuple[str, str]] = []
        find_new_names(["We meet Mia once."], ["Mia"], unlimited=True, loose=True, refusals=left)
        self.assertEqual(left, [])


def stand_in(
    trace_dir: str,
    kinds: dict[str, str],
    *,
    on: bool = True,
    unread: int | None = 0,
    texts: list[str] | None = None,
) -> Any:
    """A bot whose name reader (mocked) found the names in `kinds`, with `unread` batches not
    read (None: the reader did not read at all)."""
    texts = TEXTS if texts is None else texts

    async def lines(*_: Any) -> list[Any]:
        return [SimpleNamespace(user_id=1, text=t, topic="game", typed=False) for t in texts]

    async def keys(*_: Any) -> set[str]:
        return set()

    added: list[str] = []

    async def add_entity(*_: Any, name: str, **__: Any) -> Any:
        added.append(name)
        return SimpleNamespace(value=SimpleNamespace(id=f"e-{name}"))

    async def read(
        _g: int, _c: str, _lines: Any, _people: Any, _scan: list[Suggestion], **_kw: Any
    ) -> Any:
        if unread is None:
            return [], [], None
        found = [Found(n, "person", SPOKEN_OF, 2, ("1", "2")) for n in kinds]
        return found, [], unread

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
        _name_closeness=AsyncMock(return_value=(lambda _s: None, lambda _s: False)),
        _keep_found=AsyncMock(),
        _take_name_asks=AsyncMock(return_value=[]),
        _scan_kinds=AsyncMock(return_value={}),
        _session_start=AsyncMock(return_value=None),
        _keep_places=AsyncMock(),
    )


TEXTS = ["We call Hope home.", "Then Hope barks at Vorna.", "I hope so, Vorna said."]


class TheBotStep(unittest.TestCase):
    def run_step(self, bot: Any, texts: list[str] = TEXTS) -> list[str]:
        from dmbot.bot import DMBot

        bot._write_name_trace = lambda *a: DMBot._write_name_trace(bot, *a)
        return asyncio.run(DMBot._propose_new_names(bot, 5, "c1", texts, set(), "s1"))

    def ends(self, folder: str) -> list[tuple[str, str, str]]:
        return [(r.source, r.name, r.reason) for r in nt.read(Path(folder, "trace-c1.jsonl"))]

    def test_when_the_reader_could_not_say_the_trace_blames_the_call_not_the_word(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_step(stand_in(d, {}, unread=1)), ["Vorna"])
            self.assertIn((nt.SCAN, "Hope", scan.ORDINARY_PART_READ), self.ends(d))

    def test_when_the_reader_did_not_read_the_strict_scan_stands(self) -> None:
        texts = ["We meet Wulfgar of Icewind Dale.", "Then Wulfgar of Icewind Dale nods."]
        with tempfile.TemporaryDirectory() as d:
            made = self.run_step(stand_in(d, {}, unread=None, texts=texts), texts)
            self.assertEqual(sorted(made), ["Icewind Dale", "Wulfgar"])  # (never the join)
            self.assertNotIn("Wulfgar of Icewind Dale", [name for _s, name, _r in self.ends(d)])

    def test_a_joined_name_the_reader_split_is_offered_as_its_parts(self) -> None:
        texts = [
            "We meet Wulfgar of Icewind Dale.",
            "Then Wulfgar of Icewind Dale nods.",
        ]
        with tempfile.TemporaryDirectory() as d:
            made = self.run_step(stand_in(d, {"Wulfgar": "npc"}, texts=texts), texts)
            self.assertEqual(sorted(made), ["Icewind Dale", "Wulfgar"])
            self.assertIn((nt.SCAN, "Wulfgar of Icewind Dale", scan.SPLIT), self.ends(d))
        with tempfile.TemporaryDirectory() as d:  # (the reader said nothing of it: offered whole)
            made = self.run_step(stand_in(d, {}, texts=texts), texts)
            self.assertEqual(made, ["Wulfgar of Icewind Dale"])
        with tempfile.TemporaryDirectory() as d:  # (the reader named the whole: kept whole)
            made = self.run_step(
                stand_in(d, {"Wulfgar of Icewind Dale": "npc"}, texts=texts), texts
            )
            self.assertEqual(made, ["Wulfgar of Icewind Dale"])

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

    def test_with_the_switch_off_the_step_is_exactly_today_s(self) -> None:
        # (Supervisor, on #5137: no real campaign's suggestions change until a scored run; two
        # spellings heard once each are still too few, nothing is capitalised, no closeness read)
        texts = ["Then we met Hrothgar at the gate.", "Later Rothgar drank alone.", "Vorna came.",
                 "And Vorna left.", "we saw hrothgar again."]  # fmt: skip
        with tempfile.TemporaryDirectory() as d:
            bot = stand_in(d, {}, on=False, texts=texts)
            self.assertEqual(self.run_step(bot, texts), ["Vorna"])
            bot._name_closeness.assert_not_awaited()
            self.assertIn((nt.SCAN, "Rothgar", scan.TOO_FEW), self.ends(d))

    def test_two_spellings_heard_once_each_are_one_name_with_both(self) -> None:
        # (section 5: grouped before the "heard twice" test; before slice 3 both were lost)
        texts = ["Then we met Hrothgar at the gate.", "Later Rothgar drank alone."]
        with tempfile.TemporaryDirectory() as d:
            bot = stand_in(d, {}, texts=texts)
            self.assertEqual(self.run_step(bot, texts), ["Hrothgar"])
            self.assertEqual([c.args[3] for c in bot.memory.add_alias.await_args_list], ["Rothgar"])
            self.assertIn((nt.SCAN, "Rothgar", ng.GROUPED), self.ends(d))

    def test_with_the_switch_off_nothing_is_kept_for_the_card(self) -> None:
        texts = ["Then we met Vorna at the gate.", "And Vorna left."]
        with tempfile.TemporaryDirectory() as d:
            bot = stand_in(d, {}, on=False, texts=texts)
            self.assertEqual(self.run_step(bot, texts), ["Vorna"])
            bot._keep_found.assert_not_awaited()

    def test_each_suggestion_keeps_what_made_it_a_name(self) -> None:
        texts = ["Then we met Hrothgar at the gate.", "Later Rothgar drank alone."]
        with tempfile.TemporaryDirectory() as d:
            bot = stand_in(d, {}, texts=texts)
            self.run_step(bot, texts)
            (call,) = bot._keep_found.await_args_list
            self.assertEqual(
                (call.args[4].name, call.args[5]), ("Hrothgar", ["Hrothgar", "Rothgar"])
            )

    def test_a_name_close_to_a_secret_is_never_suggested(self) -> None:
        texts = ["Then we met Hrothgar at the gate.", "Later Rothgar drank alone."]
        with tempfile.TemporaryDirectory() as d:
            bot = stand_in(d, {}, texts=texts)
            bot._name_closeness.return_value = (lambda _s: None, lambda s: s == "Rothgar")
            self.assertEqual(self.run_step(bot, texts), [])
            # (never named, even in the dev trace: one count row)
            self.assertIn((nt.SCAN, "", ng.NEAR_SECRET), self.ends(d))
            self.assertNotIn("Hrothgar", [n for _s, n, _r in self.ends(d)])

    def test_when_the_names_cannot_be_read_nothing_is_suggested(self) -> None:
        texts = ["Then we met Hrothgar at the gate.", "Later Hrothgar drank alone."]
        with tempfile.TemporaryDirectory() as d:
            bot = stand_in(d, {}, texts=texts)
            bot._name_closeness.return_value = None
            self.assertEqual(self.run_step(bot, texts), [])

    def test_a_reader_that_did_not_read_leaves_the_strict_scan(self) -> None:
        # (unread None: the loosened rule never decides alone; a name heard once is too few)
        texts = ["We met Hrothgar at the gate.", "Then Hope barks at Vorna.", "And Vorna left."]
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                self.run_step(stand_in(d, {"Hope": "creature"}, unread=None, texts=texts), texts),
                ["Vorna"],
            )
            self.assertIn((nt.SCAN, "Hrothgar", ng.TOO_FEW), self.ends(d))


# Rule 2 (design section 11): the functions in the name paths that look at a letter's case. The
# list may only shrink: a new one fails, and one that no longer looks must leave the list.
NAME_PATHS = (
    "memory/scan.py",
    "memory/name_groups.py",
    "memory/name_reader.py",
    "memory/name_longer.py",
    "memory/name_trace.py",
    "transcript/name_pass.py",
    "names/not_names.py",
    # (the live Cleaner keeps its capital test until a word list exists, section 7: on the list
    # where it can be seen)
    "transcript/cleaner.py",
)
CASE_CALLS = frozenset({"isupper", "islower", "istitle", "capitalize", "title"})
# A pattern that looks for an upper-case letter looks at case too (the Expert, on slice 2d)
UPPER_CLASSES = ("A-Z", r"\p{Lu}", "[:upper:]", r"\p{Uppercase")
LOOKS_AT_CASE = frozenset(
    {
        "memory/scan.py:_is_name_word",  # (the strict scan; the loose one passes no lower words)
        "memory/scan.py:_candidates.leads",  # (#5086: a capitalized word leading a name)
        "memory/scan.py:find_new_names",  # (which words are said in lower case: `ordinary`)
        "memory/name_longer.py:widened",  # (#2570: a place's lower-case last word)
        # (#5133, the Expert and Supervisor: the spelling proposed for the DM to confirm gets
        # capitals when none was heard; it shapes the proposal and decides nothing)
        "memory/name_groups.py:proposed",
        # (#5133 slice 3c-2b: the guard on a "change to" rule refuses a spelling said in lower case
        # elsewhere; it only ever stops a rule, never makes a name)
        "memory/scan.py:ordinary_in",
        "names/not_names.py:_clause",  # (a lower-case clause word: "so", "and")
        "names/not_names.py:shape_of",  # (a number or a time said with a capital)
        "names/not_names.py:_lower_everywhere",  # (a word only ever said in lower case)
        "names/not_names.py:why_not",
        "transcript/cleaner.py:_by_sound.unknown",  # (the Cleaner's capital test, section 7)
        "transcript/cleaner.py:_lower_words",
        "transcript/cleaner.py:_named_words",
        "transcript/cleaner.py:_respelled",  # (keeps the heard word's capital on a fix)
    }
)
# Patterns that look for a capital: none today. A new one fails, as a new case call does.
UPPER_PATTERNS: frozenset[str] = frozenset()


def _compares_case(node: ast.Compare) -> bool:
    sides = [node.left, *node.comparators]
    for side in sides:
        if (
            isinstance(side, ast.Call)
            and isinstance(side.func, ast.Attribute)
            and side.func.attr in ("lower", "upper", "casefold")
            and any(ast.dump(o) == ast.dump(side.func.value) for o in sides if o is not side)
        ):
            return True
    return False


def case_functions(source: str | None = None) -> tuple[set[str], set[str]]:
    """`file:function` for each function in the name paths that calls a case method, and (`file:
    <module>` at the top level) each that holds a string with an upper-case pattern class.
    `source`: read this text as one file instead (the test of the test)."""
    root = Path(__file__).resolve().parents[1] / "src" / "dmbot"
    out: set[str] = set()
    patterns: set[str] = set()

    def walk(rel: str, node: ast.AST, where: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                walk(rel, child, [*where, child.name])
                continue
            # a case method, called or passed ("filter(str.isupper, ...)"), or a word compared
            # with its own lower or upper case ("w != w.lower()")
            if (isinstance(child, ast.Attribute) and child.attr in CASE_CALLS) or (
                isinstance(child, ast.Compare) and _compares_case(child)
            ):
                out.add(f"{rel}:{'.'.join(where) or '<module>'}")
            if (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and any(c in child.value for c in UPPER_CLASSES)
            ):
                patterns.add(f"{rel}:{'.'.join(where) or '<module>'}")
            walk(rel, child, where)

    if source is not None:
        walk("given", ast.parse(source), [])
    for rel in NAME_PATHS if source is None else ():
        walk(rel, ast.parse((root / rel).read_text(encoding="utf-8")), [])
    return out, patterns


class RuleTwo(unittest.TestCase):
    def test_the_functions_that_look_at_case_in_the_name_paths_only_shrink(self) -> None:
        calls, patterns = case_functions()
        self.assertEqual(calls, set(LOOKS_AT_CASE))
        self.assertEqual(
            patterns, set(UPPER_PATTERNS), "a pattern in a name path looks for a capital"
        )

    def test_the_list_sees_a_case_call_and_an_upper_case_pattern(self) -> None:
        source = (
            "import re\nCAP = re.compile('[A-Z][a-z]+')\n"
            "def f(w):\n    return w.istitle()\n"
            "def h(w):\n    return w != w.lower()\n"
            "def k(ws):\n    return filter(str.isupper, ws)\n"
            "UP = [w for w in 'ab' if w.isupper()]\n"
            "def g(w):\n    return re.match(r'\\p{Lu}', w)\n"
        )
        self.assertEqual(
            case_functions(source),
            (
                {"given:f", "given:h", "given:k", "given:<module>"},
                {"given:<module>", "given:g"},
            ),
        )
