"""Reading the SRD 5.2.1's class features (#1900): the parser on made-up lines in the shapes the
Classes chapter has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import classes
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, BODY, BOLD_LEAD, SANS = "GillSans-SemiBold", "Cambria", "Cambria-BoldItalic", "GillSans"


def at(x: float, y: float, text: str, font: str = BODY, page: int = 29) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


class Features(unittest.TestCase):
    def lines(self) -> list[Line]:
        return [
            at(63, 700, "Barbarian", TITLE, 28),
            at(63, 690, "Barbarian Class Features", TITLE, 28),  # a heading, not a feature
            at(63, 670, "Level 1: Rage", TITLE),
            at(63, 658, "You can imbue yourself with a primal power."),
            at(67, 646, "Level Bonus Rages", SANS),  # a table row: left out
            at(63, 634, "Damage Resistance. You have Resistance.", BOLD_LEAD),
            at(63, 600, "Level 7: Feral Instinct", TITLE),
            at(63, 588, "You have Advantage on Initiative rolls."),
            at(63, 560, "Barbarian Subclass:", TITLE, 30),
            at(63, 546, "Path of the Berserker", TITLE, 30),
            at(63, 530, "Channel Rage into Violent Fury", "Cambria-Italic", 30),
            at(63, 510, "Level 3: Frenzy", TITLE, 30),
            at(63, 498, "You deal extra damage.", BODY, 30),
            at(63, 470, "Monk", TITLE, 49),
            at(63, 450, "Monk Subclass: Warrior of the", TITLE, 52),
            at(63, 436, "Open Hand", TITLE, 52),
            at(63, 416, "Level 3: Open Hand Technique", TITLE, 52),
            at(63, 404, "You can manipulate your foe.", BODY, 52),
        ]

    def test_a_feature_has_its_class_level_text_and_page(self) -> None:
        rage, *_ = classes.parse_features(self.lines())
        self.assertEqual(
            (rage.name, rage.klass, rage.level, rage.subclass, rage.page),
            ("Rage", "Barbarian", 1, "", 29),
        )
        self.assertEqual(
            rage.text,
            "You can imbue yourself with a primal power.\nDamage Resistance. You have Resistance.",
        )

    def test_a_table_row_is_left_out_and_a_heading_is_not_a_feature(self) -> None:
        names = [f.name for f in classes.parse_features(self.lines())]
        self.assertEqual(names, ["Rage", "Feral Instinct", "Frenzy", "Open Hand Technique"])

    def test_a_subclass_feature_names_its_subclass_and_a_wrapped_heading_is_one(self) -> None:
        by_name = {f.name: f for f in classes.parse_features(self.lines())}
        self.assertEqual(by_name["Frenzy"].subclass, "Path of the Berserker")
        self.assertEqual(by_name["Frenzy"].klass, "Barbarian")
        self.assertEqual(by_name["Open Hand Technique"].subclass, "Warrior of the Open Hand")
        self.assertEqual(by_name["Feral Instinct"].subclass, "")  # the class's own again

    def test_a_feature_before_any_class_or_without_words_stops_the_tool(self) -> None:
        with self.assertRaisesRegex(SrdError, "before any class"):
            classes.parse_features([at(63, 700, "Level 1: Rage", TITLE), at(63, 690, "Words.")])
        with self.assertRaisesRegex(SrdError, "no description"):
            classes.parse_features(
                [at(63, 700, "Barbarian", TITLE), at(63, 690, "Level 1: Rage", TITLE)]
            )
        with self.assertRaisesRegex(SrdError, "is under"):
            classes.parse_features(
                [at(63, 700, "Bard", TITLE), at(63, 690, "Monk Subclass: Open Hand", TITLE)]
            )

    def test_a_table_in_the_middle_of_a_feature_does_not_end_it(self) -> None:
        # Sneak Attack (SRD 5.2.1 p. 61 to 62): the class table sits in the column between the
        # feature's lines, with a title for a caption and column heads set a few points in.
        lines = [
            at(314, 700, "Rogue", TITLE),
            at(314, 680, "Level 1: Sneak Attack", TITLE),
            at(314, 668, "You deal an extra 1d6 damage to one creature you hit with an attack"),
            at(63, 700, "Rogue Features", TITLE, 62),
            at(111, 690, "Proficiency Sneak", TITLE, 62),
            at(80, 680, "1 +2 Expertise, Sneak Attack 1d6", SANS, 62),
            at(314, 700, "if you have Advantage on it.", BODY, 62),
            at(314, 680, "Level 2: Cunning Action", TITLE, 62),
            at(314, 668, "You can take a Bonus Action.", BODY, 62),
        ]
        sneak, cunning = classes.parse_features(lines)
        self.assertEqual(
            sneak.text,
            "You deal an extra 1d6 damage to one creature you hit with an attack"
            " if you have Advantage on it.",
        )
        self.assertEqual(cunning.name, "Cunning Action")

    def test_a_list_of_options_still_ends_the_feature_above_it(self) -> None:
        # "Metamagic Options" is followed by titles of its own, not by a table: it is a heading.
        lines = [
            at(314, 700, "Sorcerer", TITLE),
            at(314, 680, "Level 2: Metamagic", TITLE),
            at(314, 668, "You gain two Metamagic options."),
            at(314, 650, "Metamagic Options", TITLE),
            at(314, 636, "Careful Spell", TITLE),
            at(314, 624, "When you cast a spell."),
        ]
        (metamagic,) = classes.parse_features(lines)
        self.assertEqual(metamagic.text, "You gain two Metamagic options.")

    def test_the_placeholder_that_only_says_you_gain_a_subclass_is_left_out(self) -> None:
        lines = [
            at(63, 700, "Bard", TITLE),
            at(63, 680, "Level 3: Bard Subclass", TITLE),
            at(63, 668, "You gain a Bard subclass of your choice."),
            at(63, 650, "Level 2: Expertise", TITLE),
            at(63, 638, "You gain Expertise."),
        ]
        self.assertEqual([f.name for f in classes.parse_features(lines)], ["Expertise"])

    def test_the_right_column_has_its_own_edge(self) -> None:
        lines = [
            at(314, 700, "Rogue", TITLE),
            at(314, 680, "Level 7: Evasion", TITLE),
            at(314, 668, "You dodge."),
        ]
        (evasion,) = classes.parse_features(lines)
        self.assertEqual((evasion.klass, evasion.level, evasion.text), ("Rogue", 7, "You dodge."))


if __name__ == "__main__":
    unittest.main()
