"""The lore-read scorer (#2757): where each pair of an answer key is lost, counts only."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from dmbot.devtools import score_lore as sl

DATA = Path(__file__).parent / "fixtures" / "score_lore"
ARGS = [
    "--text", str(DATA / "score_lore_text.txt"),
    "--reply", str(DATA / "score_lore_reply.json"),
    "--key", str(DATA / "score_lore_key.json"),
]  # fmt: skip


class TheScorer(unittest.TestCase):
    def run_it(self) -> dict[str, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(sl.main(ARGS), 0)
        return {
            line.split(":")[0]: line.split(": ")[1]
            for line in out.getvalue().splitlines()
            if line.startswith("p0")
        }

    def test_each_way_a_pair_is_lost_or_kept(self) -> None:
        got = self.run_it()
        self.assertEqual(got["p01"], "kept kind=ok")  # a mentor, by its short alias "Mira"
        self.assertEqual(got["p02"], "not_listed")  # the AI never wrote that pair
        self.assertEqual(got["p03"], "kept")  # a place the person was born in
        self.assertEqual(got["p04"], "kept kind=ok")  # a sister, the name in the same sentence
        self.assertEqual(got["p05"], "kept kind=ok")  # a husband is a spouse
        self.assertEqual(got["p06"], "dropped:word_not_allowed")  # a free label the quote lacks
        self.assertEqual(got["p07"], "no_entry")  # an end that is no name of the read
        # A pronoun whose name is two sentences back is held for the who-is-it check, not kept.
        self.assertEqual(got["p08"], "held_for_who_is_it")

    def test_the_output_has_no_names_or_words_of_the_text(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            sl.main(ARGS)
        shown = out.getvalue()
        for word in (
            "Mira", "Voss", "Lenna", "Corvin", "Harrowgate", "mentor", "Pell", "Daric", "Odo",
            "Cerro", "Maddox", "Ashe", "Brannoch", "sibling", "spouse",
        ):  # fmt: skip
            self.assertNotIn(word, shown)

    def test_two_ends_of_one_name_match_by_a_word_of_three_letters(self) -> None:
        self.assertTrue(sl._same("Mira", "Mira Tolliver"))
        self.assertFalse(sl._same("Al", "Al Voss"))
        self.assertFalse(sl._same("Mira", "Mirko Tolliver"))


if __name__ == "__main__":
    unittest.main()
