"""The name the table uses for each character (#2627): counted from what was said, told to the
writer beside the full name, and never a check. Invented names throughout."""

from __future__ import annotations

import unittest
from types import SimpleNamespace as NS

from dmbot.recaps import draft as rd
from dmbot.recaps import familiar as fam
from dmbot.recaps import inputs
from dmbot.recaps.beats import Beat

FULL = "Tavin Ashgrove of the Greyfen"


def said(*times: tuple[str, int]) -> list[str]:
    return [f"{form} nodded." for form, n in times for _ in range(n)]


class Counting(unittest.TestCase):
    def test_a_name_said_as_tavin_forty_times_and_in_full_twice_is_tavin(self) -> None:
        got = fam.familiar_names([FULL], said(("Tavin", 40), (FULL, 2)))
        self.assertEqual(got, {FULL: "Tavin"})

    def test_the_form_said_most_wins_even_when_it_is_the_other_part(self) -> None:
        got = fam.familiar_names([FULL], said(("Tavin", 3), ("Ashgrove", 9)))
        self.assertEqual(got[FULL], "Ashgrove")

    def test_a_confirmed_nickname_counts_as_a_form(self) -> None:
        got = fam.familiar_names(
            [FULL], said(("Tavin", 2), ("Grey", 7)), aliases={"Grey": FULL, "Other": "Someone"}
        )
        self.assertEqual(got[FULL], "Grey")

    def test_a_name_never_said_goes_by_its_first_part(self) -> None:
        self.assertEqual(fam.familiar_names([FULL], ["Nothing about them."]), {FULL: "Tavin"})

    def test_the_full_name_said_most_leaves_no_familiar_name(self) -> None:
        self.assertEqual(fam.familiar_names([FULL], said((FULL, 5), ("Tavin", 1))), {})

    def test_a_one_word_name_has_no_other_form(self) -> None:
        self.assertEqual(fam.familiar_names(["Rook"], said(("Rook", 9))), {})

    def test_small_linking_words_are_never_a_name(self) -> None:
        self.assertEqual(fam.parts_of(FULL), ["Tavin", "Ashgrove", "Greyfen"])

    def test_a_form_is_counted_as_a_whole_word_and_a_tie_goes_to_the_shorter(self) -> None:
        got = fam.familiar_names(
            ["Mira Voss"], ["Miranda Vossberg came.", "Mira nodded.", "Voss nodded."]
        )
        self.assertEqual(got, {"Mira Voss": "Mira"})  # (Miranda is not Mira; a tie: the shorter)

    def test_a_part_that_names_a_secret_leaves_the_full_name(self) -> None:
        got = fam.familiar_names(
            ["Mira Voss"], said(("Voss", 9)), secret_names=["Voss"], allowed_names=["Mira Voss"]
        )
        self.assertEqual(got, {})

    def test_the_cases_and_accents_of_the_transcript_do_not_matter(self) -> None:
        self.assertEqual(fam.familiar_names([FULL], ["TAVIN, tavin! Tavín."])[FULL], "Tavin")


class Telling(unittest.TestCase):
    def beat(self) -> Beat:
        return Beat(1, "Tavin lit the fire.", ("Tavin",), (1,), "event", present=("Tavin",))

    def test_the_names_list_gives_both_forms_for_each_character(self) -> None:
        message = rd.prose_message(
            [self.beat()], "", [FULL, "Greywater Keep"], None, familiar={FULL: "Tavin"}
        )
        listed = message.split("<names>")[1].split("</names>")[0]
        self.assertIn(f"familiar: Tavin; full: {FULL}", listed)
        self.assertIn("Greywater Keep", listed)
        self.assertNotIn("familiar: Greywater", listed)

    def test_without_familiar_names_the_list_is_as_it_was(self) -> None:
        listed = rd.prose_message([self.beat()], "", ["Mira Voss"], None).split("<names>")[1]
        self.assertEqual(listed.split("</names>")[0].strip(), "Mira Voss; Tavin")

    def test_the_writer_is_told_to_use_the_familiar_name_and_the_full_one_once(self) -> None:
        system = rd.prose_system(None, rd.length_for(3.0))
        self.assertIn('"familiar:" name', system)
        self.assertIn("Use the full name only once", system)

    def test_nothing_checks_it_a_recap_with_the_full_name_has_no_problem(self) -> None:
        text = f"{FULL} drew his blade."
        got = rd.check_prose(
            rd.Prose(text, ("soft",)),
            beats=[self.beat()],
            transcript="",
            allowed_names=[FULL],
            secret_names=[],
            pcs=[],
        )
        self.assertFalse([p for p in got if "familiar" in p.lower()])


class FromTheSession(unittest.TestCase):
    def test_characters_only_never_a_place_and_never_the_dm_sidebar(self) -> None:
        names = NS(
            names=[],
            entities={
                "a": NS(name=FULL, type="character"),
                "b": NS(name="Far Hall Keep", type="place"),
            },
        )
        lines = [
            NS(heard="", text="Tavin looked up.", sidebar=""),
            NS(
                heard="", text=f"{FULL} is the heir.", sidebar="dm"
            ),  # (said in the sidebar: not counted)
        ] + [NS(heard="", text="Hall Keep", sidebar="")] * 3
        got = inputs.familiar_for(names, [FULL, "Far Hall Keep"], lines, "", {})
        self.assertEqual(got, {FULL: "Tavin"})
