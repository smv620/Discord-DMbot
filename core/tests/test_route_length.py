"""A route's stated length, the words (#4255; Supervisor's ruling on #4255): each unit, leagues and
weeks, "about", a range, "a few" with no amount, and the set phrases a quote is read by. Pure."""

from __future__ import annotations

import unittest

from dmbot.memory import route_length as rl


class TheWords(unittest.TestCase):
    def check(self, words: str, amount: float | None, unit: str | None, approx: bool) -> None:
        got = rl.parse(words)
        assert got is not None
        self.assertEqual(
            (got.words, got.amount, got.unit, got.approx), (words, amount, unit, approx)
        )

    def test_each_unit(self) -> None:
        self.check("two days by cart", 2, rl.DAYS, False)
        self.check("20 miles", 20, rl.MILES, False)
        self.check("six hours", 6, rl.HOURS, False)
        self.check("a day's ride", 1, rl.DAYS, False)
        self.check("half a day", 0.5, rl.DAYS, False)

    def test_leagues_and_weeks_are_miles_and_days_with_their_words(self) -> None:
        self.check("two leagues", 6, rl.MILES, False)
        self.check("a week's sail", 7, rl.DAYS, False)
        self.check("3 weeks on foot", 21, rl.DAYS, False)

    def test_about_keeps_the_amount_and_says_so(self) -> None:
        self.check("about 20 miles", 20, rl.MILES, True)
        self.check("Roughly three days", 3, rl.DAYS, True)

    def test_a_range_keeps_its_low_end_and_says_about(self) -> None:
        self.check("two or three days", 2, rl.DAYS, True)
        self.check("2-3 days", 2, rl.DAYS, True)
        self.check("4 to 6 leagues", 12, rl.MILES, True)

    def test_vaguer_words_are_kept_with_no_amount(self) -> None:
        for words in ("a few days", "several miles", "a long way", "a tenday"):
            with self.subTest(words):
                self.check(words, None, None, False)
        self.assertIsNone(rl.parse("   "))

    def test_no_route_is_that_long_or_rounds_to_nothing(self) -> None:
        self.check("90000 miles", None, None, False)
        self.check("0.001 miles", None, None, False)  # (the column refuses a 0)


class TheSetPhrases(unittest.TestCase):
    def test_a_quote_gives_the_lengths_words(self) -> None:
        cases = {
            "Bryn Shander is two days' ride from Targos.": "two days' ride",
            "It is a day by cart to Easthaven": "a day by cart",
            "Kelvin's Cairn lies twenty miles from Targos": "twenty miles",
            "about three days' march along the Eastway": "about three days' march",
            "a week's sail to Luskan": "a week's sail",
            "two or three days by sled from Lonelywood.": "two or three days by sled",
            "The road runs 40 miles of road between them.": "40 miles of road",
        }
        for quote, words in cases.items():
            with self.subTest(quote):
                self.assertEqual(rl.find(quote), words)

    def test_a_quote_with_no_length_gives_none(self) -> None:
        for quote in (
            "A road runs from Targos to Bryn Shander.",
            "They spent two days in Targos.",  # (a stay, not a way)
            "The party has three days to reach Easthaven",  # (a deadline)
            # (two lengths may be two routes: which is which is never guessed)
            "Targos is a day's ride from Bryn Shander; Easthaven is three days by cart from it",
        ):
            with self.subTest(quote):
                self.assertEqual(rl.find(quote), "")


if __name__ == "__main__":
    unittest.main()
