"""Tests for splitting a list of names offered as one name (#3383), with invented names."""

from __future__ import annotations

import unittest

from dmbot.memory.background import Found
from dmbot.memory.list_names import split_list


class SplitList(unittest.TestCase):
    def test_a_comma_list_is_split(self) -> None:
        self.assertEqual(
            split_list("Aldmoor, Quillgate, Fenwick"), ["Aldmoor", "Quillgate", "Fenwick"]
        )

    def test_hyphens_belong_to_a_name_and_a_last_and_is_dropped(self) -> None:
        self.assertEqual(
            split_list("Fen-Dalry, Fen-Korg, and Lowmere"), ["Fen-Dalry", "Fen-Korg", "Lowmere"]
        )

    def test_semicolon_and_slash_split(self) -> None:
        self.assertEqual(split_list("Aldmoor; Quillgate"), ["Aldmoor", "Quillgate"])
        self.assertEqual(split_list("Aldmoor / Quillgate"), ["Aldmoor", "Quillgate"])

    def test_a_bare_and_stays_whole(self) -> None:
        self.assertEqual(split_list("Salt and Iron Company"), ["Salt and Iron Company"])
        self.assertEqual(split_list("Aldmoor and Quillgate"), ["Aldmoor and Quillgate"])

    def test_a_bare_and_splits_when_every_part_is_known_or_offered_alone(self) -> None:
        self.assertEqual(
            split_list("Aldmoor and Quillgate", known=["aldmoor", "quillgate"]),
            ["Aldmoor", "Quillgate"],
        )
        self.assertEqual(
            split_list("Aldmoor or Quillgate", offered=["aldmoor", "quillgate"]),
            ["Aldmoor", "Quillgate"],
        )
        self.assertEqual(
            split_list("Aldmoor and Quillgate", known=["aldmoor"]), ["Aldmoor and Quillgate"]
        )

    def test_a_part_that_is_not_a_name_keeps_it_whole(self) -> None:
        self.assertEqual(split_list("Aldmoor, the old road"), ["Aldmoor, the old road"])
        self.assertEqual(split_list("Aldmoor, 3 goats"), ["Aldmoor, 3 goats"])
        self.assertEqual(
            split_list("Aldmoor, Quillgate and the seven hills"),
            ["Aldmoor, Quillgate and the seven hills"],
        )

    def test_joining_words_inside_a_part_are_fine(self) -> None:
        self.assertEqual(
            split_list("Tower of Joy, Hans de Vries", offered=["tower of joy", "hans de vries"]),
            ["Tower of Joy", "Hans de Vries"],
        )

    def test_a_name_held_whole_is_never_split(self) -> None:
        self.assertEqual(
            split_list("Aldmoor, Quillgate", known=["Aldmoor, Quillgate"]), ["Aldmoor, Quillgate"]
        )

    def test_one_name_and_an_overlong_list_stay_whole(self) -> None:
        self.assertEqual(split_list("Aldmoor"), ["Aldmoor"])
        many = ", ".join(f"Town{c}" for c in "ABCDEFGHIJKLM")
        self.assertEqual(split_list(many), [many])  # thirteen: more likely a sentence
        ten = ", ".join(f"Town{c}" for c in "ABCDEFGHIJ")
        self.assertEqual(len(split_list(ten)), 10)  # a region's ten towns in a row

    def test_repeats_are_joined(self) -> None:
        self.assertEqual(split_list("Aldmoor, Aldmoor, Quillgate"), ["Aldmoor", "Quillgate"])


class Readers(unittest.TestCase):
    """The lore and session readers never offer a list as one name."""

    def test_the_lore_reader_splits_a_list_and_keeps_a_company(self) -> None:
        from dmbot.memory.background import Found, merge

        found = [
            Found("Aldmoor, Quillgate, Fenwick", "place", (), (), "the three towns"),
            Found("Salt and Iron Company", "faction", (), (), ""),
        ]
        got = merge([found])
        self.assertEqual(
            [(f.name, f.kind) for f in got],
            [
                ("Aldmoor", "place"),
                ("Quillgate", "place"),
                ("Fenwick", "place"),
                ("Salt and Iron Company", "faction"),
            ],
        )

    def test_the_session_reader_makes_one_claim_for_each_part(self) -> None:
        from dmbot.memory.claims import Extracted, Names, expand_lists

        claim = Extracted("Aldmoor, Quillgate", "is in", "Fenwick", "", ("1",), "said")
        out, lists = expand_lists([claim], Names({}))
        self.assertEqual(lists, 1)
        self.assertEqual(
            [(c.subject, c.object) for c in out], [("Aldmoor", "Fenwick"), ("Quillgate", "Fenwick")]
        )
        plain = Extracted("Aldmoor", "is in", "Fenwick", "", ("1",), "said")
        self.assertEqual(expand_lists([plain], Names({})), ([plain], 0))


class RealNames(unittest.TestCase):
    def test_a_title_after_a_comma_is_not_a_second_name(self) -> None:
        self.assertEqual(split_list("Aldmoor, City of Splendors"), ["Aldmoor, City of Splendors"])
        # ... unless that part is held or offered on its own
        self.assertEqual(
            split_list("Aldmoor, City of Splendors", offered=["city of splendors"]),
            ["Aldmoor", "City of Splendors"],
        )

    def test_a_last_pair_with_and_is_part_of_a_comma_list(self) -> None:
        self.assertEqual(
            split_list("Aldmoor, Quillgate and Fenwick"), ["Aldmoor", "Quillgate", "Fenwick"]
        )

    def test_more_than_twelve_parts_is_kept_whole(self) -> None:
        many = ", ".join(f"Town{c}" for c in "ABCDEFGHIJKLM")
        self.assertEqual(split_list(many), [many])  # thirteen: more likely a sentence
        ten = ", ".join(f"Town{c}" for c in "ABCDEFGHIJ")
        self.assertEqual(len(split_list(ten)), 10)  # a region's ten towns in a row


class ExpandClaims(unittest.TestCase):
    def test_a_big_cross_product_a_self_pair_and_a_repeat_are_handled(self) -> None:
        from dmbot.memory.claims import Extracted, Names, expand_lists

        names = Names({})
        big = Extracted("A1, B2, C3, D4", "is in", "E5, F6, G7, H8", "", ("1",), "said")
        self.assertEqual(expand_lists([big], names), ([big], 0))  # 16 pairs: kept as said
        selfie = Extracted("Aldmoor, Quillgate", "is in", "Aldmoor", "", ("1",), "said")
        out, _ = expand_lists([selfie], names)
        self.assertEqual([(c.subject, c.object) for c in out], [("Quillgate", "Aldmoor")])
        again = Extracted("Aldmoor", "is in", "Fenwick", "", ("1",), "said")
        listed = Extracted("Aldmoor, Quillgate", "is in", "Fenwick", "", ("1",), "said")
        out, _ = expand_lists([again, listed], names)
        self.assertEqual(
            [(c.subject, c.object) for c in out],
            [("Aldmoor", "Fenwick"), ("Quillgate", "Fenwick")],
        )


class DmDecides(unittest.TestCase):
    def test_the_dms_split_takes_and_or_and_ampersand_like_a_comma(self) -> None:
        self.assertEqual(
            split_list("Lords' Alliance and Harpers", dm=True), ["Lords' Alliance", "Harpers"]
        )
        self.assertEqual(split_list("Aldmoor or Quillgate & Fenwick", dm=True),
                         ["Aldmoor", "Quillgate", "Fenwick"])  # fmt: skip
        self.assertEqual(
            split_list("Aldmoor, City of Splendors", dm=True), ["Aldmoor", "City of Splendors"]
        )

    def test_the_reader_still_keeps_a_company_whole_and_the_dm_still_needs_names(self) -> None:
        self.assertEqual(split_list("Salt and Iron Company"), ["Salt and Iron Company"])
        self.assertEqual(
            split_list("Aldmoor and the old road", dm=True), ["Aldmoor and the old road"]
        )
        self.assertEqual(split_list("Aldmoor", dm=True), ["Aldmoor"])


class LoreGroups(unittest.TestCase):
    """A list a group of the same lore read leads is that group's members (#3383, part 2)."""

    def found(self) -> list[Found]:
        return [
            Found("The Ash Wolves", "faction", (), (), "the Ash Wolves ride"),
            Found(
                "The Ash Wolves, Tavin and Mira",
                "faction",
                (),
                (),
                "the Ash Wolves, Tavin and Mira",
            ),
        ]

    def test_the_members_become_people_and_get_ties_to_the_group(self) -> None:
        from dmbot.memory.background import group_connections, merge

        names = merge([self.found()])
        self.assertEqual(
            sorted((f.name, f.kind) for f in names),
            [("Mira", "npc"), ("Tavin", "npc"), ("The Ash Wolves", "faction")],
        )
        ties = group_connections([self.found()])
        self.assertEqual(
            [(c.a, c.how, c.b) for c in ties],
            [("Tavin", "member_of", "The Ash Wolves"), ("Mira", "member_of", "The Ash Wolves")],
        )

    def test_a_list_no_group_leads_gives_no_ties_and_keeps_the_lists_kind(self) -> None:
        from dmbot.memory.background import Found, group_connections, merge

        listed = [Found("Aldmoor, Quillgate", "place", (), (), "")]
        self.assertEqual(group_connections([listed]), [])
        self.assertEqual(sorted(f.kind for f in merge([listed])), ["place", "place"])
