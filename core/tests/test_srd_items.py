"""Reading the SRD 5.2.1's Magic Items A to Z chapter into items (#1912): made-up lines in the
shapes the real PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import items
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, TYPE, BODY, BOLD_LEAD = (
    "GillSans-SemiBold",
    "Cambria-Italic",
    "Cambria",
    "Cambria-BoldItalic",
)
SANS = "GillSans"
LEFT, RIGHT = 63.0, 313.5


def at(x: float, y: float, text: str, font: str = BODY, page: int = 209) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def chapter() -> list[Line]:
    """The chapter in the shapes it has: its heading and introduction, items with a one-line and
    a wrapped type line, a wrapped heading, a table (caption and rows) inside an item, a stat
    block inside an item, a word cut at the end of a line, and a second column."""
    return [
        at(LEFT, 728, "Magic Items A–Z", TITLE),
        at(LEFT, 714, "Magic items are presented in alphabetical order."),
        at(LEFT, 620, "Amulet of Health", TITLE),
        at(LEFT, 608, "Wondrous Item, Rare (Requires Attunement)", TYPE),
        at(LEFT, 592, "Your Constitution is 19 while you wear this amulet."),
        at(LEFT, 550, "Amulet of Proof against Detection", TITLE),
        at(LEFT, 540, "and Location", TITLE),
        at(LEFT, 528, "Wondrous Item, Uncommon (Requires Attunement by a", TYPE),
        at(LEFT, 516, "Wizard)", TYPE),
        at(LEFT, 500, "You can’t be targeted by Divination spells."),
        at(LEFT, 470, "Bag of Holding", TITLE),
        at(LEFT, 458, "Wondrous Item, Uncommon", TYPE),
        at(LEFT, 442, "This bag holds a 30-"),
        at(LEFT, 430, "foot-deep space. It weighs 15 pounds."),
        at(LEFT, 400, "Ammunition, +1, +2, or +3", TITLE),
        at(LEFT, 388, "Weapon (Any Ammunition), Uncommon (+1), Rare (+2),", TYPE),
        at(LEFT, 376, "or Very Rare (+3)", TYPE),
        at(LEFT, 360, "You have a bonus to attack rolls."),
        at(LEFT, 330, "Apparatus of the Crab", TITLE),
        at(LEFT, 318, "Wondrous Item, Legendary", TYPE),
        at(LEFT, 302, "The levers work as shown in the table."),
        at(LEFT, 280, "Apparatus of the Crab Levers", TITLE),
        at(67.0, 268, "Lever Up Down", TITLE),
        at(76.0, 254, "1 Legs extend. Legs retract.", SANS),
        at(LEFT, 220, "Figurine of Wondrous Power", TITLE),
        at(LEFT, 208, "Wondrous Item, Rarity Varies", TYPE),
        at(LEFT, 192, "The ebony fly becomes a Giant Fly."),
        at(LEFT, 170, "Giant Fly", TITLE),
        at(LEFT, 158, "Large Beast, Unaligned", "GillSans-Italic"),
        at(LEFT, 146, "AC 11 Initiative +1 (11)", "GillSans-Bold"),
        at(LEFT, 120, "Golden Lions (Rare). They are made in pairs.", BOLD_LEAD),
        at(RIGHT, 700, "Cloak of Protection", TITLE),
        at(RIGHT, 688, "Wondrous Item, Uncommon (Requires Attunement)", TYPE),
        at(RIGHT, 672, "You gain a +1 bonus to Armor Class."),
    ]


def found() -> dict[str, items.Item]:
    return {i.name: i for i in items.parse_items(chapter())}


class Reading(unittest.TestCase):
    def test_every_item_is_found_in_order_and_the_introduction_is_not_one(self) -> None:
        self.assertEqual(
            list(found()),
            [
                "Amulet of Health",
                "Amulet of Proof against Detection and Location",
                "Bag of Holding",
                "Ammunition, +1, +2, or +3",
                "Apparatus of the Crab",
                "Figurine of Wondrous Power",
                "Cloak of Protection",
            ],
        )

    def test_the_golden_items(self) -> None:
        bag, cloak = found()["Bag of Holding"], found()["Cloak of Protection"]
        self.assertEqual(
            (bag.category, bag.rarity, bag.attunement), ("Wondrous Item", "Uncommon", False)
        )
        self.assertEqual(
            (cloak.rarity, cloak.attunement, cloak.attunement_by), ("Uncommon", True, "")
        )

    def test_an_attunement_condition_is_kept_and_a_wrapped_type_line_is_one(self) -> None:
        amulet = found()["Amulet of Proof against Detection and Location"]
        self.assertEqual((amulet.attunement, amulet.attunement_by), (True, "a Wizard"))
        self.assertEqual(amulet.rarity, "Uncommon")

    def test_a_category_with_a_bracket_and_several_rarities(self) -> None:
        ammo = found()["Ammunition, +1, +2, or +3"]
        self.assertEqual((ammo.category, ammo.subtype), ("Weapon", "Any Ammunition"))
        self.assertEqual(ammo.rarity, "Uncommon (+1), Rare (+2), or Very Rare (+3)")
        self.assertEqual(found()["Figurine of Wondrous Power"].rarity, "Rarity Varies")

    def test_a_word_cut_at_a_line_end_keeps_the_hyphen_after_a_number(self) -> None:
        self.assertIn("30-foot-deep space", found()["Bag of Holding"].text)

    def test_tables_and_stat_blocks_inside_an_item_are_left_out_and_do_not_end_it(self) -> None:
        crab = found()["Apparatus of the Crab"]
        self.assertEqual(crab.text, "The levers work as shown in the table.")
        figurine = found()["Figurine of Wondrous Power"].text
        self.assertIn("The ebony fly becomes a Giant Fly.", figurine)
        self.assertIn("Golden Lions (Rare). They are made in pairs.", figurine)  # after the block
        self.assertNotIn("Unaligned", figurine)
        self.assertNotIn("Initiative", figurine)

    def test_the_page_is_the_headings(self) -> None:
        self.assertEqual({i.page for i in found().values()}, {209})


class TheTypeLine(unittest.TestCase):
    def lines(self) -> list[Line]:
        return [
            at(LEFT, 620, "Sending Stones", TITLE),
            at(LEFT, 608, "Wondrous Item, Uncommon", TYPE),
            at(
                LEFT, 592, "Sending Stones come in pairs, with each stone", TYPE
            ),  # an italic opening
            at(LEFT, 580, "carved to match the other so they are easily known."),
        ]

    def test_an_italic_first_line_of_the_description_is_not_part_of_the_type_line(self) -> None:
        (stones,) = items.parse_items(self.lines())
        self.assertEqual(stones.rarity, "Uncommon")
        self.assertTrue(stones.text.startswith("Sending Stones come in pairs"))
        self.assertIn("easily known.", stones.text)

    def test_a_rarity_wrapped_before_its_notes_is_still_one_line(self) -> None:
        lines = [
            at(LEFT, 620, "Wand of the War Mage, +1, +2, or +3", TITLE),
            at(LEFT, 608, "Wand, Uncommon (+1), Rare", TYPE),
            at(LEFT, 596, "(+2), or Very Rare (+3) (Requires Attunement by a Spellcaster)", TYPE),
            at(LEFT, 580, "You gain a bonus to spell attack rolls."),
        ]
        (wand,) = items.parse_items(lines)
        self.assertEqual(wand.rarity, "Uncommon (+1), Rare (+2), or Very Rare (+3)")
        self.assertEqual((wand.attunement, wand.attunement_by), (True, "a Spellcaster"))

    def test_a_rarity_that_is_not_one_the_srd_prints_stops_the_tool(self) -> None:
        with self.assertRaisesRegex(SrdError, "can't read"):
            items.parse_type("X", "Ring, Rare and then some words", 1)

    def test_the_items_with_lines_left_out_are_named(self) -> None:
        self.assertEqual(
            items.left_out(chapter()),
            {"Apparatus of the Crab": 3, "Figurine of Wondrous Power": 3},
        )


class StopsRatherThanLoses(unittest.TestCase):
    def test_a_heading_with_words_but_no_category_line_stops_the_tool(self) -> None:
        lines = chapter()
        lines.insert(
            4, at(LEFT, 610, "Mystery Item", TITLE)
        )  # right before Amulet of Health's body
        lines.insert(5, at(LEFT, 598, "It has no category line."))
        with self.assertRaisesRegex(SrdError, "Mystery Item"):
            items.parse_items(lines)

    def test_two_headings_with_one_name_stop_the_tool(self) -> None:
        lines = [
            *chapter(),
            at(RIGHT, 600, "Bag of Holding", TITLE),
            at(RIGHT, 588, "Wondrous Item, Uncommon", TYPE),
            at(RIGHT, 572, "A second bag."),
        ]
        with self.assertRaisesRegex(SrdError, "comes twice"):
            items.parse_items(lines)

    def test_an_item_with_no_description_stops_the_tool(self) -> None:
        lines = [
            *chapter(),
            at(RIGHT, 600, "Empty Item", TITLE),
            at(RIGHT, 588, "Ring, Rare", TYPE),
        ]
        with self.assertRaisesRegex(SrdError, "no description"):
            items.parse_items(lines)

    def test_a_type_line_it_cannot_read_stops_the_tool(self) -> None:
        lines = [
            *chapter(),
            at(RIGHT, 600, "Odd Item", TITLE),
            at(RIGHT, 588, "Nothing useful here", TYPE),
            at(RIGHT, 572, "Words."),
        ]
        with self.assertRaisesRegex(SrdError, "can't read"):
            items.parse_items(lines)

    def test_an_attunement_note_in_another_shape_stops_the_tool(self) -> None:
        for text in ("Ring, Rare (requires attunement)", "Ring, Rare (Requires attunement)"):
            with self.subTest(text), self.assertRaisesRegex(SrdError, "attunement|can't read"):
                items.parse_type("X", text, 1)


if __name__ == "__main__":
    unittest.main()
