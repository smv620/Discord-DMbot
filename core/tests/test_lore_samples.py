"""The invented lore samples for the smart-guess test (#2959): all five load, each document is 200
to 500 words, each has 8 to 15 statements with at least a third of each kind, every expectation is
one of the two words, the characters named as knowing a spoiler have a backstory, and every
statement's names appear in its document. Invented names only."""

from __future__ import annotations

import unittest

from tests.lore_samples import load_all

SAMPLES = load_all()


class TheSamples(unittest.TestCase):
    def test_all_five_load(self) -> None:
        self.assertEqual(
            [s.name for s in SAMPLES],
            ["council", "cult", "guild", "house", "temple"],
        )

    def test_each_document_is_200_to_500_words(self) -> None:
        for sample in SAMPLES:
            with self.subTest(sample.name):
                self.assertTrue(200 <= len(sample.text.split()) <= 500)

    def test_each_has_8_to_15_statements_with_at_least_a_third_of_each_kind(self) -> None:
        for sample in SAMPLES:
            with self.subTest(sample.name):
                total = len(sample.statements)
                worlds = sum(s.expect == "world" for s in sample.statements)
                spoilers = total - worlds
                self.assertTrue(8 <= total <= 15)
                self.assertGreaterEqual(worlds * 3, total)
                self.assertGreaterEqual(spoilers * 3, total)

    def test_every_expectation_is_world_or_spoiler(self) -> None:
        for sample in SAMPLES:
            for statement in sample.statements:
                with self.subTest(sample.name, text=statement.text):
                    self.assertIn(statement.expect, ("world", "spoiler"))

    def test_only_named_backstories_are_the_ones_that_know_a_spoiler(self) -> None:
        for sample in SAMPLES:
            names = {pc.character for pc in sample.pc_backstories}
            for statement in sample.statements:
                with self.subTest(sample.name, text=statement.text):
                    self.assertTrue(set(statement.world_for) <= names)

    def test_the_names_in_each_statement_appear_in_its_document(self) -> None:
        for sample in SAMPLES:
            text = sample.text.casefold()
            for statement in sample.statements:
                # "A — kind — B" names its two ends; a plain sentence must appear whole.
                parts = [p.strip() for p in statement.text.split(" — ")]
                names = (parts[0], parts[-1]) if len(parts) == 3 else (statement.text,)
                for name in names:
                    with self.subTest(sample.name, name=name):
                        self.assertIn(name.casefold(), text)
