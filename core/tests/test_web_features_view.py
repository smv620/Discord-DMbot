"""The campaign settings page's list of switched-on features (#3693): the bot writes the finished
list as JSON on a volume the web API reads read-only; the reader trusts only its shape. A DM's
campaign page carries it, a player's never does."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dmbot.web import features_view

GOOD = {
    "version": 1,
    "writtenAt": 1_790_000_000,
    "on": [{"name": "Written recaps", "words": "DMbot writes a recap of each session."}],
    "off": ["Spoken recaps"],
}


class Reader(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def put(self, content: object) -> None:
        text = content if isinstance(content, str) else json.dumps(content)
        (Path(self.dir.name) / features_view.FILE).write_text(text, encoding="utf-8")

    def read(self) -> object:
        return features_view.read_features(self.dir.name)

    def test_a_good_file_is_read_back_as_written(self) -> None:
        self.put(GOOD)
        self.assertEqual(
            self.read(),
            {"writtenAt": 1_790_000_000, "on": GOOD["on"], "off": ["Spoken recaps"]},
        )

    def test_no_directory_no_file_or_a_broken_file_is_none(self) -> None:
        self.assertIsNone(features_view.read_features(""))
        self.assertIsNone(self.read())  # no file yet
        for bad in ("not json", "[]", "null", "{" * 5000):
            self.put(bad)
            self.assertIsNone(self.read(), bad[:20])

    def test_an_odd_shape_is_none_never_an_error(self) -> None:
        odd = [
            {**GOOD, "version": 2},
            {**GOOD, "writtenAt": "now"},
            {**GOOD, "writtenAt": True},
            {**GOOD, "on": "x"},
            {**GOOD, "on": [{"name": "A"}]},
            {**GOOD, "on": [{"name": "", "words": "x"}]},
            {**GOOD, "on": [{"name": "A\u0007", "words": "x"}]},
            {**GOOD, "on": [{"name": "A", "words": "x" * 301}]},
            {**GOOD, "off": [3]},
            {**GOOD, "off": ["x"] * 61},
        ]
        for content in odd:
            self.put(content)
            self.assertIsNone(self.read(), str(content)[:60])

    def test_a_file_over_the_size_limit_is_none(self) -> None:
        self.put(json.dumps({**GOOD, "pad": "x" * features_view.MAX_BYTES}))
        self.assertIsNone(self.read())

    def test_extra_keys_are_dropped_and_spaces_tidied(self) -> None:
        self.put(
            {**GOOD, "secret": "sk-1234", "on": [{"name": " A  b ", "words": "c   d", "key": "k"}]}
        )
        got = self.read()
        self.assertEqual(
            got,
            {
                "writtenAt": 1_790_000_000,
                "on": [{"name": "A b", "words": "c d"}],
                "off": ["Spoken recaps"],
            },
        )
        self.assertNotIn("sk-1234", str(got))
