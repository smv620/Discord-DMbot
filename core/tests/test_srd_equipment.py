"""Reading the SRD 5.2.1's weapons, armor and weapon properties (#1919): made-up lines in the
shapes the real PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import equipment
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, BODY, SANS, ITALIC = "GillSans-SemiBold", "Cambria", "GillSans", "GillSans-Italic"


def at(x: float, y: float, text: str, font: str = SANS, page: int = 91) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def weapon_page() -> list[Line]:
    """The table as printed: a heading, rows whose last cells are printed further right, and a
    property list that wraps onto a line set in at the third column."""
    return [
        at(49.9, 751.2, "Weapons", TITLE),
        at(53.9, 736.4, "Name Damage Properties Mastery Weight Cost", TITLE),
        at(53.5, 721.4, "Simple Melee Weapons", ITALIC),
        at(61.5, 706.3, "Club 1d4 Bludgeoning Light Slow 2 lb. 1 SP"),
        at(61.5, 691.1, "Dagger 1d4 Piercing Finesse, Light, Thrown (Range 20/60) Nick 1 lb. 2 GP"),
        at(61.4, 660.9, "Handaxe 1d6 Slashing Light, Thrown (Range 20/60) Vex 2 lb."),
        at(530.0, 660.9, "5 GP"),
        at(61.4, 615.5, "Mace 1d6 Bludgeoning — Sap"),
        at(491.5, 615.5, "4 lb. 5 GP"),
        at(53.4, 555.0, "Simple Ranged Weapons", ITALIC),
        at(61.4, 524.8, "Light Crossbow 1d8 Piercing Ammunition (Range 80/320; Bolt), Loading,"),
        at(215.1, 512.3, "Two-Handed"),
        at(430.3, 524.8, "Slow 5 lb. 25 GP"),
        at(61.4, 482.0, "Sling 1d4 Bludgeoning Ammunition (Range 30/120; Bullet) Slow — 1 SP"),
        at(53.4, 466.9, "Martial Melee Weapons", ITALIC),
        at(61.4, 345.9, "Longsword 1d8 Slashing Versatile (1d10) Sap 3 lb."),
        at(525.7, 345.9, "15 GP"),
    ]


def armor_page() -> list[Line]:
    return [
        at(63.0, 453.3, "Armor", TITLE, 92),
        at(67.0, 438.4, "Armor Armor Class (AC) Strength Stealth Weight Cost", TITLE, 92),
        at(66.6, 423.4, "Light Armor (1 Minute to Don or Doff)", ITALIC, 92),
        at(75.6, 408.3, "Padded Armor 11 + Dex modifier", SANS, 92),
        at(315.7, 408.3, "— Disadvantage 8 lb. 5 GP", SANS, 92),
        at(66.6, 272.2, "Heavy Armor (10 Minutes to Don and 5 Minutes to Doff)", ITALIC, 92),
        at(74.6, 242.0, "Chain Mail 16 Str 13 Disadvantage", SANS, 92),
        at(467.7, 242.0, "55 lb. 75 GP", SANS, 92),
        at(66.6, 196.6, "Shield (Utilize Action to Don or Doff)", ITALIC, 92),
        at(74.6, 181.5, "Shield +2 — — 6 lb. 10 GP", SANS, 92),
        # the text beside the table is not rows
        at(63.0, 713.8, "The Armor table lists the game’s main armor.", BODY, 92),
    ]


class Weapons(unittest.TestCase):
    def found(self) -> dict[str, equipment.Weapon]:
        return {w.name: w for w in equipment.parse_weapons(weapon_page())}

    def test_every_row_is_read_with_its_category(self) -> None:
        found = self.found()
        self.assertEqual(
            [(n, w.category) for n, w in found.items()],
            [
                ("Club", "Simple Melee"),
                ("Dagger", "Simple Melee"),
                ("Handaxe", "Simple Melee"),
                ("Mace", "Simple Melee"),
                ("Light Crossbow", "Simple Ranged"),
                ("Sling", "Simple Ranged"),
                ("Longsword", "Martial Melee"),
            ],
        )

    def test_the_golden_rows(self) -> None:
        found = self.found()
        dagger, longsword = found["Dagger"], found["Longsword"]
        self.assertEqual(dagger.properties, "Finesse, Light, Thrown (Range 20/60)")
        self.assertEqual(
            (dagger.damage, dagger.damage_type, dagger.mastery), ("1d4", "Piercing", "Nick")
        )
        self.assertEqual((longsword.damage, longsword.damage_type), ("1d8", "Slashing"))
        self.assertEqual((longsword.properties, longsword.mastery), ("Versatile (1d10)", "Sap"))

    def test_cells_printed_further_right_join_their_row(self) -> None:
        found = self.found()
        self.assertEqual((found["Handaxe"].weight, found["Handaxe"].cost), ("2 lb.", "5 GP"))
        self.assertEqual((found["Mace"].mastery, found["Mace"].weight), ("Sap", "4 lb."))
        self.assertEqual((found["Longsword"].weight, found["Longsword"].cost), ("3 lb.", "15 GP"))

    def test_a_property_list_that_wraps_is_one_list_and_the_mastery_may_be_in_the_tail(
        self,
    ) -> None:
        crossbow = self.found()["Light Crossbow"]
        self.assertEqual(
            crossbow.properties, "Ammunition (Range 80/320; Bolt), Loading, Two-Handed"
        )
        self.assertEqual(
            (crossbow.mastery, crossbow.weight, crossbow.cost), ("Slow", "5 lb.", "25 GP")
        )

    def test_no_properties_and_no_weight_are_kept_as_printed(self) -> None:
        found = self.found()
        self.assertEqual(found["Mace"].properties, "")
        self.assertEqual((found["Sling"].weight, found["Sling"].cost), ("—", "1 SP"))

    def test_a_mastery_that_is_not_one_of_the_eight_stops_the_tool(self) -> None:
        lines = weapon_page()
        lines[3] = at(61.5, 706.3, "Club 1d4 Bludgeoning Light Smash 2 lb. 1 SP")
        with self.assertRaisesRegex(SrdError, "mastery"):
            equipment.parse_weapons(lines)

    def test_a_row_with_a_cell_missing_stops_the_tool(self) -> None:
        lines = weapon_page()
        lines[3] = at(61.5, 706.3, "Club 1d4 Bludgeoning Light Slow 2 lb.")
        with self.assertRaisesRegex(SrdError, "no cost"):
            equipment.parse_weapons(lines)

    def test_a_row_it_cannot_read_stops_the_tool(self) -> None:
        lines = [*weapon_page(), at(61.4, 300.0, "Bogus row with no damage")]
        with self.assertRaisesRegex(SrdError, "can't read"):
            equipment.parse_weapons(lines)

    def test_a_line_that_is_no_row_stops_the_tool(self) -> None:
        lines = [*weapon_page(), at(120.0, 300.0, "A stray note in the table")]
        with self.assertRaisesRegex(SrdError, "no row"):
            equipment.parse_weapons(lines)

    def test_a_weapon_twice_stops_the_tool(self) -> None:
        lines = [*weapon_page(), at(61.4, 300.0, "Club 1d4 Bludgeoning Light Slow 2 lb. 1 SP")]
        with self.assertRaisesRegex(SrdError, "comes twice"):
            equipment.parse_weapons(lines)


class TheCards(unittest.TestCase):
    """The words a weapon's or armor's row is written out as (build.equipment_entry)."""

    def card(self, row: object, kind: str) -> str:
        from dmbot.devtools.srd import build

        return str(build.equipment_entry(kind, row)["text"])

    def test_a_weapon_leads_with_its_damage_and_leaves_out_what_it_has_not(self) -> None:
        by_name = {w.name: w for w in equipment.parse_weapons(weapon_page())}
        self.assertEqual(
            self.card(by_name["Longsword"], "weapon"),
            "1d8 Slashing. Properties: Versatile (1d10). Mastery: Sap. 3 lb., 15 GP.",
        )
        self.assertEqual(
            self.card(by_name["Mace"], "weapon"), "1d6 Bludgeoning. Mastery: Sap. 4 lb., 5 GP."
        )
        self.assertEqual(
            self.card(by_name["Sling"], "weapon"),
            "1d4 Bludgeoning. Properties: Ammunition (Range 30/120; Bullet). Mastery: Slow. 1 SP.",
        )

    def test_an_armor_leads_with_its_class_and_says_only_what_applies(self) -> None:
        by_name = {a.name: a for a in equipment.parse_armor(armor_page())}
        self.assertEqual(
            self.card(by_name["Chain Mail"], "armor"),
            "AC 16. Needs Strength 13. Disadvantage on Stealth. 55 lb., 75 GP. "
            "Takes 10 minutes to don and 5 minutes to doff.",
        )
        self.assertEqual(
            self.card(by_name["Shield"], "armor"),
            "AC +2. 6 lb., 10 GP. Use the Utilize action to don or doff.",
        )
        self.assertEqual(
            self.card(by_name["Padded Armor"], "armor"),
            "AC 11 + Dex modifier. Disadvantage on Stealth. 8 lb., 5 GP. "
            "Takes 1 minute to don or doff.",
        )


class Armors(unittest.TestCase):
    def found(self) -> dict[str, equipment.Armor]:
        return {a.name: a for a in equipment.parse_armor(armor_page())}

    def test_the_rows_the_categories_and_the_text_beside_the_table(self) -> None:
        found = self.found()
        self.assertEqual(list(found), ["Padded Armor", "Chain Mail", "Shield"])
        self.assertEqual([a.category for a in found.values()], ["Light", "Heavy", "Shield"])
        self.assertEqual(found["Padded Armor"].don_doff, "1 Minute to Don or Doff")

    def test_the_golden_rows(self) -> None:
        found = self.found()
        chain, shield = found["Chain Mail"], found["Shield"]
        self.assertEqual(
            (chain.ac, chain.strength, chain.stealth), ("16", "Str 13", "Disadvantage")
        )
        self.assertEqual((chain.weight, chain.cost), ("55 lb.", "75 GP"))
        self.assertEqual((shield.ac, shield.strength, shield.stealth), ("+2", "—", "—"))
        self.assertEqual(found["Padded Armor"].ac, "11 + Dex modifier")

    def test_a_row_with_no_armor_class_stops_the_tool(self) -> None:
        lines = armor_page()
        lines[3] = at(75.6, 408.3, "Padded Armor", SANS, 92)
        with self.assertRaisesRegex(SrdError, "can't read"):
            equipment.parse_armor(lines)

    def test_a_table_without_its_column_heads_stops_the_tool(self) -> None:
        lines = [ln for ln in armor_page() if not ln.text.startswith("Armor Armor")]
        with self.assertRaisesRegex(SrdError, "column heads"):
            equipment.parse_armor(lines)


def property_lines() -> list[Line]:
    out = [
        at(63.0, 700, "Weapon Proficiency", TITLE, 89),  # before the section: left out
        at(63.0, 690, "Anyone can wield a weapon.", BODY, 89),
        at(313.5, 646.8, "Properties", TITLE, 89),
        at(313.5, 630.3, "Here are definitions of the properties.", BODY, 89),
    ]
    y = 600.0
    for name in (*equipment.PROPERTIES, "Mastery Properties", *equipment.MASTERIES):
        out += [
            at(313.5, y, name, TITLE, 90),
            at(313.5, y - 12, f"{name} means a thing.", BODY, 90),
        ]
        y -= 30
    return out


class Properties(unittest.TestCase):
    def test_every_property_and_mastery_is_read_and_the_introductions_are_left_out(self) -> None:
        found = {p.name: p for p in equipment.parse_properties(property_lines())}
        self.assertEqual(len(found), 18)
        self.assertEqual(found["Finesse"].tag, "Weapon Property")
        self.assertEqual(found["Topple"].tag, "Mastery Property")
        self.assertNotIn("Properties", found)
        self.assertNotIn("definitions", " ".join(p.text for p in found.values()))

    def test_reach_keeps_both_words_because_the_glossary_has_a_reach_of_its_own(self) -> None:
        names = [p.name for p in equipment.parse_properties(property_lines())]
        self.assertIn("Reach (Weapon Property)", names)
        self.assertNotIn("Reach", names)

    def test_a_missing_property_stops_the_tool(self) -> None:
        lines = [ln for ln in property_lines() if not ln.text.startswith("Vex")]
        with self.assertRaisesRegex(SrdError, "Vex"):
            equipment.parse_properties(lines)

    def test_a_heading_with_text_that_is_not_a_property_stops_the_tool(self) -> None:
        lines = [
            *property_lines(),
            at(313.5, 100, "Mystery Heading", TITLE, 90),
            at(313.5, 88, "Words nobody expected.", BODY, 90),
        ]
        with self.assertRaisesRegex(SrdError, "Mystery Heading"):
            equipment.parse_properties(lines)

    def test_a_property_twice_stops_the_tool(self) -> None:
        lines = [
            *property_lines(),
            at(313.5, 100, "Sap", TITLE, 90),
            at(313.5, 88, "Again.", BODY, 90),
        ]
        with self.assertRaisesRegex(SrdError, "comes twice"):
            equipment.parse_properties(lines)


if __name__ == "__main__":
    unittest.main()
