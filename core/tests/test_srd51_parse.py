"""Reading the 2014 SRD 5.1 into spells and conditions (#873): the reader on made-up lines
in the shapes the 5.1 PDF has (no PDF is needed, and none is kept in the repository)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from dmbot.devtools.srd import build, parse51
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


COMMON_WORDS = [
    "the",
    "end",
    "you",
    "your",
    "next",
    "turn",
    "can’t",
    "see",
    "ball",
    "of",
    "bat",
    "and",
    "on",
    "a",
    "tiny",
    "to",
    "in",
    "or",
    "this",
    "level",
    "nature",
    "target",
    "creature",
    "for",
    "each",
    "food",
    "by",
    "then",
    "bright",
    "streak",
    "flashes",
    "from",
    "damage",
]


def vocab(*words: str, text: tuple[str, ...] = ()) -> parse51.Vocabulary:
    """A vocabulary of a few plain words and `words`."""
    return parse51.Vocabulary([*COMMON_WORDS, *words], [say(t) for t in text])


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


class Punctuation(unittest.TestCase):
    def test_a_space_before_a_full_stop_or_comma_is_closed(self) -> None:
        self.assertEqual(
            vocab().tidy("your next turn , the end . You"), "your next turn, the end. You"
        )

    def test_a_lone_letter_beside_a_piece_with_punctuation_is_joined(self) -> None:
        words = vocab("including", "your", "lips")
        self.assertEqual(words.tidy("(includin g your turn)"), "(including your turn)")
        self.assertEqual(words.tidy("on the l ips.”"), "on the lips.”")


class CutWords(unittest.TestCase):
    def test_a_letter_that_belongs_to_the_next_piece_is_moved_across(self) -> None:
        words = vocab("nature", "specific", "symbol")
        self.assertEqual(words.tidy("by the n ature"), "by the nature")  # not "then ature"
        self.assertEqual(words.tidy("a t arget"), "a target")  # not "at arget"
        self.assertEqual(words.tidy("a s ymbol of this"), "a symbol of this")
        self.assertEqual(words.tidy("in t his"), "in this")  # not "int his"

    def test_two_pieces_that_are_not_words_are_one_word_only_if_the_pdf_has_it_whole(self) -> None:
        words = vocab(text=("a great excess of Investigation and diseases",))
        self.assertEqual(words.tidy("the exc ess and dis eases"), "the excess and diseases")
        self.assertEqual(words.tidy("(Investigat ion)"), "(Investigation)")

    def test_two_real_words_the_srd_does_not_use_stay_two(self) -> None:
        words = vocab()
        for text in (
            "gum arabic", "rotten egg", "sealable lid", "nut shells", "red fox den",
            "hind leg", "the foul mimicry", "a tiny reli quary",
        ):  # fmt: skip
            with self.subTest(text):
                self.assertEqual(words.tidy(text), text)

    def test_a_die_is_not_the_start_of_a_word(self) -> None:
        self.assertEqual(vocab("ad").tidy("roll a d4 and 3 d10"), "roll a d4 and 3d10")

    def test_a_word_is_known_at_the_common_count_and_not_below_it(self) -> None:
        n = parse51.COMMON
        known = parse51.Vocabulary(COMMON_WORDS, [say("zebra")] * n)
        unknown = parse51.Vocabulary(COMMON_WORDS, [say("zebra")] * (n - 1))
        self.assertIn("zebra", known.words)
        self.assertNotIn("zebra", unknown.words)

    def test_the_ends_of_cut_words_are_never_words_of_their_own(self) -> None:
        # "ing" is common in the PDF only because words are cut before it
        lines = [say("bark ing"), say("pass ing")] * 5
        words = parse51.Vocabulary(COMMON_WORDS, lines)
        self.assertNotIn("ing", words.words)

    def test_a_pdf_word_it_uses_often_is_a_word(self) -> None:
        words = parse51.Vocabulary(COMMON_WORDS, [say("her warhorse")] * 5)
        self.assertEqual(words.tidy("give her warhorse"), "give her warhorse")

    def test_an_ordinal_is_not_taken_for_a_cut_word(self) -> None:
        self.assertEqual(vocab("level").tidy("a 4th level spell"), "a 4th level spell")

    def test_a_line_break_hyphen_goes_unless_the_srd_hyphenates_the_word(self) -> None:
        words = vocab("thunderwave", "nine-course", "course")
        self.assertEqual(words.run_on("casts thunder-", "wave at"), "casts thunderwave at")
        self.assertEqual(words.run_on("a nine-", "course meal"), "a nine-course meal")
        self.assertEqual(words.run_on("a red-", "hot rod"), "a red-hot rod")
        self.assertEqual(words.run_on("a line", "wave"), "a line wave")


class Tables(unittest.TestCase):
    def test_a_wrapped_table_row_is_joined_and_a_new_row_is_not(self) -> None:
        lines = [
            say("d10 Behavior", "Optima"),
            say("1 The creature uses all its movement to move in a", "Optima"),
            say("random direction.", "Optima"),
            say("2–6 The creature does nothing.", "Optima"),
        ]
        text = parse51._paragraphs(
            lines,
            vocab(
                "movement", "random", "direction", "nothing", "uses", "all", "its", "does", "move"
            ),
        )
        self.assertEqual(
            text.split("\n"),
            [
                "d10 Behavior",
                "1 The creature uses all its movement to move in a random direction.",
                "2–6 The creature does nothing.",
            ],
        )


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


class Building(unittest.TestCase):
    def pages(self) -> list[list[Line]]:
        contents = [say("Spell Descriptions", "Cambria")]  # the contents page: not a heading
        spells = [say("Spell Descriptions", TITLE, page=11), *fireball()]
        traps = [say("Traps", TITLE, page=12)]
        conditions = [say("Appendix PH-A:", TITLE, page=13)]
        for name in build.CONDITIONS_51:
            conditions += [say(name, TITLE, page=13), say("• It can’t do much.", page=13)]
        after = [say("Appendix PH-B:", TITLE, page=14)]
        return [contents, *[[]] * 9, spells, traps, conditions, after]

    def document(self) -> dict[str, object]:
        return {"title": "t", "url": "u", "sha256": "s", "pages": 14}

    def test_the_files_are_made_in_the_2014_shape(self) -> None:
        files = build.build_files_51(self.pages(), self.document(), {"pointing", "finger"})
        for data in files.values():
            self.assertEqual(
                (data["source"], data["edition"], data["licence"]),
                ("SRD 5.1", "2014", "CC-BY-4.0"),
            )
        self.assertEqual([e["name"] for e in files["spells.json"]["entries"]], ["Fireball"])
        self.assertEqual(len(files["conditions.json"]["entries"]), 15)
        self.assertEqual(files["conditions.json"]["entries"][0]["page"], 13)

    def test_the_edition_picks_the_folder(self) -> None:
        files = build.build_files_51(self.pages(), self.document(), set())
        self.assertEqual(build.folder_for(files), build.OUT_51)
        self.assertEqual(build.folder_for({"spells.json": {"edition": "2024"}}), build.OUT)
        self.assertNotEqual(build.OUT, build.OUT_51)

    def test_the_document_is_told_from_its_footer(self) -> None:
        def pages(footer: str) -> list[list[Line]]:
            return [[say(footer, page=1)]]

        self.assertEqual(build.which_document(pages("System Reference Document 5.1")), "5.1")
        self.assertEqual(build.which_document(pages("System Reference Document 5.2.1")), "5.2.1")
        with self.assertRaises(SrdError):
            build.which_document(pages("Some other book"))

    def test_without_the_5_2_1_data_the_tool_stops_rather_than_guess(self) -> None:
        nowhere = Path("/nonexistent-srd52-folder")
        with mock.patch.object(build, "OUT", nowhere), self.assertRaises(SrdError):
            build.known_words()
