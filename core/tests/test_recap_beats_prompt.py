"""The beat sheet's paraphrase rule (#2713): a beat says only what the cited lines say. A wording
check on the prompt, because the model's drift (a motive added, a loose end tied up) cannot be
tested offline, but a rule quietly loosened in an edit can."""

from __future__ import annotations

import unittest

from dmbot.recaps import beats as bt


class TheParaphraseRule(unittest.TestCase):
    def test_a_beat_may_not_add_infer_or_resolve_beyond_its_cited_lines(self) -> None:
        text = " ".join(bt.SYSTEM.split())
        self.assertIn("paraphrase in your own words", text)
        for word in ("adding", "expanding", "inferring", "resolving"):
            self.assertIn(word, text)
        self.assertIn("the lines you cite say outright", text)

    def test_the_rule_still_forbids_quotes_and_keeps_its_place_in_the_list(self) -> None:
        text = " ".join(bt.SYSTEM.split())
        self.assertIn("4. Use character names, never the players' own names. No quotes", text)
        self.assertIn('5. "where" is the place', text)


if __name__ == "__main__":
    unittest.main()
