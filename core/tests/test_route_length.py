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

    def test_on_the_road_and_by_the_river(self) -> None:
        self.assertEqual(rl.find("Three days on the river."), "Three days on the river")
        self.assertEqual(rl.find("1 hour by dogsled on the road."), "1 hour by dogsled")
        # ("by the river" is beside it: a camp or a rest, not a way)
        self.assertEqual(rl.find("You camp two days by the river, then go on."), "")
        self.assertEqual(rl.find("You rest an hour by the road."), "")

    def test_a_bare_amount_to_the_routes_own_end(self) -> None:
        # (#4255 slice 1b: "to" and a name the route ends at; never "to reach", never another place)
        ends = ["Easthaven", "Bryn Shander"]
        cases = {
            "You can take the dogsled five hours to Easthaven, then rest.": "five hours",
            "It's only 1 hour to Bryn Shander, so you get there by 3:00.": "1 hour",
            "The party has three days to reach Easthaven": "",
            "It is two days to Luskan from here.": "",  # (not this route's end)
            "Two days to Easthaven, or a day to Bryn Shander.": "",  # (two lengths: none)
            "They have three days to Bryn Shander's festival.": "",  # (a deadline, not a way)
        }
        for quote, words in cases.items():
            with self.subTest(quote):
                self.assertEqual(rl.find(quote, places=ends), words)
        self.assertEqual(rl.find("five hours to Easthaven"), "")  # (no names: no such phrase)

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


class OnTheWay(unittest.TestCase):
    """A journey's length from the DM's lines on the way (#4255 slice 1b): a set phrase only on a
    line that names one of its ends, the name-tied form anywhere, exactly one across the stretch."""

    ENDS = ("Targos", "Bryn Shander")

    def test_only_a_line_naming_an_end_gives_its_set_phrase(self) -> None:
        lines = ["Kelvin's Cairn is twenty miles from here.", "You reach Bryn Shander."]
        self.assertEqual(rl.find_on_way(lines, self.ENDS), "")
        lines = ["Bryn Shander is a day by sled from here.", "You reach it."]
        self.assertEqual(rl.find_on_way(lines, self.ENDS), "a day by sled")

    def test_the_name_tied_form_counts_anywhere_and_two_give_none(self) -> None:
        self.assertEqual(
            rl.find_on_way(["Off you go.", "It's only 1 hour to Bryn Shander."], self.ENDS),
            "1 hour",
        )
        lines = ["It's 1 hour to Bryn Shander.", "Well, two hours to Bryn Shander."]
        self.assertEqual(rl.find_on_way(lines, self.ENDS), "")

    def test_no_ends_no_length(self) -> None:
        self.assertEqual(rl.find_on_way(["two days by cart to Targos."], ()), "")


if __name__ == "__main__":
    unittest.main()
