"""How the session file writes the key's names (docs/design/session_names.md, section 8): the
input description printed beside the score. Pure; invented lines."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dmbot.devtools.sample_run import __main__ as cli
from dmbot.devtools.sample_run import input_shape as shape_mod

NAMES = {
    "Dwarven Valley": ["Dwarven Valley"],
    "Hrothgar": ["Hrothgar", "Hroth"],
    "Bryn Shander": ["Bryn Shander"],
    "Targos": ["Targos"],
}


class Shape(unittest.TestCase):
    def test_each_name_is_put_in_one_kind(self) -> None:
        lines = [
            "We ride to the Dwarven Valley.",  # mid-sentence capital
            "Hrothgar waits. Then hroth laughs.",  # a capital only at a start
            "we reach bryn shander at dawn.",  # lower case only
        ]
        got = shape_mod.shape(lines, NAMES)
        self.assertEqual(
            dict(got.counts),
            {shape_mod.MID: 1, shape_mod.START: 1, shape_mod.LOWER: 1, shape_mod.ABSENT: 1},
        )
        self.assertEqual(got.capitalized, ["Dwarven Valley"])
        self.assertEqual(
            got.line(),
            "Session file, the key's 4 names: capital mid-sentence 1, capital only at a sentence "
            "start 1, lower case only 1, not in the file 1",
        )

    def test_a_start_after_an_end_mark_or_a_quote_is_a_start(self) -> None:
        got = shape_mod.shape(['He said. "Targos is cold."', "Targos, again."], NAMES)
        self.assertEqual(got.counts[shape_mod.START], 1)
        self.assertEqual(got.capitalized, [])

    def test_part_of_a_longer_word_is_not_the_name(self) -> None:
        got = shape_mod.shape(["We met Targosian traders and Hroth's-kin."], NAMES)
        self.assertEqual(got.counts[shape_mod.ABSENT], 4)

    def test_unnamed_described_and_by_design_entries_are_not_looked_for(self) -> None:
        key = {
            "nodes": [
                {"name": "Targos", "aliases": []},
                {"name": "The barbarian with the maul", "unnamed": True},
                {"name": "The kennel", "described": True, "aliases": []},
                {"name": "Wolves", "by_design": True, "aliases": []},
            ]
        }
        self.assertEqual(shape_mod.key_names(key), {"Targos": ["Targos"]})

    def test_the_run_lists_names_left_capitalized_only_for_another_version(self) -> None:
        transcript = SimpleNamespace(
            utterances=[SimpleNamespace(text="We ride with Cerric to Targos today.")]
        )
        typed = cli.describe_input(transcript, "typed")
        edited = cli.describe_input(transcript, "no-capitals")
        self.assertEqual(len(typed), 1)
        self.assertTrue(typed[0].startswith("Session file, the key's "))
        self.assertIn("Still capitalized mid-sentence in this version", edited[1])
        self.assertIn("Targos", edited[1])
