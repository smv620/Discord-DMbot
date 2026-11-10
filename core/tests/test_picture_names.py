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


class TheReviewersWrongCorrections(unittest.TestCase):
    """#4003, the reviewer: each of these was once corrected to another, real name."""

    def test_a_one_word_name_the_fold_alone_makes_equal_is_not_corrected(self) -> None:
        for read, other in (("Clara", "Dara"), ("Carl", "Cari"), ("Vern", "Vem"), ("Lina", "Iina")):
            got = check(read, "character", [Known(other, "character")])
            self.assertNotIsInstance(got, Corrected, read)

    def test_a_pair_is_never_folded_across_a_space(self) -> None:
        self.assertNotEqual(fold("Eric Lane"), fold("Eridane"))
        got = check("Eric Lane", "character", [Known("Eridane", "character")])
        self.assertNotIsInstance(got, Corrected)

    def test_a_whole_word_is_not_a_cut_name(self) -> None:
        doc = [Known("Marabel Stone", "character"), Known("Towerfall Keep", "place"),
               Known("Sephek Kaltro", "character")]  # fmt: skip
        for read in ("Mara", "Tower", "Spark", "Kaltro"):
            self.assertNotIsInstance(check(read, None, doc), Corrected, read)

    def test_a_label_clipped_at_the_page_edge_still_resolves(self) -> None:
        self.assertEqual(
            check("Sparkwalk", "character", DOC), Corrected("Syllar Sparkwalker", "cut")
        )


class Rivals(unittest.TestCase):
    def test_a_close_campaign_name_blocks_a_correction(self) -> None:
        got = check(
            "Sephik Kaltro", "character", [Known("Sephek Kaltro", "character")],
            [Known("Sephik Kaltra", "character")],
        )  # fmt: skip
        self.assertNotIsInstance(got, Corrected)

    def test_two_spellings_of_one_name_are_one_candidate(self) -> None:
        doc = [Known("Sephek Kaltro", "character"), Known("Sephek-Kaltro", "character")]
        self.assertEqual(
            check("Sephik Kaltro", "character", doc), Corrected("Sephek Kaltro", "slip")
        )

    def test_a_name_of_no_known_kind_is_a_rival(self) -> None:
        doc = [Known("Sparkwalker Spire"), Known("Syllar Sparkwalker", "character")]
        self.assertEqual(check("Sparkwal…", "character", doc), ToCheck())

    def test_initials_with_a_possessive_resolve(self) -> None:
        self.assertEqual(
            check("S. Kaltro's", "character", DOC), Corrected("Sephek Kaltro", "initials")
        )


class Edges(unittest.TestCase):
    def test_an_all_capitals_label_matches_the_texts_own_spelling(self) -> None:
        self.assertEqual(
            check("BLUE CLAM", "place", [], text="the Blue Clam inn"), Matched("Blue Clam")
        )

    def test_only_punctuation_is_to_check(self) -> None:
        self.assertEqual(check("…", None, DOC), ToCheck())

    def test_a_name_with_no_sound_code_can_still_be_cut(self) -> None:
        doc = [Known("Ηρακλειδης", "character")]
        self.assertEqual(check("Ηρακλει…", "character", doc), Corrected("Ηρακλειδης", "cut"))

    def test_two_close_names_offer_no_likely_one(self) -> None:
        doc = [Known("Sephek Kaltro", "character"), Known("Sephak Kaltro", "character")]
        self.assertEqual(check("Sefuk Kaltrow", "character", doc), ToCheck())


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
