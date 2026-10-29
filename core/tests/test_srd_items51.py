"""Reading the SRD 5.1's Magic Items chapter into items (#1920): made-up lines in the shapes the
real PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import build, items51
from dmbot.devtools.srd.items import Item
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.parse51 import Vocabulary
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, TYPE, BODY, BOLD = "GillSans-SemiBold", "Cambria-Italic", "Cambria", "Cambria-Bold"
LEAD_IN, SANS, SANS_BOLD, BULLET = "Cambria-BoldItalic", "Calibri", "Calibri-Bold", "Symbol"
LEFT, RIGHT = 57.6, 328.6
INDENT = 9.3
KNOWN = {"bag", "holds", "space", "foot", "deep", "weighs", "pounds", "cloak", "gain", "bonus"}


def at(x: float, y: float, text: str, font: str = BODY, page: int = 210) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def chapter() -> list[Line]:
    """The chapter's shapes: an introduction before the first item, a one-line and a wrapped
    type line (lower case), a wrapped heading, an attunement note, a paragraph that starts with
    an indent, a bullet list that wraps, a table and a stat block, a word cut at a line end,
    and a second column."""
    return [
        at(LEFT, 728, "Magic Items", TITLE),
        at(LEFT, 714, "Magic items are presented in alphabetical order."),
        at(LEFT, 700, "Attunement", TITLE),
        at(LEFT, 688, "Some items need attunement."),
        at(LEFT, 620, "Amulet of Health", TITLE),
        at(LEFT, 608, "Wondrous item, rare (requires attunement)", TYPE),
        at(LEFT, 592, "Your Constitution is 19 while you wear this amulet."),
        at(LEFT, 550, "Amulet of Proof against Detection and", TITLE),
        at(LEFT, 536, "Location", TITLE),
        at(LEFT, 524, "Wondrous item, uncommon (requires attunement by a", TYPE),
        at(LEFT, 512, "wizard)", TYPE),
        at(LEFT, 496, "You can’t be targeted by divination magic."),
        at(LEFT, 470, "Bag of Holding", TITLE),
        at(LEFT, 458, "Wondrous item, uncommon", TYPE),
        at(LEFT, 442, "This bag holds a 30-"),
        at(LEFT, 430, "foot-deep space. It weighs 15 pounds."),
        at(LEFT + INDENT, 418, "A second paragraph starts with an indent."),
        at(LEFT, 406, "and goes on."),
        at(LEFT, 380, "Armor, +1, +2, or +3", TITLE),
        at(LEFT, 368, "Armor (light, medium, or heavy), rare (+1), very rare", TYPE),
        at(LEFT, 356, "(+2), or legendary (+3)", TYPE),
        at(LEFT, 340, "You have a bonus to AC."),
        at(LEFT, 310, "Apparatus of the Crab", TITLE),
        at(LEFT, 298, "Wondrous item, legendary", TYPE),
        at(LEFT, 282, "The levers work as shown in the table."),
        at(LEFT, 260, "Armor Class: 20", BOLD),
        at(LEFT + INDENT, 248, "and tail aren’t extended)"),
        at(LEFT, 236, "Apparatus of the Crab Levers", SANS_BOLD),
        at(LEFT, 224, "1 Legs extend. Legs retract.", SANS),
        at(LEFT, 210, "The apparatus floats on water.", BODY),
        at(LEFT, 190, "Belt of Giant Strength", TITLE),
        at(LEFT, 178, "Wondrous item, rarity varies (requires attunement)", TYPE),
        at(LEFT, 166, "While wearing this belt you gain these benefits:"),
        at(LEFT, 154, "• You have darkvision out to a range of", BULLET),
        at(LEFT + INDENT, 142, "60 feet."),
        at(LEFT, 130, "• You can speak Dwarvish."),
        at(LEFT, 118, "In addition, you gain a bonus."),
        at(RIGHT, 700, "Cloak of Protection", TITLE),
        at(RIGHT, 688, "Wondrous item, uncommon (requires attunement)", TYPE),
        at(RIGHT, 672, "You gain a +1 bonus to AC and saving throws."),
        at(RIGHT, 650, "Robe of Useful Items", TITLE),
        at(RIGHT, 638, "Wondrous item, uncommon", TYPE),
        at(RIGHT, 626, "The robe has patches such as:"),
        at(RIGHT, 614, "• Sack", BULLET),
        at(RIGHT + INDENT, 602, "In addition, the robe has four other patches. The"),
        at(RIGHT, 590, "GM chooses the patches."),
        at(RIGHT, 570, "Cap of Notes", TITLE),
        at(RIGHT, 558, "Wondrous item, rare", TYPE),
        at(RIGHT, 546, "Armor Class: 20", BOLD),
        at(RIGHT, 534, "Speed: 30 ft. (or 0 ft. for both if the legs"),
        at(RIGHT, 522, "and tail aren’t out)"),
        at(RIGHT, 500, "Spell Scroll", TITLE),
        at(RIGHT, 628, "Scroll, varies", TYPE),
        at(RIGHT, 612, "A spell scroll bears the words of a single spell."),
    ]


def vocabulary(lines: list[Line]) -> Vocabulary:
    return Vocabulary(KNOWN, lines)


def found() -> dict[str, Item]:
    lines = chapter()
    return {i.name: i for i in items51.parse_items(lines, vocabulary(lines))}


class Reading(unittest.TestCase):
    def test_every_item_is_found_in_order_and_the_introduction_is_not_one(self) -> None:
        self.assertEqual(
            list(found()),
            [
                "Amulet of Health",
                "Amulet of Proof against Detection and Location",
                "Bag of Holding",
                "Armor, +1, +2, or +3",
                "Apparatus of the Crab",
                "Belt of Giant Strength",
                "Cloak of Protection",
                "Robe of Useful Items",
                "Cap of Notes",
                "Spell Scroll",
            ],
        )

    def test_the_type_line_is_read_in_the_5_2_1s_capitals(self) -> None:
        bag, cloak = found()["Bag of Holding"], found()["Cloak of Protection"]
        self.assertEqual(
            (bag.category, bag.rarity, bag.attunement), ("Wondrous Item", "Uncommon", False)
        )
        self.assertEqual(
            (cloak.rarity, cloak.attunement, cloak.attunement_by), ("Uncommon", True, "")
        )

    def test_a_wrapped_heading_and_a_wrapped_type_line_with_who_attunes(self) -> None:
        amulet = found()["Amulet of Proof against Detection and Location"]
        self.assertEqual((amulet.attunement, amulet.attunement_by), (True, "a wizard"))
        self.assertEqual(amulet.rarity, "Uncommon")
        self.assertEqual(amulet.text, "You can’t be targeted by divination magic.")

    def test_a_category_with_a_bracket_and_several_rarities(self) -> None:
        armor = found()["Armor, +1, +2, or +3"]
        self.assertEqual((armor.category, armor.subtype), ("Armor", "Light, medium, or heavy"))
        self.assertEqual(armor.rarity, "Rare (+1), Very Rare (+2), or Legendary (+3)")

    def test_rarities_in_words_are_the_5_2_1s(self) -> None:
        self.assertEqual(found()["Belt of Giant Strength"].rarity, "Rarity Varies")
        self.assertEqual(found()["Spell Scroll"].rarity, "Rarity Varies")  # printed "varies"

    def test_a_hyphen_after_a_number_stays_and_an_indent_starts_a_paragraph(self) -> None:
        text = found()["Bag of Holding"].text
        self.assertIn("30-foot-deep space", text)
        self.assertEqual(
            text.splitlines()[1], "A second paragraph starts with an indent. and goes on."
        )

    def test_tables_are_left_out_and_end_the_paragraph(self) -> None:
        crab = found()["Apparatus of the Crab"].text
        self.assertNotIn("Legs extend", crab)
        self.assertEqual(
            crab.splitlines()[-1], "The apparatus floats on water."
        )  # a new paragraph after the table

    def test_a_stat_line_and_a_bullet_keep_their_wrapped_lines(self) -> None:
        self.assertIn(
            "Armor Class: 20 and tail aren’t extended)", found()["Apparatus of the Crab"].text
        )
        belt = found()["Belt of Giant Strength"].text.splitlines()
        self.assertEqual(belt[1], "• You have darkvision out to a range of 60 feet.")
        self.assertEqual(belt[2], "• You can speak Dwarvish.")
        self.assertEqual(belt[3], "In addition, you gain a bonus.")  # not part of the bullet

    def test_an_indented_paragraph_after_a_short_bullet_is_a_paragraph(self) -> None:
        robe = found()["Robe of Useful Items"].text.splitlines()
        self.assertEqual(robe[1], "• Sack")
        self.assertEqual(
            robe[2], "In addition, the robe has four other patches. The GM chooses the patches."
        )

    def test_an_unindented_wrap_after_a_stat_line_stays_with_it(self) -> None:
        cap = found()["Cap of Notes"].text.splitlines()
        self.assertEqual(cap[0], "Armor Class: 20")
        self.assertEqual(
            cap[1], "Speed: 30 ft. (or 0 ft. for both if the legs and tail aren’t out)"
        )

    def test_the_items_with_lines_left_out_are_named(self) -> None:
        self.assertEqual(items51.left_out(chapter()), {"Apparatus of the Crab": 2})

    def test_the_page_is_the_headings(self) -> None:
        self.assertEqual({i.page for i in found().values()}, {210})


class Stops(unittest.TestCase):
    def read(self, lines: list[Line]) -> list[Item]:
        return items51.parse_items(lines, vocabulary(lines))

    def test_an_unreadable_type_line_stops_the_tool(self) -> None:
        lines = [
            at(LEFT, 700, "Odd Thing", TITLE),
            at(LEFT, 688, "Wondrous item, sometimes mighty", TYPE),
            at(LEFT, 672, "Words."),
        ]
        with self.assertRaisesRegex(SrdError, "Odd Thing.*category and rarity"):
            self.read(lines)

    def test_an_unknown_attunement_note_stops_the_tool(self) -> None:
        lines = [
            at(LEFT, 700, "Odd Thing", TITLE),
            at(LEFT, 688, "Wondrous item, rare (requires attunement if you dare)", TYPE),
            at(LEFT, 672, "Words."),
        ]
        with self.assertRaises(SrdError):
            self.read(lines)

    def test_a_name_that_comes_twice_stops_the_tool(self) -> None:
        item = [
            at(LEFT, 700, "Bag of Holding", TITLE),
            at(LEFT, 688, "Wondrous item, uncommon", TYPE),
            at(LEFT, 672, "Words."),
        ]
        again = [at(RIGHT, line.y, line.text, line.first_font.split("+")[-1]) for line in item]
        with self.assertRaisesRegex(SrdError, "comes twice"):
            self.read(item + again)

    def test_an_item_without_words_stops_the_tool(self) -> None:
        lines = [at(LEFT, 700, "Empty Thing", TITLE), at(LEFT, 688, "Ring, rare", TYPE)]
        with self.assertRaisesRegex(SrdError, "no description"):
            self.read(lines)

    def test_a_title_with_words_but_no_type_line_stops_the_tool(self) -> None:
        lines = [
            at(LEFT, 700, "First Thing", TITLE),
            at(LEFT, 688, "Ring, rare", TYPE),
            at(LEFT, 672, "Words."),
            at(LEFT, 640, "Lost Thing", TITLE),
            at(LEFT, 628, "Words that would be lost."),
        ]
        with self.assertRaisesRegex(SrdError, "Lost Thing.*no type line"):
            self.read(lines)

    def test_the_list_ends_at_the_sentient_items_section(self) -> None:
        lines = [
            at(LEFT, 700, "Wings of Flying", TITLE),
            at(LEFT, 688, "Wondrous item, rare", TYPE),
            at(LEFT, 672, "Words."),
            at(LEFT, 640, "Sentient Magic Items", TITLE),
            at(LEFT, 628, "Rules about minds."),
        ]
        self.assertEqual(build.item_lines_51(lines), lines[:3])
        with self.assertRaisesRegex(SrdError, "Sentient Magic Items"):
            build.item_lines_51(lines[:3])
