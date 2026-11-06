"""The nine feeling words are one list in two places (#3606): `reputation_words.WORDS` here and
`FEELINGS` in the website's `knows-feeling-words.ts`. The picker sends a word the core door must
accept, so the two are pinned to each other, in order. Skipped until the website file exists
(WebDev's #3627); after that a change to either list fails here."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from dmbot.memory import reputation_words

WEB_FILE = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "content" / "knows-feeling-words.ts"
)


class Pinned(unittest.TestCase):
    @unittest.skipUnless(WEB_FILE.exists(), "the website's feeling words are not on this branch")
    def test_the_websites_list_is_the_cores_in_the_same_order(self) -> None:
        text = WEB_FILE.read_text()
        found = re.search(r"FEELINGS\b[^=]*=\s*\[(.*?)\]", text, re.S)
        self.assertIsNotNone(found, "FEELINGS not found in the website file")
        assert found is not None
        words = re.findall(r"""["']([a-z ]+)["']""", found.group(1))
        self.assertEqual(tuple(words), reputation_words.WORDS)

    def test_the_core_accepts_every_word_the_picker_can_send(self) -> None:
        for word in reputation_words.WORDS:
            self.assertEqual(reputation_words.word_of(word), word)
