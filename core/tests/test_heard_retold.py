"""`heard.drop_retold` (#3172 item d): the recap read aloud at a session's start is not the
session's own play, so its names don't count as heard in it. Pure. Invented ids."""

from __future__ import annotations

import unittest

from dmbot.memory.heard import drop_retold
from dmbot.recaps.split import PLAY, RECAP, Part, Split

MIRA, TOLL = "a" * 32, "b" * 32
DM, ANNA = 7, 8


class DropRetold(unittest.TestCase):
    def setUp(self) -> None:
        # (lines 1 to 4 were the recap read aloud, the rest play)
        self.split = Split((Part(RECAP, 1, 4), Part(PLAY, 5, 40)), method="ai")

    def retold(self, n: int) -> bool:
        return self.split.used_as(n) == RECAP

    def test_only_play_lines_count(self) -> None:
        got = drop_retold({(MIRA, DM): {2, 3, 9}, (MIRA, ANNA): {12}}, self.retold)
        self.assertEqual(got, {(MIRA, DM): 1, (MIRA, ANNA): 1})

    def test_a_name_only_the_recap_said_is_not_heard(self) -> None:
        self.assertEqual(drop_retold({(TOLL, DM): {1, 4}}, self.retold), {})

    def test_an_unsure_recap_takes_nothing_away(self) -> None:
        unsure = Split((Part(RECAP, 1, 4), Part(PLAY, 5, 40)), unsure=True, method="ai")
        got = drop_retold({(TOLL, DM): {1, 4}}, lambda n: unsure.used_as(n) == RECAP)
        self.assertEqual(got, {(TOLL, DM): 2})

    def test_no_recap_keeps_every_line_once(self) -> None:
        got = drop_retold({(MIRA, DM): [5, 5, 6]}, lambda n: False)
        self.assertEqual(got, {(MIRA, DM): 2})  # (one line counted twice was said once)

    def test_nothing_in_nothing_out(self) -> None:
        self.assertEqual(drop_retold({}, self.retold), {})

    def test_the_bots_own_recap_set_and_a_line_outside_every_part(self) -> None:
        recap = frozenset({1, 2, 3, 4})  # (as `_recap_lines` gives it)
        counts = {(MIRA, DM): {3, 41}}  # (41 is past every part: play)
        got = drop_retold(counts, lambda n: n in recap)
        self.assertEqual(got, {(MIRA, DM): 1})
        self.assertEqual(counts, {(MIRA, DM): {3, 41}})  # (the input is left as it was)
