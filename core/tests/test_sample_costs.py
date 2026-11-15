"""The sample run's cost block (#4087): lines for each step with tokens and dollars where they are
known, and a total that is the sum of the lines. No database, no network."""

from __future__ import annotations

import unittest

from dmbot.devtools.sample_run import costs


class TheCostBlock(unittest.TestCase):
    def test_a_line_with_tokens_and_one_without(self) -> None:
        self.assertEqual(
            costs.CostLine("recap (no audio)", 0.386, 5875, 1133).text(),
            "- recap (no audio): tokens in 5875, out 1133, cost $0.39",
        )
        self.assertEqual(
            costs.CostLine("lore read", 0.0).text(), "- lore read: tokens not recorded, cost $0.00"
        )

    def test_the_total_is_the_sum_of_the_lines(self) -> None:
        lines = [
            costs.CostLine("lore read", 0.12),
            costs.CostLine("AI decides (lore and session)", 0.02, 5875, 1133),
            costs.CostLine("recap (no audio)", 0.386, 100, 50),
        ]
        out = costs.block(lines)
        self.assertEqual(out[0], "Cost of this run:")
        self.assertEqual(out[-1], "total cost $0.53")
        self.assertEqual(len(out), 5)

    def test_a_run_with_no_paid_step_totals_zero(self) -> None:
        self.assertEqual(costs.block([])[-1], "total cost $0.00")

    def test_the_ledger_lines_name_the_steps_and_price_a_credit_at_a_tenth_of_a_cent(self) -> None:
        lines = costs.ledger_lines({"import": 40_000, "lore": 12_000, "script": 500})
        self.assertEqual(
            [(x.name, round(x.dollars, 3)) for x in lines],
            [
                ("session import (cleaning and story read)", 40.0),
                ("lore read", 12.0),
                ("script", 0.5),
            ],
        )

    def test_the_recap_tools_spend_line_is_read_and_nothing_else_of_its_report(self) -> None:
        report = (
            "The recap said something about Mira Voss and a secret.\n"
            "Models: fast m1, strong m2\n"
            "Calls: 15 (12 fast, 3 strong); tokens in 9000, out 2500; spend $0.100 + $0.286\n"
        )
        line = costs.recap_line(report)
        assert line is not None
        self.assertEqual(
            (line.tokens_in, line.tokens_out, round(line.dollars, 3)), (9000, 2500, 0.386)
        )
        self.assertNotIn("Mira", line.text())

    def test_a_recap_with_no_price_on_file_is_a_line_that_says_so_and_the_total_is_incomplete(
        self,
    ) -> None:
        report = "Calls: 3 (3 fast); tokens in 10, out 5; spend no price on file for this model\n"
        line = costs.recap_line(report)
        assert line is not None
        self.assertTrue(line.unpriced)
        self.assertIn("some calls had no price on file", line.text())
        self.assertIn("incomplete", costs.block([line])[-1])
        self.assertIsNone(costs.recap_line("nothing here"))

    def test_part_of_a_recap_priced_adds_what_is_priced_and_marks_it_short(self) -> None:
        report = "tokens in 9, out 4; spend $0.100 + no price on file for this model\n"
        line = costs.recap_line(report)
        assert line is not None
        self.assertEqual((round(line.dollars, 3), line.unpriced), (0.1, True))

    def test_a_source_that_could_not_be_read_makes_the_total_incomplete(self) -> None:
        self.assertIn("incomplete", costs.block([costs.CostLine("x", 0.5)], incomplete=True)[-1])
        self.assertNotIn("incomplete", costs.block([costs.CostLine("x", 0.5)])[-1])
