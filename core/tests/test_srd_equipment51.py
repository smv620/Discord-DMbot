"""Reading the SRD 5.1's Equipment chapter (#1931): made-up lines in the shapes the real PDF has
(no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import build, equipment51
from dmbot.devtools.srd.equipment import Armor as ArmorRow
from dmbot.devtools.srd.equipment import Weapon as WeaponRow
from dmbot.devtools.srd.equipment51 import Row
from dmbot.devtools.srd.gear import Gear as GearRow
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.parse51 import Vocabulary
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, BODY, LEAD = "GillSans-SemiBold", "Cambria", "Cambria-BoldItalic"
CAL, BOLD, ITAL = "Calibri", "Calibri-Bold", "Calibri-Italic"
LEFT, RIGHT = 57.6, 328.6


def at(x: float, y: float, text: str, font: str = CAL, page: int = 66) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def vocab(lines: list[Line] | None = None) -> Vocabulary:
    return Vocabulary({"acid", "rope"}, lines or [])


def weapon_lines() -> list[Line]:
    """The table as printed: a header, italic sub-headings, rows whose properties wrap onto the
    right, a weapon with no damage, and a weight that is a dash."""
    return [
        at(LEFT, 113, "Name Cost Damage Weight Properties", BOLD, 65),
        at(LEFT, 102, "Simple Melee Weapons", ITAL, 65),
        at(66.6, 700, "Club 1 sp 1d4 bludgeoning 2 lb. Light"),
        at(66.6, 690, "Dagger 2 gp 1d4 piercing 1 lb. Finesse,"),
        at(332.1, 690, "light, thrown (range 20/60)"),
        at(66.6, 680, "Quarterstaff 2 sp 1d6 bludgeoning 4 lb. Versatile"),
        at(335.0, 680, "(1d8)"),
        at(57.6, 660, "Simple Ranged Weapons", ITAL),
        at(66.6, 650, "Crossbow, light 25 gp 1d8 piercing 5 lb. Ammunition"),
        at(348.5, 650, "(range 80/320), loading, two-handed"),
        at(66.6, 640, "Sling 1 sp 1d4 bludgeoning — Ammunition"),
        at(348.5, 640, "(range 30/120)"),
        at(57.6, 620, "Martial Melee Weapons", ITAL),
        at(66.6, 610, "Longsword 15 gp 1d8 slashing 3 lb. Versatile"),
        at(335.0, 610, "(1d10)"),
        at(66.6, 600, "Morningstar 15 gp 1d8 piercing 4 lb. —"),
        at(57.6, 580, "Martial Ranged Weapons", ITAL),
        at(66.6, 570, "Net 1 gp — 3 lb. Special,"),
        at(330.7, 570, "thrown (range 5/15)"),
    ]


def weapons() -> dict[str, WeaponRow]:
    rows = equipment51.rows_of(weapon_lines())
    return {w.name: w for w in equipment51.parse_weapons(rows)}


class Weapons(unittest.TestCase):
    def test_every_weapon_is_found_with_its_category_and_no_mastery(self) -> None:
        self.assertEqual(
            list(weapons()),
            ["Club", "Dagger", "Quarterstaff", "Crossbow, Light", "Sling", "Longsword",
             "Morningstar", "Net"],
        )  # fmt: skip
        self.assertEqual(weapons()["Club"].category, "Simple Melee")
        self.assertEqual(weapons()["Net"].category, "Martial Ranged")
        self.assertTrue(all(w.mastery == "" for w in weapons().values()))

    def test_the_golden_longsword(self) -> None:
        sword = weapons()["Longsword"]
        self.assertEqual(
            (sword.damage, sword.damage_type, sword.properties, sword.weight, sword.cost),
            ("1d8", "Slashing", "Versatile (1d10)", "3 lb.", "15 GP"),
        )

    def test_properties_are_written_as_the_5_2_1_writes_them(self) -> None:
        self.assertEqual(weapons()["Dagger"].properties, "Finesse, Light, Thrown (Range 20/60)")
        self.assertEqual(
            weapons()["Crossbow, Light"].properties,
            "Ammunition (Range 80/320), Loading, Two-Handed",
        )
        self.assertEqual(weapons()["Morningstar"].properties, "")  # a dash is none

    def test_a_weapon_with_no_damage_and_one_with_no_weight(self) -> None:
        self.assertEqual((weapons()["Net"].damage, weapons()["Net"].damage_type), ("", ""))
        self.assertEqual(weapons()["Sling"].weight, "—")

    def test_a_row_it_cannot_read_stops_the_tool(self) -> None:
        lines = [*weapon_lines(), at(66.6, 560, "Mystery 5 zz 1d4 piercing 1 lb. —")]
        with self.assertRaisesRegex(SrdError, "can't read.*Mystery"):
            equipment51.parse_weapons(equipment51.rows_of(lines))

    def test_a_damage_without_a_type_stops_the_tool(self) -> None:
        lines = [at(57.6, 700, "Simple Melee Weapons", ITAL), at(66.6, 690, "Odd 1 gp 1d4 1 lb. —")]
        with self.assertRaisesRegex(SrdError, "Odd"):
            equipment51.parse_weapons(equipment51.rows_of(lines))

    def test_a_weapon_that_comes_twice_stops_the_tool(self) -> None:
        lines = [*weapon_lines(), at(66.6, 560, "Club 1 sp 1d4 bludgeoning 2 lb. Light")]
        with self.assertRaisesRegex(SrdError, "comes twice"):
            equipment51.parse_weapons(equipment51.rows_of(lines))


def armor_lines() -> list[Line]:
    return [
        at(LEFT, 130, "Armor", BOLD, 63),
        at(LEFT, 115, "Armor Cost Armor Class (AC) Strength Stealth Weight", BOLD, 63),
        at(LEFT, 104, "Light Armor", ITAL, 63),
        at(66.6, 93, "Padded 5", CAL, 63),
        at(157, 93, "gp 11 + Dex modifier — Disadvantage 8", CAL, 63),
        at(408.2, 93, "lb.", CAL, 63),
        at(66.6, 700, "Hide 10 gp 12 + Dex modifier (max 2) — — 12", CAL, 64),
        at(410.5, 700, "lb.", CAL, 64),
        at(57.6, 690, "Heavy Armor", ITAL, 64),
        at(66.6, 680, "Splint 200 gp 17 Str", CAL, 64),
        at(305.0, 680, "15 Disadvantage 60 lb.", CAL, 64),
        at(57.6, 660, "Shield", ITAL, 64),
        at(66.6, 650, "Shield 10 gp +2 — — 6 lb.", CAL, 64),
        at(57.6, 420, "Donning and Doffing Armor", BOLD, 64),
        at(57.6, 407, "Category Don Doff", BOLD, 64),
        at(57.6, 396, "Light Armor 1 minute 1 minute", CAL, 64),
        at(57.6, 385, "Heavy Armor 10 minutes 5 minutes", CAL, 64),
        at(57.6, 363, "Shield 1 action 1 action", CAL, 64),
    ]


class Armor(unittest.TestCase):
    def read(self, lines: list[Line]) -> dict[str, ArmorRow]:
        return {a.name: a for a in equipment51.parse_armor(equipment51.rows_of(lines))}

    def test_rows_are_joined_across_pieces_and_far_right_cells(self) -> None:
        armors = self.read(armor_lines())
        self.assertEqual(list(armors), ["Padded", "Hide", "Splint", "Shield"])
        self.assertEqual(armors["Padded"].ac, "11 + Dex modifier")
        self.assertEqual(armors["Hide"].ac, "12 + Dex modifier (max 2)")
        self.assertEqual(armors["Hide"].weight, "12 lb.")

    def test_the_golden_splint_and_the_shield(self) -> None:
        splint, shield = self.read(armor_lines())["Splint"], self.read(armor_lines())["Shield"]
        self.assertEqual(
            (splint.ac, splint.strength, splint.stealth), ("17", "Str 15", "Disadvantage")
        )
        self.assertEqual((splint.weight, splint.cost), ("60 lb.", "200 GP"))
        self.assertEqual((shield.category, shield.ac, shield.strength), ("Shield", "+2", "—"))

    def test_the_time_to_don_and_doff_comes_from_the_second_table(self) -> None:
        armors = self.read(armor_lines())
        self.assertEqual(armors["Splint"].don_doff, "10 minutes to don and 5 minutes to doff")
        self.assertEqual(armors["Shield"].don_doff, "1 action to don and 1 action to doff")

    def test_an_armor_with_no_time_stops_the_tool(self) -> None:
        lines = [ln for ln in armor_lines() if "Heavy Armor 10" not in ln.text]
        with self.assertRaisesRegex(SrdError, "no time to don"):
            self.read(lines)

    def test_a_row_it_cannot_read_stops_the_tool(self) -> None:
        with self.assertRaisesRegex(SrdError, "can't read"):
            self.read([*armor_lines()[:4], at(66.6, 80, "Odd 5 gp eleven", CAL, 63)])


def property_lines() -> list[Line]:
    lines = [at(RIGHT, 360, "Weapon Properties", TITLE, 64)]
    for n, name in enumerate(equipment51.PROPERTIES):
        lines.append(at(337.9, 340 - 20 * n, f"{name}. What {name} does.", LEAD, 64))
        lines.append(at(RIGHT, 330 - 20 * n, "More words.", BODY, 64))
    return lines


class Properties(unittest.TestCase):
    def test_the_eleven_properties_are_found_and_reach_keeps_both_words(self) -> None:
        found = equipment51.parse_properties(property_lines(), vocab())
        self.assertEqual(len(found), 11)
        self.assertIn("Reach (Weapon Property)", [p.name for p in found])
        self.assertEqual({p.tag for p in found}, {"Weapon Property"})
        self.assertEqual(found[0].text, "What Ammunition does. More words.")

    def test_a_missing_property_stops_the_tool(self) -> None:
        lines = [ln for ln in property_lines() if not ln.text.startswith("Special")]
        with self.assertRaisesRegex(SrdError, "Special"):
            equipment51.parse_properties(lines, vocab())

    def test_a_property_that_is_not_one_stops_the_tool(self) -> None:
        extra = at(337.9, 20, "Sharp. Cuts.", LEAD, 64)
        with self.assertRaisesRegex(SrdError, "Sharp"):
            equipment51.parse_properties([*property_lines(), extra], vocab())


def gear_rows() -> list[Row]:
    lines = [
        at(LEFT, 711, "Item Cost Weight", BOLD, 69),
        at(LEFT, 700, "Abacus 2 gp 2 lb.", CAL, 69),
        at(LEFT, 689, "Acid (vial) 25 gp 1 lb.", CAL, 69),
        at(LEFT, 666, "Ammunition", ITAL, 69),
        at(66.6, 655, "Arrows (20) 1 gp 1 lb.", CAL, 69),
        at(66.6, 644, "Crossbow bolts (20) 1 gp 1½ lb.", CAL, 69),
        at(LEFT, 610, "Healer’s kit 5 gp 3 lb.", CAL, 69),
        at(LEFT, 600, "Holy symbol", ITAL, 69),
        at(66.6, 589, "Amulet 5 gp 1 lb.", CAL, 69),
        at(66.6, 578, "Emblem 5 gp —", CAL, 69),
        at(RIGHT, 700, "Rope, hempen (50 feet) 1 gp 10 lb.", CAL, 69),
        at(RIGHT, 689, "Rope, silk (50 feet) 10 gp 5 lb.", CAL, 69),
        at(RIGHT, 678, "Tent, two-person 2 gp 20 lb.", CAL, 69),
        at(RIGHT, 667, "Waterskin 2 sp 5 lb. (full)", CAL, 69),
    ]
    return equipment51.rows_of(lines, columns=True)


def gear_words() -> list[Line]:
    return [
        at(66.9, 195, "Acid. As an action, you can splash this vial", LEAD, 66),
        at(LEFT, 183, "onto a creature within 5 feet of you.", BODY, 66),
        at(66.9, 160, "Healer’s Kit. This kit has ten uses.", LEAD, 66),
        at(66.9, 140, "Holy Symbol. A holy symbol represents a god.", LEAD, 66),
        at(337.9, 181, "Rope. Rope has 2 hit points.", LEAD, 66),
        at(337.9, 150, "Tent. A tent sleeps two.", LEAD, 66),
    ]


class Gear(unittest.TestCase):
    def read(self, rows: list[Row] | None = None, words: list[Line] | None = None):  # type: ignore[no-untyped-def]
        found = equipment51.parse_gear(rows or gear_rows(), words or gear_words(), vocab())
        return {g.name: g for g in found}

    def test_a_paragraph_gives_its_entry_its_words_and_the_row_its_price_and_weight(self) -> None:
        acid = self.read()["Acid"]
        self.assertEqual((acid.cost, acid.weight), ("25 GP", "1 lb."))
        self.assertEqual(
            acid.text,
            "As an action, you can splash this vial onto a creature within 5 feet of you.",
        )
        self.assertEqual(self.read()["Healer’s Kit"].cost, "5 GP")

    def test_a_row_with_no_paragraph_says_its_row_in_words(self) -> None:
        self.assertEqual(self.read()["Abacus"].text, "Abacus: 2 GP, 2 lb.")
        self.assertEqual(self.read()["Waterskin"].weight, "5 lb. (full)")

    def test_a_heading_over_rows_is_one_entry_with_its_rows_as_lines(self) -> None:
        ammunition = self.read()["Ammunition"]
        self.assertEqual(
            ammunition.text, "Arrows (20): 1 GP, 1 lb.\nCrossbow Bolts (20): 1 GP, 1½ lb."
        )
        self.assertEqual((ammunition.cost, ammunition.weight), ("Varies", "Varies"))
        holy = self.read()["Holy Symbol"]
        self.assertEqual(holy.text.splitlines()[1:], ["Amulet: 5 GP, 1 lb.", "Emblem: 5 GP."])

    def test_one_paragraph_for_two_rows_is_one_entry_listing_both(self) -> None:
        rope = self.read()["Rope"]
        self.assertEqual((rope.cost, rope.weight), ("Varies", "Varies"))
        self.assertIn("Rope, hempen (50 feet): 1 GP, 10 lb.", rope.text)
        self.assertIn("Rope, silk (50 feet): 10 GP, 5 lb.", rope.text)

    def test_one_paragraph_for_one_row_keeps_the_rows_price(self) -> None:
        tent = self.read()["Tent"]
        self.assertEqual(
            (tent.cost, tent.weight, tent.text), ("2 GP", "20 lb.", "A tent sleeps two.")
        )

    def test_a_paragraph_with_no_row_stops_the_tool(self) -> None:
        words = [*gear_words(), at(66.9, 100, "Ghost. A thing.", LEAD, 66)]
        with self.assertRaisesRegex(SrdError, "ghost"):
            self.read(words=words)

    def test_a_row_it_cannot_read_stops_the_tool(self) -> None:
        rows = equipment51.rows_of([at(LEFT, 700, "Abacus two gp", CAL, 69)], columns=True)
        with self.assertRaisesRegex(SrdError, "can't read"):
            self.read(rows=rows)

    def test_a_name_that_comes_twice_stops_the_tool(self) -> None:
        rows = [*gear_rows(), *equipment51.rows_of([at(LEFT, 50, "Abacus 2 gp 2 lb.", CAL, 69)])]
        with self.assertRaisesRegex(SrdError, "comes twice"):
            self.read(rows=rows)


def tool_rows() -> list[Row]:
    lines = [
        at(RIGHT, 655, "Tools", BOLD, 70),
        at(RIGHT, 641, "Item Cost Weight", BOLD, 70),
        at(RIGHT, 630, "Artisan’s tools", ITAL, 70),
        at(337.6, 618, "Alchemist’s supplies 50 gp 8 lb.", CAL, 70),
        at(337.6, 607, "Brewer’s supplies 20 gp 9 lb.", CAL, 70),
        at(337.6, 431, "Disguise kit 25 gp 3 lb.", CAL, 70),
        at(RIGHT, 409, "Gaming set", ITAL, 70),
        at(337.6, 398, "Dice set 1 sp —", CAL, 70),
        at(337.6, 387, "Playing card set 5 sp —", CAL, 70),
        at(RIGHT, 365, "Musical instrument", ITAL, 70),
        at(337.6, 354, "Bagpipes 30 gp 6 lb.", CAL, 70),
        at(337.6, 343, "Drum 6 gp 3 lb.", CAL, 70),
        at(RIGHT, 222, "Thieves’ tools 25 gp 1 lb.", CAL, 70),
    ]
    return equipment51.rows_of(lines, columns=True)


def tool_words() -> list[Line]:
    return [
        at(337.9, 177, "Artisan’s Tools. These tools are for a craft.", LEAD, 70),
        at(66.9, 700, "Disguise Kit. This pouch lets you disguise.", LEAD, 71),
        at(66.9, 600, "Gaming Set. A wide range of game pieces.", LEAD, 71),
        at(66.9, 400, "Musical Instrument. Several common kinds.", LEAD, 71),
        at(66.9, 150, "Thieves’ Tools. A small file and lock picks.", LEAD, 71),
    ]


class Tools(unittest.TestCase):
    def read(self, rows: list[Row] | None = None, words: list[Line] | None = None):  # type: ignore[no-untyped-def]
        found = equipment51.parse_tools(rows or tool_rows(), words or tool_words(), vocab())
        return {t.name: t for t in found}

    def test_every_tool_is_found_in_order_with_its_group(self) -> None:
        tools = self.read()
        self.assertEqual(
            list(tools),
            ["Alchemist’s Supplies", "Brewer’s Supplies", "Disguise Kit", "Gaming Set",
             "Musical Instrument", "Thieves’ Tools"],
        )  # fmt: skip
        self.assertEqual(tools["Brewer’s Supplies"].group, "Artisan’s Tools")
        self.assertEqual(tools["Thieves’ Tools"].group, "Other Tools")

    def test_an_artisans_tool_with_its_own_paragraph_uses_it(self) -> None:
        disguise = self.read()["Disguise Kit"]
        self.assertEqual(disguise.group, "Artisan’s Tools")
        self.assertEqual(disguise.text, "This pouch lets you disguise.")

    def test_the_artisans_tools_share_one_paragraph_and_have_their_own_prices(self) -> None:
        alchemist = self.read()["Alchemist’s Supplies"]
        self.assertEqual((alchemist.cost, alchemist.weight), ("50 GP", "8 lb."))
        self.assertEqual(alchemist.text, "These tools are for a craft.")

    def test_a_gaming_set_and_a_musical_instrument_list_their_kinds(self) -> None:
        self.assertEqual(
            self.read()["Gaming Set"].variants, "Dice Set (1 SP), Playing Card Set (5 SP)"
        )
        self.assertEqual(
            self.read()["Musical Instrument"].variants,
            "Bagpipes (30 GP, 6 lb.), Drum (6 GP, 3 lb.)",
        )
        self.assertEqual(self.read()["Gaming Set"].cost, "Varies")

    def test_a_tool_with_no_paragraph_stops_the_tool(self) -> None:
        words = [ln for ln in tool_words() if not ln.text.startswith("Thieves")]
        with self.assertRaisesRegex(SrdError, "Thieves"):
            self.read(words=words)


def mount_rows() -> list[Row]:
    lines = [
        at(RIGHT, 153, "Mounts and Other Animals", BOLD, 71),
        at(RIGHT, 127, "Item Cost Speed Capacity", BOLD, 71),
        at(RIGHT, 116, "Camel 50 gp 50 ft. 480 lb.", CAL, 71),
        at(RIGHT, 105, "Donkey or mule 8 gp 40 ft. 420 lb.", CAL, 71),
        at(57.6, 636, "Tack, Harness, and Drawn Vehicles", BOLD, 72),
        at(57.6, 621, "Item Cost Weight", BOLD, 72),
        at(57.6, 610, "Barding ×4 ×2", CAL, 72),
        at(57.6, 587, "Carriage 100 gp 600 lb.", CAL, 72),
        at(57.6, 554, "Feed (per day) 5 cp 10 lb.", CAL, 72),
        at(57.6, 543, "Saddle", ITAL, 72),
        at(66.6, 532, "Exotic 60 gp 40 lb.", CAL, 72),
        at(57.6, 465, "Stabling (per day) 5 sp —", CAL, 72),
        at(57.6, 454, "Wagon 35 gp 400 lb.", CAL, 72),
        at(57.6, 423, "Waterborne Vehicles", BOLD, 72),
        at(57.6, 408, "Item Cost Speed", BOLD, 72),
        at(57.6, 397, "Galley 30,000 gp 4 mph", CAL, 72),
        at(57.6, 364, "Rowboat 50 gp 1½ mph", CAL, 72),
    ]
    return equipment51.rows_of(lines, columns=True)


class Mounts(unittest.TestCase):
    def read(self, rows: list[Row] | None = None) -> dict[str, GearRow]:
        return {g.name: g for g in equipment51.parse_mounts(rows or mount_rows())}

    def test_mounts_drawn_vehicles_and_ships_are_found_and_tack_is_left_out(self) -> None:
        self.assertEqual(
            list(self.read()),
            ["Camel", "Donkey or Mule", "Carriage", "Wagon", "Galley", "Rowboat"],
        )

    def test_a_mount_has_a_speed_and_a_capacity(self) -> None:
        camel = self.read()["Camel"]
        self.assertEqual(
            (camel.group, camel.cost, dict(camel.extra)),
            ("Mount", "50 GP", {"speed": "50 ft.", "capacity": "480 lb."}),
        )

    def test_a_vehicle_has_a_weight_or_a_speed(self) -> None:
        self.assertEqual(self.read()["Carriage"].weight, "600 lb.")
        self.assertEqual(dict(self.read()["Rowboat"].extra), {"speed": "1½ mph"})

    def test_an_unknown_tack_row_stops_the_tool(self) -> None:
        rows = [*mount_rows(), *equipment51.rows_of([at(57.6, 440, "Hammock 1 gp 2 lb.", CAL, 72)])]
        with self.assertRaisesRegex(SrdError, "can't read"):
            self.read(rows)

    def test_the_words_of_a_mount_and_a_ship(self) -> None:
        text = build.gear_entry(self.read()["Camel"])["text"]
        self.assertEqual(text, "Speed 50 ft. Carrying capacity 480 lb.")
        self.assertEqual(build.gear_entry(self.read()["Rowboat"])["text"], "Speed 1½ mph.")


class SpecialWeapons(unittest.TestCase):
    def test_the_lance_and_the_net_are_found(self) -> None:
        lines = [
            at(RIGHT, 383, "Special Weapons", TITLE, 65),
            at(337.9, 359, "Lance. You have disadvantage up close.", LEAD, 65),
            at(337.9, 312, "Net. A creature is restrained.", LEAD, 65),
        ]
        found = equipment51.parse_special_weapons(lines, vocab())
        self.assertEqual(found["Net"], "A creature is restrained.")

    def test_a_missing_one_stops_the_tool(self) -> None:
        lines = [at(337.9, 359, "Lance. You have disadvantage.", LEAD, 65)]
        with self.assertRaisesRegex(SrdError, "Lance and the Net"):
            equipment51.parse_special_weapons(lines, vocab())
