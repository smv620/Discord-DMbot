"""The website's "yes" codec and line-reference parser (#5237, follow-up of #5144): the small pure
helpers the end-to-end test rests on, so a later edit that breaks one fails here and says where."""

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory.models import NAME_MAX
from dmbot.memory.name_found import Found
from dmbot.recaps import heard_fix
from dmbot.recaps import name_answers as na

# (The stand-ins are typed Any: the real Database and consent objects are not needed, and the
# functions only read the few attributes the stand-ins give.)


class YesNoteTest(unittest.TestCase):
    def test_a_yes_note_reads_back_what_was_written(self) -> None:
        note = na.yes_note("fixed", "place", "Hrothgar Hall", ["Rothgar Hall", "Roth Gar"])
        self.assertEqual(
            na.read_yes_note(note),
            ("fixed", "place", "Hrothgar Hall", ["Rothgar Hall", "Roth Gar"]),
        )

    def test_each_stage_reads_back(self) -> None:
        for fixed in ("fixed", "later", "failed"):  # (the three stages the page reads)
            with self.subTest(fixed):
                note = na.yes_note(fixed, "place", "Hrothgar Hall", ["Rothgar Hall"])
                self.assertEqual(
                    na.read_yes_note(note), (fixed, "place", "Hrothgar Hall", ["Rothgar Hall"])
                )

    def test_the_note_format_is_pinned(self) -> None:
        # (a stored row is read back by this format, so the literal text is checked, not just
        # the round trip: a swap made on both sides would otherwise pass)
        note = na.yes_note("fixed", "place", "Hrothgar Hall", ["Rothgar Hall"])
        self.assertEqual(note, "fixed|place|Hrothgar Hall|Rothgar Hall")

    def test_a_note_with_no_rules_reads_back_with_an_empty_list(self) -> None:
        note = na.yes_note("fixed", "place", "Hrothgar Hall", [])
        self.assertEqual(na.read_yes_note(note), ("fixed", "place", "Hrothgar Hall", []))

    def test_a_note_is_cut_to_the_column_by_whole_rules(self) -> None:
        rules = [f"rule{i:02d}" + "x" * 4 for i in range(40)]  # ten letters each
        note = na.yes_note("fixed", "place", "Hrothgar Hall", rules)
        self.assertLessEqual(len(note), na.NOTE_ROOM)
        back = na.read_yes_note(note)
        assert back is not None
        kept = back[3]
        self.assertLess(len(kept), 40)
        self.assertEqual(kept, rules[: len(kept)])  # whole, and in the given order

    def test_a_long_name_is_cut_to_the_names_own_limit(self) -> None:
        note = na.yes_note("fixed", "place", "n" * 300, [])
        back = na.read_yes_note(note)
        assert back is not None
        self.assertLessEqual(len(back[2]), NAME_MAX)
        self.assertEqual(len(back[2]), NAME_MAX)

    def test_a_note_that_is_not_a_yes_note_reads_as_none(self) -> None:
        self.assertIsNone(na.read_yes_note(""))
        self.assertIsNone(na.read_yes_note("Split into 3 names."))
        self.assertIsNone(na.read_yes_note("bogus|place|Name"))


class FoundTextTest(unittest.TestCase):
    def test_the_found_text_format_is_pinned(self) -> None:
        # (Found is (how, line_ref, typed, times), but the text is written and read as
        # times then typed; the literal text is checked so a swap on both sides still fails)
        self.assertEqual(
            na.found_text(Found("introduced", "s1:1000:7", 2, 5)),
            "introduced|5|2|s1:1000:7",
        )

    def test_what_made_a_name_a_name_reads_back(self) -> None:
        text = na.found_text(Found("introduced", "s1:1000:7", 2, 5))
        self.assertEqual(na.read_found(text), ("introduced", "s1:1000:7", 5, 2))

    def test_a_found_text_with_no_line_reads_back_with_none(self) -> None:
        text = na.found_text(Found("typed", None, 0, 3))
        self.assertEqual(na.read_found(text), ("typed", None, 3, 0))
        back = na.read_found(text)
        assert back is not None
        self.assertIsNone(back[1])

    def test_a_damaged_found_text_reads_as_none(self) -> None:
        self.assertIsNone(na.read_found("introduced|x|y"))
        self.assertIsNone(na.read_found("a|b|c|d"))  # (the counts are not digits)
        self.assertIsNone(na.read_found(""))


class SessionOfRefTest(unittest.TestCase):
    def test_the_session_of_a_line_reference(self) -> None:
        self.assertEqual(heard_fix.session_of_ref("s1:1000:7"), "s1")
        self.assertEqual(heard_fix.session_of_ref("a:b:1000:7"), "a:b")  # (a colon in the id)

    def test_a_reference_that_is_not_one_gives_none(self) -> None:
        for bad in (None, "", "s1:x:7", "s1:1000"):
            with self.subTest(bad):
                self.assertIsNone(heard_fix.session_of_ref(bad))


class SessionLinesTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_session_lines_are_none_when_they_cannot_be_read(self) -> None:
        consent: Any = SimpleNamespace(recorded_in=AsyncMock(return_value=frozenset({7})))
        self.assertIsNone(await heard_fix.session_lines(None, consent, 1, "c", "s1"))
        broken: Any = SimpleNamespace(lines=AsyncMock(side_effect=RuntimeError("down")))
        self.assertIsNone(await heard_fix.session_lines(broken, consent, 1, "c", "s1"))
        self.assertIsNone(await heard_fix.session_lines(broken, consent, 1, "c", None))

    async def test_only_lines_of_people_recorded_now_count_and_sidebar_lines_never(self) -> None:
        lines: Any = [
            SimpleNamespace(user_id=7, text="a", sidebar=None),
            SimpleNamespace(user_id=8, text="b", sidebar=None),
            SimpleNamespace(user_id=7, text="c", sidebar="x"),
        ]
        transcripts: Any = SimpleNamespace(lines=AsyncMock(return_value=lines))
        consent: Any = SimpleNamespace(recorded_in=AsyncMock(return_value=frozenset({7})))
        self.assertEqual(await heard_fix.session_lines(transcripts, consent, 1, "c", "s1"), ["a"])

    async def test_a_consent_lookup_that_fails_gives_none(self) -> None:
        transcripts: Any = SimpleNamespace(lines=AsyncMock(return_value=[]))
        consent: Any = SimpleNamespace(recorded_in=AsyncMock(side_effect=RuntimeError("down")))
        self.assertIsNone(await heard_fix.session_lines(transcripts, consent, 1, "c", "s1"))

    async def test_with_nobody_recorded_there_is_nothing_to_test_against(self) -> None:
        lines: Any = [SimpleNamespace(user_id=8, text="b", sidebar=None)]
        transcripts: Any = SimpleNamespace(lines=AsyncMock(return_value=lines))
        consent: Any = SimpleNamespace(recorded_in=AsyncMock(return_value=frozenset({7})))
        self.assertIsNone(await heard_fix.session_lines(transcripts, consent, 1, "c", "s1"))
