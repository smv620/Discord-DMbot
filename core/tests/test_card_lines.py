"""The three lines at the top of a character's card, the pure part (#5050). Invented names."""

from __future__ import annotations

import re
import unittest
from typing import ClassVar

from dmbot.memory import card_lines as cl
from dmbot.memory import heard
from dmbot.recaps.beats import Beat
from dmbot.transcript.models import Line

DM, PLAYER, OTHER = 1, 2, 3


def line(n: int, user: int, text: str, **kw: object) -> Line:
    return Line(n * 1000, user, text, text, **kw)  # type: ignore[arg-type]


def beat(what: str, lines: tuple[int, ...], where: str = "") -> Beat:
    return Beat(1, what, (), lines, "event", where=where)


def has(word: str):  # type: ignore[no-untyped-def]
    return lambda text: word in text


class TheSharedFilter(unittest.TestCase):
    LINES: ClassVar[list[Line]] = [
        line(1, DM, "Orla by the ford."),
        line(2, DM, "Orla", sidebar="question"),
        line(3, PLAYER, "  "),
        line(4, OTHER, "Orla again."),
        line(5, PLAYER, "Orla said it."),
    ]

    def test_the_card_and_the_count_look_at_the_same_lines(self) -> None:
        seen = [n for n, _ in heard.countable(self.LINES, {DM, PLAYER})]
        self.assertEqual(seen, [1, 5])  # (sidebar, blank, a speaker who did not agree: out)
        counted = heard.count_lines(self.LINES, {DM, PLAYER}, lambda t: {"orla"} & {"orla"})
        self.assertEqual(counted, {("orla", DM): 1, ("orla", PLAYER): 1})


class TheHits(unittest.TestCase):
    def test_the_first_hit_is_the_earliest_session_and_its_first_line(self) -> None:
        sessions = [
            [line(1, DM, "Nothing yet.")],
            [line(1, DM, "Rook"), line(2, DM, "Orla here."), line(3, PLAYER, "Orla too.")],
            [line(1, DM, "Orla later.")],
        ]
        hit = cl.find_hit(sessions, {DM, PLAYER}, has("Orla"))
        self.assertEqual(hit, cl.Hit(1, 2))

    def test_the_last_hit_reads_the_newest_session_from_its_end(self) -> None:
        newest_first = [
            [line(1, DM, "Orla begins."), line(2, DM, "More."), line(3, PLAYER, "Orla ends.")],
            [line(1, DM, "Orla long ago.")],
        ]
        hit = cl.find_hit(newest_first, {DM, PLAYER}, has("Orla"), newest=True)
        self.assertEqual(hit, cl.Hit(0, 3))

    def test_a_name_only_a_sidebar_or_a_stranger_said_is_never_a_hit(self) -> None:
        sessions = [[line(1, DM, "Orla?", sidebar="question"), line(2, OTHER, "Orla!")]]
        self.assertIsNone(cl.find_hit(sessions, {DM, PLAYER}, has("Orla")))
        self.assertIsNone(cl.find_hit([], {DM}, has("Orla")))


class TheScene(unittest.TestCase):
    BEATS: ClassVar[tuple[Beat, ...]] = (
        beat("The party reached the Northlook inn", (3, 4, 5), "Bryn Shander"),
        beat("An argument over the ship's price", (20, 21)),
    )

    def test_the_beat_that_cites_the_line_is_the_scene(self) -> None:
        self.assertEqual(cl.scene_near(self.BEATS, 4), self.BEATS[0])
        self.assertEqual(cl.scene_near(self.BEATS, 21), self.BEATS[1])

    def test_a_line_between_scenes_belongs_to_the_nearer_and_a_tie_to_the_earlier(self) -> None:
        self.assertEqual(cl.scene_near(self.BEATS, 8), self.BEATS[0])
        self.assertEqual(cl.scene_near(self.BEATS, 18), self.BEATS[1])
        self.assertEqual(cl.scene_near(self.BEATS, 12), self.BEATS[0])  # (7 and 8 away: 12-5=7)

    def test_no_beats_is_no_scene(self) -> None:
        self.assertIsNone(cl.scene_near((), 3))
        self.assertIsNone(cl.scene_near((beat("x", ()),), 3))


class TheWords(unittest.TestCase):
    FIRST = 1_700_000_000  # 14 Nov 2023

    def test_a_scene_line_says_the_session_the_day_and_what_was_happening(self) -> None:
        text = cl.when_line(
            cl.FIRST_MET,
            3,
            self.FIRST,
            beat("The party reached the Northlook inn.", (1,), "Bryn Shander"),
        )
        self.assertEqual(
            text,
            "First met: Session 3, 14 Nov. The party reached the Northlook inn in Bryn Shander.",
        )

    def test_the_place_is_not_said_twice(self) -> None:
        text = cl.when_line(
            cl.LAST_MENTIONED,
            1,
            self.FIRST,
            beat("They reached Bryn Shander", (1,), "Bryn Shander"),
        )
        self.assertEqual(text, "Last mentioned: Session 1, 14 Nov. They reached Bryn Shander.")

    def test_a_session_with_no_recap_says_the_day_alone(self) -> None:
        self.assertEqual(
            cl.when_line(cl.LAST_MENTIONED, 7, self.FIRST, None),
            "Last mentioned: Session 7, 14 Nov.",
        )

    def test_the_lore_line_names_the_document_and_the_heading(self) -> None:
        self.assertEqual(
            cl.lore_line("Icewind Dale notes", "Bryn Shander"),
            "First met: in your lore, *Icewind Dale notes*, under 'Bryn Shander'.",
        )
        self.assertEqual(cl.lore_line("Notes"), "First met: in your lore, *Notes*.")

    def test_the_who_line_is_the_sentence_or_says_why_there_is_none(self) -> None:
        self.assertEqual(cl.who_line("A halfling innkeeper.\n", "Shafina"), "A halfling innkeeper.")
        self.assertEqual(cl.who_line(None, "Shafina"), "DMbot doesn't know much about Shafina yet.")
        self.assertEqual(cl.who_line("", "Shafina", pending=True), cl.STILL_WRITING)

    def test_a_long_sentence_is_cut_at_a_word_and_never_refused(self) -> None:
        long = "word " * 100
        cut = cl.cut_sentence(long)
        self.assertLessEqual(len(cut), cl.WHO_MAX)
        self.assertTrue(cut.endswith("…") and not cut.endswith(" …"))
        self.assertEqual(cl.cut_sentence("short"), "short")

    def test_the_words_are_plain(self) -> None:
        lines = [
            cl.when_line(cl.FIRST_MET, 1, self.FIRST, beat("They met", (1,), "the inn")),
            cl.lore_line("Notes", "Inn"),
            cl.NEVER_AT_THE_TABLE,
            cl.STILL_WRITING,
            cl.who_line(None, "Shafina"),
        ]
        for text in lines:
            self.assertIsNone(re.search(r"\b(node|entity|graph)\b", text, re.I), text)
