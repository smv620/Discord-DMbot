"""Reading the SRD's monsters (#900): both readers on made-up lines in the shapes the two
PDFs have (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from dmbot.devtools.srd import build, monsters, monsters51, parse51
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.pdf import Line, Piece
from tests.monster_fixtures import BOLD, PLAIN, TITLE, goblin, say


class Newer(unittest.TestCase):
    def test_a_stat_block_is_read(self) -> None:
        (m,) = monsters.parse_monsters(goblin())
        self.assertEqual((m.name, m.section, m.page), ("Goblin Warrior", "Monsters A–Z", 290))
        self.assertEqual(
            (m.size, m.type, m.alignment), ("Small", "Fey (Goblinoid)", "Chaotic Neutral")
        )
        self.assertEqual((m.ac, m.initiative, m.hp, m.hit_dice), (15, "+2 (12)", 10, "3d6"))
        self.assertEqual(m.speed, "30 ft.")
        self.assertEqual(m.abilities[0], ("str", 8, -1))
        self.assertEqual(m.abilities[5], ("cha", 8, -1))
        self.assertEqual(m.saves, "Dex +4")  # only the one that is not the modifier
        self.assertEqual(m.gear, "Leather Armor, Scimitar, Shield, Shortbow")  # a wrapped value
        self.assertEqual((m.cr, m.xp, m.pb), ("1/4", 50, 2))
        self.assertEqual(m.challenge, "1/4 (XP 50; PB +2)")

    def test_the_traits_and_actions_are_a_paragraph_each(self) -> None:
        (m,) = monsters.parse_monsters(goblin())
        self.assertEqual(
            m.text.split("\n"),
            [
                "Traits",
                "Keen Sight. The goblin sees well at long range, even in the dark.",
                "Actions",
                "Scimitar. Melee Attack Roll: +4, reach 5 ft. Hit: 5 (1d6 + 2) Slashing damage, "
                "plus 2 (1d4) if the attack roll had Advantage.",
                # a bold name still inside its brackets carries on; a spell list line is its own
                "Spellcasting (At Will or 1/Day). The goblin casts a spell.",
                "At Will: Light",
            ],
        )

    def test_a_group_name_in_front_of_the_first_creature_is_not_an_entry(self) -> None:
        lines = [say("Goblins", TITLE), *goblin()]
        (m,) = monsters.parse_monsters(lines)
        self.assertEqual(m.name, "Goblin Warrior")
        other = [*goblin(), say("Hobgoblins", TITLE)]  # the next group's name at the end
        self.assertNotIn("Hobgoblins", monsters.parse_monsters(other)[0].text)

    def test_the_animals_section_is_told_by_its_heading(self) -> None:
        wolf = [say("Wolf", TITLE), *goblin()[1:]]
        wolf[0] = say("Wolf", TITLE)
        found = monsters.parse_monsters([*goblin(), say("Animals", TITLE), *wolf])
        self.assertEqual([m.section for m in found], ["Monsters A–Z", "Animals"])

    def test_gaps_in_numbers_are_closed(self) -> None:
        lines = goblin()
        lines[13] = say("CR 10 (XP 5,900, or 7 ,200 in lair; PB +4)", BOLD)
        lines[19] = say("age, plus 2 (1d4) if the roll was +7 , reach 5 ft.", PLAIN)
        (m,) = monsters.parse_monsters(lines)
        self.assertEqual(m.challenge, "10 (XP 5,900, or 7,200 in lair; PB +4)")
        self.assertEqual((m.xp, m.pb), (5900, 4))
        self.assertIn("+7, reach 5 ft.", m.text)

    def test_a_block_the_tool_cannot_read_stops_it(self) -> None:
        lines = goblin()
        del lines[3]  # no HP line
        with self.assertRaises(SrdError):
            monsters.parse_monsters(lines)
        bad = goblin()
        bad[13] = say("CR one hundred", BOLD)
        with self.assertRaises(SrdError):
            monsters.parse_monsters(bad)
        odd = goblin()
        odd.insert(4, say("Oddity something", "Optima-Regular"))  # a line that is nobody's
        with self.assertRaisesRegex(SrdError, "can't place the line"):
            monsters.parse_monsters(odd)
        no_table = goblin()
        del no_table[6:8]  # no ability table
        with self.assertRaises(SrdError):
            monsters.parse_monsters(no_table)

    def test_the_saving_throws_are_those_that_differ_from_the_modifier(self) -> None:
        abilities = {
            "str": (10, 0, 0), "dex": (14, 2, 5), "con": (10, 0, 0),
            "int": (8, -1, -1), "wis": (8, -1, 1), "cha": (10, 0, 0),
        }  # fmt: skip
        self.assertEqual(monsters._saves(abilities), "Dex +5, Wis +1")
        abilities["wis"] = (8, -1, -2)
        self.assertEqual(monsters._saves(abilities), "Dex +5, Wis −2")


# ---- the 5.1 -------------------------------------------------------------------------

C_BOLD, C_ITALIC, C_LEAD, C_BODY = "Calibri-Bold", "Calibri-Italic", "Calibri-BoldItalic", "Calibri"


def c(text: str, font: str, *, y: float = 500.0, page: int = 315) -> Line:
    return Line(57.6, y, [Piece(57.6, y, f"ABCDEF+{font}", text)], page)


def goblin51() -> list[Line]:
    return [
        c("Goblin   ", C_BOLD, y=607.9),
        c("Small  humanoid  (goblinoid),  neutral  evil   ", C_ITALIC, y=592.3),
        c("Armor  Class  15  (leather  armor,  shield)   ", C_BOLD, y=574.1),
        c("Hit  Points   7  (2d6)   ", C_BOLD, y=558.7),
        c("Speed  30  ft.   ", C_BOLD, y=543.6),
        c("STR    DEX    CON    INT    WIS    CHA   ", C_BOLD, y=523.4),
        c("8  (−1)    14  (+2)    10  (+0)    10  (+0)    8  (−1)    8  (−1)   ", C_BODY, y=508.1),
        c("Saving  Throws   Dex  +4   ", C_BOLD, y=500.0),
        c("Skills   Stealth  +6   ", C_BOLD, y=487.9),
        c("Senses   darkvision  60  ft.,  passive  Perception  9   ", C_BOLD, y=472.8),
        c("Languages   Common,  Goblin   ", C_BOLD, y=457.4),
        c("Challenge   1/4  (50  XP)   ", C_BOLD, y=442.3),
        c("Nimble  Escape.   The  goblin  can  take  the  Disengage  or  ", C_LEAD, y=411.8),
        c("Hide  action  as  a  bonus  action.   ", C_BODY, y=399.8),
        c("Actions   ", C_BOLD, y=376.6),
        c("Scimitar.  Melee  Weapon  Attack:  +4  to  hit.   ", C_LEAD, y=360.0),
        c("Hit:  5  (1d6  +  2)  slashing  damage.   ", C_BODY, y=348.0),
        c("At  will:  light   ", C_BODY, y=332.0),  # a gap of a paragraph's size: its own row
    ]


def labelled51(label: str, value: str, y: float) -> Line:
    """A spell's labelled line in the 5.1's shape: the label in bold, then the value."""
    bold, plain = Piece(57.6, y, "X+Cambria-Bold", label), Piece(120.0, y, "X+Cambria", value)
    return Line(57.6, y, [bold, plain], 11)


def fireball51() -> list[Line]:
    """A spell in the 5.1's shape, so the pages have a spells section to read."""
    return [
        c("Fireball", "GillSans-SemiBold", y=700.0),
        c("3rd-level evocation", "Cambria-Italic", y=690.0),
        labelled51("Casting Time:", " 1 action", 680.0),
        labelled51("Range:", " 150 feet", 670.0),
        labelled51("Components:", " V, S", 660.0),
        labelled51("Duration:", " Instantaneous", 650.0),
        c("A bright streak flashes.", "Cambria", y=640.0),
    ]


def words51() -> parse51.Vocabulary:
    return parse51.Vocabulary(
        [
            "the",
            "goblin",
            "can",
            "take",
            "disengage",
            "hide",
            "action",
            "as",
            "a",
            "bonus",
            "small",
        ],
        [],
    )


SECTIONS = [(261, "Monsters")]


class Older(unittest.TestCase):
    def test_a_stat_block_is_read(self) -> None:
        (m,) = monsters51.parse_monsters(goblin51(), SECTIONS, words51())
        self.assertEqual((m.name, m.section, m.page), ("Goblin", "Monsters", 315))
        self.assertEqual(
            (m.size, m.type, m.alignment), ("Small", "humanoid (goblinoid)", "neutral evil")
        )
        self.assertEqual(
            (m.ac, m.ac_note, m.hp, m.hit_dice), (15, "(leather armor, shield)", 7, "2d6")
        )
        self.assertEqual(m.abilities[1], ("dex", 14, 2))
        self.assertEqual(m.saves, "Dex +4")
        self.assertEqual(
            (m.skills, m.senses), ("Stealth +6", "darkvision 60 ft., passive Perception 9")
        )
        self.assertEqual((m.cr, m.xp, m.pb, m.challenge), ("1/4", 50, 0, "1/4 (50 XP)"))
        self.assertEqual(
            m.text.split("\n"),
            [
                "Nimble Escape. The goblin can take the Disengage or Hide action as a bonus "
                "action.",
                "Actions",
                "Scimitar. Melee Weapon Attack: +4 to hit. Hit: 5 (1d6 + 2) slashing damage.",
                "At will: light",
            ],
        )

    def test_labels_with_stray_spaces_or_a_dropped_plural_are_read(self) -> None:
        lines = goblin51()
        lines[2] = c("Armo  r  Class  15  (leather  armor,  shield)   ", C_BOLD, y=574.1)
        lines[7] = c("Damage  Resistance  fire   ", C_BOLD, y=500.0)  # the "s" is lost
        lines[11] = c("Chall  enge   1/4  (50  XP)   ", C_BOLD, y=442.3)
        (m,) = monsters51.parse_monsters(lines, SECTIONS, words51())
        self.assertEqual((m.ac, m.resistances, m.cr), (15, "fire", "1/4"))

    def test_a_type_line_over_two_lines_and_a_cut_size_word(self) -> None:
        lines = goblin51()
        lines[1:2] = [
            c("S  mall  humanoid  (goblinoid),  neutral", C_ITALIC),
            c("evil   ", C_ITALIC),
        ]
        (m,) = monsters51.parse_monsters(lines, SECTIONS, words51())
        self.assertEqual(
            (m.size, m.type, m.alignment), ("Small", "humanoid (goblinoid)", "neutral evil")
        )

    def test_a_label_in_the_middle_of_a_line_splits_it(self) -> None:
        lines = goblin51()
        lines[9] = Line(  # replaces the Senses line: the end of the skills, then the label
            57.6,
            480.0,
            [
                Piece(57.6, 480.0, "X+Calibri", "and  Perception  +2  "),
                Piece(120.0, 487.9, "X+Calibri-Bold", "Senses  "),
                Piece(160.0, 487.9, "X+Calibri", "passive  Perception  9"),
            ],
            315,
        )
        (m,) = monsters51.parse_monsters(lines, SECTIONS, words51())
        self.assertEqual(
            (m.skills, m.senses), ("Stealth +6 and Perception +2", "passive Perception 9")
        )

    def test_the_books_prose_and_the_next_groups_name_end_the_block(self) -> None:
        lines = [
            *goblin51(),
            c("Goblins are petty.", "Cambria", y=300.0),
            c("Golems", "GillSans-SemiBold", y=290.0),
        ]
        (m,) = monsters51.parse_monsters(lines, SECTIONS, words51())
        self.assertNotIn("petty", m.text)
        self.assertNotIn("Golems", m.text)
        groups = [*goblin51(), c("Golems", "GillSans-SemiBold", y=290.0)]
        self.assertNotIn("Golems", monsters51.parse_monsters(groups, SECTIONS, words51())[0].text)

    def test_a_creature_belongs_to_the_last_section_before_its_page(self) -> None:
        later = [Line(x.x, x.y, x.pieces, 370) for x in goblin51()]
        found = monsters51.parse_monsters(
            [*goblin51(), *later], [(261, "Monsters"), (366, "Appendix MM-A")], words51()
        )
        self.assertEqual([m.section for m in found], ["Monsters", "Appendix MM-A"])

    def test_split_numbers_are_put_back(self) -> None:
        lines = goblin51()
        lines[3] = c("Hit  Points  136  (17d10  +  8  5)   ", C_BOLD, y=558.7)
        lines[11] = c("Challenge   8  (3,  900  XP)   ", C_BOLD, y=442.3)
        (m,) = monsters51.parse_monsters(lines, SECTIONS, words51())
        self.assertEqual((m.hit_dice, m.xp, m.challenge), ("17d10 + 85", 3900, "8 (3,900 XP)"))

    def test_a_block_the_tool_cannot_read_stops_it(self) -> None:
        lines = goblin51()
        del lines[3]  # no hit points
        with self.assertRaises(SrdError):
            monsters51.parse_monsters(lines, SECTIONS, words51())


# ---- the data files --------------------------------------------------------------------


def on_page(lines: list[Line], page: int) -> list[Line]:
    return [Line(x.x, x.y, x.pieces, page) for x in lines]


class Building(unittest.TestCase):
    def pages51(self) -> list[list[Line]]:
        title = "GillSans-SemiBold"
        contents = [c("Spell Descriptions", "Cambria", page=1)]  # not a heading
        spells = [c("Spell Descriptions", title, page=11), *on_page(fireball51(), 11)]
        conditions = [c("Appendix PH-A:", title, page=14)]
        for name in build.CONDITIONS_51:
            conditions += [c(name, title, page=14), c("• It can’t do much.", "Cambria", page=14)]
        return [
            contents,
            *[[]] * 9,
            spells,
            [c("Traps", title, page=12)],
            [c("Monsters (A)", title, page=13), *on_page(goblin51(), 13)],
            conditions,
            [c("Appendix PH-B:", title, page=15)],
            [c("Appendix MM-A:", title, page=16), *on_page(goblin51(), 16)],
            [c("Appendix MM-B:", title, page=17), *on_page(goblin51(), 17)],
        ]

    def test_the_5_1_monsters_are_found_between_their_headings(self) -> None:
        document = {"title": "t", "url": "u", "sha256": "s", "pages": 17}
        files = build.build_files_51(
            self.pages51(), document, set(), (document | {"word_list_sha256": "w"}, set(), set())
        )
        self.assertEqual(set(files), {"spells.json", "conditions.json", "monsters.json"})
        creatures = files["monsters.json"]
        self.assertEqual(creatures["document"]["word_list_sha256"], "w")
        self.assertEqual(
            [(e["section"], e["page"]) for e in creatures["entries"]],
            [
                ("Monsters", 13),
                ("Appendix MM-A: Miscellaneous Creatures", 16),
                ("Appendix MM-B: Nonplayer Characters", 17),
            ],
        )
        self.assertEqual(creatures["entries"][0]["ac_note"], "(leather armor, shield)")

    def test_without_monster_input_the_5_1_files_are_the_two_old_ones(self) -> None:
        files = build.build_files_51(self.pages51(), {"pages": 17}, set())
        self.assertEqual(set(files), {"spells.json", "conditions.json"})

    def test_the_monster_entry_has_the_fields_in_order(self) -> None:
        (m,) = monsters.parse_monsters(goblin())
        entry = build.monster_entry(m, build.EDITION)
        self.assertEqual(list(entry)[:4], ["name", "section", "page", "size"])
        self.assertEqual(list(entry)[-4:], ["initiative", "gear", "pb", "text"])
        self.assertEqual((entry["str"], entry["dex"], entry["saves"]), (8, 15, "Dex +4"))
        older = build.monster_entry(
            monsters51.parse_monsters(goblin51(), SECTIONS, words51())[0], build.EDITION_51
        )
        self.assertIn("ac_note", older)
        self.assertIn("condition_immunities", older)
        self.assertNotIn("initiative", older)

    def test_the_5_1_monsters_need_the_built_5_2_1_data(self) -> None:
        nowhere = Path("/nonexistent-srd52-folder")
        with mock.patch.object(build, "OUT", nowhere), self.assertRaises(SrdError):
            build.known_words(monsters_too=True)


if __name__ == "__main__":
    unittest.main()
