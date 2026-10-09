"""Reading the 2014 SRD 5.1 into spells and conditions (#873): the reader on made-up lines
in the shapes the 5.1 PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest

from dmbot.devtools.srd import parse51
from dmbot.devtools.srd.parse import SrdError
from dmbot.devtools.srd.pdf import Line, Piece

TITLE, ITALIC, BOLD, BODY = "GillSans-SemiBold", "Cambria-Italic", "Cambria-Bold", "Cambria"
LEAD = "Cambria-BoldItalic"


def say(text: str, font: str = BODY, *, x: float = 63.0, page: int = 114) -> Line:
    return Line(x, 500.0, [Piece(x, 500.0, f"ABCDEF+{font}", text)], page)


def labelled(label: str, value: str) -> Line:
    """A labelled line as the PDF sets it: the label in bold, its value after it."""
    return Line(
        63.0,
        500.0,
        [Piece(63.0, 500.0, f"ABCDEF+{BOLD}", label), Piece(110.0, 500.0, "ABCDEF+Cambria", value)],
        114,
    )


def vocab(*words: str, text: tuple[str, ...] = ()) -> parse51.Vocabulary:
    return parse51.Vocabulary(words, [say(t) for t in text])


def fireball() -> list[Line]:
    return [
        say("Fireball", TITLE),
        say("3rd-level evocation", ITALIC),
        labelled("Casting Time:", " 1 action"),
        labelled("Range:", " 150 feet"),
        labelled("Components:", " V, S, M (a tiny ball of bat guano and"),
        say("sulfur)", BODY),
        labelled("Duration:", " Instantaneous"),
        say("A bright streak flashes from your poin", BODY),
        say("ting finger.", BODY),
        say("At Higher Levels.", LEAD),
        say("The damage increases by 1d6.", BODY),
    ]


class Cleaning(unittest.TestCase):
    def test_noise_is_taken_out_and_hyphens_closed(self) -> None:
        self.assertEqual(
            parse51.clean("a\t15 - foot- radius\xa0sphere\xad"), "a 15-foot-radius sphere"
        )

    def test_a_cut_word_is_joined_only_when_the_whole_word_is_known(self) -> None:
        words = vocab("higher", "advantage", "the", "he", "ad")
        self.assertEqual(words.tidy("hig her levels"), "higher levels")
        self.assertEqual(words.tidy("adva ntage"), "advantage")
        self.assertEqual(words.tidy("the he"), "the he")  # two words stay two

    def test_ordinals_and_a_lone_letter_beside_a_piece_are_put_back(self) -> None:
        words = vocab("dropping", "knocked", "to")
        self.assertEqual(words.tidy("the 3r d level"), "the 3rd level")
        self.assertEqual(words.tidy("d ropping to"), "dropping to")
        self.assertEqual(words.tidy("knocke d to"), "knocked to")

    def test_a_lone_letter_between_two_words_is_a_lost_word_and_is_counted(self) -> None:
        words = vocab("beast", "level")
        text = words.tidy("beast t level")
        self.assertEqual(text, "beast t level")
        self.assertEqual(dict(parse51.strays([text, "a cat", "I see"])), {"t": 1})


class Spells(unittest.TestCase):
    def test_a_spell_is_read(self) -> None:
        words = vocab("pointing", "finger", "sulfur")
        (spell,) = parse51.parse_spells(fireball(), words)
        self.assertEqual((spell.name, spell.level, spell.school), ("Fireball", 3, "Evocation"))
        self.assertFalse(spell.ritual)
        self.assertEqual(spell.casting_time, "1 action")
        self.assertEqual(spell.components, "V, S, M (a tiny ball of bat guano and sulfur)")
        self.assertEqual(spell.duration, "Instantaneous")
        self.assertEqual(
            spell.text,
            "A bright streak flashes from your pointing finger.\n"
            "At Higher Levels. The damage increases by 1d6.",
        )
        self.assertEqual(spell.page, 114)

    def test_a_cantrip_and_a_ritual_level_line(self) -> None:
        self.assertEqual(parse51._kind(say("Evocation cantrip", ITALIC)), (0, "Evocation", False))
        self.assertEqual(
            parse51._kind(say("1st- level divination (ritual)", ITALIC)), (1, "Divination", True)
        )
        self.assertIsNone(parse51._kind(say("3rd-level cooking", ITALIC)))

    def test_a_missing_label_stops_the_tool(self) -> None:
        lines = fireball()
        del lines[2:4]  # no Casting Time
        with self.assertRaises(SrdError):
            parse51.parse_spells(lines, vocab())

    def test_a_singular_components_label_is_read_as_components(self) -> None:
        lines = fireball()
        lines[4] = labelled("Component:", " V, S")
        (spell,) = parse51.parse_spells(lines, vocab())
        self.assertTrue(spell.components.startswith("V, S"))


class Conditions(unittest.TestCase):
    def test_each_named_condition_is_read_and_a_missing_one_stops_the_tool(self) -> None:
        lines = [
            say("Blinded", TITLE, page=358),
            say("• A blinded creature can’t see.", BODY, page=358),
            say("Prone", TITLE, page=359),
            say("• A prone creature crawls.", BODY, page=359),
        ]
        found = parse51.parse_conditions(lines, vocab(), ["Blinded", "Prone"])
        self.assertEqual([(c.name, c.page) for c in found], [("Blinded", 358), ("Prone", 359)])
        self.assertEqual(found[0].text, "• A blinded creature can’t see.")
        with self.assertRaises(SrdError):
            parse51.parse_conditions(lines, vocab(), ["Blinded", "Stunned"])
