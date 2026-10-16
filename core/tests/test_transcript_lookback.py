"""The look-back pass (#127): a name split across two pieces of one person's speech becomes a
question for the DM, never a change; and the seams that must not ask. Without Discord or a
database. Made-up names."""

import unittest
from typing import Any

from dmbot.transcript import lookback as lb
from dmbot.transcript.cleaner import clean
from tests.test_transcript_cleaner import (
    BELLEROS,
    EVERYONE,
    KAZETH,
    alias,
    lookup,
)


def piece(names: Any, heard: str, start: int, end: int) -> lb.Piece:
    return lb.Piece(start, end, heard, clean(names, heard, scene=EVERYONE))


def seam(
    first: str, second: str, *, gap: int = 1000, names: Any = None, scene: Any = EVERYONE
) -> lb.Seam | None:
    names = names or lookup()
    before = piece(names, first, 0, 1500)
    after = piece(names, second, 1500 + gap, 3000 + gap)
    return lb.find(names, before, after, scene=scene)


class Asks(unittest.TestCase):
    def test_a_name_split_by_a_pause_becomes_one_question_showing_both_pieces(self) -> None:
        found = seam("we meet Ka", "Zeth at dawn")
        assert found is not None
        self.assertEqual((found.tail, found.head), ("Ka", "Zeth"))
        self.assertEqual(found.question.heard, "Ka Zeth")
        self.assertEqual([name for _, name in found.question.options], ["Ka'zeth"])
        self.assertEqual(found.question.options[0][0], KAZETH)
        self.assertEqual(found.prev.heard, "we meet Ka")
        # where the words are: the tail in the earlier piece, the head at the start of this one
        self.assertEqual(("we meet Ka"[found.prev_start : found.prev_end]), "Ka")
        self.assertEqual(("Zeth at dawn"[found.question.start : found.question.end]), "Zeth")

    def test_a_confirmed_name_a_little_unlike_it_is_asked_about_too(self) -> None:
        found = seam("I think Belle", "Ross has the key")
        assert found is not None
        self.assertEqual([name for _, name in found.question.options], ["Belleros"])
        self.assertEqual(found.question.options[0][0], BELLEROS)

    def test_the_head_may_be_followed_by_more_words(self) -> None:
        found = seam("so Ka", "Zeth the one with the key")
        assert found is not None
        self.assertEqual(found.head, "Zeth")

    def test_pieces_that_overlap_a_little_still_count(self) -> None:
        self.assertIsNotNone(seam("we meet Ka", "Zeth at dawn", gap=-500))


class DoesNotAsk(unittest.TestCase):
    def test_too_late_a_pause_or_too_much_overlap(self) -> None:
        self.assertIsNone(seam("we meet Ka", "Zeth at dawn", gap=lb.GAP_MS + 1))
        self.assertIsNone(seam("we meet Ka", "Zeth at dawn", gap=-lb.OVERLAP_MS - 1))

    def test_a_piece_that_began_before_the_other_is_not_a_seam(self) -> None:
        names = lookup()
        later = piece(names, "we meet Ka", 5000, 6000)
        earlier = piece(names, "Zeth at dawn", 100, 900)
        self.assertIsNone(lb.find(names, later, earlier, scene=EVERYONE))

    def test_a_run_of_real_words_is_never_read_as_a_name(self) -> None:
        # "Bell or us" is how Belleros might be heard in three pieces of ordinary words; the
        # Cleaner never reads those as a name, so the seam is not asked about either.
        self.assertIsNone(seam("we meet Bell", "or us at dawn"))
        self.assertIsNone(seam("we meet bell or", "us at dawn"))

    def test_words_that_are_already_right_or_unrelated_ask_nothing(self) -> None:
        self.assertIsNone(seam("then we ride to Bryn", "Shander tonight"))
        self.assertIsNone(seam("we went to the market", "and bought bread"))
        self.assertIsNone(seam("", "Zeth at dawn"))
        self.assertIsNone(seam("we meet Ka", "   "))

    def test_nothing_when_a_piece_alone_already_has_a_fix_on_those_words(self) -> None:
        # "Kazeth" is fixed inside the earlier piece itself: the seam leaves it to that.
        self.assertIsNone(seam("we meet Kazeth", "Zeth at dawn"))

    def test_a_seam_next_to_a_secret_name_asks_nothing(self) -> None:
        secret = lookup(more_aliases=(alias(BELLEROS, "Silas Vane", secret=True),))
        self.assertIsNone(seam("I met Silas", "Vain tonight", names=secret))
        # and the same seam is fine when there is no such secret
        self.assertIsNone(seam("I met Silas", "Vain tonight"))

    def test_a_head_that_is_the_whole_second_piece_is_left(self) -> None:
        # Taking it out would leave an empty line.
        self.assertIsNone(seam("I think Belle", "Ross"))
        self.assertIsNone(seam("I think Belle", "Ross "))

    def test_a_tail_that_ends_in_punctuation_is_left(self) -> None:
        # Writing the name over "Belle." would lose the full stop.
        self.assertIsNone(seam("I think Belle.", "Ross has the key"))
        self.assertIsNone(seam("I think Belle...", "Ross has the key"))
        self.assertIsNotNone(seam("I think Belle", "Ross has the key"))

    def test_a_long_earlier_piece_still_finds_the_seam_at_the_right_place(self) -> None:
        long_one = "so yesterday evening at the old inn near the harbour we all met Ka"
        found = seam(long_one, "Zeth at dawn")
        assert found is not None
        self.assertEqual(long_one[found.prev_start : found.prev_end], "Ka")
        self.assertEqual(found.question.heard, "Ka Zeth")
        long_two = "Zeth at dawn and then the whole party went down to the river bank"
        found = seam("we meet Ka", long_two)
        assert found is not None
        self.assertEqual(long_two[found.question.start : found.question.end], "Zeth")

    def test_a_head_followed_straight_away_by_punctuation_is_left(self) -> None:
        # Taking "Zeth" out of "Zeth, the one…" would leave a stray comma at the start.
        self.assertIsNone(seam("so Ka", "Zeth, the one with the key"))

    def test_a_split_name_in_the_scene_is_found(self) -> None:
        # The Cleaner reads the two pieces as one line, so its one-word rule still holds: a
        # split name needs the name to have been in the scene or the words to be alike enough.
        self.assertIsNotNone(seam("we meet Ka", "Zeth at dawn", scene=EVERYONE))


class HeadEdit(unittest.TestCase):
    def test_the_head_goes_with_the_spaces_after_it(self) -> None:
        self.assertEqual(lb.head_edit("Zeth at dawn", 4), (0, 5))
        self.assertEqual(lb.head_edit("Zeth", 4), (0, 4))
        self.assertEqual(lb.head_edit("Zeth   at", 4), (0, 7))


if __name__ == "__main__":
    unittest.main()
