"""Reading the SRD 5.2.1 into spells and conditions (#866): the parser on made-up lines
in the shapes the real PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from dmbot.devtools.srd import build, parse, toolbox
from dmbot.devtools.srd import equipment as equipment_reader
from dmbot.devtools.srd.pdf import COLUMN_SPLIT, FOOTER_Y, Line, Piece, lines_of
from tests.monster_fixtures import goblin
from tests.test_srd_toolbox import FIXTURE_LEFT_OUT, encounters, magic_items
from tests.test_srd_toolbox import chapter as toolbox_chapter

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

    def test_a_spell_that_ran_into_the_next_one_stops_the_tool(self) -> None:
        # The second title is in a font the tool doesn't recognise as a title: without a
        # guard, its spell would be swallowed whole by the one before.
        lines = [
            *header("Alarm", "Level 1 Abjuration (Ranger, Wizard)"),
            say("You set an alarm."),
            say("Alter Self", "Cambria-Bold"),
            say("Level 2 Transmutation (Sorcerer, Wizard)", ITALIC),
            say("Casting Time: Action", "GillSans"),
            say("You change."),
        ]
        with self.assertRaisesRegex(parse.SrdError, "'Alarm' on page 107: another spell is inside"):
            parse.parse_spells(lines)

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

    def test_a_hyphen_before_a_number_belongs_to_the_text(self) -> None:
        # "10-foot-by-" at a line's end, then "10-foot": the hyphen is real.
        text = self.spell("Ten 10-foot-by-", "10-foot panels.")
        self.assertTrue(text.startswith("Ten 10-foot-by-10-foot panels."))

    def test_a_stat_block_that_wraps_is_read_as_lines_of_its_own(self) -> None:
        optima = "Optima-Regular"
        lines = [
            *header("Find Steed", "Level 2 Conjuration (Paladin)"),
            say("You summon a steed."),
            say("Otherworldly Steed", "GillSans"),
            say("AC 10 + 1 per spell level", "Optima-Bold"),
            say("HP 5 + 10 per spell level (the steed has a number of Hit", "Optima-Bold"),
            say("Dice [d10s] equal to the spell’s level)", optima),
            say("Immunities Poison, Psychic; Charmed, Exhaustion,", "Optima-Bold"),
            say("Frightened, Paralyzed, Poisoned", optima),
            say("Speed 60 ft.", "Optima-Bold"),
            say("The word steed is in the document.", BODY),
        ]
        (spell,) = parse.parse_spells(lines)
        rows = spell.text.splitlines()
        self.assertIn("HP 5 + 10 per spell level (the steed has a number of Hit Dice [d10s] "
                      "equal to the spell’s level)", rows)  # fmt: skip
        self.assertIn("Immunities Poison, Psychic; Charmed, Exhaustion, Frightened, Paralyzed, "
                      "Poisoned", rows)  # fmt: skip
        self.assertIn("Speed 60 ft.", rows)  # a new entry is a new line

    def test_a_stat_block_line_in_italic_or_a_bracketed_name_carries_on(self) -> None:
        optima = "Optima-Regular"
        lines = [
            *header("Giant Insect", "Level 4 Conjuration (Druid)"),
            say("You summon an insect."),
            say("Bonus Actions", "GillSans"),
            say("Venomous Spew (Centipede Only). Constitution Saving", "Optima-BoldItalic"),
            say("Throw: Your spell save DC, one creature.", "Optima-Italic"),
            say("Failure: The target is Poisoned.", optima),
            say("Healing Touch (Celestial Only; Recharges after a Long", "Optima-BoldItalic"),
            say("Rest). One creature regains Hit Points.", "Optima-BoldItalic"),
            say("Fey Step (Fey Only). The steed teleports.", "Optima-BoldItalic"),
            say("The word insect is in the document.", BODY),
        ]
        (spell,) = parse.parse_spells(lines)
        rows = spell.text.splitlines()
        self.assertIn(
            "Venomous Spew (Centipede Only). Constitution Saving Throw: Your spell save DC, "
            "one creature. Failure: The target is Poisoned.",
            rows,
        )
        self.assertIn(
            "Healing Touch (Celestial Only; Recharges after a Long Rest). One creature regains "
            "Hit Points.",
            rows,
        )
        self.assertIn("Fey Step (Fey Only). The steed teleports.", rows)  # a new entry: a new line

    def test_a_bold_name_at_a_lines_start_is_not_a_heading_but_a_heading_is(self) -> None:
        lines = [
            *header("Find Steed", "Level 2 Conjuration (Paladin)"),
            say("This creature uses the Otherworldly", BODY),
            say("Steed", "Cambria-Bold"),
            say("stat block. If you have a steed, it is replaced.", BODY),
            line(piece(" "), piece("Using a Higher-Level Spell Slot.", BOLD_LEAD), piece(" More.")),
        ]
        (spell,) = parse.parse_spells(lines)
        self.assertEqual(
            spell.text.splitlines(),
            [
                "This creature uses the Otherworldly Steed stat block. If you have a steed, it is "
                "replaced.",
                "Using a Higher-Level Spell Slot. More.",
            ],
        )

    def test_a_table_header_in_two_columns_gets_a_space_between_them(self) -> None:
        columns = Line(313.5, 500, [Piece(313.5, 500, "F+GillSans-SemiBold", "Temperature"),
                                    Piece(429.7, 500, "F+GillSans-SemiBold", "Wind")])  # fmt: skip
        self.assertEqual(columns.text, "Temperature Wind")

    def test_words_in_two_fonts_have_no_space_put_between_them(self) -> None:
        # Pieces drawn from one starting point share its x, so the later ones' x is far from
        # the line's start without there being a gap: "four Ghouls." stays "four Ghouls.".
        bold = Line(63.0, 500, [
            Piece(63.0, 500, "F+Cambria", "over four "),
            Piece(63.0, 500, "F+Cambria-Bold", "Ghouls"),
            Piece(137.3, 500, "F+Cambria", ". If you use a level 8 spell slot"),
        ])  # fmt: skip
        self.assertEqual(bold.text, "over four Ghouls. If you use a level 8 spell slot")
        spaced = Line(63.0, 500, [Piece(63.0, 500, "F+Cambria", "Name "),
                                  Piece(120.0, 500, "F+Cambria", "value")])  # fmt: skip
        self.assertEqual(spaced.text, "Name value")

    def test_ability_names_set_in_small_capitals_are_written_once(self) -> None:
        lines = [
            *header("Giant Insect", "Level 4 Conjuration (Druid)"),
            say("You summon an insect."),
            say("MOD SAVE MOD SAVE MOD SAVE", "GillSans"),
            say("Str 17 +3 +3 dex 13 +1 +1 con 15 +2 +2", "GillSans"),
            say("int 4 −3 −3 WiS 14 +2 +2 chA 3 −4 −4", "GillSans"),
            say("The word con and int and dex appear in the text.", BODY),
        ]
        (spell,) = parse.parse_spells(lines)
        self.assertIn("Str 17 +3 +3 Dex 13 +1 +1 Con 15 +2 +2", spell.text)
        self.assertIn("Int 4 −3 −3 Wis 14 +2 +2 Cha 3 −4 −4", spell.text)
        self.assertIn("The word con and int and dex appear in the text.", spell.text)  # not scores

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


def at(x: float, text: str, font: str = TITLE, page: int = 180) -> Line:
    return line(piece(text, font, x=x), page=page)


class RulesGlossary(unittest.TestCase):
    """The Rules Definitions (#1884): which title lines start an entry, and what is kept."""

    def test_a_title_at_a_column_edge_starts_an_entry_in_either_column(self) -> None:
        lines = [
            say("Rules Definitions", TITLE),
            at(63, "Cover"),
            at(63, "Cover protects.", BODY),
            at(314, "Darkness"),
            at(314, "It is dark.", BODY),
        ]
        self.assertEqual(
            [(r.name, r.text) for r in parse.parse_rules(lines)],
            [("Cover", "Cover protects."), ("Darkness", "It is dark.")],
        )

    def test_a_tag_is_split_from_the_name_and_a_condition_is_left_to_conditions(self) -> None:
        lines = [
            say("Rules Definitions", TITLE),
            at(63, "Dash [Action]"),
            at(63, "You gain movement.", BODY),
            at(63, "Blinded [Condition]"),
            at(63, "You can’t see.", BODY),
        ]
        (dash,) = parse.parse_rules(lines)
        self.assertEqual((dash.name, dash.tag, dash.page), ("Dash", "Action", 180))

    def test_a_table_caption_is_not_an_entry_and_ends_the_text(self) -> None:
        lines = [
            say("Rules Definitions", TITLE),
            at(63, "Carrying Capacity"),
            at(63, "You can carry a lot.", BODY),
            at(63, "Carrying Capacity"),  # the table's caption: a title at the edge...
            at(67, "Creature Size"),  # ...whose column heads sit a few points in
            at(67, "Tiny 30 lb.", BODY),
            at(63, "Cover"),
            at(63, "Cover protects.", BODY),
        ]
        rules = parse.parse_rules(lines)
        self.assertEqual([r.name for r in rules], ["Carrying Capacity", "Cover"])
        self.assertEqual(rules[0].text, "You can carry a lot.")

    def test_table_rows_in_the_sans_font_go_but_a_bullet_and_its_wrapped_line_stay(self) -> None:
        row = line(
            piece("Attack", "GillSans", x=314.0), piece("Dodge", "GillSans", x=372.0), page=180
        )
        bullet = line(piece("• Furniture of your size or", "GillSans", x=314.0), page=180)
        lines = [
            say("Rules Definitions", TITLE),
            at(314, "Difficult Terrain"),
            at(314, "It counts if it has:", BODY),
            bullet,
            at(314, "larger", "GillSans"),
            row,
        ]
        (rule,) = parse.parse_rules(lines)
        self.assertEqual(rule.text, "It counts if it has:\n• Furniture of your size or larger")

    def test_the_see_also_line_is_kept_apart_as_names(self) -> None:
        lines = [
            say("Rules Definitions", TITLE),
            at(63, "Dash [Action]"),
            at(63, "You gain movement. See also “Speed.”", BODY),
            at(63, "Expertise"),
            at(63, "Double it. See also “Playing the Game” (“Proficiency”).", BODY),
        ]
        dash, expertise = parse.parse_rules(lines)
        self.assertEqual((dash.text, dash.see_also), ("You gain movement.", ("Speed",)))
        self.assertEqual(expertise.see_also, ("Playing the Game", "Proficiency"))
        twice = [
            say("Rules Definitions", TITLE),
            at(63, "Hit"),
            at(63, "See also “Hit.” See also “Hit.”", BODY),
        ]
        self.assertEqual(parse.parse_rules(twice)[0].see_also, ("Hit",))

    def test_a_pdf_without_the_heading_or_an_entry_without_words_stops_the_tool(self) -> None:
        with self.assertRaisesRegex(parse.SrdError, "Rules Definitions"):
            parse.parse_rules([at(63, "Cover")])
        with self.assertRaisesRegex(parse.SrdError, "Cover"):
            parse.parse_rules([say("Rules Definitions", TITLE), at(63, "Cover")])


class MendingACutAfterANumber(unittest.TestCase):
    """A line that ends "10-" and goes on "foot" is "10-foot", not "10foot" (#1914)."""

    def test_the_hyphen_after_a_number_stays(self) -> None:
        mender = parse.Mender([])
        self.assertEqual(mender.join("in a 10-", "foot Emanation"), "in a 10-foot Emanation")
        self.assertEqual(mender.join("a 100 -", "foot high"), "a 100-foot high")

    def test_a_word_cut_in_two_is_still_one_word(self) -> None:
        mender = parse.Mender([say("The spell ignites.")])
        self.assertEqual(mender.join("It ig-", "nites"), "It ignites")

    def test_no_shipped_text_has_a_number_glued_to_foot(self) -> None:
        from dmbot.rules import index

        for entry in index.srd().entries:
            with self.subTest(entry.name):
                self.assertNotRegex(entry.text, r"\d(?:foot|feet)\b")


class Building(unittest.TestCase):
    def setUp(self) -> None:
        # the made-up Gameplay Toolbox has its own left-out counts, not the real chapter's
        patcher = patch.dict(toolbox.EXPECTED_LEFT_OUT, FIXTURE_LEFT_OUT, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pages(self) -> list[list[Line]]:
        contents = [say("Spell Descriptions", "Cambria")]  # the contents page: not a heading
        spells = [
            say("Spell Descriptions", TITLE, 11),
            *header("Fireball")[:6],
            say("Boom.", BODY, 11),
        ]
        glossary = [say("Rules Glossary", TITLE, 12), say("Rules Definitions", TITLE, 12),
                    say("Blinded [Condition]", TITLE, 12), say("You can’t see.", BODY, 12),
                    say("Cover", TITLE, 12), say("Cover protects a target.", BODY, 12)]  # fmt: skip
        # the chapter as the toolbox reader's own tests have it, with its Combat Encounters and
        # Magic Items sections after it (the items' own heading ends them)
        toolbox = toolbox_chapter()
        combat = [*encounters(), *magic_items()]
        classes = [
            say("Classes", TITLE, 20),
            say("Barbarian", TITLE, 20),
            say("Level 1: Rage", TITLE, 20),
            say("You can enter a Rage.", BODY, 20),
        ]
        origins = [
            say("Character Origins", TITLE, 21),
            say("Sage", TITLE, 21),
            say("Ability Scores: Constitution, Intelligence, Wisdom", TITLE, 21),
            say("Feat: Alert (see “Feats”)", TITLE, 21),
            say("Skill Proficiencies: Arcana and History", TITLE, 21),
            say("Tool Proficiency: Calligrapher’s Supplies", TITLE, 21),
            say("Equipment: Choose A or B: (A) Robe; or (B) 50 GP", TITLE, 21),
            say("Goliath", TITLE, 21),
            say("Creature Type: Humanoid", TITLE, 21),
            say("Size: Medium (about 7–8 feet tall)", TITLE, 21),
            say("Speed: 35 feet", TITLE, 21),
            say("As a Goliath, you have these special traits.", BODY, 21),
            # the three tables the chapter's species have (named in origins.SPECIES_TABLES)
            *[
                line
                for table in ("Draconic Ancestors", "Elven Lineages", "Fiendish Legacies")
                for line in (say(table, TITLE, 21), say("Some cells", "GillSans", 21))
            ],
        ]
        feats = [
            say("Feats", TITLE, 21),
            say("Origin Feats", TITLE, 21),
            say("Alert", TITLE, 21),
            say("Origin Feat", ITALIC, 21),
            say("You gain the following benefits.", BODY, 21),
        ]
        equipment = [say("Equipment", TITLE, 21), say("Properties", TITLE, 21)] + [
            line
            for name in (*equipment_reader.PROPERTIES, *equipment_reader.MASTERIES)
            for line in (say(name, TITLE, 21), say(f"What {name} does.", BODY, 21))
        ]
        weapons = [
            Line(49.9, 751.0, [piece("Weapons", TITLE, 49.9, 751.0)], 21),
            Line(53.4, 721.0, [piece("Simple Melee Weapons", "GillSans-Italic", 53.4, 721.0)], 21),
            Line(
                61.4,
                706.0,
                [piece("Club 1d4 Bludgeoning Light Slow 2 lb. 1 SP", "GillSans", 61.4, 706.0)],
                21,
            ),
        ]
        armor = [
            say("Armor", TITLE, 21),
            Line(
                67.0,
                438.0,
                [piece("Armor Armor Class (AC) Strength Stealth Weight Cost", TITLE, 67.0, 438.0)],
                21,
            ),
            Line(
                66.6,
                423.0,
                [piece("Shield (Utilize Action to Don or Doff)", "GillSans-Italic", 66.6, 423.0)],
                21,
            ),
            Line(74.6, 408.0, [piece("Shield +2 — — 6 lb. 10 GP", "GillSans", 74.6, 408.0)], 21),
        ]
        tools = [
            say("Tools", TITLE, 24),
            say("Artisan’s Tools", TITLE, 24),
            say("Alchemist’s Supplies (50 GP)", TITLE, 24),
            say("Ability: Intelligence Weight: 8 lb.", TITLE, 24),
            say("Utilize: Identify a substance (DC 15)", TITLE, 24),
            say("Adventuring Gear", TITLE, 24),
            say("Backpack (2 GP)", TITLE, 24),
            say("A Backpack holds up to 30 pounds.", BODY, 24),
        ]
        gear_table = [  # a page of its own: the weights
            say("Adventuring Gear", TITLE, 25),
            say("Item Weight Cost", TITLE, 25),
            say("Backpack 5 lb. 2 GP", "GillSans", 25),
        ]
        mounts = [
            say("Mounts and Vehicles", TITLE, 26),
            say("Mounts and Other Animals", TITLE, 26),
            say("Item Carrying Capacity Cost", TITLE, 26),
            say("Horse, Riding 480 lb. 75 GP", "GillSans", 26),
            say("Tack, Harness, and Drawn Vehicles", TITLE, 26),
            say("Cart 200 lb. 15 GP", "GillSans", 26),
            say("Lifestyle Expenses", TITLE, 26),
        ]
        items = [
            say("Magic Items A–Z", TITLE, 22),
            say("Bag of Holding", TITLE, 22),
            say("Wondrous Item, Uncommon", ITALIC, 22),
            say("It holds more than it should.", BODY, 22),
        ]
        monsters = [say("Monsters", TITLE, 23)]
        creatures = [say("Monsters A–Z", TITLE, 24), *goblin()]
        return [
            contents, *[[]] * 9, classes, origins, feats, equipment, weapons, armor, tools,
            gear_table, mounts, spells, glossary, toolbox, combat, items, monsters, creatures,
        ]  # fmt: skip

    def document(self) -> dict[str, Any]:
        return {"title": "t", "url": "u", "sha256": "s", "pages": 13}

    def test_the_files_are_made_from_the_sections(self) -> None:
        files = build.build_files(self.pages(), self.document())
        self.assertEqual(
            set(files),
            {
                "spells.json",
                "conditions.json",
                "rules.json",
                "features.json",
                "feats.json",
                "equipment.json",
                "origins.json",
                "items.json",
                "monsters.json",
                "toolbox.json",
            },
        )
        creatures = files["monsters.json"]
        self.assertEqual([e["name"] for e in creatures["entries"]], ["Goblin Warrior"])
        self.assertNotIn("section", creatures)  # each creature names its own
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
        features = files["features.json"]
        self.assertEqual((features["kind"], features["section"]), ("feature", "Classes"))
        self.assertEqual(
            features["entries"],
            [
                {
                    "name": "Rage",
                    "class": "Barbarian",
                    "level": 1,
                    "subclass": "",
                    "text": "You can enter a Rage.",
                    "page": 20,
                }
            ],
        )
        feats = files["feats.json"]
        self.assertEqual((feats["kind"], feats["section"]), ("feat", "Feats"))
        self.assertEqual(
            feats["entries"],
            [
                {
                    "name": "Alert",
                    "category": "Origin",
                    "prerequisite": "",
                    "text": "You gain the following benefits.",
                    "page": 21,
                }
            ],
        )
        entries = files["origins.json"]["entries"]  # two kinds in one file
        self.assertEqual(
            [(e["kind"], e["name"]) for e in entries],
            [("species", "Goliath"), ("background", "Sage")],
        )
        self.assertEqual(entries[0]["speed"], "35 feet")
        self.assertEqual(entries[1]["feat"], "Alert")
        gear = files["equipment.json"]["entries"]
        self.assertEqual(
            [(e["kind"], e["name"]) for e in gear if e["kind"] != "rule"],
            [
                ("weapon", "Club"),
                ("armor", "Shield"),
                ("tool", "Alchemist’s Supplies"),
                ("gear", "Backpack"),
                ("gear", "Horse, Riding"),
                ("gear", "Cart"),
            ],
        )
        self.assertEqual(sum(e["kind"] == "rule" for e in gear), 18)
        club = gear[0]
        self.assertEqual(
            (club["category"], club["damage"], club["mastery"], club["cost"]),
            ("Simple Melee", "1d4", "Slow", "1 SP"),
        )
        magic = files["items.json"]
        self.assertEqual((magic["kind"], magic["section"]), ("item", "Magic Items"))
        self.assertEqual(
            magic["entries"],
            [
                {
                    "name": "Bag of Holding",
                    "category": "Wondrous Item",
                    "subtype": "",
                    "rarity": "Uncommon",
                    "attunement": False,
                    "attunement_by": "",
                    "text": "It holds more than it should.",
                    "page": 22,
                }
            ],
        )
        rules = files["rules.json"]
        self.assertEqual((rules["kind"], rules["section"]), ("rule", "Rules Glossary"))
        self.assertEqual([e["name"] for e in rules["entries"]], ["Cover"])  # not the condition
        self.assertEqual(rules["entries"][0]["see_also"], [])

    def test_a_different_document_stops_the_tool(self) -> None:
        pages = self.pages()
        glossary = next(
            i for i, page in enumerate(pages) if page and page[0].text == "Rules Glossary"
        )
        pages[glossary] = [say("Something else", TITLE, 12)]  # no Rules Glossary
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
