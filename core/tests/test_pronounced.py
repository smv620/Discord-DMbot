"""The pronunciation parser (#2171): what counts as a name said aloud with its sound, and which
name it belongs to."""

from __future__ import annotations

from unittest import TestCase

from dmbot.transcript import pronounced as p

KAEL = "e" * 32
MIRA = "f" * 32


class WhatCounts(TestCase):
    def phrases(self, text: str) -> list[str]:
        return [x for x, _ in p.find(text)]

    def test_the_usual_ways_of_saying_it(self) -> None:
        for line, want in [
            ("it's pronounced KAY-el", "Kay-el"),
            ("Kael is pronounced Kay-el by the way", "Kay-el"),
            ("you pronounce it like Kale", "Kale"),
            ("pronounce it as Kay el", "Kay el"),
            ("say it like Kay-el, okay", "Kay-el"),
        ]:
            self.assertEqual(self.phrases(line), [want], line)

    def test_a_question_or_a_loose_phrase_is_not_one(self) -> None:
        for line in [
            "how do you pronounce it?",
            "how is it pronounced",
            "say it again",
            "you pronounce it wrong",
            "it's pronounced differently",
            "pronounce it, Kay-el",
        ]:
            self.assertEqual(self.phrases(line), [], line)

    def test_it_stops_at_a_joining_word_and_is_short(self) -> None:
        self.assertEqual(self.phrases("it's pronounced Kay-el and he is tall"), ["Kay-el"])
        self.assertEqual(self.phrases("pronounced " + "a" * 40), [])

    def test_the_cheap_check(self) -> None:
        self.assertTrue(p.mentions_pronunciation("it's pronounced Kay-el"))
        self.assertFalse(p.mentions_pronunciation("we go to the tavern"))


class WhichName(TestCase):
    def test_the_name_said_before_it_in_the_same_line(self) -> None:
        w = p.PronunciationWatcher()
        got = w.note(1, "Kael is pronounced Kay-el", [(KAEL, 0)], 0.0)
        self.assertEqual(got, [p.Pronounced(KAEL, "Kay-el")])

    def test_the_name_said_just_before_in_another_line_or_by_another_person(self) -> None:
        w = p.PronunciationWatcher()
        self.assertEqual(w.note(1, "we meet Kael", [(KAEL, 8)], 0.0), [])
        got = w.note(2, "it's pronounced Kay-el", [], 5.0)
        self.assertEqual(got, [p.Pronounced(KAEL, "Kay-el")])

    def test_too_long_ago_or_two_names_attach_to_none(self) -> None:
        w = p.PronunciationWatcher()
        w.note(1, "Kael", [(KAEL, 0)], 0.0)
        self.assertEqual(w.note(2, "it's pronounced Kay-el", [], 40.0), [])
        w = p.PronunciationWatcher()
        w.note(1, "Kael and Mira", [(KAEL, 0), (MIRA, 9)], 0.0)
        self.assertEqual(w.note(2, "it's pronounced Kay-el", [], 3.0), [])
        self.assertEqual(
            w.note(2, "Kael, Mira, it's pronounced Kay-el", [(KAEL, 0), (MIRA, 6)], 4.0), []
        )

    def test_a_speaker_who_stops_is_forgotten(self) -> None:
        w = p.PronunciationWatcher()
        w.note(1, "Kael", [(KAEL, 0)], 0.0)
        w.forget(1)
        self.assertEqual(w.note(2, "it's pronounced Kay-el", [], 1.0), [])
