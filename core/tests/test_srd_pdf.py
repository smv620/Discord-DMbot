"""Putting the PDF reader's characters into pieces (#1892): the pure part of `read_pages`, on
made-up characters in the shapes the real SRD PDF has (no PDF is needed, none is kept)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import monsters
from dmbot.devtools.srd.pdf import Char, lines_of, mark_paragraphs, pieces_of
from tests.test_srd_parse import BODY, BOLD_LEAD, TITLE

LABEL = "GillSans-SemiBold-SC700"
SANS = "GillSans"
WIDTH = 5.0  # a made-up width for every character
MADE_UP = ""  # the font of a space the reader makes up between two pieces


def run(text: str, font: str, x: float, y: float, *, start: float | None = None) -> list[Char]:
    """The characters of one run drawn from `x` (its first character's box starts at `start`)."""
    left = x if start is None else start
    return [
        Char(ch, font, left + i * WIDTH, y, left + i * WIDTH, left + (i + 1) * WIDTH)
        for i, ch in enumerate(text)
    ]


def newline(y: float) -> list[Char]:
    return [Char("\r", MADE_UP, 0.0, y, 0.0, 0.0), Char("\n", MADE_UP, 0.0, y, 0.0, 0.0)]


def texts(chars: list[Char]) -> list[str]:
    return [line.text for line in lines_of(pieces_of(chars), 1)]


class AbilityTable(unittest.TestCase):
    """The ability table lost its Dex, Con, Wis and Cha labels in pypdf and pdfminer.six."""

    def test_every_label_and_number_of_a_row_comes_through(self) -> None:
        row = [
            *run("Str", LABEL, 66, 594),
            *run(" 21 +5 +5", SANS, 80, 594),
            *run("Dex", LABEL, 143, 594),
            *run(" 9 −1 +3", SANS, 162, 594),
            *run("Con", LABEL, 220, 594),
            *run(" 15 +2 +6", SANS, 240, 594),
        ]
        self.assertEqual(texts(row), ["Str 21 +5 +5 Dex 9 −1 +3 Con 15 +2 +6"])

    def test_a_number_right_after_a_label_still_gets_its_space(self) -> None:
        # "Con" and "22" touch in the PDF (the gap is under a point); the parser needs a space.
        row = [
            *run("Con", LABEL, 470, 600),
            *run("22", SANS, 490, 600, start=470 + 3 * WIDTH + 0.4),
        ]
        self.assertEqual(texts(row), ["Con 22"])

    def test_a_row_the_parser_reads_gives_the_creatures_scores(self) -> None:
        rows = [
            [
                *run("Str", LABEL, 66, 594),
                *run(" 21 +5 +5", SANS, 80, 594),
                *run("Dex", LABEL, 143, 594),
                *run(" 9 −1 +3", SANS, 162, 594),
                *run("Con", LABEL, 220, 594),
                *run(" 15 +2 +6", SANS, 240, 594),
            ],
            [
                *run("Int", LABEL, 66, 580),
                *run(" 18 +4 +8", SANS, 80, 580),
                *run("Wis", LABEL, 143, 580),
                *run(" 15 +2 +6", SANS, 162, 580),
                *run("Cha", LABEL, 220, 580),
                *run(" 18 +4 +4", SANS, 240, 580),
            ],
        ]
        found = {
            name.lower(): (int(score), mod, save)
            for chars in rows
            for line in lines_of(pieces_of(chars), 1)
            for name, score, mod, save in monsters.ABILITY.findall(line.text)
        }
        self.assertEqual(
            found,
            {
                "str": (21, "+5", "+5"),
                "dex": (9, "−1", "+3"),
                "con": (15, "+2", "+6"),
                "int": (18, "+4", "+8"),
                "wis": (15, "+2", "+6"),
                "cha": (18, "+4", "+4"),
            },
        )


class Spaces(unittest.TestCase):
    def test_a_space_the_reader_made_up_is_kept(self) -> None:
        chars = [
            *run("equals", BODY, 10, 500),
            Char(" ", MADE_UP, 40, 500, 40, 40),
            *run("=", SANS, 45, 500),
        ]
        self.assertEqual(texts(chars), ["equals ="])

    def test_a_wide_gap_between_two_fonts_gets_a_space(self) -> None:
        chars = [*run("equals", BODY, 10, 500), *run("ten", SANS, 70, 500)]
        self.assertEqual(texts(chars), ["equals ten"])

    def test_letters_of_one_word_in_two_fonts_stay_one_word(self) -> None:
        chars = [
            *run("Gameplay", TITLE, 63, 700),
            *run("Toolbox", SANS, 63 + 8 * WIDTH + 0.4, 700, start=63 + 8 * WIDTH + 0.4),
        ]
        self.assertEqual(texts(chars), ["GameplayToolbox"])  # a gap under a point is no space

    def test_a_letter_and_a_number_a_little_apart_get_no_space(self) -> None:
        chars = [
            *run("Con", LABEL, 470, 600),
            *run("22", SANS, 490, 600, start=470 + 3 * WIDTH + 0.2),
        ]
        self.assertEqual(texts(chars), ["Con22"])


def paragraphs(
    chars: list[Char], carried: tuple[bool, bool, bool] = (False, False, False)
) -> tuple[list[str], tuple[bool, bool, bool]]:
    lines = lines_of(pieces_of(chars), 1)
    state = mark_paragraphs(lines, carried)
    return [line.text for line in lines], state


class Paragraphs(unittest.TestCase):
    """The PDF draws a paragraph's indent as a gap; the parsers look for a leading space."""

    def test_an_indented_first_line_after_a_sentence_gets_the_leading_space(self) -> None:
        chars = [
            *run("It ends here.", BODY, 63, 500),
            *newline(500),
            *run("A new one starts", BODY, 71, 488),
            *newline(488),
            *run("and goes on", BODY, 63, 476),
        ]
        self.assertEqual(
            paragraphs(chars)[0], ["It ends here.", " A new one starts", "and goes on"]
        )

    def test_a_line_indented_in_mid_sentence_does_not_start_a_paragraph(self) -> None:
        chars = [
            *run("The target must", BODY, 63, 500),
            *newline(500),
            *run("make a save", BODY, 71, 488),
        ]
        self.assertEqual(paragraphs(chars)[0], ["The target must", "make a save"])

    def test_the_hanging_lines_under_a_bold_lead_in_do_not_start_paragraphs(self) -> None:
        chars = [
            *run("Healing Light.", BOLD_LEAD, 63, 500),
            *run(" The target regains.", BODY, 63 + 14 * WIDTH, 500),
            *newline(500),
            *run("to 4d12 plus", BODY, 71, 488),
        ]
        self.assertEqual(
            paragraphs(chars)[0], ["Healing Light. The target regains.", "to 4d12 plus"]
        )

    def test_the_right_column_has_its_own_edge(self) -> None:
        chars = [
            *run("It ends here.", BODY, 313.5, 500),
            *newline(500),
            *run("A new one", BODY, 321.5, 488),
        ]
        self.assertEqual(paragraphs(chars)[0], ["It ends here.", " A new one"])

    def test_a_hanging_block_runs_on_across_a_column_and_a_sentence_end(self) -> None:
        # Wish (SRD 5.2.1 p. 175): every line of the block is drawn 8 points in; the left
        # column ends "... from the game." and the right one goes on "Similarly, ...". The PDF
        # has a space before "Similarly", but it is the same paragraph.
        left = [
            *run("when it is gone", BODY, 71, 500),
            *newline(500),
            *run("from the game.", BODY, 71, 488),
        ]
        right = [Char(" ", MADE_UP, 313.5, 734, 313.5, 313.5), *run("Similarly,", BODY, 321.5, 734)]
        lines = lines_of(pieces_of(left + right), 1)
        mark_paragraphs(lines, (False, False, False))
        self.assertEqual([line.text for line in lines][-1], "Similarly,")

    def test_a_hanging_block_runs_on_across_a_page(self) -> None:
        # Antipathy/Sympathy (p. 109 to 110): the page before ends on an indented line, and the
        # next page begins "Wisdom saving throw" with a space glyph, mid-sentence.
        chars = [Char(" ", BODY, 71, 734, 71, 73), *run("Wisdom saving", BODY, 73, 734)]
        text, _ = paragraphs(chars, carried=(True, False, False))
        self.assertEqual(text, ["Wisdom saving"])

    def test_a_page_that_starts_a_paragraph_keeps_its_indent(self) -> None:
        # Gate and Dimension Door: a page that follows a sentence end, in a block that is not
        # hanging, starts a paragraph at its first indented line.
        chars = [*run("If you, the other", BODY, 71, 734)]
        text, _ = paragraphs(chars, carried=(False, False, True))
        self.assertEqual(text, [" If you, the other"])

    def test_an_indent_after_a_heading_or_a_table_row_starts_a_paragraph(self) -> None:
        for font in (TITLE, SANS):
            chars = [
                *run("Cantrip", font, 63, 500),
                *newline(500),
                *run("A new one", BODY, 71, 488),
            ]
            self.assertEqual(paragraphs(chars)[0][1], " A new one", font)


class Hyphens(unittest.TestCase):
    def test_the_hyphen_the_typesetter_added_reads_as_a_hyphen(self) -> None:
        chars = [*run("immedi", BODY, 63, 500), Char("￾", BODY, 93, 500, 93, 98), *newline(500)]
        self.assertEqual(texts(chars), ["immedi -"])

    def test_a_hyphen_at_a_lines_end_is_kept_for_the_parsers_to_read(self) -> None:
        chars = [*run("long-", BODY, 63, 500), *newline(500), *run("dead", BODY, 63, 488)]
        self.assertEqual(texts(chars), ["long -", "dead"])

    def test_a_hyphen_inside_a_line_is_left_alone(self) -> None:
        self.assertEqual(texts(run("long-dead sage", BODY, 63, 500)), ["long-dead sage"])
