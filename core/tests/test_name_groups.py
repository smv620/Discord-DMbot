"""One name heard several ways becomes one suggestion (docs/design/session_names.md, section 5;
`memory/name_groups.py`). Pure: invented names, no store, no Discord, no AI."""

from __future__ import annotations

import unittest

from dmbot.memory import name_groups as ng
from dmbot.memory.name_groups import Spelling
from dmbot.memory.scan import Match, same_thing


def names(settled: ng.Settled) -> list[tuple[str, tuple[str, ...], int]]:
    return [(g.name, g.also, g.times) for g in settled.offered]


def reasons(settled: ng.Settled) -> dict[str, str]:
    return {n: why for n, why, _t in settled.refused}


class TheFold(unittest.TestCase):
    """The design asks for a test, not an assumption: does a two-word spelling fold with a
    one-word one?"""

    def test_a_name_heard_as_two_words_folds_with_the_one_word_spellings(self) -> None:
        self.assertTrue(same_thing("Hrothgar", "hroth gar"))
        self.assertTrue(same_thing("Rothgar", "hroth gar"))
        self.assertTrue(same_thing("Bryn Shander", "Brin Shandar"))

    def test_names_that_only_look_alike_stay_apart(self) -> None:
        self.assertFalse(same_thing("Red Larch", "Redlarch Mill"))
        self.assertFalse(same_thing("Kael", "Kaela"))  # (one word: as alike as the Cleaner needs)


class TheOrder(unittest.TestCase):
    def test_spellings_heard_once_each_are_grouped_before_the_count(self) -> None:
        got = ng.settle([Spelling("Hrothgar", 1), Spelling("Rothgar", 1), Spelling("hroth gar", 1)])
        self.assertEqual(names(got), [("Hrothgar", ("Rothgar", "hroth gar"), 3)])
        self.assertEqual(reasons(got), {"Rothgar": ng.GROUPED, "hroth gar": ng.GROUPED})

    def test_the_reader_finding_a_scan_spelling_again_never_loses_the_name(self) -> None:
        # (each reader's hearings add across the group, then the larger: the reader's one hearing
        # of "Hrothgar" never hides the scan's two across the group)
        got = ng.settle(
            [
                Spelling("Hrothgar", 1),
                Spelling("Rothgar", 1),
                Spelling("Hrothgar", 1, why="spoken_of"),
            ]
        )
        self.assertEqual(names(got), [("Hrothgar", ("Rothgar",), 2)])

    def test_every_spelling_ends_exactly_once(self) -> None:
        given = ["Hrothgar", "Rothgar", "Vorna", "Ostrin", "hroth gar", "Dwarven Valley", "Valley"]
        got = ng.settle([Spelling(n, 1) for n in given[:4]] + [Spelling(n, 2) for n in given[4:]])
        ends = [g.name for g in got.offered] + [n for n, _why, _t in got.refused]
        self.assertEqual(sorted(ends), sorted(given))

    def test_one_spelling_heard_once_by_the_scan_has_too_few_hearings(self) -> None:
        got = ng.settle([Spelling("Vorna", 1)])
        self.assertEqual((names(got), reasons(got)), ([], {"Vorna": ng.TOO_FEW}))

    def test_a_name_introduced_once_is_enough(self) -> None:
        got = ng.settle([Spelling("hrothgar", 1, why=ng.INTRODUCED)])
        self.assertEqual(names(got), [("Hrothgar", (), 1)])

    def test_a_reader_name_spoken_of_once_needs_a_second_hearing_in_its_group(self) -> None:
        self.assertEqual(names(ng.settle([Spelling("Vorna", 1, why="spoken_of")])), [])
        got = ng.settle(
            [Spelling("Vorna", 1, why="spoken_of"), Spelling("Vornah", 1, why="addressed")]
        )
        self.assertEqual(names(got), [("Vorna", ("Vornah",), 2)])

    def test_both_readers_finding_one_spelling_count_its_lines_once(self) -> None:
        got = ng.settle([Spelling("Vorna", 3), Spelling("Vorna", 2, why="spoken_of")])
        self.assertEqual(names(got), [("Vorna", (), 3)])


class TheSpellingShown(unittest.TestCase):
    def test_a_typed_spelling_is_shown_even_when_heard_less(self) -> None:
        got = ng.settle([Spelling("Rothgar", 4), Spelling("Hrothgar", 1, typed=1)])
        self.assertEqual(names(got), [("Hrothgar", ("Rothgar",), 5)])

    def test_a_typed_spelling_counts_once(self) -> None:
        got = ng.settle([Spelling("Hrothgar", 1, typed=1)])
        self.assertEqual(names(got), [("Hrothgar", (), 1)])

    def test_otherwise_the_most_heard_then_the_first_heard(self) -> None:
        got = ng.settle([Spelling("Rothgar", 1), Spelling("Hrothgar", 3)])
        self.assertEqual(names(got)[0][0], "Hrothgar")
        got = ng.settle([Spelling("Rothgar", 2), Spelling("Hrothgar", 2)])
        self.assertEqual(names(got)[0][0], "Rothgar")


class TheGuards(unittest.TestCase):
    def test_a_group_with_a_spelling_close_to_a_secret_is_dropped_whole_and_never_named(
        self,
    ) -> None:
        got = ng.settle(
            [Spelling("Hrothgar", 3), Spelling("Rothgar", 2)],
            near_secret=lambda s: s == "Rothgar",
        )
        self.assertEqual((names(got), got.refused, got.near_secret), ([], [], 2))

    def test_a_secret_is_never_escaped_through_a_spelling_close_to_a_known_name(self) -> None:
        # ("Vornah" is close to a known name and to a secret: "Vorna", alike, goes with it)
        got = ng.settle(
            [Spelling("Vornah", 2), Spelling("Vorna", 2)],
            heard_wrong=lambda s: Match("e1", "Vornar") if s == "Vornah" else None,
            near_secret=lambda s: s == "Vornah",
        )
        self.assertEqual((names(got), got.near_secret), ([], 2))

    def test_spellings_close_to_one_known_name_are_one_question_that_says_so(self) -> None:
        known = Match("e1", "Hrothgard")
        got = ng.settle(
            [Spelling("Hrothgar", 2), Spelling("Hrothgarr", 2), Spelling("hroth gar", 1)],
            heard_wrong=lambda s: known if s != "hroth gar" else None,
        )
        self.assertEqual(len(got.offered), 1)
        self.assertEqual(got.offered[0].heard_wrong, known)

    def test_a_borderline_match_with_two_kinds_stays_apart_and_joins_without_them(self) -> None:
        # ("Bryn Shander" and "Brin Shandar" fold, but less alike than a one-word match needs)
        apart = ng.settle(
            [Spelling("Bryn Shander", 2, why="place", kind="place"),
             Spelling("Brin Shandar", 2, why="addressed", kind="person")]
        )  # fmt: skip
        self.assertEqual(len(apart.offered), 2)
        joined = ng.settle(
            [Spelling("Bryn Shander", 2, why="place"), Spelling("Brin Shandar", 2, why="place")]
        )
        self.assertEqual(len(joined.offered), 1)

    def test_an_ordinary_word_found_by_the_scan_alone_is_refused_even_typed(self) -> None:
        got = ng.settle([Spelling("Hope", 3, ordinary=True)])
        self.assertEqual((names(got), reasons(got)), ([], {"Hope": ng.ORDINARY}))
        got = ng.settle([Spelling("Hope", 2, ordinary=True, typed=1)])
        self.assertEqual(names(got), [])

    def test_an_ordinary_word_the_reader_also_found_is_offered(self) -> None:
        got = ng.settle(
            [
                Spelling("Hope", 3, ordinary=True),
                Spelling("Hope", 2, why="addressed", kind="person"),
            ]
        )
        self.assertEqual(names(got), [("Hope", (), 3)])
        self.assertEqual(got.offered[0].kind, "person")


class TheList(unittest.TestCase):
    def test_introduced_or_typed_first_then_times_heard(self) -> None:
        got = ng.settle(
            [
                Spelling("Vorna", 9),
                Spelling("Ostrin", 1, why=ng.INTRODUCED),
                Spelling("Tamsin", 1, typed=1),
                Spelling("Garreth", 4),
            ]
        )
        self.assertEqual([g.name for g in got.offered], ["Ostrin", "Tamsin", "Vorna", "Garreth"])

    def test_nothing_is_dropped_for_being_over_a_number(self) -> None:
        many = [Spelling(f"Name{chr(65 + i)}x", 2) for i in range(25)]
        self.assertEqual(len(ng.settle(many).offered), 25)


class TheTwoHeadRules(unittest.TestCase):
    def test_a_part_never_leads_its_whole_even_heard_more(self) -> None:
        got = ng.settle([Spelling("Valley", 6), Spelling("Dwarven Valley", 2)])
        self.assertEqual(names(got), [("Dwarven Valley", ("Valley",), 8)])

    def test_a_split_spelling_never_leads_the_joined_one(self) -> None:
        got = ng.settle([Spelling("hroth gar", 3), Spelling("Hrothgar", 1)])
        self.assertEqual(names(got)[0][0], "Hrothgar")


class TheKind(unittest.TestCase):
    def test_a_different_kind_guess_never_splits_near_identical_spellings(self) -> None:
        got = ng.settle(
            [Spelling("Hrothgarr", 2, why="addressed", kind="person"),
             Spelling("Hrothgar", 2, why="place", kind="place")]
        )  # fmt: skip
        self.assertEqual(len(got.offered), 1)


class TheCapitals(unittest.TestCase):
    def test_a_name_heard_only_in_lower_case_is_proposed_with_capitals(self) -> None:
        got = ng.settle([Spelling("heart of the mountain", 2, why="place")])
        self.assertEqual(names(got)[0][0], "Heart of the Mountain")

    def test_a_typed_or_capitalised_spelling_is_kept_as_it_was(self) -> None:
        self.assertEqual(names(ng.settle([Spelling("de Vries", 2)]))[0][0], "de Vries")
        got = ng.settle([Spelling("mcgregor", 1, typed=1)])
        self.assertEqual(names(got)[0][0], "mcgregor")


class TheTypedCount(unittest.TestCase):
    def test_a_group_counts_its_typed_hearings_for_the_card(self) -> None:
        got = ng.settle([Spelling("Rothgar", 3), Spelling("Hrothgar", 1, typed=1)])
        self.assertEqual((got.offered[0].name, got.offered[0].typed_times), ("Hrothgar", 1))

    def test_never_more_typed_than_heard(self) -> None:
        # (one key only the reader found, one only the scan: each counted once in `times`)
        got = ng.settle(
            [Spelling("Hrothgar", 1, why=ng.INTRODUCED, typed=1), Spelling("Rothgar", 1, typed=1)]
        )
        group = got.offered[0]
        self.assertLessEqual(group.typed_times, group.times)
