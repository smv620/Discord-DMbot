"""Reading the 2014 SRD 5.1's Classes chapter into class features (#1903): made-up lines in the
shapes the 5.1 PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import build, classes51, parse51
from dmbot.devtools.srd.classes import Feature
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.pdf import Line, Piece
from dmbot.rules.index import CLASS_NAMES

TITLE, BODY, BOLD, SYMBOL = "GillSans-SemiBold", "Cambria", "Cambria-Bold", "Symbol"
TABLE, TABLE_BOLD = "Calibri", "Calibri-Bold"
LEFT, RIGHT = 57.6, 328.6
WORDS = [
    "the", "you", "your", "can", "gain", "choose", "level", "at", "a", "of", "to", "and", "in",
    "one", "two", "when", "this", "rage", "style", "path", "bonus", "action", "use", "damage",
]  # fmt: skip


def at(x: float, y: float, text: str, font: str = BODY, page: int = 8) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def vocab() -> parse51.Vocabulary:
    return parse51.Vocabulary(WORDS, [])


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def rows(first: int, last: int, page: int = 8, item: str = "Rage") -> list[Line]:
    """Plain table rows for levels first..last, so a class table has its 20 levels."""
    return [
        at(61.1, 260 - n, f"{ordinal(n)} +3 {item} 3", TABLE, page) for n in range(first, last + 1)
    ]


def barbarian() -> list[Line]:
    """One class as the PDF has it: the title, the intro blocks set around the table, the
    table (a feature's name split by another column's numbers, the last one across columns),
    the features with a sub-heading and an option list, the subclass and a flavour section."""
    return [
        at(LEFT, 700, "Barbarian", TITLE),
        at(LEFT, 660, "Class Features", TITLE),
        at(LEFT, 646, "As a barbarian, you gain the following class features."),
        at(LEFT, 620, "Hit Points", TITLE),
        at(LEFT, 608, "Hit Dice: 1d12 per barbarian level", BOLD),
        at(LEFT, 596, "Hit Points at 1st Level: 12 + your Constitution", BOLD),
        at(66.6, 584, "modifier"),
        at(LEFT, 560, "Proficiencies", TITLE),
        at(LEFT, 548, "Armor: Light armor, medium armor, shields", BOLD),
        at(LEFT, 520, "Equipment", TITLE),
        at(LEFT, 508, "You start with the following equipment:"),
        at(LEFT, 490, "• (a) a greataxe or (b) any martial melee weapon", SYMBOL),
        at(LEFT, 440, "The Barbarian", TABLE_BOLD),
        at(LEFT, 420, "Level Bonus Features Rages", TABLE_BOLD),
        at(61.7, 400, "1st +2 Rage, 2 Fighting Style", TABLE),
        at(61.1, 380, "2nd +2 Reckless 2", TABLE),
        at(141.1, 369, "Attack,", TABLE),
        at(141.1, 358, "Danger Sense", TABLE),
        at(61.1, 340, "3rd +2 Primal Path 3", TABLE),
        at(61.1, 320, "4th +2 Ability Score 3", TABLE),
        at(141.1, 309, "Improvement", TABLE),
        at(61.1, 290, "5th +3 Aura of Protection 3", TABLE),
        *rows(6, 19),
        at(61.1, 270, "20th +6 Primal Unlimited", TABLE),
        at(411.8, 700, "Champion", TABLE, 9),
        at(RIGHT, 650, "Rage", TITLE, 9),
        at(RIGHT, 630, "In battle, you fight with primal ferocity."),
        at(RIGHT, 618, "• You have advantage on Strength checks", SYMBOL),
        at(337.9, 606, "and saving throws."),
        at(RIGHT, 580, "Fighting Style", TITLE, 9),
        at(RIGHT, 568, "You adopt a particular style of fighting."),
        at(RIGHT, 550, "Archery", TITLE, 9),
        at(RIGHT, 538, "You gain a +2 bonus to attack rolls."),
        at(RIGHT, 510, "Reckless Attack", TITLE, 9),
        at(RIGHT, 498, "Starting at 2nd level, you can throw aside all concern."),
        at(RIGHT, 484, "Danger Sense", TITLE, 9),
        at(RIGHT, 476, "At 2nd level, you notice danger."),
        at(RIGHT, 470, "Primal Path", TITLE, 9),
        at(RIGHT, 458, "At 3rd level, you choose a path."),
        at(RIGHT, 440, "Cantrips", TITLE, 9),  # a sub-heading: part of Primal Path
        at(RIGHT, 428, "You know two cantrips of your choice."),
        at(RIGHT, 400, "Ability Score Improvement", TITLE, 9),
        at(RIGHT, 388, "When you reach 4th level, you can increase one score."),
        at(RIGHT, 366, "Aura of Protection", TITLE, 9),
        at(RIGHT, 354, "Starting at 5th level, you can protect your friends."),
        at(RIGHT, 340, "Protection", TITLE, 9),  # a sub-heading, not a feature of its own
        at(RIGHT, 328, "You can use your reaction to impose disadvantage."),
        at(RIGHT, 320, "Primal Champion", TITLE, 9),
        at(RIGHT, 308, "You embody the power of the wilds."),
        at(LEFT, 700, "Martial Archetypes", TITLE, 10),  # an introduction: left out
        at(LEFT, 688, "Different fighters choose different paths.", BODY, 10),
        at(LEFT, 660, "Path of the Berserker", TITLE, 10),
        at(LEFT, 648, "Some barbarians are berserkers.", BODY, 10),
        at(LEFT, 630, "Frenzy", TITLE, 10),
        at(LEFT, 618, "Starting when you choose this path at 3rd level, you can rage.", BODY, 10),
        at(LEFT, 590, "Mindless Rage", TITLE, 10),
        at(LEFT, 578, "Beginning at 6th level, you can’t be charmed while raging.", BODY, 10),
        at(LEFT, 550, "Breaking Your Oath", TITLE, 10),
        at(LEFT, 538, "A paladin who breaks an oath may atone.", BODY, 10),
    ]  # fmt: skip


def features() -> list[Feature]:
    return classes51.parse_class(barbarian(), "Barbarian", vocab())


class ClassFeatures(unittest.TestCase):
    def test_a_class_feature_has_its_level_from_the_table(self) -> None:
        by_name = {f.name: f for f in features()}
        self.assertEqual(
            [(n, by_name[n].level) for n in by_name if not by_name[n].subclass],
            [
                ("Rage", 1),
                ("Fighting Style", 1),
                ("Reckless Attack", 2),
                ("Danger Sense", 2),
                ("Primal Path", 3),
                ("Ability Score Improvement", 4),
                ("Aura of Protection", 5),
                ("Primal Champion", 20),
            ],
        )

    def test_a_name_the_numbers_or_a_column_split_is_whole_and_a_part_of_a_name_is_not_it(
        self,
    ) -> None:
        names = [f.name for f in features()]
        self.assertIn("Reckless Attack", names)  # "Reckless 2" then "Attack,"
        self.assertIn(
            "Primal Champion", names
        )  # "Primal Unlimited" then "Champion", in the next column
        self.assertNotIn("Protection", names)  # only "Aura of Protection" is in the table

    def test_the_proficiencies_and_the_equipment_are_left_out(self) -> None:
        text = " ".join(f.text for f in features())
        for left_out in (
            "Hit Dice",
            "Armor:",
            "greataxe",
            "following equipment",
            "class features.",
        ):
            self.assertNotIn(left_out, text)

    def test_a_sub_heading_is_part_of_the_feature_above_it(self) -> None:
        by_name = {f.name: f for f in features()}
        self.assertEqual(
            by_name["Primal Path"].text,
            "At 3rd level, you choose a path.\nCantrips\nYou know two cantrips of your choice.",
        )
        self.assertNotIn("Cantrips", by_name)

    def test_an_option_list_is_left_out(self) -> None:
        by_name = {f.name: f for f in features()}
        self.assertEqual(
            by_name["Fighting Style"].text, "You adopt a particular style of fighting."
        )
        self.assertNotIn("Archery", by_name["Fighting Style"].text)

    def test_a_bullet_and_its_wrapped_line_are_one_row(self) -> None:
        rage = {f.name: f for f in features()}["Rage"]
        self.assertEqual(
            rage.text,
            "In battle, you fight with primal ferocity.\n"
            "• You have advantage on Strength checks and saving throws.",
        )

    def test_a_subclass_feature_gets_its_level_from_its_first_sentence(self) -> None:
        subclass = [(f.name, f.level, f.subclass) for f in features() if f.subclass]
        self.assertEqual(
            subclass,
            [
                ("Frenzy", 3, "Path of the Berserker"),
                ("Mindless Rage", 6, "Path of the Berserker"),
            ],
        )

    def test_an_introduction_and_a_flavour_section_are_not_features(self) -> None:
        text = " ".join(f.text for f in features())
        self.assertNotIn("Different fighters", text)  # the subclasses' introduction
        self.assertNotIn("berserkers", text)  # the subclass's own introduction
        self.assertNotIn("atone", text)  # the section after the last subclass feature

    def test_the_page_is_the_headings(self) -> None:
        by_name = {f.name: f for f in features()}
        self.assertEqual((by_name["Rage"].page, by_name["Frenzy"].page), (9, 10))

    def test_a_missing_subclass_stops_the_tool(self) -> None:
        lines = [line for line in barbarian() if line.page != 10]
        with self.assertRaises(SrdError):
            classes51.parse_class(lines, "Barbarian", vocab())

    def test_a_class_without_a_table_stops_the_tool(self) -> None:
        lines = [line for line in barbarian() if not line.first_font.startswith("Calibri")]
        with self.assertRaises(SrdError):
            classes51.parse_class(lines, "Barbarian", vocab())

    def test_levels_that_go_backwards_stop_the_tool(self) -> None:
        lines = barbarian()
        for line in lines:
            if line.text.startswith("20th"):
                line.pieces[0] = Piece(
                    line.x, line.y, line.pieces[0].font, "1st +6 Primal Unlimited"
                )
        # Primal Champion is then at level 1, after Primal Path at 3
        with self.assertRaises(SrdError):
            classes51.parse_class(lines, "Barbarian", vocab())

    def test_a_subclass_heading_with_no_level_that_is_not_a_known_introduction_stops_the_tool(
        self,
    ) -> None:
        lines = barbarian()
        lines += [
            at(LEFT, 520, "Mystery Section", TITLE, 10),
            at(LEFT, 508, "Nobody knows what this is.", BODY, 10),
        ]
        # inserted before the subclass's first feature, so it would be lost without a word
        at_subclass = next(i for i, ln in enumerate(lines) if ln.text == "Path of the Berserker")
        lines.insert(at_subclass + 1, lines.pop())
        lines.insert(at_subclass + 1, lines.pop())
        with self.assertRaises(SrdError) as raised:
            classes51.parse_class(lines, "Barbarian", vocab())
        self.assertIn("Mystery Section", str(raised.exception))

    def test_a_name_in_the_table_with_no_heading_stops_the_tool(self) -> None:
        lines = [ln for ln in barbarian() if ln.text != "Danger Sense" or ln.first_font != TITLE]
        with self.assertRaises(SrdError) as raised:
            classes51.parse_class(lines, "Barbarian", vocab())
        self.assertIn("danger sense", str(raised.exception))

    def test_a_heading_that_comes_twice_stops_the_tool(self) -> None:
        lines = barbarian()
        lines += [
            at(RIGHT, 300, "Rage", TITLE, 9),
            at(RIGHT, 288, "A second Rage, which is a mistake."),
        ]
        at_subclass = next(i for i, ln in enumerate(lines) if ln.text == "Martial Archetypes")
        lines[at_subclass:at_subclass] = lines[-2:]
        with self.assertRaises(SrdError):
            classes51.parse_class(lines[:-2], "Barbarian", vocab())

    def test_a_table_that_does_not_start_with_the_20_levels_stops_the_tool(self) -> None:
        lines = [ln for ln in barbarian() if not ln.text.startswith("5th")]
        with self.assertRaises(SrdError):
            classes51.parse_class(lines, "Barbarian", vocab())

    def test_the_subclass_heading_twice_stops_the_tool(self) -> None:
        lines = barbarian()
        lines += [at(LEFT, 300, "Path of the Berserker", TITLE, 10)]
        with self.assertRaises(SrdError):
            classes51.parse_class(lines, "Barbarian", vocab())

    def test_a_list_of_options_after_the_last_feature_is_left_out_by_name(self) -> None:
        lines = barbarian()
        lines += [
            at(RIGHT, 300, "Fighting Style", TITLE, 9),
            at(RIGHT, 288, "A second listing of the options."),
        ]
        at_subclass = next(i for i, ln in enumerate(lines) if ln.text == "Martial Archetypes")
        lines[at_subclass:at_subclass] = lines[-2:]
        text = " ".join(f.text for f in classes51.parse_class(lines[:-2], "Barbarian", vocab()))
        self.assertNotIn("second listing", text)


class TheTable(unittest.TestCase):
    def test_a_wide_table_printed_in_two_pieces_joins_each_row_to_the_cell_at_its_height(
        self,
    ) -> None:
        # The Monk's: the first columns, then the Features cell of each row further along.
        lines = [
            at(61.7, 472, "1st +2 1d4 — — Unarmored", TABLE, 26),
            at(61.1, 461, "2nd +2 1d4 2 +10", TABLE, 26),
            at(61.1, 450, "3rd +2 1d4 3 +10", TABLE, 26),
            at(403.4, 472, "Defense, Martial Arts", TABLE, 26),
            at(309.0, 461, "ft. Ki, Unarmored Movement", TABLE, 26),
            at(309.0, 450, "ft. Monastic Tradition, Deflect Missiles", TABLE, 26),
        ]
        rows = classes51.table_levels(lines)
        self.assertEqual(classes51.level_in("Unarmored Defense", rows), 1)
        self.assertEqual(classes51.level_in("Martial Arts", rows), 1)
        self.assertEqual(classes51.level_in("Ki", rows), 2)
        self.assertEqual(classes51.level_in("Deflect Missiles", rows), 3)
        self.assertIsNone(classes51.level_in("Slow Fall", rows))

    def test_a_name_must_start_the_item_so_a_part_of_a_longer_name_is_not_found(self) -> None:
        rows = classes51.table_levels(
            [at(61.7, 400, "6th +3 Aura of Protection, Channel Divinity (1/rest)", TABLE)]
        )
        self.assertIsNone(classes51.level_in("Protection", rows))
        self.assertEqual(classes51.level_in("Aura of Protection", rows), 6)
        self.assertEqual(classes51.level_in("Channel Divinity", rows), 6)  # the bracket is left out

    def test_the_tables_apostrophe_is_the_headings(self) -> None:
        rows = classes51.table_levels([at(61.7, 400, "1st +2 Thievesʼ Cant", TABLE)])
        self.assertEqual(classes51.level_in("Thieves’ Cant", rows), 1)


class TheWholeChapter(unittest.TestCase):
    def lines(self) -> list[Line]:
        out: list[Line] = []
        for number, klass in enumerate(CLASS_NAMES):
            page = 8 + number
            subclass = classes51.SUBCLASSES[klass]
            out += [
                at(LEFT, 700, klass, TITLE, page),
                at(LEFT, 440, f"The {klass}", TABLE_BOLD, page),
                at(61.7, 400, "1st +2 Core Feature", TABLE, page),
                *rows(2, 20, page, "Core Feature"),
                at(RIGHT, 650, "Core Feature", TITLE, page),
                at(RIGHT, 630, "You can do the core thing.", BODY, page),
                at(LEFT, 600, subclass, TITLE, page + 1),
                at(LEFT, 560, "Wonder", TITLE, page + 1),
                at(LEFT, 548, "At 3rd level, you can do a wonder.", BODY, page + 1),
            ]
        return out

    def test_every_class_is_read_in_order(self) -> None:
        found = classes51.parse_features51(self.lines(), vocab())
        self.assertEqual(len(found), 2 * len(CLASS_NAMES))
        self.assertEqual([f.klass for f in found][::2], list(CLASS_NAMES))

    def test_the_classes_must_be_all_there_in_order(self) -> None:
        without_wizard = [line for line in self.lines() if line.page < 8 + 11]
        with self.assertRaises(SrdError):
            classes51.parse_features51(without_wizard, vocab())

    def test_the_build_writes_the_features_file_in_the_2014_shape(self) -> None:
        pages: list[list[Line]] = [[] for _ in range(7)]
        by_page: dict[int, list[Line]] = {}
        for line in self.lines():
            by_page.setdefault(line.page, []).append(line)
        pages += [by_page[n] for n in sorted(by_page)]
        pages.append([at(LEFT, 700, "Beyond 1st Level", TITLE, 56)])
        pages.append([at(LEFT, 700, "Not part of the classes", TITLE, 57)])
        found = build.class_lines_51(pages)
        self.assertEqual(found[-1].page, by_page[max(by_page)][-1].page)
        self.assertNotIn("Beyond 1st Level", " ".join(line.text for line in found))
        entry = build.feature_entry(classes51.parse_features51(found, vocab())[0])
        self.assertEqual(list(entry), ["name", "class", "level", "subclass", "text", "page"])

    def test_without_the_chapters_end_the_tool_stops(self) -> None:
        pages = [[at(LEFT, 700, "Barbarian", TITLE, 8)]]
        with self.assertRaises(SrdError):
            build.class_lines_51(pages)


if __name__ == "__main__":
    unittest.main()
