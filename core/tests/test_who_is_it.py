"""The "who is he?" decision rule (#2790): accept, ask the DM, or keep an unnamed person. Invented
names; no AI."""

from __future__ import annotations

import unittest

from dmbot.memory.who_is_it import (
    NOT_LISTED,
    Candidate,
    Verdict,
    decide,
    noise,
)


def ranked(*scores: float) -> list[Candidate]:
    return [Candidate(f"N{i}", s) for i, s in enumerate(scores)]


class TheRule(unittest.TestCase):
    def verdict(self, *scores: float) -> Verdict:
        return decide(ranked(*scores)).verdict

    def test_the_owners_cases(self) -> None:
        self.assertEqual(self.verdict(45, 44), Verdict.ASK)  # SNR 1.02
        self.assertEqual(self.verdict(70), Verdict.ACCEPT)  # alone
        self.assertEqual(self.verdict(45, 40, 39, 39, 39), Verdict.ASK)
        self.assertEqual(self.verdict(50, 20, 15, 10, 5), Verdict.ACCEPT)
        self.assertEqual(self.verdict(85, 10, 5), Verdict.ACCEPT)
        self.assertEqual(self.verdict(85, 84), Verdict.ACCEPT)  # 80 or more needs no comparing
        self.assertEqual(self.verdict(35), Verdict.UNNAMED)

    def test_someone_not_listed_competes_as_a_candidate(self) -> None:
        close = [Candidate("Ken", 45), Candidate(NOT_LISTED, 44)]
        self.assertEqual(decide(close).verdict, Verdict.ASK)
        on_top = [Candidate(NOT_LISTED, 60), Candidate("Ken", 30)]
        self.assertEqual(decide(on_top).verdict, Verdict.UNNAMED)
        high = [Candidate(NOT_LISTED, 90), Candidate("Ken", 5)]
        self.assertEqual(decide(high).verdict, Verdict.UNNAMED)

    def test_the_thresholds_are_inclusive_where_the_owner_said_at_least(self) -> None:
        self.assertEqual(self.verdict(80, 79), Verdict.ACCEPT)
        self.assertEqual(self.verdict(40), Verdict.ACCEPT)  # one candidate: 40 is enough
        self.assertEqual(self.verdict(39.9), Verdict.UNNAMED)

    def test_the_accepted_or_asked_name_is_the_top_one(self) -> None:
        d = decide([Candidate("Mira", 10), Candidate("Ken", 60), Candidate("Bram", 5)])
        self.assertEqual((d.verdict, d.name), (Verdict.ACCEPT, "Ken"))
        self.assertIsNotNone(d.snr)
        asked = decide([Candidate("Mira", 50), Candidate("Ken", 49)])
        self.assertEqual((asked.verdict, asked.name), (Verdict.ASK, "Mira"))
        self.assertIsNone(decide([Candidate("Ken", 90)]).snr)
        self.assertIsNone(decide([Candidate("Ken", 20)]).name)

    def test_the_noise_is_the_mean_plus_three_population_deviations(self) -> None:
        self.assertAlmostEqual(noise([44]), 44.0)  # one other: its own value
        self.assertAlmostEqual(noise([20, 15, 10, 5]), 12.5 + 3 * (125 / 4) ** 0.5)
        d = decide(ranked(45, 44))
        assert d.snr is not None
        self.assertAlmostEqual(d.snr, 45 / 44)

    def test_the_runner_up_must_be_at_most_two_thirds_of_the_top(self) -> None:
        self.assertEqual(self.verdict(60, 40), Verdict.ACCEPT)  # 0.667 exactly: SNR 1.5
        self.assertEqual(self.verdict(60, 41), Verdict.ASK)

    def test_only_the_top_five_count_and_order_does_not_matter(self) -> None:
        shuffled = [Candidate(n, s) for n, s in zip("ABCDEF", (5, 50, 10, 15, 20, 1), strict=True)]
        self.assertEqual(decide(shuffled).name, "B")
        many = ranked(50, 20, 15, 10, 5, 49, 48)  # the 49 and 48 beat 5 but 49 is the runner-up
        self.assertEqual(decide(many).verdict, Verdict.ASK)

    def test_the_sixth_candidate_never_counts_toward_the_noise(self) -> None:
        # Counting the sixth (a 0) would give noise 40 and SNR 1.25; cut first, noise is 20.
        self.assertEqual(decide(ranked(50, 20, 20, 20, 20, 0)).verdict, Verdict.ACCEPT)
        d = decide(ranked(60, 10, 5, 4, 3, 2, 1, 0, 0))
        assert d.snr is not None
        self.assertAlmostEqual(d.snr, 60 / noise([10, 5, 4, 3]))

    def test_untrusted_numbers_are_dropped_or_held_to_the_range(self) -> None:
        bad = [Candidate("Ken", 70), Candidate("Mira", float("nan")), Candidate("Bram", -50)]
        self.assertEqual(decide(bad).verdict, Verdict.ACCEPT)  # -50 counts as 0: SNR is infinite
        self.assertEqual(decide([Candidate("Ken", float("nan"))]).verdict, Verdict.UNNAMED)
        self.assertEqual(decide([Candidate("Ken", 150)]).verdict, Verdict.ACCEPT)

    def test_a_tie_with_someone_not_listed_goes_to_not_listed_in_either_order(self) -> None:
        for order in ([("Ken", 45), (NOT_LISTED, 45)], [(NOT_LISTED, 45), ("Ken", 45)]):
            with self.subTest(order=order):
                d = decide([Candidate(n, c) for n, c in order])
                self.assertEqual(d.verdict, Verdict.UNNAMED)

    def test_the_not_listed_words_match_however_the_ai_cases_them(self) -> None:
        d = decide([Candidate(" Someone Not Listed ", 90), Candidate("Ken", 5)])
        self.assertEqual(d.verdict, Verdict.UNNAMED)

    def test_noise_needs_someone_to_compare_with(self) -> None:
        with self.assertRaises(ValueError):
            noise([])

    def test_no_candidates_is_an_unnamed_person_and_zero_noise_is_clear(self) -> None:
        self.assertEqual(decide([]).verdict, Verdict.UNNAMED)
        zero = decide(ranked(45, 0, 0))
        self.assertEqual(zero.verdict, Verdict.ACCEPT)


if __name__ == "__main__":
    unittest.main()
