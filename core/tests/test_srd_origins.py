"""Reading the SRD 5.2.1's feats, species and backgrounds (#1913): made-up lines in the shapes
the real PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from dmbot.devtools.srd import origins
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, ITALIC, BODY, LEAD, SANS = (
    "GillSans-SemiBold",
    "Cambria-Italic",
    "Cambria",
    "Cambria-BoldItalic",
    "GillSans",
)
LEFT, RIGHT = 63.0, 313.5


def at(x: float, y: float, text: str, font: str = BODY, page: int = 83) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def background_lines() -> list[Line]:
    return [
        at(LEFT, 330, "Character Backgrounds", TITLE),
        at(LEFT, 316, "Your background is a collection of characteristics."),
        at(LEFT, 300, "Background Descriptions", TITLE),
        at(LEFT, 282, "Acolyte", TITLE),
        at(LEFT, 268, "Ability Scores: Intelligence, Wisdom, Charisma", TITLE),
        at(LEFT, 255, "Feat: Magic Initiate (Cleric) (see “Feats”)", TITLE),
        at(LEFT, 242, "Skill Proficiencies: Insight and Religion", TITLE),
        at(LEFT, 229, "Tool Proficiency: Calligrapher’s Supplies", TITLE),
        at(LEFT, 216, "Equipment: Choose A or B: (A) Calligrapher’s Supplies,", TITLE),
        at(71.0, 203, "Book (prayers), Holy Symbol, Robe, 8 GP; or (B) 50 GP", SANS),
        at(RIGHT, 282, "Sage", TITLE),
        at(RIGHT, 268, "Ability Scores: Constitution, Intelligence, Wisdom", TITLE),
        at(RIGHT, 255, "Feat: Magic Initiate (Wizard) (see “Feats”)", TITLE),
        at(RIGHT, 242, "Skill Proficiencies: Arcana and History", TITLE),
        at(RIGHT, 229, "Tool Proficiency: Calligrapher’s Supplies", TITLE),
        at(RIGHT, 216, "Equipment: Choose A or B: (A) Quarterstaff, Callig-", TITLE),
        at(321.5, 203, "rapher’s Supplies, Robe; or (B) 50 GP", SANS),
        at(RIGHT, 180, "Character Species", TITLE),
        at(RIGHT, 166, "When you choose your species, you pick traits."),
    ]


def species_lines() -> list[Line]:
    return [
        at(LEFT, 600, "Dragonborn", TITLE, 84),
        at(LEFT, 586, "Creature Type: Humanoid", TITLE, 84),
        at(LEFT, 573, "Size: Medium (about 5–7 feet tall)", TITLE, 84),
        at(LEFT, 560, "Speed: 30 feet", TITLE, 84),
        at(LEFT, 543, "As a Dragonborn, you have these special traits.", BODY, 84),
        at(71.0, 530, "Draconic Ancestry. Choose the kind of dragon.", LEAD, 84),
        at(LEFT, 500, "Draconic Ancestors", TITLE, 84),  # a table: left out
        at(67.0, 486, "Dragon Damage Type", TITLE, 84),
        at(66.6, 472, "Black Acid Gold Fire", SANS, 84),
        at(71.0, 450, "Breath Weapon. You exhale energy.", LEAD, 84),
        at(RIGHT, 600, "Human", TITLE, 84),
        at(RIGHT, 586, "Creature Type: Humanoid", TITLE, 84),
        at(RIGHT, 573, "Size: Medium (about 4–7 feet tall) or Small (about 2–4", TITLE, 84),
        at(321.5, 560, "feet tall), chosen when you select this species", SANS, 84),
        at(RIGHT, 547, "Speed: 30 feet", TITLE, 84),
        at(RIGHT, 530, "As a Human, you have these special traits.", BODY, 84),
        at(321.5, 517, "Resourceful. You gain Heroic Inspiration.", LEAD, 84),
    ]


def feat_lines() -> list[Line]:
    return [
        at(LEFT, 700, "Feat Descriptions", TITLE, 87),
        at(LEFT, 686, "The feats are organized by category."),
        at(LEFT, 660, "Origin Feats", TITLE, 87),
        at(LEFT, 642, "Alert", TITLE, 87),
        at(LEFT, 629, "Origin Feat", ITALIC, 87),
        at(LEFT, 613, "You gain the following benefits.", BODY, 87),
        at(71.0, 601, "Initiative Proficiency. You add your Proficiency Bonus.", LEAD, 87),
        at(LEFT, 570, "General Feats", TITLE, 87),
        at(LEFT, 552, "Grappler", TITLE, 87),
        at(LEFT, 539, "General Feat (Prerequisite: Level 4+, Strength or", ITALIC, 87),
        at(LEFT, 527, "Dexterity 13+)", ITALIC, 87),
        at(LEFT, 511, "You gain the following benefits.", BODY, 87),
        at(LEFT, 470, "Defense", TITLE, 87),
        at(LEFT, 457, "Fighting Style Feat (Prerequisite: Fighting Style", ITALIC, 87),
        at(LEFT, 445, "Feature)", ITALIC, 87),
        at(LEFT, 429, "You gain a +1 bonus to Armor Class.", BODY, 87),
    ]


class Backgrounds(unittest.TestCase):
    def found(self) -> dict[str, origins.Background]:
        return {b.name: b for b in origins.parse_backgrounds(background_lines())}

    def test_each_background_has_its_five_parts_and_the_introduction_is_left_out(self) -> None:
        found = self.found()
        self.assertEqual(list(found), ["Acolyte", "Sage"])  # not "Background Descriptions"
        acolyte = found["Acolyte"]
        self.assertEqual(acolyte.ability_scores, "Intelligence, Wisdom, Charisma")
        self.assertEqual(acolyte.skills, "Insight and Religion")
        self.assertEqual(acolyte.tool, "Calligrapher’s Supplies")
        self.assertTrue(acolyte.equipment.endswith("Robe, 8 GP; or (B) 50 GP"))
        self.assertNotIn("characteristics", acolyte.text)

    def test_the_feat_it_gives_is_named_without_the_pointer(self) -> None:
        self.assertEqual(self.found()["Sage"].feat, "Magic Initiate (Wizard)")

    def test_a_word_cut_at_a_line_end_is_put_back(self) -> None:
        self.assertIn("Quarterstaff, Calligrapher’s Supplies, Robe", self.found()["Sage"].equipment)

    def test_the_text_is_the_lines_as_printed(self) -> None:
        self.assertEqual(
            self.found()["Acolyte"].text.splitlines()[:2],
            [
                "Ability Scores: Intelligence, Wisdom, Charisma",
                "Feat: Magic Initiate (Cleric) (see “Feats”)",
            ],
        )

    def test_a_background_with_no_feat_line_stops_the_tool(self) -> None:
        lines = [
            ln for ln in background_lines() if not ln.text.startswith("Feat: Magic Initiate (C")
        ]
        with self.assertRaisesRegex(SrdError, "no Feat"):
            origins.parse_backgrounds(lines)

    def test_a_name_that_comes_twice_stops_the_tool(self) -> None:
        lines = background_lines()
        lines = [ln if ln.text != "Sage" else at(LEFT, 282, "Acolyte", TITLE) for ln in lines]
        with self.assertRaises(SrdError):
            origins.parse_backgrounds(lines)


ONE_TABLE = {"Dragonborn": "Draconic Ancestors"}  # the made-up chapter has just this one


class SpeciesReading(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(origins.SPECIES_TABLES, ONE_TABLE, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def found(self) -> dict[str, origins.Species]:
        return {s.name: s for s in origins.parse_species([*background_lines(), *species_lines()])}

    def test_each_species_has_its_size_and_speed_and_the_backgrounds_are_not_species(self) -> None:
        found = self.found()
        self.assertEqual(list(found), ["Dragonborn", "Human"])
        self.assertEqual(found["Dragonborn"].speed, "30 feet")
        self.assertEqual(found["Dragonborn"].creature_type, "Humanoid")

    def test_a_size_that_wraps_is_one_value(self) -> None:
        self.assertEqual(
            self.found()["Human"].size,
            "Medium (about 4–7 feet tall) or Small (about 2–4 feet tall), "
            "chosen when you select this species",
        )

    def test_the_traits_are_kept_and_a_table_inside_is_left_out(self) -> None:
        text = self.found()["Dragonborn"].text
        self.assertIn("Draconic Ancestry. Choose the kind of dragon.", text)
        self.assertIn("Breath Weapon. You exhale energy.", text)  # after the table
        self.assertNotIn("Acid", text)
        self.assertNotIn("Dragon Damage", text)

    def test_a_species_that_has_a_table_left_out_says_so_on_its_last_line(self) -> None:
        text = self.found()["Dragonborn"].text
        self.assertTrue(text.endswith("(The Draconic Ancestors table is not included here.)"))
        self.assertNotIn("not included", self.found()["Human"].text)

    def test_a_table_that_is_not_named_stops_the_tool(self) -> None:
        lines = species_lines()
        lines.insert(10, at(LEFT, 440, "Unexpected Table", TITLE, 84))
        lines.insert(11, at(66.6, 428, "Some cells", SANS, 84))
        with self.assertRaisesRegex(SrdError, "Unexpected Table"):
            origins.parse_species(lines)

    def test_a_named_table_that_is_missing_stops_the_tool(self) -> None:
        lines = [ln for ln in species_lines() if ln.y < 400 or ln.y > 520 or ln.x != LEFT]
        lines = [ln for ln in lines if ln.text not in ("Draconic Ancestors", "Dragon Damage Type")]
        lines = [ln for ln in lines if ln.text != "Black Acid Gold Fire"]
        with self.assertRaises(SrdError):
            origins.parse_species(lines)

    def test_a_paragraph_that_carries_on_in_the_next_column_is_one_paragraph(self) -> None:
        lines = species_lines()
        lines.insert(10, at(LEFT, 440, "Radiant Pulse. You gather energy that", LEAD, 84))
        lines.insert(11, at(321.5, 700, "spreads out around you.", BODY, 84))  # next column's top
        dragonborn = origins.parse_species(lines)[0]
        self.assertIn("gather energy that spreads out around you.", dragonborn.text)

    def test_a_species_with_no_speed_stops_the_tool(self) -> None:
        lines = [ln for ln in species_lines() if ln.text != "Speed: 30 feet" or ln.x != LEFT]
        with self.assertRaisesRegex(SrdError, "no Speed"):
            origins.parse_species(lines)

    def test_a_heading_with_words_under_it_inside_a_species_stops_the_tool(self) -> None:
        lines = species_lines()
        lines.insert(6, at(LEFT, 520, "Mystery Heading", TITLE, 84))
        lines.insert(7, at(LEFT, 508, "Words under a heading nobody expected.", BODY, 84))
        with self.assertRaisesRegex(SrdError, "Mystery Heading"):
            origins.parse_species(lines)


class Feats(unittest.TestCase):
    def found(self) -> dict[str, origins.Feat]:
        return {f.name: f for f in origins.parse_feats(feat_lines())}

    def test_every_feat_has_its_category_and_the_category_headings_are_not_feats(self) -> None:
        found = self.found()
        self.assertEqual(list(found), ["Alert", "Grappler", "Defense"])
        self.assertEqual(
            [f.category for f in found.values()], ["Origin", "General", "Fighting Style"]
        )

    def test_a_prerequisite_is_kept_even_when_its_line_wraps(self) -> None:
        found = self.found()
        self.assertEqual(found["Alert"].prerequisite, "")
        self.assertEqual(found["Grappler"].prerequisite, "Level 4+, Strength or Dexterity 13+")
        self.assertEqual(found["Defense"].prerequisite, "Fighting Style Feature")

    def test_the_text_is_the_description(self) -> None:
        self.assertIn("Initiative Proficiency. You add", self.found()["Alert"].text)
        self.assertNotIn("Origin Feat", self.found()["Alert"].text)

    def test_an_italic_first_line_of_the_description_is_not_part_of_the_category_line(
        self,
    ) -> None:
        lines = feat_lines()[:6]
        lines.insert(6, at(LEFT, 600, "An italic opening line of the description.", ITALIC, 87))
        (alert,) = origins.parse_feats(lines)
        self.assertEqual(alert.category, "Origin")
        self.assertIn("An italic opening line", alert.text)

    def test_a_heading_with_words_but_no_category_line_stops_the_tool(self) -> None:
        lines = [
            *feat_lines(),
            at(LEFT, 400, "Mystery Feat", TITLE, 87),
            at(LEFT, 388, "No category line here.", BODY, 87),
        ]
        with self.assertRaisesRegex(SrdError, "Mystery Feat"):
            origins.parse_feats(lines)

    def test_a_category_line_it_cannot_read_stops_the_tool(self) -> None:
        lines = [
            *feat_lines(),
            at(LEFT, 400, "Odd Feat", TITLE, 87),
            at(LEFT, 388, "Strange Feat", ITALIC, 87),
            at(LEFT, 376, "Words.", BODY, 87),
        ]
        with self.assertRaisesRegex(SrdError, "can't read"):
            origins.parse_feats(lines)

    def test_a_line_that_is_not_text_inside_a_feat_stops_the_tool(self) -> None:
        lines = feat_lines()
        lines.insert(7, at(66.6, 596, "A table row", SANS, 87))
        with self.assertRaisesRegex(SrdError, "not text"):
            origins.parse_feats(lines)

    def test_a_feat_that_comes_twice_stops_the_tool(self) -> None:
        lines = [
            *feat_lines(),
            at(LEFT, 400, "Alert", TITLE, 87),
            at(LEFT, 388, "Origin Feat", ITALIC, 87),
            at(LEFT, 376, "Again.", BODY, 87),
        ]
        with self.assertRaisesRegex(SrdError, "comes twice"):
            origins.parse_feats(lines)

    def test_a_feat_with_no_description_stops_the_tool(self) -> None:
        lines = [
            *feat_lines(),
            at(LEFT, 400, "Empty Feat", TITLE, 87),
            at(LEFT, 388, "Origin Feat", ITALIC, 87),
        ]
        with self.assertRaisesRegex(SrdError, "no description"):
            origins.parse_feats(lines)


if __name__ == "__main__":
    unittest.main()
