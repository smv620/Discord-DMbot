"""Reading the SRD 5.2.1 into spells and conditions (#866): the parser on made-up lines
in the shapes the real PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.devtools.srd import build, parse
from dmbot.devtools.srd.pdf import COLUMN_SPLIT, FOOTER_Y, Line, Piece, lines_of

TITLE, ITALIC, BODY = "GillSans-SemiBold", "Cambria-Italic", "Cambria"
BOLD_LEAD = "Cambria-BoldItalic"


def piece(text: str, font: str = BODY, x: float = 63.0, y: float = 500.0) -> Piece:
    return Piece(x, y, f"ABCDEF+{font}", text)  # the PDF puts a random prefix on fonts


def line(*pieces: Piece, page: int = 107) -> Line:
    return Line(pieces[0].x, pieces[0].y, list(pieces), page)


def say(text: str, font: str = BODY, page: int = 107) -> Line:
    return line(piece(text, font), page=page)


def labelled(label: str, value: str, font: str = TITLE) -> Line:
    return line(piece(label + ":", font), piece(" " + value, "GillSans"))


def header(
    name: str,
    kind: str = "Level 3 Evocation (Sorcerer, Wizard)",
    *,
    cast: str = "Action",
    range_: str = "150 feet",
    components: str = "V, S",
    duration: str = "Instantaneous",
) -> list[Line]:
    return [
        say(name, TITLE),
        say(kind, ITALIC),
        labelled("Casting Time", cast),
        labelled("Range", range_),
        labelled("Components", components),
        labelled("Duration", duration),
    ]


class Fireball(unittest.TestCase):
    def lines(self) -> list[Line]:
        return [
            *header("Fireball", components="V, S, M (a ball of bat guano)"),
            say("A bright streak flashes from you to a point you "),
            say("choose within range. "),
            say(" Flammable objects start burning."),
            line(
                piece(" "), piece("Using a Higher-Level Spell Slot.", BOLD_LEAD), piece(" It grows")
            ),
            say("by 1d6."),
        ]

    def test_a_spell_is_read_whole(self) -> None:
        (spell,) = parse.parse_spells(self.lines())
        self.assertEqual(
            (spell.name, spell.level, spell.school, spell.classes, spell.page),
            ("Fireball", 3, "Evocation", ("Sorcerer", "Wizard"), 107),
        )
        self.assertEqual(
            (spell.casting_time, spell.range, spell.components, spell.duration),
            ("Action", "150 feet", "V, S, M (a ball of bat guano)", "Instantaneous"),
        )

    def test_paragraphs_start_at_an_indent_and_at_a_bold_lead_in(self) -> None:
        (spell,) = parse.parse_spells(self.lines())
        self.assertEqual(
            spell.text,
            "A bright streak flashes from you to a point you choose within range.\n"
            "Flammable objects start burning.\n"
            "Using a Higher-Level Spell Slot. It grows by 1d6.",
        )

    def test_a_cantrip_has_level_zero(self) -> None:
        lines = [
            *header("Fire Bolt", "Evocation Cantrip (Sorcerer, Wizard)"),
            say("You hurl fire."),
        ]
        (spell,) = parse.parse_spells(lines)
        self.assertEqual((spell.level, spell.school), (0, "Evocation"))

    def test_a_long_list_of_classes_wraps(self) -> None:
        lines = [
            say("Aid", TITLE),
            say("Level 2 Abjuration (Bard, Cleric, Druid, Paladin, ", ITALIC),
            say("Ranger) ", ITALIC),
            labelled("Casting Time", "Action"),
            labelled("Range", "30 feet"),
            labelled("Components", "V, S"),
            labelled("Duration", "8 hours"),
            say("Choose up to three creatures."),
        ]
        (spell,) = parse.parse_spells(lines)
        self.assertEqual(spell.classes, ("Bard", "Cleric", "Druid", "Paladin", "Ranger"))

    def test_a_value_that_wraps_stays_one_value(self) -> None:
        lines = [
            *header("Clone", "Level 8 Necromancy (Wizard)")[:4],
            labelled("Components", "V, S, M (a diamond worth 1,000+ GP and"),
            say("a sealable vessel)", "GillSans"),
            labelled("Duration", "Instantaneous"),
            say("This spell grows an inert duplicate."),
        ]
        (spell,) = parse.parse_spells(lines)
        self.assertEqual(
            spell.components, "V, S, M (a diamond worth 1,000+ GP and a sealable vessel)"
        )
        self.assertEqual(spell.text, "This spell grows an inert duplicate.")

    def test_two_spells_end_where_the_next_title_begins(self) -> None:
        alter = header("Alter Self", "Level 2 Transmutation (Sorcerer, Wizard)")
        lines = [
            *header("Alarm", "Level 1 Abjuration (Ranger, Wizard)"),
            say("You set an alarm."),
            *alter,
            say("You change."),
        ]
        first, second = parse.parse_spells(lines)
        self.assertEqual((first.name, first.text), ("Alarm", "You set an alarm."))
        self.assertEqual((second.name, second.text), ("Alter Self", "You change."))

    def test_the_documents_own_slips_are_read_as_meant(self) -> None:
        # Barkskin says "Component:"; some labels are set in the body's font; one title is
        # in small capitals and comes out as "Acid SplASh".
        lines = [
            say("Barkskin", TITLE),
            say("Level 2 Transmutation (Druid, Ranger)", ITALIC),
            labelled("Casting Time", "Bonus Action"),
            labelled("Range", "Touch"),
            labelled("Component", "V, S, M (a handful of bark)"),
            labelled("Duration", "1 hour"),
            say("Your skin turns to bark."),
            line(piece("Acid SplASh", "GillSans-SemiBold-SC700")),
            say("Evocation Cantrip (Sorcerer, Wizard)", ITALIC),
            labelled("Casting Time", "Action", BODY + "-Bold"),
            labelled("Range", "60 feet", BODY + "-Bold"),
            labelled("Components", "V, S", BODY + "-Bold"),
            labelled("Duration", "Instantaneous", BODY + "-Bold"),
            say("You create an acidic bubble."),
        ]
        bark, splash = parse.parse_spells(lines)
        self.assertEqual(bark.components, "V, S, M (a handful of bark)")
        self.assertEqual(splash.name, "Acid Splash")
        self.assertEqual(splash.duration, "Instantaneous")
        self.assertEqual(splash.text, "You create an acidic bubble.")

    def test_something_unexpected_stops_with_where(self) -> None:
        lines = [say("Wish", TITLE), say("Level 9 Conjuration (Sorcerer, Wizard)", ITALIC),
                 labelled("Casting Time", "Action"), say("You speak.")]  # fmt: skip
        with self.assertRaisesRegex(parse.SrdError, r"'Wish' on page 107: no Range, Components"):
            parse.parse_spells(lines)
        with self.assertRaisesRegex(parse.SrdError, "no description"):
            parse.parse_spells(header("Wish", "Level 9 Conjuration (Sorcerer, Wizard)"))


class CutWords(unittest.TestCase):
    """The PDF draws a hyphen that cuts a word like one that belongs to it."""

    def spell(self, *body: str) -> str:
        lines = [*header("Test"), *[say(b) for b in body]]
        # The document's own words: so the joined forms exist somewhere.
        lines += [say("creature and damage and Hit Points and twenty-five and shape-shifts.")]
        (spell,) = parse.parse_spells(lines)
        return spell.text

    def test_a_cut_word_is_joined_with_or_without_a_space_before_the_hyphen(self) -> None:
        text = self.spell("The crea-", "ture takes dam -", "age.")
        self.assertTrue(text.startswith("The creature takes damage."))

    def test_a_word_that_belongs_with_a_hyphen_keeps_it(self) -> None:
        text = self.spell("It lasts twenty-", "five minutes, as it shape-", "shifts.")
        self.assertTrue(text.startswith("It lasts twenty-five minutes, as it shape-shifts."))

    def test_an_unknown_word_is_joined_because_cut_ones_are_commoner(self) -> None:
        text = self.spell("The zebra-", "fish swims.")
        self.assertTrue(text.startswith("The zebrafish swims."))

    def test_a_table_row_that_wraps_is_joined_too(self) -> None:
        lines = [
            *header("Confusion", "Level 4 Enchantment (Bard)"),
            say("Roll the die."),
            say("1d10 Behavior for the Turn", "GillSans"),
            say("1 Roll 1d4 for the direc -", "GillSans"),
            say("tion: 1, north.", "GillSans"),
            say("The direction is a word in the document."),
        ]
        (spell,) = parse.parse_spells(lines)
        self.assertIn("1 Roll 1d4 for the direction: 1, north.", spell.text.splitlines())
        self.assertIn("1d10 Behavior for the Turn", spell.text.splitlines())


class Conditions(unittest.TestCase):
    def lines(self) -> list[Line]:
        return [
            say("Blindsight", TITLE, 177),
            say("You see without sight.", BODY, 177),
            say("Blinded [Condition]", TITLE, 177),
            say(
                "While you have the Blinded condition, you experience the following effects.",
                BODY,
                177,
            ),
            line(piece(" "), piece("Can’t See.", BOLD_LEAD), piece(" You can’t see."), page=177),
            say("Bloodied", TITLE, 177),
            say("Half its Hit Points.", BODY, 177),
            say("Charmed [Condition]", TITLE, 178),
            say("It can’t attack the charmer.", BODY, 178),
        ]

    def test_only_entries_tagged_condition_are_taken(self) -> None:
        blinded, charmed = parse.parse_conditions(self.lines())
        self.assertEqual((blinded.name, blinded.page), ("Blinded", 177))
        self.assertEqual(
            blinded.text,
            "While you have the Blinded condition, you experience the following effects.\n"
            "Can’t See. You can’t see.",
        )
        self.assertEqual((charmed.name, charmed.text), ("Charmed", "It can’t attack the charmer."))

    def test_an_entry_with_no_words_stops_the_tool(self) -> None:
        with self.assertRaisesRegex(parse.SrdError, "'Blinded' on page 177: no description"):
            parse.parse_conditions([say("Blinded [Condition]", TITLE, 177), say("Bloodied", TITLE)])


class Reading(unittest.TestCase):
    def test_columns_come_left_first_then_right_top_down(self) -> None:
        pieces = [
            Piece(314.0, 700.0, "F", "right top\n"),
            Piece(63.0, 650.0, "F", "left lower\n"),
            Piece(63.0, 700.0, "F", "left top\n"),
            Piece(314.0, 650.0, "F", "right lower\n"),
        ]
        self.assertEqual(
            [ln.text for ln in lines_of(pieces)],
            ["left top", "left lower", "right top", "right lower"],
        )
        self.assertLess(63.0, COLUMN_SPLIT)

    def test_pieces_on_one_line_join_and_the_footer_goes(self) -> None:
        pieces = [
            Piece(63.0, 600.0, "A+GillSans-SemiBold", "Casting Time:"),
            Piece(120.0, 600.2, "A+GillSans", " Action"),
            Piece(63.0, FOOTER_Y - 10, "F", "System Reference Document 5.2.1"),
            Piece(18.0, FOOTER_Y - 10, "F", "109"),
        ]
        (only,) = lines_of(pieces, page=109)
        self.assertEqual(
            (only.text, only.page, only.first_font),
            ("Casting Time: Action", 109, "GillSans-SemiBold"),
        )


class Building(unittest.TestCase):
    def pages(self) -> list[list[Line]]:
        contents = [say("Spell Descriptions", "Cambria")]  # the contents page: not a heading
        spells = [
            say("Spell Descriptions", TITLE, 11),
            *header("Fireball")[:6],
            say("Boom.", BODY, 11),
        ]
        glossary = [say("Rules Glossary", TITLE, 12), say("Blinded [Condition]", TITLE, 12),
                    say("You can’t see.", BODY, 12)]  # fmt: skip
        toolbox = [say("Gameplay Toolbox", TITLE, 13)]
        return [contents, *[[]] * 9, spells, glossary, toolbox]

    def document(self) -> dict[str, Any]:
        return {"title": "t", "url": "u", "sha256": "s", "pages": 13}

    def test_the_files_are_made_from_the_sections(self) -> None:
        files = build.build_files(self.pages(), self.document())
        self.assertEqual(set(files), {"spells.json", "conditions.json"})
        spells, conditions = files["spells.json"], files["conditions.json"]
        for data in (spells, conditions):
            self.assertEqual(
                (data["source"], data["edition"], data["licence"]),
                ("SRD 5.2.1", "2024", "CC-BY-4.0"),
            )
            self.assertEqual(data["document"], self.document())
        self.assertEqual([e["name"] for e in spells["entries"]], ["Fireball"])
        self.assertEqual(spells["entries"][0]["classes"], ["Sorcerer", "Wizard"])
        self.assertEqual((spells["kind"], spells["section"]), ("spell", "Spell Descriptions"))
        self.assertEqual([e["name"] for e in conditions["entries"]], ["Blinded"])
        self.assertEqual(conditions["entries"][0]["page"], 12)

    def test_a_different_document_stops_the_tool(self) -> None:
        pages = self.pages()
        pages[11] = [say("Something else", TITLE, 12)]  # no Rules Glossary
        with self.assertRaisesRegex(parse.SrdError, "Rules Glossary"):
            build.build_files(pages, self.document())

    def test_the_same_pages_give_the_same_text(self) -> None:
        first = build.dump(build.build_files(self.pages(), self.document())["spells.json"])
        second = build.dump(build.build_files(self.pages(), self.document())["spells.json"])
        self.assertEqual(first, second)
        self.assertTrue(first.endswith("}\n"))
        self.assertIn("’", build.dump({"x": "Can’t"}))  # words stay as written, not \u escapes


if __name__ == "__main__":
    unittest.main()
