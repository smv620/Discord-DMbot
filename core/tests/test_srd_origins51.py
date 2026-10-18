"""Reading the SRD 5.1's races, its Acolyte background and its Grappler feat (#1928): made-up
lines in the shapes the real PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import origins51
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.parse51 import Vocabulary
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, BODY, LEAD, TABLE = "GillSans-SemiBold", "Cambria", "Cambria-BoldItalic", "Calibri"
BOLD, ITALIC, BULLET = "Cambria-Bold", "Cambria-Italic", "Symbol"
LEFT, RIGHT, INDENT = 57.6, 328.6, 9.3


def at(x: float, y: float, text: str, font: str = BODY, page: int = 3) -> Line:
    return Line(x, y, [Piece(x, y, f"ABCDEF+{font}", text)], page)


def races() -> list[Line]:
    """The chapter's shapes: an introduction, a race with a subrace, a race with a table in it,
    a race whose size and speed are said in lower case, and a second column."""
    return [
        at(LEFT, 665, "Races", TITLE),
        at(LEFT, 646, "Racial Traits", TITLE),
        at(LEFT, 630, "Each race has traits."),
        at(RIGHT, 618, "Dwarf", TITLE),
        at(RIGHT, 600, "Dwarf Traits", TITLE),
        at(RIGHT, 582, "Your dwarf character has abilities."),
        at(RIGHT + INDENT, 558, "Ability Score Increase. Your Constitution score", LEAD),
        at(RIGHT, 547, "increases by 2."),
        at(RIGHT + INDENT, 535, "Size. Dwarves are short. Your size is Medium.", LEAD),
        at(RIGHT + INDENT, 523, "Speed. Your base walking speed is 25 feet.", LEAD),
        at(LEFT, 708, "Hill Dwarf", TITLE, 4),
        at(LEFT, 696, "As a hill dwarf, you have keen senses.", BODY, 4),
        at(LEFT + INDENT, 673, "Ability Score Increase. Your Wisdom score", LEAD, 4),
        at(LEFT, 661, "increases by 1.", BODY, 4),
        at(LEFT, 583, "Human", TITLE, 4),
        at(LEFT, 565, "Human Traits", TITLE, 4),
        at(
            LEFT + INDENT,
            523,
            "Size. Humans vary. Whatever your position, your size is Medium.",
            LEAD,
            4,
        ),
        at(LEFT + INDENT, 499, "Speed. Your base walking speed is 30 feet.", LEAD, 4),
        at(RIGHT, 703, "Dragonborn", TITLE, 5),
        at(RIGHT, 685, "Dragonborn Traits", TITLE, 5),
        at(RIGHT + INDENT, 600, "Size. Dragonborn are tall. Your size is Medium.", LEAD, 5),
        at(RIGHT + INDENT, 588, "Speed. Your base walking speed is 30 feet.", LEAD, 5),
        at(RIGHT + INDENT, 576, "Draconic Ancestry. Choose a dragon from the table.", LEAD, 5),
        at(RIGHT, 436, "Draconic Ancestry", TABLE + "-Bold", 5),
        at(RIGHT, 410, "Black Acid 5 by 30 ft. line (Dex. save)", TABLE, 5),
        at(RIGHT + INDENT, 390, "Breath Weapon. You can exhale.", LEAD, 5),
    ]


def vocab(lines: list[Line]) -> Vocabulary:
    return Vocabulary({"dwarf", "human"}, lines)


def found() -> dict[str, origins51.Race]:
    lines = races()
    return {r.name: r for r in origins51.parse_races(lines, vocab(lines))}


class Races(unittest.TestCase):
    def test_every_race_is_found_and_the_introduction_is_not_one(self) -> None:
        self.assertEqual(list(found()), ["Dwarf", "Human", "Dragonborn"])

    def test_size_and_speed_are_read_from_the_traits_in_either_case(self) -> None:
        self.assertEqual((found()["Dwarf"].size, found()["Dwarf"].speed), ("Medium", "25 feet"))
        self.assertEqual((found()["Human"].size, found()["Human"].speed), ("Medium", "30 feet"))

    def test_a_subrace_stays_in_its_races_text_under_its_own_heading(self) -> None:
        text = found()["Dwarf"].text
        self.assertIn("Subrace: Hill Dwarf\nAs a hill dwarf, you have keen senses.", text)
        self.assertIn("Ability Score Increase. Your Wisdom score increases by 1.", text)
        self.assertNotIn("Dwarf Traits", text)  # (the race's own second title is not text)

    def test_each_trait_is_a_paragraph_and_the_race_pages_are_its_own(self) -> None:
        lines = found()["Dwarf"].text.splitlines()
        self.assertEqual(lines[0], "Your dwarf character has abilities.")
        self.assertEqual(
            lines[1], "Ability Score Increase. Your Constitution score increases by 2."
        )
        self.assertEqual((found()["Dwarf"].page, found()["Human"].page), (3, 4))

    def test_a_table_is_left_out_and_ends_the_paragraph(self) -> None:
        text = found()["Dragonborn"].text
        self.assertNotIn("Black Acid", text)
        self.assertEqual(text.splitlines()[-2], "Breath Weapon. You can exhale.")
        self.assertEqual(
            text.splitlines()[-1], "(The Draconic Ancestry table is not included here.)"
        )

    def test_a_race_without_a_size_or_speed_stops_the_tool(self) -> None:
        lines = [ln for ln in races() if "walking speed is 25" not in ln.text]
        with self.assertRaisesRegex(SrdError, "Dwarf.*size or its speed"):
            origins51.parse_races(lines, vocab(lines))

    def test_a_race_that_comes_twice_stops_the_tool(self) -> None:
        lines = [*races(), *races()]
        with self.assertRaisesRegex(SrdError, "comes twice"):
            origins51.parse_races(lines, vocab(lines))

    def test_no_race_at_all_stops_the_tool(self) -> None:
        with self.assertRaisesRegex(SrdError, "no race found"):
            origins51.parse_races([at(LEFT, 700, "Races", TITLE)], vocab([]))


def background() -> list[Line]:
    return [
        at(RIGHT, 209, "Acolyte", TITLE, 60),
        at(RIGHT, 190, "You have spent your life in a temple.", BODY, 60),
        at(LEFT, 564, "Skill Proficiencies: Insight, Religion", BOLD, 61),
        at(LEFT, 552, "Languages: Two of your choice", BOLD, 61),
        at(LEFT, 540, "Equipment: A holy symbol (a gift to you when you", BOLD, 61),
        at(66.6, 528, "entered the priesthood), and a pouch", BODY, 61),
        at(LEFT, 482, "Feature: Shelter of the Faithful", TITLE, 61),
        at(LEFT, 470, "You command the respect of those who share your faith.", BODY, 61),
        at(LEFT, 248, "Suggested Characteristics", TITLE, 61),
        at(LEFT, 235, "Acolytes are shaped by temples.", BODY, 61),
    ]


class Background(unittest.TestCase):
    def read(self, lines: list[Line]) -> origins51.Background:
        end = next(i for i, ln in enumerate(lines) if ln.text == "Suggested Characteristics")
        return origins51.parse_background(lines[:end], vocab(lines))

    def test_the_acolytes_lines_and_feature(self) -> None:
        acolyte = self.read(background())
        self.assertEqual(acolyte.name, "Acolyte")
        self.assertEqual(
            (acolyte.skills, acolyte.languages), ("Insight, Religion", "Two of your choice")
        )
        self.assertEqual(
            acolyte.equipment,
            "A holy symbol (a gift to you when you entered the priesthood), and a pouch",
        )
        self.assertEqual(
            acolyte.text.splitlines()[-2:],
            [
                "Feature: Shelter of the Faithful",
                "You command the respect of those who share your faith.",
            ],
        )

    def test_the_labelled_lines_come_first_as_in_the_5_2_1s_backgrounds(self) -> None:
        rows = self.read(background()).text.splitlines()
        self.assertEqual([r.partition(":")[0] for r in rows[:3]], list(origins51.LABELS))
        self.assertTrue(rows[3].startswith("You have spent"))

    def test_a_missing_labelled_line_stops_the_tool(self) -> None:
        lines = [ln for ln in background() if not ln.text.startswith("Languages")]
        with self.assertRaisesRegex(SrdError, "Languages"):
            self.read(lines)

    def test_no_feature_stops_the_tool(self) -> None:
        lines = [ln for ln in background() if "Feature" not in ln.text and "respect" not in ln.text]
        with self.assertRaisesRegex(SrdError, "no Feature"):
            self.read(lines)


def feat() -> list[Line]:
    return [
        at(LEFT, 470, "Grappler", TITLE, 75),
        at(LEFT, 452, "Prerequisite: Strength 13 or higher", ITALIC, 75),
        at(LEFT, 434, "You have developed skills. You gain the following benefits:", BODY, 75),
        at(LEFT, 392, "• You have advantage on attack rolls against a", BULLET, 75),
        at(66.9, 380, "creature you are grappling.", BODY, 75),
    ]


class Feat(unittest.TestCase):
    def test_the_prerequisite_and_the_words(self) -> None:
        lines = feat()
        grappler = origins51.parse_feat(lines, vocab(lines))
        self.assertEqual(
            (grappler.name, grappler.prerequisite), ("Grappler", "Strength 13 or higher")
        )
        self.assertEqual(
            grappler.text.splitlines()[1],
            "• You have advantage on attack rolls against a creature you are grappling.",
        )

    def test_a_feat_without_a_prerequisite_line_stops_the_tool(self) -> None:
        lines = [feat()[0], *feat()[2:]]
        with self.assertRaisesRegex(SrdError, "no Prerequisite"):
            origins51.parse_feat(lines, vocab(lines))


class Building(unittest.TestCase):
    """The glue in `build`: where each chapter's pages are found, and the files' shapes."""

    def pages(self) -> list[list[Line]]:
        feat_page = [at(LEFT, 695, "Feats", TITLE, 75), *feat()]
        return [
            [], [],
            [ln for ln in races() if ln.page == 3],
            [ln for ln in races() if ln.page == 4],
            [ln for ln in races() if ln.page == 5],
            [at(LEFT, 700, "Barbarian", TITLE, 8)],
            [at(LEFT, 700, "Backgrounds", TITLE, 60), *background()],
            [],
            feat_page,
        ]  # fmt: skip

    def test_the_races_background_and_feat_files_have_the_5_2_1s_shapes(self) -> None:
        from dmbot.devtools.srd import build

        pages = self.pages()
        everything = [ln for page in pages for ln in page]

        def header(kind: str, section: str | None) -> dict[str, object]:
            return {"source": "SRD 5.1", "edition": "2014", "kind": kind, "section": section}

        files = build.origin_files_51(pages, header, vocab(everything))
        entries = files["origins.json"]["entries"]
        self.assertEqual(
            [(e["kind"], e["name"]) for e in entries],
            [("species", "Dwarf"), ("species", "Human"), ("species", "Dragonborn"),
             ("background", "Acolyte")],
        )  # fmt: skip
        dwarf, acolyte = entries[0], entries[-1]
        self.assertEqual(
            (dwarf["creature_type"], dwarf["size"], dwarf["speed"], dwarf["section"]),
            ("", "Medium", "25 feet", "Races"),
        )
        self.assertEqual(
            (acolyte["ability_scores"], acolyte["feat"], acolyte["tool"], acolyte["section"]),
            ("", "", "", "Backgrounds"),
        )
        (grappler,) = files["feats.json"]["entries"]
        self.assertEqual(
            (grappler["name"], grappler["category"], grappler["prerequisite"]),
            ("Grappler", "", "Strength 13 or higher"),
        )
        self.assertEqual(files["feats.json"]["section"], "Feats")
