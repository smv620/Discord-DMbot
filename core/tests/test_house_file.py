"""The house-rules file (#969): the plain-text format both ways, and what differs from
DMbot's copy. Pure: no Discord, no database."""

from __future__ import annotations

import time
import unittest

from dmbot.rules import house_file as hf
from dmbot.rules.house import HOUSE_RULES_MAX, NUMBER_MAX, RULE_MAX, HouseRule


def rule(number: int, text: str, instead: str | None = None) -> HouseRule:
    return HouseRule(number, "c1", text, instead, None, None, 7, 1, 1)


def file_rule(number: int, text: str, instead: str | None = None) -> hf.FileRule:
    return hf.FileRule(number, text, instead)


class Writing(unittest.TestCase):
    def test_one_numbered_line_for_each_rule_lowest_first(self) -> None:
        text = hf.write(
            "Frostmaiden",
            [
                rule(12, "Potions are a bonus action", "Drinking takes an action"),
                rule(3, "No flanking"),
            ],
        )
        lines = text.splitlines()
        self.assertEqual(lines[0], "House rules: Frostmaiden")
        self.assertTrue(lines[1].startswith("# "))
        self.assertEqual(lines[4:], [
            "3. No flanking",
            "12. Potions are a bonus action (instead of: Drinking takes an action)",
        ])  # fmt: skip
        self.assertTrue(text.endswith("\n"))

    def test_an_empty_campaign_still_has_a_title_and_help(self) -> None:
        self.assertEqual(len(hf.write("Empty", []).splitlines()), 4)

    def test_the_words_stay_on_one_line(self) -> None:
        text = hf.write("A\nB", [rule(1, "two\nlines  here", "x\ny")])
        self.assertEqual(text.splitlines()[0], "House rules: A B")
        self.assertIn("1. two lines here (instead of: x y)", text)


class Reading(unittest.TestCase):
    def test_both_ways_without_loss(self) -> None:
        mine = [rule(1, "A"), rule(5, "B (not that one)", "C"), rule(40, "Ünïcode ✓ ok")]
        parsed = hf.parse(hf.write("X", mine))
        self.assertEqual(parsed.problems, ())
        self.assertEqual(
            parsed.rules,
            (
                file_rule(1, "A"),
                file_rule(5, "B (not that one)", "C"),
                file_rule(40, "Ünïcode ✓ ok"),
            ),
        )
        self.assertTrue(hf.compare(mine, parsed.rules).empty)

    def test_a_rule_that_contains_the_marker_is_written_with_brackets(self) -> None:
        text = hf.write("X", [rule(1, "Use this (instead of: that) always")])
        self.assertEqual(
            hf.parse(text).rules, (file_rule(1, "Use this [instead of: that) always"),)
        )
        self.assertEqual(hf.parse(text).problems, ())

    def test_marker_words_in_the_instead_of_are_bracketed_too(self) -> None:
        text = hf.write("X", [rule(1, "Rule", "Book (instead of: other) rule")])
        parsed = hf.parse(text)
        self.assertEqual(parsed.problems, ())
        self.assertEqual(parsed.rules, (file_rule(1, "Rule", "Book [instead of: other) rule"),))

    def test_a_damaged_first_rule_is_reported_not_taken_for_the_title(self) -> None:
        for first in ("1) Potions are a bonus action", "1 Potions", "1.Potions"):
            parsed = hf.parse(f"{first}\n2. Two\n")
            self.assertEqual([r.number for r in parsed.rules], [2], first)
            self.assertEqual([(p.line, p.why) for p in parsed.problems], [(1, hf.NOT_A_RULE)])

    def test_the_title_comments_and_blank_lines_are_ignored(self) -> None:
        parsed = hf.parse("﻿Our table\n\n# a note\n  # another\n2. Two\n\n1. One\n")
        self.assertEqual(parsed.rules, (file_rule(1, "One"), file_rule(2, "Two")))
        self.assertEqual(parsed.problems, ())

    def test_a_file_with_no_title_works_too(self) -> None:
        self.assertEqual(hf.parse("1. One").rules, (file_rule(1, "One"),))

    def test_other_lines_are_reported_never_guessed(self) -> None:
        parsed = hf.parse("Title\n1. Fine\nthis is not a rule\n3) wrong dot\n4.\n5. \n")
        self.assertEqual(parsed.rules, (file_rule(1, "Fine"),))
        self.assertEqual([(p.line, p.why) for p in parsed.problems], [
            (3, hf.NOT_A_RULE), (4, hf.NOT_A_RULE), (5, hf.NOT_A_RULE), (6, hf.NOT_A_RULE),
        ])  # fmt: skip

    def test_a_second_title_like_line_after_a_rule_is_a_problem(self) -> None:
        parsed = hf.parse("1. One\nHouse rules again\n")
        self.assertEqual([p.why for p in parsed.problems], [hf.NOT_A_RULE])

    def test_bad_numbers_long_rules_and_repeats_are_reported(self) -> None:
        long = "x" * (RULE_MAX + 1)
        text = f"0. Zero\n{NUMBER_MAX + 1}. Big\n1. {long}\n2. ok ()\n3. Three\n3. Again\n"
        parsed = hf.parse(text)
        self.assertEqual([r.number for r in parsed.rules], [2, 3])
        self.assertEqual(
            [(p.line, p.why) for p in parsed.problems],
            [(1, hf.BAD_NUMBER), (2, hf.BAD_NUMBER), (3, hf.TOO_LONG), (6, hf.TWICE)],
        )
        self.assertEqual(next(r for r in parsed.rules if r.number == 3).rule, "Three")

    def test_a_long_instead_of_is_reported(self) -> None:
        parsed = hf.parse(f"1. Rule{hf.MARK}{'y' * (RULE_MAX + 1)})\n")
        self.assertEqual([p.why for p in parsed.problems], [hf.TOO_LONG])

    def test_the_most_it_keeps_is_the_stores_limit(self) -> None:
        text = "\n".join(f"{n}. Rule {n}" for n in range(1, HOUSE_RULES_MAX + 6))
        parsed = hf.parse(text)
        self.assertEqual(len(parsed.rules), HOUSE_RULES_MAX)
        self.assertEqual([p.why for p in parsed.problems], [hf.TOO_MANY])

    def test_a_shown_bad_line_is_cut_short(self) -> None:
        parsed = hf.parse("1. ok\n" + "z" * 500)
        self.assertLessEqual(len(parsed.problems[0].text), 80)

    def test_nonsense_never_raises(self) -> None:
        for text in ("", "\x00\x01", "1.", "." * 1000, "9" * 400 + ". x", "\n\n\n"):
            hf.parse(text)


class Hostile(unittest.TestCase):
    """A file may come from anywhere (later: a link, an upload): reading it costs time
    in proportion to its size, and what is reported stays short."""

    def fast(self, text: str) -> hf.Parsed:
        started = time.perf_counter()
        parsed = hf.parse(text)
        self.assertLess(time.perf_counter() - started, 1.0)
        return parsed

    def test_a_long_run_of_blanks_is_not_slow(self) -> None:
        for gap in (" ", " \t"):
            parsed = self.fast("1. a" + gap * 50_000 + "b")
            self.assertEqual([p.why for p in parsed.problems], [hf.TOO_LONG])  # over a line
        self.assertEqual(self.fast("1. a" + " " * 1_500 + "b").problems, ())

    def test_a_file_over_the_limit_is_refused_unread(self) -> None:
        parsed = self.fast("1. a\n" * 100_000)
        self.assertEqual((parsed.rules, [p.why for p in parsed.problems]), ((), [hf.TOO_BIG]))

    def test_many_bad_lines_are_listed_briefly(self) -> None:
        parsed = self.fast("junk\n" * 30_000)
        self.assertEqual(len(parsed.problems), hf.MAX_PROBLEMS + 1)
        self.assertIn("more lines could not be used", parsed.problems[-1].why)

    def test_reading_stops_at_the_most_rules(self) -> None:
        parsed = self.fast("\n".join(f"{n}. r" for n in range(1, 5_000)))
        self.assertEqual(len(parsed.rules), HOUSE_RULES_MAX)
        self.assertEqual([p.why for p in parsed.problems], [hf.TOO_MANY])

    def test_odd_characters_never_raise(self) -> None:
        for text in ("²³. x", "١٢. x", "1\u2028. x\u2029y", "1. x\r\n2. y\r\n", "\ufeff1. x"):
            hf.parse(text)
        self.assertEqual([r.number for r in hf.parse("1. x\r\n2. y\r\n").rules], [1, 2])

    def test_comparing_two_big_lists_is_fast(self) -> None:
        mine = [rule(n, f"mine {n}") for n in range(1, 2001)]
        theirs = [file_rule(n + 5000, f"theirs {n}") for n in range(1, 2001)]
        started = time.perf_counter()
        diff = hf.compare(mine, theirs)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual((len(diff.added), len(diff.removed)), (2000, 2000))


class Comparing(unittest.TestCase):
    def test_nothing_differs(self) -> None:
        diff = hf.compare(
            [rule(1, "A"), rule(2, "B", "C")], [file_rule(1, "A"), file_rule(2, "B", "C")]
        )
        self.assertTrue(diff.empty)
        self.assertEqual((diff.count, diff.same), (0, 2))

    def test_added_changed_and_removed(self) -> None:
        mine = [rule(1, "Keep"), rule(2, "Old words"), rule(3, "Gone")]
        theirs = [file_rule(1, "Keep"), file_rule(2, "New words"), file_rule(9, "Brand new")]
        diff = hf.compare(mine, theirs)
        self.assertEqual(diff.added, (file_rule(9, "Brand new"),))
        self.assertEqual([(a.number, b.rule) for a, b in diff.changed], [(2, "New words")])
        self.assertEqual([r.number for r in diff.removed], [3])
        self.assertEqual((diff.same, diff.count), (1, 3))
        self.assertFalse(diff.empty)

    def test_a_change_to_only_the_instead_of_is_a_change(self) -> None:
        diff = hf.compare([rule(1, "A", "B")], [file_rule(1, "A", "C")])
        self.assertEqual(len(diff.changed), 1)
        diff = hf.compare([rule(1, "A", "B")], [file_rule(1, "A")])
        self.assertEqual(len(diff.changed), 1)

    def test_the_same_words_under_another_number_are_moved_not_added_and_removed(self) -> None:
        diff = hf.compare(
            [rule(4, "Same words"), rule(5, "Other")],
            [file_rule(11, "Same words"), file_rule(5, "Other")],
        )
        self.assertEqual(diff.moved, (hf.Moved(4, 11),))
        self.assertEqual((diff.added, diff.removed, diff.changed), ((), (), ()))
        self.assertEqual(diff.count, 1)

    def test_two_copies_of_one_rule_move_one_at_a_time(self) -> None:
        diff = hf.compare(
            [rule(1, "Twin"), rule(2, "Twin")], [file_rule(8, "Twin"), file_rule(9, "Twin")]
        )
        self.assertEqual(sorted((m.old, m.new) for m in diff.moved), [(1, 8), (2, 9)])

    def test_blank_spaces_do_not_make_a_difference(self) -> None:
        self.assertTrue(hf.compare([rule(1, "A  b\n c")], [file_rule(1, "A b c")]).empty)

    def test_an_empty_file_removes_everything_and_an_empty_campaign_adds_everything(self) -> None:
        self.assertEqual(len(hf.compare([rule(1, "A")], []).removed), 1)
        self.assertEqual(len(hf.compare([], [file_rule(1, "A")]).added), 1)


class Names(unittest.TestCase):
    def test_plain_letters_and_digits_only(self) -> None:
        self.assertEqual(hf.filename("Frostmaiden"), "house-rules-frostmaiden.txt")
        self.assertEqual(hf.filename("Curse of  Strahd: 2!"), "house-rules-curse-of-strahd-2.txt")
        self.assertEqual(hf.filename("../../etc/passwd"), "house-rules-etc-passwd.txt")
        self.assertEqual(hf.filename("龍の巣"), "house-rules-campaign.txt")
        self.assertEqual(hf.filename(""), "house-rules-campaign.txt")
        self.assertLessEqual(len(hf.filename("a" * 500)), len("house-rules-.txt") + hf.NAME_MAX)


if __name__ == "__main__":
    unittest.main()
