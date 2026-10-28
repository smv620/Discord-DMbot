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


def ranked(*scores: float, names: str = "ABCDEFG") -> list[Candidate]:
    return [Candidate(names[i], s) for i, s in enumerate(scores)]


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

    def test_no_candidates_is_an_unnamed_person_and_zero_noise_is_clear(self) -> None:
        self.assertEqual(decide([]).verdict, Verdict.UNNAMED)
        zero = decide(ranked(45, 0, 0))
        self.assertEqual(zero.verdict, Verdict.ACCEPT)


if __name__ == "__main__":
    unittest.main()
