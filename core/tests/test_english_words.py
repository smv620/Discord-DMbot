"""The English word list (#5154): SCOWL's lower-case words up to size 60, in the package, with its
notice beside it, made again the same way by the devtool. Pure: no database, no Discord."""

from __future__ import annotations

import gzip
import unittest
from importlib import resources

from dmbot.names.english import is_english_word


class TheList(unittest.TestCase):
    def test_ordinary_words_are_in_it(self) -> None:
        for word in ("hope", "will", "grace", "bell", "Hope"):
            self.assertTrue(is_english_word(word), word)

    def test_names_are_not(self) -> None:
        for word in ("hrothgar", "thorin", "belleros", "vorna"):
            self.assertFalse(is_english_word(word), word)

    def test_it_holds_only_lower_case_letters_sorted_once(self) -> None:
        raw = resources.files("dmbot.names").joinpath("data/english_words.txt.gz").read_bytes()
        words = gzip.decompress(raw).decode("ascii").split("\n")
        self.assertEqual(words[-1], "")  # (ends with a newline)
        words = words[:-1]
        self.assertTrue(all(w.isascii() and w.isalpha() and w.islower() for w in words))
        self.assertEqual(words, sorted(set(words)))
        self.assertGreater(len(words), 70_000)

    def test_its_notice_and_attribution_are_beside_it(self) -> None:
        data = resources.files("dmbot.names").joinpath("data")
        notice = data.joinpath("SCOWL-Copyright.txt").read_text("utf-8")
        self.assertIn("Copyright 2000-2018 by Kevin Atkinson", notice)
        self.assertIn("SCOWL", data.joinpath("ATTRIBUTION.md").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
