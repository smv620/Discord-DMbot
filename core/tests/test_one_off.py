"""One-off actions and predictions (#3244): the word check that keeps a hit or a guess off the
story graph. Pure words, no database."""

from __future__ import annotations

import unittest

from dmbot.memory import one_off


class OneOffWords(unittest.TestCase):
    def test_a_moment_is_named_past_its_helper_word(self) -> None:
        moments = ("hits", "swings at", "can see", "cannot catch", "can't catch", "is hit by")
        more = ("feeds and waters", "just swung at", "spotted", "chases", "isn't hitting")
        for text in (*moments, *more, "throws", "watches"):
            self.assertTrue(one_off.is_one_off(text), text)

    def test_a_lasting_tie_or_an_ability_is_not_a_moment(self) -> None:
        lasting = ("is held by", "carries", "owns", "serves", "hold", "can survive", "can carry")
        other = (
            "is",
            "",
            "   ",
            "has stature in",
            "owes",
            "cope with",
            "connected to",
            "seen as a hero",
            "seen by the guild",
            "caught and imprisoned",
            "attacked and kidnapped",
            "fed the orphans",
            "feeds the orphans",
            "struck a deal with",
            "sees to the upkeep of",
            "has hit points",
            "watches over",
            "caught by the guards",
        )
        for text in (*lasting, *other):
            self.assertFalse(one_off.is_one_off(text), text)

    def test_a_prediction_is_read_from_its_first_words(self) -> None:
        for text in ("will likely go to", "will", "probably will reach", "also will visit",
                     "is going to reach", "are about to leave", "shall return to"):  # fmt: skip
            self.assertTrue(one_off.is_prediction(text), text)
        # a hedge is about now, and a promise is a lasting stance: both stay with the DM
        for text in ("is going", "goes to", "seems to be", "perhaps knows", "", "is a friend of",
                     "might be the brother of", "may know", "would die for", "will always serve",
                     "will never betray"):  # fmt: skip
            self.assertFalse(one_off.is_prediction(text), text)


if __name__ == "__main__":
    unittest.main()
