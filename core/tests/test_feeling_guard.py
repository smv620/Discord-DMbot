"""A strong feeling word needs a cue in the line the reader quoted (#3606): "a heated debate" is not
hostile. Pure; invented names."""

from __future__ import annotations

import json
import unittest
from collections import Counter

from dmbot.memory import background as bg
from dmbot.memory import reputation_words as words


def read(word: str, quote: str) -> tuple[list[tuple[str, str]], Counter[str]]:
    text = f"{quote}. Elsewhere they met again."
    lost: Counter[str] = Counter()
    item = {"a": "Orla", "how": "knows", "words": word, "b": "Brant", "quote": quote}
    got = bg.read_connections(json.dumps({"connections": [item]}), text, dropped=lost)
    return [(c.how, c.words) for c in got], lost


class TheRule(unittest.TestCase):
    def test_a_strong_word_with_no_cue_steps_down_until_one_fits(self) -> None:
        self.assertEqual(
            words.held_to_the_quote("hostile", "Orla and Brant had a heated debate"),
            ("distrusts", True),
        )
        self.assertEqual(
            words.held_to_the_quote("sworn enemy", "they argued at the market"), ("distrusts", True)
        )
        self.assertEqual(words.held_to_the_quote("devoted", "Orla knew Brant"), ("close", True))

    def test_a_cue_keeps_the_word(self) -> None:
        self.assertEqual(words.held_to_the_quote("hostile", "Orla hated Brant"), ("hostile", False))
        self.assertEqual(
            words.held_to_the_quote("sworn enemy", "Orla swore a vow against Brant"),
            ("sworn enemy", False),
        )
        self.assertEqual(words.held_to_the_quote("devoted", "Orla loved Brant"), ("devoted", False))

    def test_a_sworn_enemy_with_only_a_hostile_cue_steps_down_once(self) -> None:
        self.assertEqual(
            words.held_to_the_quote("sworn enemy", "Orla fought Brant"), ("hostile", True)
        )

    def test_the_other_words_are_left_alone(self) -> None:
        for word in ("wary", "distrusts", "neutral", "trusts", "friendly", "close"):
            self.assertEqual(words.held_to_the_quote(word, "x"), (word, False))


class OnTheLoreRead(unittest.TestCase):
    def test_a_debate_read_as_hostile_is_kept_as_distrusts_and_counted(self) -> None:
        got, lost = read("hostile", "Orla and Brant had a heated debate")
        self.assertEqual(got, [("knows", "distrusts")])
        self.assertEqual(lost["feeling_demoted"], 1)

    def test_open_enmity_is_kept_as_said(self) -> None:
        got, lost = read("hostile", "Orla hunted Brant for years")
        self.assertEqual(got, [("knows", "hostile")])
        self.assertEqual(lost["feeling_demoted"], 0)
