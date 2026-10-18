"""Putting the PDF reader's characters into pieces (#1892): the pure part of `read_pages`, on
made-up characters in the shapes the real SRD PDF has (no PDF is needed, none is kept)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import monsters
from dmbot.devtools.srd.pdf import Char, lines_of, pieces_of
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
        self.assertEqual({k: v[0] for k, v in found.items()}, {
            "str": 21, "dex": 9, "con": 15, "int": 18, "wis": 15, "cha": 18,
        })  # fmt: skip


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
        chars = [*run("Gameplay ", TITLE, 63, 700), *run("Toolbox", TITLE, 63 + 9 * WIDTH, 700)]
        self.assertEqual(texts(chars), ["Gameplay Toolbox"])


class Paragraphs(unittest.TestCase):
    """The PDF draws a paragraph's indent as a gap; the parsers look for a leading space."""

    def lines(self, chars: list[Char]) -> list[str]:
        return texts(chars)

    def test_an_indented_first_line_after_a_sentence_gets_the_leading_space(self) -> None:
        chars = [
            *run("It ends here.", BODY, 63, 500),
            *newline(500),
            *run("A new one starts", BODY, 71, 488),
            *newline(488),
            *run("and goes on", BODY, 63, 476),
        ]
        self.assertEqual(self.lines(chars), ["It ends here.", " A new one starts", "and goes on"])

    def test_a_line_indented_in_mid_sentence_does_not_start_a_paragraph(self) -> None:
        chars = [
            *run("The target must", BODY, 63, 500),
            *newline(500),
            *run("make a save", BODY, 71, 488),
        ]
        self.assertEqual(self.lines(chars), ["The target must", "make a save"])

    def test_the_hanging_lines_under_a_bold_lead_in_do_not_start_paragraphs(self) -> None:
        chars = [
            *run("Healing Light.", BOLD_LEAD, 63, 500),
            *run(" The target regains.", BODY, 63 + 14 * WIDTH, 500),
            *newline(500),
            *run("to 4d12 plus", BODY, 71, 488),
        ]
        self.assertEqual(self.lines(chars), ["Healing Light. The target regains.", "to 4d12 plus"])

    def test_the_right_column_has_its_own_edge(self) -> None:
        chars = [
            *run("It ends here.", BODY, 313.5, 500),
            *newline(500),
            *run("A new one", BODY, 321.5, 488),
        ]
        self.assertEqual(self.lines(chars), ["It ends here.", " A new one"])


class Hyphens(unittest.TestCase):
    def test_the_hyphen_the_typesetter_added_reads_as_a_hyphen(self) -> None:
        chars = [*run("immedi", BODY, 63, 500), Char("￾", BODY, 93, 500, 93, 98), *newline(500)]
        self.assertEqual(texts(chars), ["immedi -"])

    def test_a_hyphen_at_a_lines_end_is_kept_for_the_parsers_to_read(self) -> None:
        chars = [*run("long-", BODY, 63, 500), *newline(500), *run("dead", BODY, 63, 488)]
        self.assertEqual(texts(chars), ["long -", "dead"])

    def test_a_hyphen_inside_a_line_is_left_alone(self) -> None:
        self.assertEqual(texts(run("long-dead sage", BODY, 63, 500)), ["long-dead sage"])
