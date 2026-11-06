"""Words taken for names outside the beat sheet (#2741): what kind each is, reported as counts and
never as the name; a title written with a capital on every word is not failed for ordinary words;
a plural of a known name passes; and an invented name, or a secret one, still fails."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.recaps import calls as cl
from dmbot.recaps import draft as rd
from dmbot.recaps.text import words
from tests import recap_fixtures as fx
from tests.test_recaps import beats

KNOWN = set(words("Mira Oskar Greywater Keep the silver lock door below stair gate"))


def finish(title: str, bullets: list[str], **kw: Any) -> tuple[Any, list[str]]:
    args: dict[str, Any] = {
        "beats": beats(),
        "summary": fx.SUMMARY,
        "allowed_names": fx.NAMES,
        "secret_names": [],
    }
    return rd.check_finish((title, bullets), **{**args, **kw})


class Titles(unittest.TestCase):
    def test_a_title_of_ordinary_words_is_not_a_name_outside_the_beat_sheet(self) -> None:
        # The words are capitalised because it is a title; none is in the beats or the summary.
        for title in ("Shadows Beneath The Keep", "Whispers In The Dark", "A Hollow Descent"):
            with self.subTest(title):
                done, problems = finish(title, ["[2] Mira reached the gate."])
                self.assertEqual(problems, [])
                self.assertIsNotNone(done)

    def test_an_invented_name_in_a_title_still_fails_and_is_counted_as_unknown(self) -> None:
        counts: dict[str, int] = {}
        done, problems = finish(
            "Zorblax Rising", ["[2] Mira reached the gate."], name_counts=counts
        )
        self.assertIsNone(done)
        self.assertTrue(any("names that are not in the beat sheet" in p for p in problems))
        self.assertEqual(
            counts, {"unknown": 1, "ordinary_word": 1}
        )  # ("Rising" is spared, counted)

    def test_a_bullet_that_opens_with_an_ordinary_word_is_not_failed_for_it(self) -> None:
        for bullet in ("[2] Decide who keeps the silver key.", "[2] Reached the gate at dusk."):
            with self.subTest(bullet):
                done, problems = finish("The Gate", [bullet])
                self.assertEqual(problems, [])
                self.assertIsNotNone(done)

    def test_the_title_words_it_spared_are_counted_so_the_report_shows_the_fix(self) -> None:
        counts: dict[str, int] = {}
        done, _ = finish(
            "Shadows Beneath The Keep", ["[2] Mira reached the gate."], name_counts=counts
        )
        self.assertIsNotNone(done)
        self.assertGreaterEqual(counts.get("ordinary_word", 0), 1)  # words the summary lacks

    def test_a_secret_name_in_a_title_is_still_refused(self) -> None:
        done, problems = finish(
            "The Fall of Baron Vesk", ["[2] Mira reached the gate."], secret_names=["Baron Vesk"]
        )
        self.assertIsNone(done)
        self.assertTrue(any("keeps secret" in p for p in problems))

    def test_a_plural_of_a_known_name_passes_in_a_bullet(self) -> None:
        done, problems = finish("The Gate", ["[2] The two Oskars waited at the gate."])
        self.assertEqual(problems, [])
        self.assertIsNotNone(done)

    def test_an_invented_name_in_a_bullet_still_fails(self) -> None:
        counts: dict[str, int] = {}
        done, _ = finish("The Gate", ["[2] Mira met Kelvarn at the gate."], name_counts=counts)
        self.assertIsNone(done)
        self.assertEqual(counts, {"unknown": 1})


class Kinds(unittest.TestCase):
    def kind(self, name: str, **kw: Any) -> str:
        return rd.name_category(name, known=KNOWN, **kw)

    def test_each_kind_in_fixed_words(self) -> None:
        self.assertEqual(self.kind("Mira", pcs=["Mira Vale"]), "pc_form")
        self.assertEqual(self.kind("Miri", aliases={"Miri": "Mira Vale"}), "heard_as")
        self.assertEqual(self.kind("Oskars"), "plural")
        self.assertEqual(self.kind("Descent"), "ordinary_word")
        self.assertEqual(self.kind("Zorblax"), "unknown")
        self.assertTrue(
            {self.kind(n) for n in ("Oskars", "Descent", "Zorblax")} <= set(rd.NAME_CATEGORIES)
        )

    def test_counts_hold_numbers_and_fixed_words_never_the_names(self) -> None:
        counts = rd.count_categories(["Zorblax", "Kelvarn", "Descent"], known=KNOWN)
        self.assertEqual(counts, {"unknown": 2, "ordinary_word": 1})
        self.assertNotIn("Zorblax", repr(counts))


class Reporting(unittest.TestCase):
    def test_the_calls_line_names_the_kinds_with_counts_only(self) -> None:
        event = cl.CallEvent("prose", 2, "careful", 10, 20, 100, "end_turn", checks=["names"])
        event.names = {"unknown": 2, "ordinary_word": 1}
        line = cl.line(event)
        self.assertIn("names: ordinary_word 1, unknown 2", line)

    def test_a_call_with_no_such_words_says_nothing_of_names(self) -> None:
        event = cl.CallEvent("prose", 1, "fast", 10, 20, 100, "end_turn", checks=[])
        self.assertNotIn("names:", cl.line(event))

    def test_check_prose_fills_the_counts_for_an_invented_name(self) -> None:
        counts: dict[str, int] = {}
        text = fx.SUMMARY.replace("Nothing answered.", "Zorblax answered.")
        problems = rd.check_prose(
            rd.Prose(text, ("1. Low strings.", "2. A drone.")),
            beats=beats(),
            transcript="",
            allowed_names=fx.NAMES,
            secret_names=[],
            name_counts=counts,
        )
        self.assertTrue(any("names that are not allowed" in p for p in problems))
        self.assertEqual(counts.get("unknown"), 1)
