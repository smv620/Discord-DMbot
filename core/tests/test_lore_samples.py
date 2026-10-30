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

    def test_each_has_a_title(self) -> None:
        for sample in SAMPLES:
            with self.subTest(sample.name):
                self.assertTrue(sample.title.strip())

    def test_at_most_one_pc_backstory(self) -> None:
        for sample in SAMPLES:
            with self.subTest(sample.name):
                self.assertLessEqual(len(sample.pc_backstories), 1)

    def test_world_for_names_only_pcs_with_a_backstory(self) -> None:
        for sample in SAMPLES:
            names = {pc.character for pc in sample.pc_backstories}
            for statement in sample.statements:
                with self.subTest(sample.name, text=statement.text):
                    self.assertTrue(set(statement.world_for) <= names)

    def test_world_for_is_empty_on_every_world_statement(self) -> None:
        # Only a spoiler can be known to a character because of a backstory.
        for sample in SAMPLES:
            for statement in sample.statements:
                if statement.expect == "world":
                    with self.subTest(sample.name, text=statement.text):
                        self.assertEqual(statement.world_for, ())

    def test_the_names_in_each_statement_appear_in_its_document(self) -> None:
        for sample in SAMPLES:
            for statement in sample.statements:
                # "A — kind — B" names its two ends; a plain sentence must appear whole. The match
                # is exact, case included, so a name is written the way the document writes it.
                parts = [p.strip() for p in statement.text.split(" — ")]
                names = (parts[0], parts[-1]) if len(parts) == 3 else (statement.text,)
                for name in names:
                    with self.subTest(sample.name, name=name):
                        self.assertIn(name, sample.text)
