"""A distance or a direction said at the table (#4670, MapBot slice 4c-2): the strict shapes, names
the caller knows only, lengths in miles only. Invented names; no database."""

from __future__ import annotations

import unittest

from dmbot.maps import said_phrases as said
from dmbot.memory.models import name_key

NAMES = {name_key(n): i for n, i in (("Targos", "t"), ("Bryn Shander", "b"), ("Bryn", "x"))}


class TheShapes(unittest.TestCase):
    def one(self, line: str) -> said.Said:
        (got,) = said.read(line, NAMES)
        return got

    def test_distances(self) -> None:
        for line in (
            "Targos is 20 miles from Bryn Shander.",
            "Targos lies about 20 miles away from Bryn Shander",
            "It's 20 miles from Targos to Bryn Shander.",
            "twenty miles between Targos and Bryn Shander",
            "From Targos to Bryn Shander is 20 miles.",
        ):
            got = self.one(line)
            self.assertEqual(
                (got.kind, got.a_id, got.b_id, got.value), (said.FAR, "t", "b", 20), line
            )

    def test_leagues_are_miles_and_a_range_keeps_its_low_end(self) -> None:
        self.assertEqual(self.one("Targos is two leagues from Bryn Shander").value, 6)
        self.assertEqual(self.one("Targos is 20 to 30 miles from Bryn Shander").value, 20)

    def test_directions(self) -> None:
        for line, angle in (
            ("Targos is north of Bryn Shander.", 0.0),
            ("Targos lies to the north-east of Bryn Shander", 45.0),
            ("Targos sits due west of Bryn Shander", 270.0),
            ("Targos is northeast of Bryn Shander", 45.0),
        ):
            got = self.one(line)
            self.assertEqual(
                (got.kind, got.a_id, got.b_id, got.value), (said.WAY, "t", "b", angle), line
            )

    def test_the_longest_name_wins(self) -> None:
        self.assertEqual(self.one("Targos is 5 miles from Bryn Shander").b_id, "b")

    def test_what_is_never_taken(self) -> None:
        for line in (
            "Targos is two days from Bryn Shander",  # (a time is never compared)
            "Luskan is 20 miles from Bryn Shander",  # (not a kept place)
            "we walked 20 miles north of Targos",  # (one name only)
            "Targos is 20 miles from here, not Bryn Shander",  # (no set shape)
            "Is Targos north of Bryn Shander?",  # (a question is not a statement)
            "Targos is 20 miles from Targos",  # (one place twice)
        ):
            self.assertEqual(said.read(line, NAMES), [], line)

    def test_two_statements_in_one_line(self) -> None:
        got = said.read(
            "Targos is 20 miles from Bryn Shander. Bryn Shander is east of Targos.", NAMES
        )
        self.assertEqual(
            [(g.kind, g.a_id, g.b_id) for g in got], [(said.FAR, "t", "b"), (said.WAY, "b", "t")]
        )

    def test_numbers_with_a_point_or_a_comma_are_read_whole(self) -> None:
        # (the reviewer's: never 5 for 2.5, never 200 for 1,200)
        self.assertEqual(self.one("It's 2.5 miles from Targos to Bryn Shander.").value, 2.5)
        self.assertEqual(self.one("It is 1,200 miles from Targos to Bryn Shander").value, 1200)

    def test_a_tag_question_is_not_a_statement(self) -> None:
        self.assertEqual(said.read("Targos is 20 miles from Bryn Shander, right?", NAMES), [])
        self.assertEqual(said.read("Targos is north of Bryn Shander, isn't it?", NAMES), [])
