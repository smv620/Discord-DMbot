"""The respelled sample session (dev only, #5133): a few key names written several ways from a
fixed table, in turn, so slice 3's grouping can be scored before the spoken recording exists."""

from __future__ import annotations

import unittest

from dmbot.devtools import sample_respell as sr
from dmbot.memory.scan import _same_thing as same_thing


class TheRespelling(unittest.TestCase):
    def test_a_name_s_spellings_take_turns_and_round_again(self) -> None:
        text = "Vaelrith came. Vaelrith left. Vaelrith sat. Vaelrith rose."
        self.assertEqual(
            sr.respell(text), "Vaelrith came. Vale rith left. Valerith sat. Vaelrith rose."
        )

    def test_only_whole_names_are_respelled_and_every_other_word_stays(self) -> None:
        text = "We passed Bryn Shandering and the Termalaines, then Bryn Shander."
        self.assertEqual(sr.respell(text), text)  # (the first whole Bryn Shander is itself)
        two = "Bryn Shander, then Bryn Shander again."
        self.assertEqual(sr.respell(two), "Bryn Shander, then Brin Shandar again.")

    def test_the_same_text_is_respelled_the_same_way_every_time(self) -> None:
        text = "Jebidiah and Termalaine. " * 5
        self.assertEqual(sr.respell(text), sr.respell(text))

    def test_the_table_has_two_or_three_spellings_each_and_one_split(self) -> None:
        # (the ruling's table: each first spelling the name itself, so it is still heard right)
        self.assertTrue(all(2 <= len(v) <= 3 and v[0] == k for k, v in sr.RESPELLINGS.items()))
        self.assertTrue(
            any(len(s.split()) > len(k.split()) for k, v in sr.RESPELLINGS.items() for s in v)
        )

    def test_which_spellings_fold_today_is_known(self) -> None:
        # (the series is honest: some realistic spellings don't fold with today's rule; this
        # list is what the PR reports, and a change to the rule shows here first)
        folds = {(k, s): same_thing(k, s) for k, v in sr.RESPELLINGS.items() for s in v[1:]}
        self.assertEqual(
            sorted(pair for pair, ok in folds.items() if not ok),
            [("Jebidiah", "Jebediah"), ("Vaelrith", "Valerith")],
        )
