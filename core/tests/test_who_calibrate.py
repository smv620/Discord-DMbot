"""The calibration runner for "who is he?" (#2790, slice 1): the estimate, the run, the two bars
and what it prints. A stand-in AI that answers from the set's own key; invented passages; no
network and no key."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import unittest
from typing import Any
from unittest import mock

from dmbot.ai import Reply
from dmbot.devtools import who_calibrate
from dmbot.devtools.common import Metered
from dmbot.memory import who_ask
from dmbot.memory.who_calibration import ASK, Passage, Report, bars, load, tally
from dmbot.memory.who_is_it import Decision, Verdict

SET = who_calibrate.DEFAULT_SET
MODEL = "claude-haiku-5-5"  # a model with a price on file


class FromTheKey:
    """Answers each question from the passage's right answer: sure where the text settles it, an
    even split where it leaves it open. `fail` passage ids raise on the scoring call."""

    def __init__(self, passages: list[Passage], fail: frozenset[str] = frozenset()) -> None:
        self.by_text = {who_ask.quoted(p.text): p for p in passages}
        self.fail = fail
        self.calls = 0

    async def complete(self, system: str, text: str, **kwargs: Any) -> Reply:
        self.calls += 1
        found = re.search(r"<passage>\n(.*)\n</passage>", text, re.S)
        assert found is not None
        p = self.by_text[found.group(1)]
        scoring = text.endswith(who_ask.SCORES_ASK)
        if scoring and p.id in self.fail:
            raise RuntimeError("down")
        if p.answer == ASK:
            first, second = p.known[0], p.known[1]
            body: dict[str, Any] = (
                {
                    "candidates": [
                        {"name": first, "confidence": 45},
                        {"name": second, "confidence": 44},
                    ]
                }
                if scoring
                else {"name": first if self.calls % 2 else second}
            )
        else:
            body = (
                {"candidates": [{"name": p.answer, "confidence": 95}]}
                if scoring
                else {"name": p.answer}
            )
        return Reply(json.dumps(body), cut=False, input_tokens=100, output_tokens=10)


class TheEstimate(unittest.TestCase):
    def test_six_calls_a_passage_and_tokens_for_each(self) -> None:
        passages = load(SET)[:3]
        calls, tokens_in, tokens_out = who_calibrate.estimate(passages)
        self.assertEqual(calls, 3 * (who_ask.SAMPLES + 1))
        self.assertGreater(tokens_in, calls * 100)  # the instructions alone are that long
        self.assertEqual(
            tokens_out,
            3 * (who_calibrate.SCORE_OUT_TOKENS + who_ask.SAMPLES * who_calibrate.PICK_OUT_TOKENS),
        )

    def test_the_whole_set_costs_little_on_the_fast_model(self) -> None:
        _, tokens_in, tokens_out = who_calibrate.estimate(load(SET))
        money = who_calibrate.dollars(MODEL, tokens_in, tokens_out)
        assert money is not None
        self.assertLess(money, who_calibrate.MAX_USD)

    def test_a_model_with_no_price_has_no_figure(self) -> None:
        self.assertIsNone(who_calibrate.dollars("no-such-model", 1, 1))


class TheRun(unittest.TestCase):
    def test_answers_from_the_key_meet_the_sure_bar_leave_the_band_empty_and_ask_open_ones(
        self,
    ) -> None:
        passages = load(SET)
        fake = FromTheKey(passages)
        report, left = asyncio.run(who_calibrate.run_set(fake, passages, MODEL))
        self.assertEqual(left, 0)
        self.assertEqual(report.total, len(passages))
        self.assertEqual(fake.calls, len(passages) * (who_ask.SAMPLES + 1))
        sure, likely = bars(report)
        self.assertTrue(sure.met)
        self.assertIsNone(likely.met)  # nothing landed in the band
        open_ = sum(p.answer == ASK for p in passages)
        self.assertEqual(report.ambiguous.total, open_)
        self.assertEqual(report.ambiguous.right, open_)
        self.assertEqual(report.ai_failed, 0)

    def test_a_failed_call_is_counted_apart_and_never_as_wrong(self) -> None:
        passages = load(SET)[:4]
        fake = FromTheKey(passages, fail=frozenset({passages[0].id}))
        report, _ = asyncio.run(who_calibrate.run_set(fake, passages, MODEL))
        self.assertEqual(report.total, 4)
        self.assertEqual(report.ai_failed, 1)
        wrong = sum(t - r for t, r in report.counts().values())
        self.assertEqual(wrong, 0)
        text = "\n".join(who_calibrate.render(report, 0))
        self.assertIn("1 passages got no answer", text)

    def test_it_stops_starting_passages_at_the_cap(self) -> None:
        passages = load(SET)[:5]
        fake = FromTheKey(passages)
        # Each passage costs 600 tokens in and 60 out here: a tiny cap stops after the first.
        report, left = asyncio.run(who_calibrate.run_set(fake, passages, MODEL, max_usd=1e-9))
        self.assertEqual(report.total, 1)
        self.assertEqual(left, 4)


class TheBars(unittest.TestCase):
    def report(self, high: tuple[int, int], band: tuple[int, int]) -> Report:
        r = Report()
        r.accepted_high.total, r.accepted_high.right = high
        r.accepted_band.total, r.accepted_band.right = band
        return r

    def test_ninety_and_eighty_percent_are_the_bars(self) -> None:
        sure, likely = bars(self.report((10, 9), (10, 8)))
        self.assertTrue(sure.met)
        self.assertTrue(likely.met)
        sure, likely = bars(self.report((10, 8), (10, 7)))
        self.assertFalse(sure.met)
        self.assertFalse(likely.met)

    def test_an_empty_band_has_nothing_to_judge(self) -> None:
        _, likely = bars(self.report((5, 5), (0, 0)))
        self.assertIsNone(likely.share)
        self.assertIn("nothing to judge", likely.line())

    def test_the_lines_are_counts_only(self) -> None:
        sure, likely = bars(self.report((19, 18), (5, 3)))
        self.assertEqual(sure.line(), "sure: 18 of 19 right (95%), bar 90%: met")
        self.assertEqual(likely.line(), "likely: 3 of 5 right (60%), bar 80%: NOT met")


class TheOutput(unittest.TestCase):
    def test_it_prints_counts_and_never_a_passage_or_a_name(self) -> None:
        passages = load(SET)
        metered = Metered(FromTheKey(passages))
        report, left = asyncio.run(who_calibrate.run_set(metered, passages, MODEL))
        text = "\n".join(who_calibrate.render(report, left, metered, MODEL))
        self.assertIn("sure:", text)
        self.assertIn("likely:", text)
        self.assertIn("ai_failed: 0", text)
        self.assertIn(f"calls {len(passages) * (who_ask.SAMPLES + 1)}", text)
        for p in passages:
            for name in (*p.known, p.phrase):
                if len(name) > 3:
                    self.assertNotIn(name, text)
            self.assertNotIn(p.text, text)

    def test_without_yes_nothing_is_called(self) -> None:
        code, out = self.main([])
        self.assertEqual(code, 0)
        self.assertIn("calls, about", out)
        self.assertIn("No calls made", out)

    def test_a_model_with_no_price_stops_before_any_call(self) -> None:
        code, _ = self.main(
            ["--yes"], {"AI_MODEL_FAST": "claude-no-price-1", "ANTHROPIC_API_KEY": "x"}
        )
        self.assertEqual(code, 2)

    def test_yes_without_a_key_stops(self) -> None:
        code, _ = self.main(["--yes"])
        self.assertEqual(code, 2)

    def test_an_estimate_over_the_cap_is_not_started(self) -> None:
        code, _ = self.main(["--yes", "--max-usd", "0.000001"], {"ANTHROPIC_API_KEY": "x"})
        self.assertEqual(code, 2)

    @staticmethod
    def main(argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
        """Run the tool's main with only `env` set, a client that fails if it is ever built, and
        stdout captured."""

        def never(*a: Any, **k: Any) -> Any:
            raise AssertionError("no client may be built")

        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, env or {}, clear=True),
            mock.patch.object(who_calibrate, "AnthropicClient", never),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = asyncio.run(who_calibrate.main_async(who_calibrate.parse_args(argv)))
        return code, out.getvalue()


class TheTally(unittest.TestCase):
    def test_tally_counts_an_accept_and_a_failure(self) -> None:
        p = Passage("x", "Ken ran. He fell.", "He", ("Ken",), "Ken", "pronoun")
        r = Report()
        tally(r, p, Decision(Verdict.ACCEPT, "Ken", None, 95.0))
        tally(r, p, None)
        self.assertEqual(r.counts()["accepted_high"], (1, 1))
        self.assertEqual(r.ai_failed, 1)
        self.assertNotIn("ai_failed", r.counts())  # a failure is never a wrong answer
        self.assertEqual(r.total, 2)


if __name__ == "__main__":
    unittest.main()
