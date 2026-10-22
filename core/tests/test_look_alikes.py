"""The look-alike finder (#2426): which names may be one name written several ways. Pure code; the
names are invented. Rejected and merged names never reach it (the store passes confirmed ones)."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from dmbot.memory import lookalikes as la
from dmbot.memory.lookalikes import Name


def groups(*spellings: str, apart: tuple[tuple[int, int], ...] = ()) -> list[list[str]]:
    names = [Name(f"{i:032x}", s, mentions=len(spellings) - i) for i, s in enumerate(spellings)]
    gone = {la.pair(names[a].id, names[b].id) for a, b in apart}
    return [[n.name for n in g] for g in la.find(names, apart=gone)]


class WhatLooksAlike(unittest.TestCase):
    def test_spaces_hyphens_and_case_are_not_a_difference(self) -> None:
        self.assertEqual(
            groups("Bel Eros", "Bel-Eros", "BELEROS"), [["Bel Eros", "Bel-Eros", "BELEROS"]]
        )

    def test_a_leading_the_a_or_an_is_dropped(self) -> None:
        self.assertEqual(groups("Belleros", "the Belleros"), [["Belleros", "the Belleros"]])
        self.assertEqual(groups("Silver Hand", "A Silver Hand"), [["Silver Hand", "A Silver Hand"]])

    def test_a_letter_or_two_apart_in_a_long_enough_name(self) -> None:
        self.assertEqual(groups("Belleros", "Beleros"), [["Belleros", "Beleros"]])
        self.assertEqual(
            groups("Waterdeep Harbour", "Waterdeep Harbor"),
            [["Waterdeep Harbour", "Waterdeep Harbor"]],
        )

    def test_short_names_a_letter_apart_are_two_people(self) -> None:
        self.assertEqual(groups("Bryn", "Bran"), [])
        self.assertEqual(groups("Mira Stone", "Kira Stone"), [])

    def test_numbers_and_letters_that_number_things_are_not_spellings(self) -> None:
        self.assertEqual(groups("Room 12", "Room 13"), [])
        self.assertEqual(groups("Goblin Cave A", "Goblin Cave B"), [])

    def test_a_name_that_sounds_the_same(self) -> None:
        self.assertEqual(groups("Ulfgar", "Wolfgar"), [])  # not the same first letter or sound
        self.assertEqual(groups("Kellan", "Kelan"), [["Kellan", "Kelan"]])

    def test_one_name_inside_the_other_as_whole_words(self) -> None:
        self.assertEqual(
            groups("Belleros", "Belleros the Elder"), [["Belleros", "Belleros the Elder"]]
        )
        self.assertEqual(groups("Captain Varn", "Varn"), [["Captain Varn", "Varn"]])

    def test_a_short_word_alone_is_no_tie(self) -> None:
        self.assertEqual(groups("Al", "Al the Smith"), [])
        self.assertEqual(
            groups("The Guild", "Guild of Thieves"), [["The Guild", "Guild of Thieves"]]
        )

    def test_a_group_is_every_name_joined_by_any_of_these(self) -> None:
        found = groups("Belleros", "Beleros", "Bel Eros the Elder", "the Belleros", "Waterdeep")
        self.assertEqual(len(found), 1)
        self.assertEqual(
            sorted(found[0]), sorted(["Belleros", "Beleros", "Bel Eros the Elder", "the Belleros"])
        )

    def test_unrelated_names_make_no_group(self) -> None:
        self.assertEqual(groups("Waterdeep", "Neverwinter", "Mira Voss", "Dawnbreaker"), [])

    def test_the_most_heard_name_is_first_in_its_group(self) -> None:
        names = [Name("a" * 32, "Beleros", 2), Name("b" * 32, "Belleros", 9)]
        self.assertEqual([n.name for n in la.find(names)[0]], ["Belleros", "Beleros"])

    def test_a_pair_the_dm_said_is_not_the_same_is_never_joined(self) -> None:
        self.assertEqual(groups("Belleros", "Beleros", apart=((0, 1),)), [])

    def test_a_third_name_can_still_join_them(self) -> None:
        # Said apart for the pair, but each is the same as the third: the group is the DM's to tell.
        found = groups("Belleros", "Beleros", "the Belleros", apart=((0, 1),))
        self.assertEqual(len(found), 1)

    def test_accents_are_not_a_difference(self) -> None:
        self.assertEqual(groups("Zoë Marsh", "Zoe Marsh"), [["Zoë Marsh", "Zoe Marsh"]])

    def test_the_biggest_group_comes_first(self) -> None:
        found = groups("Oskar Vance", "Oskar Vence", "Belleros", "Beleros", "Bel Eros")
        self.assertEqual([len(g) for g in found], [3, 2])

    def test_five_thousand_names_are_quick_even_when_they_share_a_word_and_a_letter(self) -> None:
        names = [
            Name(f"{i:032x}", f"Captain {chr(98 + i % 3)}{i:04}x{i * 7 % 997}", 0)
            for i in range(5000)
        ]
        calls = 0
        real = la._alike

        def counted(a: object, b: object) -> bool:
            nonlocal calls
            calls += 1
            return real(a, b)  # type: ignore[arg-type]

        start = time.perf_counter()
        with patch.object(la, "_alike", counted):
            la.find(names)
        self.assertLess(calls, 500_000)  # not the 12 million pairs of comparing everyone
        self.assertLess(time.perf_counter() - start, 15.0)

    def test_very_long_names_do_not_make_it_slow(self) -> None:
        names = [Name(f"{i:032x}", ("ab" * 50) + str(i)) for i in range(3000)]
        start = time.perf_counter()
        la.find(names)
        self.assertLess(time.perf_counter() - start, 10.0)

    def test_empty_one_and_unreadable_names(self) -> None:
        self.assertEqual(la.find([]), [])
        self.assertEqual(la.find([Name("a" * 32, "Belleros")]), [])
        odd = [Name(f"{i:032x}", n) for i, n in enumerate(["!!!", "???", "…", "龍", "龍"])]
        self.assertTrue(all(len(g) > 1 for g in la.find(odd)))

    def test_the_same_spelling_twice_groups(self) -> None:
        self.assertEqual(groups("Belleros", "Belleros"), [["Belleros", "Belleros"]])


if __name__ == "__main__":
    unittest.main()
