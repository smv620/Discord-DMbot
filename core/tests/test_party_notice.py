"""The one line the DM screen gets after an import linked the party (#3036): counts only."""

from __future__ import annotations

import unittest

from dmbot.memory.party import party_notice


class TheLine(unittest.TestCase):
    def test_several_characters_know_each_other(self) -> None:
        self.assertEqual(
            party_notice(4), "🎭 **The party** is set up: 4 player characters grouped together."
        )

    def test_one_character_is_just_in_the_party(self) -> None:
        self.assertEqual(party_notice(1), "🎭 **The party** has 1 player character.")
