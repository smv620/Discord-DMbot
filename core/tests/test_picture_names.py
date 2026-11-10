"""Names read from a picture, checked against the whole document (#4003): matched, corrected only
when exactly one of the document's names plainly fits, otherwise a name for the DM to check. Pure,
invented names."""

from __future__ import annotations

import unittest

from dmbot.memory.picture_names import Corrected, Known, Matched, ToCheck, check, fold

DOC = [
    Known("Sephek Kaltro", "character"),
    Known("Torrga Icevein", "character"),
    Known("Syllar Sparkwalker", "character"),
    Known("Termalaine", "place"),
    Known("Okthandom Korran", "character"),
    Known("Bryn Shander", "place"),
]
TEXT = "The caravan reached Bryn Shander at dusk, where the Blue Clam stood shuttered."


class Matches(unittest.TestCase):
    def test_the_documents_own_spelling_matches(self) -> None:
        self.assertEqual(check("sephek kaltro", "character", DOC), Matched("Sephek Kaltro"))

    def test_a_possessive_on_a_map_label_still_matches(self) -> None:
        self.assertEqual(check("Termalaine's", "place", DOC), Matched("Termalaine"))

    def test_the_campaigns_name_matches(self) -> None:
        got = check("Hlin Trollbane", "character", DOC, [Known("Hlin Trollbane", "character")])
        self.assertEqual(got, Matched("Hlin Trollbane"))

    def test_a_name_written_in_the_text_the_reader_did_not_list_matches(self) -> None:
        self.assertEqual(check("Blue Clam", "place", DOC, text=TEXT), Matched("Blue Clam"))

    def test_part_of_a_word_in_the_text_is_not_a_match(self) -> None:
        self.assertIsInstance(check("Clam", None, [], text="The Clamshell inn"), ToCheck)


class Corrections(unittest.TestCase):
    def test_a_spelling_slip_is_corrected(self) -> None:
        self.assertEqual(
            check("Sephik Kaltro", "character", DOC), Corrected("Sephek Kaltro", "slip")
        )

    def test_letters_a_picture_confuses_are_folded(self) -> None:
        self.assertEqual(fold("Okthandorn"), fold("Okthandom"))
        self.assertEqual(
            check("Torrga Lcevein", "character", DOC), Corrected("Torrga Icevein", "slip")
        )
        self.assertEqual(
            check("Okthandorn Korran", "character", DOC),
            Corrected("Okthandom Korran", "slip"),
        )

    def test_a_name_cut_short_is_corrected(self) -> None:
        self.assertEqual(
            check("Sparkwal…", "character", DOC), Corrected("Syllar Sparkwalker", "cut")
        )
        self.assertEqual(check("Terma-", "place", DOC), Corrected("Termalaine", "cut"))

    def test_initials_before_a_whole_last_word_are_corrected(self) -> None:
        self.assertEqual(
            check("S. Kaltro", "character", DOC), Corrected("Sephek Kaltro", "initials")
        )


class NeverAGuess(unittest.TestCase):
    def test_two_names_cut_the_same_way_go_to_the_dm(self) -> None:
        doc = [Known("Sparkwalker Spire", "place"), Known("Syllar Sparkwalker", "character")]
        self.assertEqual(check("Sparkwal…", None, doc), ToCheck())

    def test_two_people_with_the_same_last_word_and_initial_go_to_the_dm(self) -> None:
        doc = [Known("Sephek Kaltro", "character"), Known("Saria Kaltro", "character")]
        self.assertEqual(check("S. Kaltro", "character", doc), ToCheck())

    def test_initials_alone_never_resolve(self) -> None:
        self.assertIsInstance(check("S. K.", "character", DOC), ToCheck)

    def test_three_letters_are_too_short_to_cut(self) -> None:
        self.assertIsInstance(check("Ter…", "place", DOC), ToCheck)

    def test_another_kind_is_never_a_candidate(self) -> None:
        # (a camp's map label is not Torrga the merchant)
        self.assertIsInstance(check("Torrga Lcevein", "place", DOC), ToCheck)

    def test_a_correction_never_points_at_another_of_the_campaigns_names(self) -> None:
        got = check("Sephik Kaltro", "character", [], [Known("Sephek Kaltro", "character")])
        self.assertIsInstance(got, ToCheck)

    def test_two_near_names_go_to_the_dm(self) -> None:
        doc = [Known("Kaltro Vane", "character"), Known("Kaltra Vane", "character")]
        self.assertEqual(check("Kaltri Vane", "character", doc), ToCheck())


class ForTheDM(unittest.TestCase):
    def test_a_name_that_lines_up_with_nothing_is_to_check(self) -> None:
        self.assertEqual(check("Grimsby Hollow", "place", DOC), ToCheck())

    def test_one_close_name_is_offered_as_likely_never_applied(self) -> None:
        got = check("Sefak Kaltrow", "character", DOC)
        self.assertEqual(got, ToCheck(likely="Sephek Kaltro"))

    def test_an_empty_name_is_to_check(self) -> None:
        self.assertEqual(check("  ", None, DOC), ToCheck())


if __name__ == "__main__":
    unittest.main()
