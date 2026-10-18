"""Reading the SRD 5.1's core rules chapters into rules (#1943): made-up lines in the shapes the
real PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dmbot.devtools.srd import build, rules51
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.parse51 import Vocabulary
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, BODY, LEAD, SANS = "GillSans-SemiBold", "Cambria", "Cambria-BoldItalic", "Calibri"
SANS_BOLD, SANS_LEAD, BULLET = "Calibri-Bold", "Calibri-BoldItalic", "Symbol"
LEFT, RIGHT, INDENT = 57.6, 328.6, 9.3
DATA = Path(__file__).resolve().parents[1] / "src" / "dmbot" / "rules" / "data" / "srd51"


def at(x: float, y: float, text: str, font: str = BODY, page: int = 84) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def vocab(*lines: Line) -> Vocabulary:
    return Vocabulary({"the", "and", "you", "rest", "hour", "long"}, list(lines), set())


def duplicate_names() -> list[Line]:
    """Every title the DUPLICATES table names, with a line of words under it, in the order the
    chapter has them (the skills lists, then the ability sections, and so on)."""
    lines: list[Line] = []
    y = 700.0
    for title, number in rules51.DUPLICATES:
        if rules51.DUPLICATES[title, number] in rules51.SKILL_LISTS:
            continue
        lines += [at(LEFT, y, title, TITLE), at(LEFT, y - 12, f"Words of {title} {number}.")]
        y -= 30
    return lines


def chapters() -> list[Line]:
    """The chapters' shapes: the opening title that wraps over two lines, a rule with a wrapped
    title and a bulleted list, prose set in the sans fonts with a bold lead-in, a table with its
    caption, the skills lists, a minus sign drawn above its number, an odd apostrophe, and a title
    that comes twice."""
    return [
        at(LEFT, 696, "Using Ability", TITLE, 76),
        at(LEFT, 665, "Scores", TITLE, 76),
        at(LEFT, 649, "Six abilities describe a creature.", BODY, 76),
        at(LEFT, 394, "Ability Scores and Modifiers", TITLE, 76),
        at(
            LEFT,
            380,
            "Each ability also has a modifier, derived from the score and",
            BODY,
            76,
        ),
        Line(
            159.0,
            239.0,
            [Piece(159.0, 239.0, "ABCDEF+Cambria", "−")],
            76,
        ),
        Line(
            LEFT,
            228.0,
            [
                Piece(LEFT, 228.0, "ABCDEF+Cambria", "ranging from "),
                Piece(165.0, 228.0, "ABCDEF+Cambria", "5 to +10."),
            ],
            76,
        ),
        at(LEFT, 184, "Ability Scores and Modifiers", SANS_BOLD, 76),
        at(LEFT, 169, "Score Modifier", SANS_BOLD, 76),
        at(67.0, 158, "1 −5", SANS, 76),
        at(RIGHT, 712, "12–13 +1", SANS, 76),
        at(RIGHT, 700, "Typical Difficulty Classes", SANS_BOLD, 76),
        at(RIGHT, 690, "Very easy 5", SANS, 76),
        at(RIGHT, 696, "Advantage and", TITLE, 76),
        at(RIGHT, 684, "Disadvantage", TITLE, 76),
        at(RIGHT, 670, "When you have advantage, roll twice.", BODY, 76),
        at(LEFT, 600, "Skills", TITLE, 77),
        at(LEFT, 588, "The skills of each ability are listed.", BODY, 77),
        at(LEFT, 560, "Strength", TITLE, 77),
        at(LEFT, 548, "• Athletics", BULLET, 77),
        at(LEFT, 520, "Dexterity", TITLE, 77),
        at(LEFT, 508, "• Acrobatics", BULLET, 77),
        at(LEFT, 496, "• Stealth", BULLET, 77),
        at(LEFT, 480, "Intelligence", TITLE, 77),
        at(LEFT, 478, "• Arcana", BULLET, 77),
        at(LEFT, 476, "Wisdom", TITLE, 77),
        at(LEFT, 474, "• Insight", BULLET, 77),
        at(LEFT, 472, "Charisma", TITLE, 77),
        at(LEFT, 471, "• Persuasion", BULLET, 77),
        at(LEFT, 468, "Sometimes the GM asks for a skill by name.", BODY, 77),
        at(LEFT, 470, "Short Rest", TITLE, 87),
        at(LEFT, 458, "A short rest is a period of downtime, at least 1 hour long.", BODY, 87),
        at(
            LEFT + INDENT,
            446,
            "Passive Perception. When you hide, there’s a chance",
            SANS_LEAD,
            87,
        ),
        at(LEFT, 434, "someone will notice you.", SANS, 87),
        at(RIGHT, 640, "Travel Pace", SANS_BOLD, 84),
        at(RIGHT, 630, "Fast 400 feet", SANS, 84),
        at(RIGHT, 620, "Size Categories", SANS_BOLD, 92),
        at(RIGHT, 610, "Tiny 2½ by 2½ ft.", SANS, 92),
        at(LEFT, 410, "Combat Step by Step", TITLE, 90),
        at(LEFT, 398, "1. Determine surprise. The GM decides who is surprised.", SANS_BOLD, 90),
        at(LEFT + INDENT, 386, "The adversaries are̶how far away.", SANS, 90),
        at(LEFT, 360, "Spellcasting", TITLE, 100),
        at(LEFT, 348, "Magic permeates the worlds.", BODY, 100),
    ]


def read(lines: list[Line]) -> dict[str, str]:
    return {r.name: r.text for r in rules51.parse_rules(lines, vocab(*lines))}


class Reading(unittest.TestCase):
    def test_a_title_that_wraps_is_one_rule_and_the_openings_are_not_rules(self) -> None:
        rules = read(chapters() + duplicate_names())
        self.assertIn("Advantage and Disadvantage", rules)
        self.assertNotIn("Using Ability Scores", rules)
        self.assertNotIn("Spellcasting", rules)

    def test_prose_set_in_the_sans_fonts_is_read_and_a_table_is_not(self) -> None:
        rules = read(chapters() + duplicate_names())
        self.assertEqual(
            rules["Short Rest"].splitlines(),
            [
                "A short rest is a period of downtime, at least 1 hour long.",
                "Passive Perception. When you hide, there’s a chance someone will notice you.",
            ],
        )
        text = rules["Ability Scores and Modifiers"]
        self.assertNotIn("Score Modifier", text)
        self.assertNotIn("1 −5", text)
        self.assertNotIn("12–13", text)

    def test_a_minus_sign_drawn_above_its_number_is_put_back(self) -> None:
        text = read(chapters() + duplicate_names())["Ability Scores and Modifiers"]
        self.assertIn("ranging from −5 to +10.", text)

    def test_odd_characters_are_put_right_and_a_numbered_bold_start_is_a_paragraph(self) -> None:
        text = read(chapters() + duplicate_names())["Combat Step by Step"]
        self.assertEqual(
            text.splitlines(),
            [
                "1. Determine surprise. The GM decides who is surprised.",
                "The adversaries are—how far away.",
            ],
        )

    def test_the_skills_lists_are_lines_of_the_skills_rule(self) -> None:
        rules = read(chapters() + duplicate_names())
        self.assertEqual(
            rules["Skills"].splitlines(),
            [
                "The skills of each ability are listed.",
                "Strength: Athletics",
                "Dexterity: Acrobatics, Stealth",
                "Intelligence: Arcana",
                "Wisdom: Insight",
                "Charisma: Persuasion",
                "Sometimes the GM asks for a skill by name.",
            ],
        )
        self.assertNotIn("Strength (Skills)", rules)

    def test_a_title_that_comes_twice_gets_the_name_for_its_place(self) -> None:
        rules = read(chapters() + duplicate_names())
        for name in (
            "Hit Points",
            "Hit Points (Constitution)",
            "Initiative",
            "Initiative (Dexterity)",
            "Difficult Terrain (Combat)",
            "Spellcasting Ability (Wisdom)",
            "Saving Throws (Spells)",
        ):
            self.assertIn(name, rules)
        self.assertEqual(rules["Hit Points"], "Words of Hit Points 1.")
        self.assertEqual(rules["Hit Points (Constitution)"], "Words of Hit Points 0.")

    def test_the_left_out_are_the_openings_and_the_tables_with_their_lines(self) -> None:
        found = rules51.left_out(chapters() + duplicate_names())
        self.assertEqual(found["Using Ability Scores"], 1)
        self.assertEqual(found["Spellcasting"], 1)
        self.assertEqual(found["Ability Scores and Modifiers"], 4)  # the caption and its rows
        self.assertEqual(found["Size Categories"], 2)
        self.assertEqual(set(found), set(rules51.TABLES) | set(rules51.LEFT_OUT))


class Stops(unittest.TestCase):
    def test_a_title_that_comes_twice_with_no_name_for_it(self) -> None:
        lines = chapters() + duplicate_names()
        lines += [
            at(LEFT, 90, "Cover", TITLE, 96),
            at(LEFT, 78, "One."),
            at(LEFT, 60, "Cover", TITLE, 96),
        ]
        lines += [at(LEFT, 48, "Two.")]
        with self.assertRaisesRegex(SrdError, "'Cover' comes twice"):
            rules51.parse_rules(lines, vocab(*lines))

    def test_a_rule_with_no_words(self) -> None:
        lines = chapters() + duplicate_names() + [at(LEFT, 90, "Cover", TITLE, 96)]
        with self.assertRaisesRegex(SrdError, "Cover.*page 96.*no description"):
            rules51.parse_rules(lines, vocab(*lines))

    def test_a_title_that_is_not_at_a_columns_edge(self) -> None:
        lines = [*chapters(), at(120.0, 90, "Cover", TITLE, 96)]
        with self.assertRaisesRegex(SrdError, "not at a column's edge"):
            rules51.parse_rules(lines, vocab(*lines))

    def test_a_table_that_is_not_there(self) -> None:
        lines = [
            ln
            for ln in chapters()
            if ln.text != "Ability Scores and Modifiers" or ln.first_font == TITLE
        ]
        with self.assertRaisesRegex(SrdError, "Tables not found"):
            rules51.parse_rules(lines, vocab(*lines))

    def test_a_name_for_a_title_that_does_not_come_twice(self) -> None:
        lines = [ln for ln in chapters()]
        with self.assertRaisesRegex(SrdError, "Named duplicates that are not in the chapters"):
            rules51.parse_rules(lines, vocab(*lines))


class Building(unittest.TestCase):
    def pages(self) -> list[list[Line]]:
        return [*([[]] * 10), chapters() + duplicate_names(), [at(LEFT, 700, "Spell Lists", TITLE)]]

    def header(self, kind: str, section: str | None) -> dict[str, str]:
        return {"kind": kind, "section": section or ""}

    def test_the_file_holds_the_rules_in_the_5_2_1_glossarys_shape(self) -> None:
        lines = chapters() + duplicate_names()
        found = rules51.left_out(lines)
        with patch.dict(rules51.EXPECTED_LEFT_OUT, found, clear=True):
            files = build.rules_files_51(self.pages(), self.header, vocab(*lines))
        data = files["rules.json"]
        self.assertEqual((data["kind"], data["section"]), ("rule", "Rules"))
        short_rest = next(e for e in data["entries"] if e["name"] == "Short Rest")
        self.assertEqual(
            set(short_rest), {"name", "tag", "text", "page", "see_also"}
        )  # as rules.json of the 5.2.1
        self.assertEqual(short_rest["page"], 87)

    def test_the_build_stops_when_what_is_left_out_changes(self) -> None:
        lines = chapters() + duplicate_names()
        with self.assertRaisesRegex(SrdError, "leave out something new"):
            build.rules_files_51(self.pages(), self.header, vocab(*lines))  # (the real counts)


class TheData(unittest.TestCase):
    text: str
    data: dict[str, Any]
    by_name: dict[str, dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = (DATA / "rules.json").read_text(encoding="utf-8")
        cls.data = json.loads(cls.text)
        cls.by_name = {e["name"]: e for e in cls.data["entries"]}

    def test_the_file_is_what_the_tool_writes(self) -> None:
        self.assertEqual(build.dump(self.data), self.text)

    def test_words_the_repair_could_have_glued_or_split_are_right(self) -> None:
        self.assertIn(
            "During a round, each participant", self.by_name["The Order of Combat"]["text"]
        )
        skills = self.by_name["Skills"]["text"].splitlines()
        self.assertEqual(skills[-3].split(":")[0], "Charisma")
        self.assertTrue(skills[-2].startswith("Sometimes, the GM might ask for an ability check"))
        self.assertTrue(skills[-1].startswith("For example, if a character attempts to climb"))
        self.assertNotIn(".,", self.by_name["Skills"]["text"])
        for e in self.data["entries"]:
            self.assertNotRegex(e["text"], r"— \w", e["name"])  # a dash at a line's end
            self.assertNotRegex(e["text"], r"(?m)^\d\.[A-Z]", e["name"])  # "1.Choose"
        self.assertIn("1. Choose a target.", self.by_name["Making an Attack"]["text"])
        self.assertIn("ranging from −5", self.by_name["Ability Scores and Modifiers"]["text"])

    def test_it_is_the_5_1s_rules_section(self) -> None:
        self.assertEqual(
            (self.data["source"], self.data["edition"], self.data["kind"], self.data["section"]),
            ("SRD 5.1", "2014", "rule", "Rules"),
        )
        self.assertEqual(len(self.data["entries"]), 164)

    def test_a_short_rest_is_at_least_an_hour(self) -> None:
        self.assertIn("at least 1 hour long", self.by_name["Short Rest"]["text"])

    def test_grappling_needs_a_target_no_more_than_one_size_larger(self) -> None:
        self.assertIn("no more than one size larger than you", self.by_name["Grappling"]["text"])

    def test_the_actions_and_the_other_named_rules_are_there(self) -> None:
        for name in (
            *("Attack", "Dash", "Disengage", "Dodge", "Help", "Hide", "Ready", "Search"),
            *("Use an Object", "Grappling", "Shoving a Creature", "Cover", "Short Rest"),
            *("Long Rest", "Concentration", "Opportunity Attacks", "Two-Weapon Fighting"),
            *("Falling", "Suffocating", "Vision and Light"),
        ):  # fmt: skip
            self.assertIn(name, self.by_name)
            self.assertTrue(self.by_name[name]["text"].strip(), name)

    def test_no_title_is_twice_and_no_odd_character_is_left(self) -> None:
        names = [e["name"] for e in self.data["entries"]]
        self.assertEqual(len(names), len(set(names)))
        for e in self.data["entries"]:
            self.assertNotIn("ʼ", e["text"])
            self.assertNotIn("̶", e["text"])
            self.assertNotIn("\t", e["text"])
            self.assertEqual(e["see_also"], [])


if __name__ == "__main__":
    unittest.main()
