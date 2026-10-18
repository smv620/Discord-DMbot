"""Reading the SRD 5.2.1's Gameplay Toolbox chapter into rules, poisons, traps and curses (#1935):
made-up lines in the shapes the real PDF has (no PDF is needed, and none is kept in the
repository)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.devtools.srd import build, toolbox
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, ITALIC, BODY = "GillSans-SemiBold", "Cambria-Italic", "Cambria"
BOLD, BOLD_LEAD, SANS = "Cambria-Bold", "Cambria-BoldItalic", "GillSans"
LEFT, RIGHT = 63.0, 314.0
DATA = Path(__file__).resolve().parents[1] / "src" / "dmbot" / "rules" / "data" / "srd52"
TERRAINS = (
    *("Arctic", "Coastal", "Desert", "Forest", "Grassland", "Hill"),
    *("Mountain", "Swamp", "Underdark", "Urban", "Waterborne"),
)


FIXTURE_LEFT_OUT = {
    "Creating a Background": 1,
    "1: Choose Abilities": 1,
    "2: Choose a Feat": 0,
    "3: Choose Skill Proficiencies": 0,
    "4: Choose a Tool Proficiency": 0,
    "5: Choose Equipment": 0,
    "Curses": 1,
    "Example Contagions": 1,
    "Environmental Effects": 0,
    "Sample Poisons": 1,
    "Example Traps": 1,
}


def at(x: float, y: float, text: str, font: str = BODY, page: int = 192) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def terrain_table(top: float = 266.0) -> list[Line]:
    """The Travel Terrain table: its column heads, eleven rows (the last with a dagger on a line
    of its own above it) and its two notes."""
    lines = [at(67.0, top, toolbox.TERRAIN_HEADER, TITLE)]
    y = top - 15
    for name in TERRAINS:
        if name == "Waterborne":
            lines.append(at(162.0, y + 3, "† ", SANS))
        pace = "Fast*" if name == "Arctic" else "Special" if name == "Waterborne" else "Normal"
        lines.append(at(67.0, y, f"{name} {pace} 2d8 × 10 feet 10 15 20", SANS))
        y -= 15
    lines += [
        at(LEFT, y, "*Appropriate equipment is necessary to keep up a Fast pace.", SANS),
        at(LEFT, y - 6, "†", SANS),
        at(
            65.0, y - 9, "Characters’ rate of travel while waterborne depends on the vehicle.", SANS
        ),
    ]
    return lines


def two(x: float, y: float, label: str, x_rhs: float, rhs: str) -> Line:
    """A formula's bold label and, further along the same line, its right-hand side."""
    pieces = [
        Piece(x, y, "ABCDEF+Cambria-Bold", label),
        Piece(x_rhs, y, "ABCDEF+Cambria", rhs),
    ]
    return Line(x, y, pieces, 192)


def formulas() -> list[Line]:
    """The boxed pace formulas, as the PDF sets them: bold labels, an "=" on a line of its own."""
    return [
        Line(
            364.0,
            734.0,
            [
                Piece(364.0, 734.0, "ABCDEF+Cambria-Bold", "Miles per hour "),
                Piece(440.0, 734.0, "ABCDEF+Cambria", " Speed ÷ 10\n"),
            ],
            192,
        ),
        at(434.0, 733.0, "="),
        Line(
            327.0,
            721.0,
            [
                Piece(327.0, 721.0, "ABCDEF+Cambria-Bold", "Miles per day"),
                Piece(389.0, 721.0, "ABCDEF+Cambria", " (Normal pace) "),
                Piece(461.0, 721.0, "ABCDEF+Cambria", " Miles per hour \n"),
            ],
            192,
        ),
        at(456.0, 720.0, "="),
        at(340.0, 708.0, "× number of hours traveled (typically 8)"),
        two(328.0, 695.0, "Fast pace ", 379.0, " Miles per day × 1⅓ (round down)\n"),
        at(374.0, 694.0, "="),
        two(326.0, 682.0, "Slow pace ", 380.0, " Miles per day × 2/3 (round down)\n"),
        at(374.0, 681.0, "="),
    ]


def chapter() -> list[Line]:
    """The chapter in the shapes it has: a rule with a wrapped heading, the travel section with
    its formulas printed in the next column, a terrain table, a section that is left out, the
    sample fear table with a word cut at a line's end, bullets set in the serif font, a poison, a
    contagion, a trap with its levels table, a trap whose levels wrap, and an environmental curse
    that holds one named curse."""
    return [
        at(66.0, 723, "Gameplay Toolbox", TITLE),
        at(LEFT, 694, "Travel Pace", TITLE),
        at(LEFT, 680, "A group can travel at a Normal, Fast, or Slow pace."),
        at(LEFT, 650, "Special Movement", TITLE),
        at(LEFT, 633, "Translate the party’s Speed into travel rates using these rules:"),
        at(LEFT, 281, "Travel Terrain", TITLE),
        *terrain_table(),
        *formulas(),
        at(RIGHT, 666, "If the characters are flying, they can move at a Fast pace."),
        at(RIGHT, 622, "Vehicles", TITLE, 192),
        at(RIGHT, 605, "Characters in a vehicle use its speed."),
        at(RIGHT, 544, "Creating a Background", TITLE),
        at(RIGHT, 529, "Advice on building a background."),
        at(RIGHT, 401, "1: Choose Abilities", TITLE),
        at(RIGHT, 385, "Choose three abilities."),
        at(LEFT, 666, "2: Choose a Feat", TITLE, 193),
        at(LEFT, 618, "3: Choose Skill Proficiencies", TITLE, 193),
        at(LEFT, 545, "4: Choose a Tool Proficiency", TITLE, 193),
        at(LEFT, 496, "5: Choose Equipment", TITLE, 193),
        at(LEFT, 419, "Curses and ", TITLE, 193),
        at(LEFT, 400, "Magical Contagions", TITLE, 193),
        at(LEFT, 385, "A curse is a magical burden.", BODY, 193),
        at(LEFT, 305, "Curses", TITLE, 193),
        at(LEFT, 288, "A curse takes one of these forms.", BODY, 193),
        at(LEFT, 399, "Environmental Curses", TITLE, 193),
        at(LEFT, 386, "Some locations are suffused with evil.", BODY, 193),
        at(
            71.0,
            338,
            " Demonic Possession. Demonic Possession arises from the Abyss.",
            BOLD_LEAD,
            193,
        ),
        at(LEFT, 326, "A creature must succeed on a DC 15 Charisma saving throw.", BODY, 193),
        at(LEFT, 729, "Environmental Effects", TITLE, 195),
        at(LEFT, 729, "Fear and Mental Stress", TITLE, 196),
        at(LEFT, 714, "Adventurers are made of sterner stuff.", BODY, 196),
        at(LEFT, 598, "Fear Effects", TITLE, 196),
        at(LEFT, 581, "Use the Frightened condition as the baseline.", BODY, 196),
        at(LEFT, 499, "Sample Fear DCs", TITLE, 196),
        at(67.0, 484, "Example Save DC", TITLE, 196),
        at(67.0, 469, "When the characters open a sarcophagus, a 10", SANS, 196),
        at(67.0, 456, "harmless yet terrifying appari-", SANS, 196),
        at(67.0, 444, "tion appears.", SANS, 196),
        at(67.0, 429, "A portal to the Abyss opens, revealing a 20", SANS, 196),
        at(67.0, 416, "realm of torment.", SANS, 196),
        at(LEFT, 356, "At your discretion, consider these examples:", BODY, 196),
        at(LEFT, 279, "• The creature must take the Dash action on each of its", BODY, 196),
        at(74.0, 267, "turns.", BODY, 196),
        at(LEFT, 243, "• Attack rolls against the creature have Advantage.", BODY, 196),
        at(RIGHT, 732, "Sample Poisons", TITLE, 197),
        at(RIGHT, 715, "Example poisons are detailed here.", BODY, 197),
        at(RIGHT, 648, "Assassin’s Blood (150 GP)", TITLE, 197),
        at(RIGHT, 635, "Ingested Poison", ITALIC, 197),
        at(RIGHT, 619, "A creature must make a DC 10 Constitution saving throw.", BODY, 197),
        at(RIGHT, 552, "Burnt Othur Fumes (1,500 GP)", TITLE, 197),
        at(RIGHT, 539, "Inhaled Poison", ITALIC, 197),
        at(RIGHT, 524, "A creature must make a DC 13 Constitution saving throw.", BODY, 197),
        at(LEFT, 532, "Example Contagions", TITLE, 194),
        at(LEFT, 519, "The following examples show how contagions work.", BODY, 194),
        at(LEFT, 463, "Cackle Fever", TITLE, 194),
        at(LEFT, 451, "Magical Contagion", ITALIC, 194),
        at(LEFT, 435, "Cheaply made potions are tainted by Cackle Fever.", BODY, 194),
        at(
            71.0,
            220,
            "Fighting the Contagion. At the end of each Long Rest, a DC 13 save.",
            BOLD_LEAD,
            194,
        ),
        at(LEFT, 274, "Example Traps", TITLE, 199),
        at(LEFT, 258, "Traps are presented in alphabetical order.", BODY, 199),
        at(LEFT, 238, "Collapsing Roof", TITLE, 199),
        at(LEFT, 226, "Deadly Trap (Levels 1–4)", ITALIC, 199),
        at(LEFT, 211, "Trigger: A creature crosses a trip wire", BOLD, 199),
        at(LEFT, 199, "Duration: Instantaneous", BOLD, 199),
        at(LEFT, 181, "This trap uses a trip wire to collapse the ceiling.", BODY, 199),
        at(71.0, 133, " The first creature that crosses it takes damage.", BODY, 199),
        at(322.0, 638, " At Higher Levels. You can scale the trap.", BOLD_LEAD, 199),
        at(322.0, 598, "Levels Bludgeoning Damage Save DC", TITLE, 199),
        at(325.0, 583, "5–10 22 (4d10) 15", SANS, 199),
        at(323.0, 568, "11–16 55 (10d10) 17", SANS, 199),
        at(LEFT, 483, "Rolling Stone", TITLE, 201),
        at(LEFT, 470, "Deadly Trap (Levels 11–16) or Nuisance Trap (Levels", ITALIC, 201),
        at(LEFT, 458, "17–20)", ITALIC, 201),
        at(LEFT, 443, "Trigger: A creature moves onto a pressure plate", BOLD, 201),
        at(LEFT, 432, "Duration: Until the stone stops rolling", BOLD, 201),
        at(71.0, 353, " On its turn, the stone moves 60 feet.", BODY, 201),
    ]


def parsed() -> toolbox.Toolbox:
    return toolbox.parse_toolbox(chapter())


class Rules(unittest.TestCase):
    def test_every_kept_title_is_a_rule_and_the_left_out_ones_are_not(self) -> None:
        names = [r.name for r in parsed().rules]
        self.assertEqual(
            names,
            [
                "Travel Pace",
                "Special Movement",
                "Travel Terrain",
                "Vehicles",
                "Curses and Magical Contagions",
                "Environmental Curses",
                "Fear and Mental Stress",
                "Fear Effects",
                "Cackle Fever",
            ],
        )

    def test_a_heading_that_wraps_is_one_title(self) -> None:
        rule = next(r for r in parsed().rules if r.name.startswith("Curses and"))
        self.assertEqual(rule.text, "A curse is a magical burden.")

    def test_a_title_just_below_another_is_not_part_of_it(self) -> None:
        # "Fear and Mental Stress" / "Fear Effects" are lines apart; only a title that ends on
        # "and" (or a word like it) carries on.
        names = [r.name for r in parsed().rules]
        self.assertIn("Fear and Mental Stress", names)
        self.assertIn("Fear Effects", names)

    def test_the_formulas_follow_the_sentence_that_promises_them(self) -> None:
        rule = next(r for r in parsed().rules if r.name == "Special Movement")
        self.assertEqual(
            rule.text.splitlines(),
            [
                "Translate the party’s Speed into travel rates using these rules:",
                "Miles per hour = Speed ÷ 10",
                "Miles per day (Normal pace) = Miles per hour × number of hours traveled "
                "(typically 8)",
                "Fast pace = Miles per day × 1⅓ (round down)",
                "Slow pace = Miles per day × 2/3 (round down)",
                "If the characters are flying, they can move at a Fast pace.",
            ],
        )

    def test_the_terrain_table_is_a_line_a_terrain_and_two_notes(self) -> None:
        rule = next(r for r in parsed().rules if r.name == "Travel Terrain")
        rows = rule.text.splitlines()
        self.assertEqual(len(rows), 13)
        self.assertEqual(
            rows[0],
            "Arctic: Fast* Maximum Pace; Encounter Distance 2d8 × 10 feet; Foraging DC 10; "
            "Navigation DC 15; Search DC 20",
        )
        self.assertTrue(rows[10].startswith("Waterborne: Special† Maximum Pace"))
        self.assertTrue(rows[11].startswith("*Appropriate equipment"))
        self.assertTrue(rows[12].startswith("†Characters’ rate of travel"))

    def test_the_fear_table_is_put_in_the_text_a_line_a_row_with_cut_words_mended(self) -> None:
        rule = next(r for r in parsed().rules if r.name == "Fear Effects")
        self.assertEqual(
            rule.text.splitlines(),
            [
                "Use the Frightened condition as the baseline.",
                "DC 10: When the characters open a sarcophagus, a harmless yet terrifying "
                "apparition appears.",
                "DC 20: A portal to the Abyss opens, revealing a realm of torment.",
                "At your discretion, consider these examples:",
                "• The creature must take the Dash action on each of its turns.",
                "• Attack rolls against the creature have Advantage.",
            ],
        )

    def test_a_contagion_is_a_rule_with_its_kind_as_the_tag(self) -> None:
        rule = next(r for r in parsed().rules if r.name == "Cackle Fever")
        self.assertEqual(rule.tag, "Magical Contagion")
        self.assertIn("Fighting the Contagion. At the end of each Long Rest", rule.text)

    def test_the_left_out_titles_are_named_with_their_lines(self) -> None:
        self.assertEqual(
            toolbox.left_out(chapter()),
            FIXTURE_LEFT_OUT,
        )


class Hazards(unittest.TestCase):
    def test_a_poison_has_its_type_price_and_words(self) -> None:
        poisons = {p.name: p for p in parsed().poisons}
        blood = poisons["Assassin’s Blood"]
        self.assertEqual((blood.type, blood.price), ("Ingested", "150 GP"))
        self.assertEqual(blood.text, "A creature must make a DC 10 Constitution saving throw.")
        self.assertEqual(poisons["Burnt Othur Fumes"].price, "1,500 GP")

    def test_a_trap_has_its_levels_trigger_duration_and_text_with_the_levels_table(self) -> None:
        roof = {t.name: t for t in parsed().traps}["Collapsing Roof"]
        self.assertEqual(roof.severity, "Deadly Trap (Levels 1–4)")
        self.assertEqual(roof.trigger, "A creature crosses a trip wire")
        self.assertEqual(roof.duration, "Instantaneous")
        self.assertEqual(
            roof.text.splitlines(),
            [
                "This trap uses a trip wire to collapse the ceiling.",
                "The first creature that crosses it takes damage.",
                "At Higher Levels. You can scale the trap.",
                "Levels 5–10: Bludgeoning Damage 22 (4d10); Save DC 15",
                "Levels 11–16: Bludgeoning Damage 55 (10d10); Save DC 17",
            ],
        )

    def test_a_levels_line_that_wraps_is_one_severity(self) -> None:
        stone = {t.name: t for t in parsed().traps}["Rolling Stone"]
        self.assertEqual(
            stone.severity, "Deadly Trap (Levels 11–16) or Nuisance Trap (Levels 17–20)"
        )
        self.assertEqual(stone.duration, "Until the stone stops rolling")

    def test_the_one_named_curse_is_split_from_the_section_it_is_printed_in(self) -> None:
        parsed_ = parsed()
        [curse] = parsed_.curses
        self.assertEqual(curse.name, "Demonic Possession")
        self.assertTrue(curse.text.startswith("Demonic Possession arises from the Abyss."))
        self.assertIn("DC 15 Charisma saving throw", curse.text)
        section = next(r for r in parsed_.rules if r.name == "Environmental Curses")
        self.assertEqual(section.text, "Some locations are suffused with evil.")


class Stops(unittest.TestCase):
    def without(self, *texts: str) -> list[Line]:
        return [ln for ln in chapter() if not any(ln.text.startswith(t) for t in texts)]

    def test_a_poison_without_its_type(self) -> None:
        with self.assertRaisesRegex(SrdError, "Assassin’s Blood.*page 197.*no type"):
            toolbox.parse_toolbox(self.without("Ingested Poison"))

    def test_a_poison_with_a_type_it_does_not_know(self) -> None:
        lines = chapter()
        for ln in lines:
            if ln.text == "Ingested Poison":
                ln.pieces[0] = Piece(ln.x, ln.y, ln.pieces[0].font, "Swallowed Poison")
        with self.assertRaisesRegex(SrdError, "no type line"):
            toolbox.parse_toolbox(lines)

    def test_a_trap_without_its_trigger(self) -> None:
        with self.assertRaisesRegex(SrdError, "Collapsing Roof.*no Trigger"):
            toolbox.parse_toolbox(self.without("Trigger: A creature crosses"))

    def test_a_trap_without_its_duration(self) -> None:
        with self.assertRaisesRegex(SrdError, "Collapsing Roof.*no Duration"):
            toolbox.parse_toolbox(self.without("Duration: Instantaneous"))

    def test_a_trap_without_an_effect(self) -> None:
        lines = [
            ln for ln in chapter() if not (ln.page == 201 and ln.text.startswith(" On its turn"))
        ]
        with self.assertRaisesRegex(SrdError, "Rolling Stone.*no effect"):
            toolbox.parse_toolbox(lines)

    def test_a_title_it_has_no_place_for(self) -> None:
        lines = [*chapter(), at(LEFT, 90, "Brand New Hazard", TITLE, 201), at(LEFT, 78, "Words.")]
        with self.assertRaisesRegex(SrdError, "Page 201.*Brand New Hazard"):
            toolbox.parse_toolbox(lines)

    def test_a_table_it_does_not_know(self) -> None:
        lines = chapter()
        at_ = next(
            i for i, ln in enumerate(lines) if ln.text == "Levels Bludgeoning Damage Save DC"
        )
        lines[at_] = at(322.0, 598, "Levels Acid Damage Save DC", TITLE, 199)
        with self.assertRaisesRegex(SrdError, "table on page 199 that the tool doesn't know"):
            toolbox.parse_toolbox(lines)

    def test_a_name_that_comes_twice_in_a_kind(self) -> None:
        lines = [*chapter(), *(ln for ln in chapter() if 635 <= ln.y <= 650 and ln.page == 197)]
        lines.insert(len(lines), at(RIGHT, 620, "A creature takes damage.", BODY, 197))
        with self.assertRaisesRegex(SrdError, "comes twice"):
            toolbox.parse_toolbox(lines)

    def test_a_chapter_that_does_not_start_with_its_title(self) -> None:
        with self.assertRaisesRegex(SrdError, "don't start with"):
            toolbox.parse_toolbox(chapter()[1:])

    def test_a_left_out_title_that_is_not_in_the_chapter(self) -> None:
        with self.assertRaisesRegex(SrdError, "not in the chapter"):
            toolbox.left_out(self.without("1: Choose Abilities"))

    def test_four_formulas_not_three(self) -> None:
        lines = [
            ln for ln in chapter() if not (ln.page == 192 and ln.x >= 320 and 680 <= ln.y <= 682.5)
        ]
        with self.assertRaisesRegex(SrdError, "3 formulas, not the four"):
            toolbox.parse_toolbox(lines)

    def test_a_formula_that_is_only_an_equals_sign(self) -> None:
        with self.assertRaisesRegex(SrdError, "a formula with nothing on either side"):
            toolbox.parse_toolbox(self.without("Slow pace"))


class TheRealChapterCounts(unittest.TestCase):
    def test_the_expected_left_out_counts_are_the_ones_attribution_names(self) -> None:
        attribution = (DATA / "ATTRIBUTION.md").read_text(encoding="utf-8")
        for title in toolbox.EXPECTED_LEFT_OUT:
            self.assertIn(title, attribution)
        self.assertEqual(set(toolbox.EXPECTED_LEFT_OUT), set(toolbox.LEFT_OUT))


class TheData(unittest.TestCase):
    """The file the tool made from the real PDF (the rebuild is checked by running the tool
    twice, and by this: the file is exactly what `dump` makes of itself)."""

    text: str
    data: dict[str, Any]
    by_name: dict[tuple[str, str], dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = (DATA / "toolbox.json").read_text(encoding="utf-8")
        cls.data = json.loads(cls.text)
        cls.by_name = {(e["kind"], e["name"]): e for e in cls.data["entries"]}

    def test_the_file_is_what_the_tool_writes(self) -> None:
        self.assertEqual(build.dump(self.data), self.text)

    def test_how_many_of_each(self) -> None:
        kinds = [e["kind"] for e in self.data["entries"]]
        self.assertEqual(
            {k: kinds.count(k) for k in ("rule", "poison", "trap", "curse")},
            {"rule": 36, "poison": 14, "trap": 8, "curse": 1},
        )

    def test_two_poisons_with_their_type_and_price(self) -> None:
        purple = self.by_name["poison", "Purple Worm Poison"]
        self.assertEqual((purple["type"], purple["price"]), ("Injury", "2,000 GP"))
        self.assertIn("DC 21 Constitution saving throw", purple["text"])
        self.assertIn("35 (10d6) Poison damage", purple["text"])
        blood = self.by_name["poison", "Assassin’s Blood"]
        self.assertEqual((blood["type"], blood["price"]), ("Ingested", "150 GP"))

    def test_a_traps_dcs_and_its_levels(self) -> None:
        pit = self.by_name["trap", "Spiked Pit"]
        self.assertEqual(pit["severity"], "Deadly Trap (Levels 1–4)")
        self.assertIn("DC 15 Intelligence (Investigation) check", pit["text"])
        self.assertIn(
            "Levels 11–16: Pit Depth 60 feet; Damage 21 (6d6) Bludgeoning plus 36 (8d8) Piercing",
            pit["text"],
        )
        roof = self.by_name["trap", "Collapsing Roof"]
        self.assertIn("DC 13 Dexterity saving throw", roof["text"])
        self.assertIn("DC 11 Wisdom (Perception) check", roof["text"])
        self.assertIn("Levels 17–20: Bludgeoning Damage 99 (18d10); Save DC 19", roof["text"])

    def test_travel_pace_is_there_with_its_terrain_table(self) -> None:
        terrain = self.by_name["rule", "Travel Terrain"]["text"]
        self.assertIn("Forest: Normal Maximum Pace; Encounter Distance 2d8 × 10 feet", terrain)
        self.assertIn("Foraging DC 10; Navigation DC 15; Search DC 15", terrain)
        move = self.by_name["rule", "Special Movement"]["text"]
        self.assertIn("Fast pace = Miles per day × 1⅓ (round down)", move)

    def test_every_entry_says_where_it_is_and_the_file_is_the_dms_chapter(self) -> None:
        self.assertEqual(self.data["section"], "Gameplay Toolbox")
        for e in self.data["entries"]:
            self.assertGreaterEqual(e["page"], 192)
            self.assertLessEqual(e["page"], 201)
            self.assertTrue(e["text"].strip())

    def test_nothing_the_glossary_already_has_by_name_is_repeated(self) -> None:
        glossary = json.loads((DATA / "rules.json").read_text(encoding="utf-8"))["entries"]
        taken = {e["name"].casefold() for e in glossary}
        mine = {e["name"].casefold() for e in self.data["entries"] if e["kind"] == "rule"}
        self.assertEqual(taken & mine, set())


if __name__ == "__main__":
    unittest.main()
