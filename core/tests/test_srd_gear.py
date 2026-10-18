"""Reading the SRD 5.2.1's tools, adventuring gear, mounts and vehicles (#1927): made-up lines in
the shapes the real PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import build, gear
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, BODY, SANS = "GillSans-SemiBold", "Cambria", "GillSans"
LEFT, RIGHT = 63.0, 313.5


def at(x: float, y: float, text: str, font: str = BODY, page: int = 93) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def pieces(y: float, page: int, *cells: tuple[float, str]) -> Line:
    """One printed line made of several pieces, as the PDF reader groups them."""
    return Line(cells[0][0], y, [Piece(x, y, f"ABCDEF+{SANS}", t) for x, t in cells], page)


def tool_lines() -> list[Line]:
    """Tools as printed: a group heading with words, a title with a price, label lines in the
    heading font, a wrapped line set in under its label, a word cut at a line end, and a
    Variants line that carries on in the next column."""
    return [
        at(LEFT, 363, "Artisan’s Tools", TITLE),
        at(LEFT, 347, "Artisan’s Tools are each focused on crafting items."),
        at(LEFT, 303, "Brewer’s Supplies (20 GP)", TITLE),
        at(LEFT, 291, "Ability: Intelligence Weight: 9 lb.", TITLE),
        at(LEFT, 280, "Utilize: Detect poisoned drink (DC 15), or identify alco-", TITLE),
        at(71.0, 268, "hol (DC 10)", SANS),
        at(LEFT, 257, "Craft: Antitoxin", TITLE),
        at(LEFT, 220, "Other Tools", TITLE),
        at(LEFT, 200, "Gaming Set (Varies)", TITLE),
        at(LEFT, 188, "Ability: Wisdom Weight: —", TITLE),
        at(LEFT, 176, "Utilize: Discern whether someone is cheating (DC 10)", TITLE),
        at(LEFT, 164, "Variants: Dice (1 SP), dragonchess (1 GP), playing cards", TITLE),
        at(71.0, 152, "(5 SP), three-dragon ante (1 GP)", SANS),
        at(RIGHT, 700, "Thieves’ Tools (25 GP)", TITLE),
        at(RIGHT, 688, "Ability: Dexterity Weight: 1 lb.", TITLE),
        at(RIGHT, 676, "Utilize: Pick a lock (DC 15), or disarm a trap (DC 15)", TITLE),
    ]


def gear_lines() -> list[Line]:
    """The gear section: its heading and words, entries with a price in the title, a small table
    inside an entry, the Spell Scroll's wrapped title, and (on a page of its own) the table of
    weights."""
    return [
        at(RIGHT, 530, "Adventuring Gear", TITLE, 94),
        at(RIGHT, 515, "The Adventuring Gear table includes gear."),
        at(RIGHT, 460, "Acid (25 GP)", TITLE, 94),
        at(RIGHT, 447, "You can throw a vial of Acid."),
        at(LEFT, 733, "Adventuring Gear", TITLE, 95),
        at(66.9, 719, "Item Weight Cost", TITLE, 95),
        at(66.9, 704, "Acid 1 lb. 25 GP", SANS, 95),
        at(66.9, 689, "Arcane Focus Varies Varies", SANS, 95),
        at(66.9, 674, "Spell Scroll (Cantrip) — 30 GP", SANS, 95),
        at(66.9, 659, "Spell Scroll (Level 1) — 50 GP", SANS, 95),
        at(66.9, 644, "Waterskin 5 lb. (full) 2 SP", SANS, 95),
        at(LEFT, 620, "Arcane Focus (Varies)", TITLE, 96),
        at(LEFT, 607, "An Arcane Focus channels magic."),
        at(LEFT, 550, "Arcane Focuses", TITLE, 96),
        at(67.0, 535, "Focus Weight Cost", TITLE, 96),
        at(66.6, 520, "Crystal 1 lb. 10 GP", SANS, 96),
        at(LEFT, 480, "Spell Scroll (Cantrip, 30 GP;", TITLE, 96),
        at(LEFT, 470, "Level 1, 50 GP)", TITLE, 96),
        at(LEFT, 455, "A Spell Scroll is a magic item."),
        at(RIGHT, 430, "Waterskin (2 SP)", TITLE, 96),
        at(RIGHT, 417, "A Waterskin holds up to 4 pints."),
    ]


def mount_lines() -> list[Line]:
    return [
        at(LEFT, 266, "Mounts and Vehicles", TITLE, 100),
        at(LEFT, 251, "A mount can help you move quickly."),
        at(RIGHT, 615, "Mounts and Other Animals", TITLE, 100),
        at(317.5, 600, "Item Carrying Capacity Cost", TITLE, 100),
        at(317.1, 585, "Camel 450 lb. 50 GP", SANS, 100),
        at(317.1, 570, "Horse, Riding 480 lb. 75 GP", SANS, 100),
        at(RIGHT, 452, "Tack, Harness, and Drawn Vehicles", TITLE, 100),
        at(317.5, 437, "Item Weight Cost", TITLE, 100),
        at(317.1, 423, "Cart 200 lb. 15 GP", SANS, 100),
        at(317.1, 407, "Feed per day 10 lb. 5 CP", SANS, 100),
        at(317.1, 362, "Saddle", "GillSans-Italic", 100),
        at(325.1, 347, "Exotic 40 lb. 60 GP", SANS, 100),
        at(317.1, 302, "Stabling per day — 5 SP", SANS, 100),
        at(RIGHT, 247, "Large Vehicles", TITLE, 100),
        at(RIGHT, 230, "The following notes apply."),
        at(63.2, 733, "Airborne and Waterborne Vehicles", TITLE, 101),
        at(
            67.2,
            718,
            "Ship Speed Crew Passengers Cargo (Tons) AC HP Damage Threshold Cost",
            TITLE,
            101,
        ),
        at(66.8, 704, "Airship 8 mph 10 20 1 13 300 —", SANS, 101),
        at(495.0, 704, "40,000 GP", SANS, 101),
        pieces(
            689, 101, (66.8, "Galley "), (138.9, "4 mph "), (183.0, "80 — "), (290.9, "150 15 500 ")
        ),
        at(437.6, 689, "20 30,000 GP", SANS, 101),
        at(66.8, 674, "Keelboat 1 mph 1 6 1/2 ", SANS, 101),
        pieces(674, 101, (340.1, "15 "), (372.1, "100 10 3,000 GP")),
        at(63.0, 577, "Crew", TITLE, 101),
        at(63.0, 564, "A ship larger than a Keelboat needs a crew."),
        at(63.0, 250, "Lifestyle Expenses", TITLE, 101),
    ]


def tools() -> dict[str, gear.Tool]:
    return {t.name: t for t in gear.parse_tools(tool_lines())}


def stuff() -> dict[str, gear.Gear]:
    return {g.name: g for g in gear.parse_gear(gear_lines())}


def mounts() -> dict[str, gear.Gear]:
    return {g.name: g for g in gear.parse_mounts(mount_lines())}


class Tools(unittest.TestCase):
    def test_every_tool_is_found_with_its_group(self) -> None:
        self.assertEqual(list(tools()), ["Brewer’s Supplies", "Gaming Set", "Thieves’ Tools"])
        self.assertEqual(
            [t.group for t in tools().values()],
            ["Artisan’s Tools", "Other Tools", "Other Tools"],
        )

    def test_the_label_lines_are_read_and_the_group_introduction_is_not_a_tool(self) -> None:
        brewer = tools()["Brewer’s Supplies"]
        self.assertEqual(
            (brewer.cost, brewer.ability, brewer.weight), ("20 GP", "Intelligence", "9 lb.")
        )
        self.assertEqual(brewer.craft, "Antitoxin")
        self.assertEqual(tools()["Thieves’ Tools"].ability, "Dexterity")

    def test_a_wrapped_line_and_a_cut_word_are_one_line(self) -> None:
        self.assertEqual(
            tools()["Brewer’s Supplies"].utilize,
            "Detect poisoned drink (DC 15), or identify alcohol (DC 10)",
        )

    def test_variants_are_kept_whole_and_a_tool_with_none_has_none(self) -> None:
        self.assertEqual(
            tools()["Gaming Set"].variants,
            "Dice (1 SP), dragonchess (1 GP), playing cards (5 SP), three-dragon ante (1 GP)",
        )
        self.assertEqual(
            (tools()["Gaming Set"].cost, tools()["Gaming Set"].weight), ("Varies", "—")
        )
        self.assertEqual(tools()["Thieves’ Tools"].variants, "")

    def test_a_tool_without_an_ability_stops_the_tool(self) -> None:
        lines = [
            at(LEFT, 300, "Odd Tool (5 GP)", TITLE),
            at(LEFT, 288, "Utilize: Something", TITLE),
        ]
        with self.assertRaisesRegex(SrdError, "Odd Tool.*Ability"):
            gear.parse_tools(lines)

    def test_an_unreadable_label_line_stops_the_tool(self) -> None:
        lines = [
            at(LEFT, 300, "Odd Tool (5 GP)", TITLE),
            at(LEFT, 288, "Ability: Dexterity Weight: 1 lb.", TITLE),
            at(LEFT, 276, "Mystery: something", TITLE),
        ]
        with self.assertRaisesRegex(SrdError, "can't read"):
            gear.parse_tools(lines)

    def test_a_tool_that_comes_twice_stops_the_tool(self) -> None:
        one = [
            at(LEFT, 300, "Odd Tool (5 GP)", TITLE),
            at(LEFT, 288, "Ability: Dexterity Weight: 1 lb.", TITLE),
            at(LEFT, 276, "Utilize: Something (DC 10)", TITLE),
        ]
        again = [at(RIGHT, ln.y, ln.text, TITLE) for ln in one]
        with self.assertRaisesRegex(SrdError, "comes twice"):
            gear.parse_tools(one + again)


class Gear(unittest.TestCase):
    def test_every_entry_is_found_and_the_gear_heading_is_not_one(self) -> None:
        self.assertEqual(list(stuff()), ["Acid", "Arcane Focus", "Spell Scroll", "Waterskin"])

    def test_cost_from_the_title_and_weight_from_the_table(self) -> None:
        acid = stuff()["Acid"]
        self.assertEqual(
            (acid.cost, acid.weight, acid.group), ("25 GP", "1 lb.", "Adventuring Gear")
        )
        self.assertEqual(stuff()["Waterskin"].weight, "5 lb. (full)")
        self.assertEqual(stuff()["Arcane Focus"].weight, "Varies")

    def test_one_and_a_half_printed_as_11_2_is_read_as_one_and_a_half(self) -> None:
        lines = [
            ln
            if ln.text != "A Waterskin holds up to 4 pints."
            else at(RIGHT, 417, "It holds 11/2 pints.")
            for ln in gear_lines()
        ]
        waterskin = {g.name: g for g in gear.parse_gear(lines)}["Waterskin"]
        self.assertEqual(waterskin.text, "It holds 1½ pints.")

    def test_a_small_table_inside_an_entry_stays_as_its_lines(self) -> None:
        self.assertEqual(
            stuff()["Arcane Focus"].text,
            "An Arcane Focus channels magic.\nArcane Focuses\n"
            "Focus Weight Cost\nCrystal 1 lb. 10 GP",
        )

    def test_the_spell_scrolls_wrapped_title_is_one_entry_with_both_prices(self) -> None:
        scroll = stuff()["Spell Scroll"]
        self.assertEqual(scroll.cost, "Cantrip, 30 GP; Level 1, 50 GP")
        self.assertEqual(scroll.text, "A Spell Scroll is a magic item.")

    def test_a_cost_that_differs_from_the_table_stops_the_tool(self) -> None:
        lines = [ln for ln in gear_lines()]
        lines = [
            at(ln.x, ln.y, ln.text.replace("Acid 1 lb. 25 GP", "Acid 1 lb. 26 GP"), SANS, 95)
            if "Acid 1 lb." in ln.text
            else ln
            for ln in lines
        ]
        with self.assertRaisesRegex(SrdError, "Acid.*25 GP.*26 GP"):
            gear.parse_gear(lines)

    def test_a_table_row_with_no_entry_stops_the_tool(self) -> None:
        extra = at(66.9, 600, "Ghost 1 lb. 1 GP", SANS, 95)
        with self.assertRaisesRegex(SrdError, "Ghost"):
            gear.parse_gear([*gear_lines(), extra])

    def test_an_entry_with_no_row_in_the_table_stops_the_tool(self) -> None:
        extra = [at(LEFT, 400, "Orphan (1 GP)", TITLE, 96), at(LEFT, 388, "Words.")]
        with self.assertRaisesRegex(SrdError, "Orphan.*no row"):
            gear.parse_gear([*gear_lines(), *extra])

    def test_an_entry_with_no_words_stops_the_tool(self) -> None:
        lines = [ln for ln in gear_lines() if ln.text != "You can throw a vial of Acid."]
        with self.assertRaisesRegex(SrdError, "Acid.*no description"):
            gear.parse_gear(lines)

    def test_an_unreadable_table_row_stops_the_tool(self) -> None:
        with self.assertRaisesRegex(SrdError, "can't read"):
            gear.parse_gear([*gear_lines(), at(66.9, 600, "Mystery row", SANS, 95)])


class Mounts(unittest.TestCase):
    def test_mounts_and_drawn_vehicles_and_ships_are_found(self) -> None:
        self.assertEqual(
            list(mounts()),
            ["Camel", "Horse, Riding", "Cart", "Airship", "Galley", "Keelboat"],
        )
        self.assertEqual([m.group for m in mounts().values()][:3], ["Mount", "Mount", "Vehicle"])

    def test_a_mount_has_a_capacity_and_a_cost(self) -> None:
        horse = mounts()["Horse, Riding"]
        self.assertEqual((horse.cost, dict(horse.extra)), ("75 GP", {"capacity": "480 lb."}))

    def test_rows_that_are_not_vehicles_are_left_out(self) -> None:
        for name in ("Feed per day", "Saddle", "Exotic", "Stabling per day"):
            self.assertNotIn(name, mounts())

    def test_a_ships_row_is_joined_from_its_pieces(self) -> None:
        galley = dict(mounts()["Galley"].extra)
        self.assertEqual(galley["speed"], "4 mph")
        self.assertEqual(
            [galley[k] for k in gear.SHIP_CELLS],
            ["80", "—", "150", "15", "500", "20"],
        )
        self.assertEqual(mounts()["Galley"].cost, "30,000 GP")
        keelboat = dict(mounts()["Keelboat"].extra)
        self.assertEqual((keelboat["cargo"], keelboat["ac"], keelboat["hp"]), ("1/2", "15", "100"))

    def test_a_ship_row_with_a_missing_cell_stops_the_tool(self) -> None:
        lines = [ln for ln in mount_lines() if ln.x != 340.1]  # (the Keelboat's tail)
        with self.assertRaisesRegex(SrdError, "Keelboat"):
            gear.parse_mounts(lines)

    def test_a_missing_table_stops_the_tool(self) -> None:
        with self.assertRaisesRegex(SrdError, "table was not found"):
            gear.parse_mounts([ln for ln in mount_lines() if ln.page != 100])


class Entries(unittest.TestCase):
    def test_a_ship_is_read_out_as_words_without_its_dashes(self) -> None:
        text = build.gear_entry(mounts()["Airship"])["text"]
        self.assertEqual(text, "Speed 8 mph. Crew 10. Passengers 20. Cargo 1 ton. AC 13. HP 300.")
        self.assertIn("Damage threshold 20.", build.gear_entry(mounts()["Galley"])["text"])

    def test_a_mount_and_a_cart_are_read_out_as_words(self) -> None:
        self.assertEqual(build.gear_entry(mounts()["Camel"])["text"], "Carrying capacity 450 lb.")
        self.assertEqual(build.gear_entry(mounts()["Cart"])["text"], "Weighs 200 lb.")

    def test_a_tool_entry_has_its_lines_as_printed(self) -> None:
        entry = build.tool_entry(tools()["Brewer’s Supplies"])
        self.assertEqual(entry["kind"], "tool")
        self.assertEqual(
            entry["text"],
            "Utilize: Detect poisoned drink (DC 15), or identify alcohol (DC 10)\nCraft: Antitoxin",
        )
