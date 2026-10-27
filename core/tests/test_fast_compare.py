"""Measuring the fast tier against a candidate model (#2055): the option reaches the client, the
price table knows the new model, the default is unchanged, and the comparison tables are made from
counts. A fake client stands in for the AI; nothing here calls it."""

from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any
from unittest.mock import patch

from dmbot.ai import (
    DEFAULT_MODELS,
    FAST_CANDIDATE,
    PREVIOUS_FAST,
    PRICE_PER_MILLION,
    AIModels,
    AIModelTier,
    Reply,
)
from dmbot.config import parse_ai_models
from dmbot.devtools import fast_compare, sidebar_check
from dmbot.devtools.claims import cost
from dmbot.devtools.common import Metered, with_fast_model
from dmbot.devtools.costs.prices import price_of
from dmbot.devtools.recap_measure import __main__ as recap_measure
from dmbot.devtools.story_measure import __main__ as story_measure

NEW = FAST_CANDIDATE


class FakeClient:
    """Answers every call with the same short text and 100 tokens in, 20 out."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.calls = 0
        self.tier = AIModelTier.FAST  # (sidebar_check prints it)

    async def complete(self, system: str, text: str, **kwargs: Any) -> Reply:
        self.calls += 1
        return Reply("[]", False, 100, 20, self.model)


class ThePrices(unittest.TestCase):
    def test_the_new_model_has_a_price_everywhere_prices_are_kept(self) -> None:
        self.assertEqual(PRICE_PER_MILLION[NEW], (0.10, 0.50))
        self.assertEqual(cost.PRICES[NEW], (0.10, 0.50))
        cost_in, cost_out = price_of(NEW)
        self.assertEqual((cost_in.dollars, cost_out.dollars), (0.10, 0.50))
        self.assertIn("pricing", cost_in.source)
        self.assertEqual(cost_in.checked, "2026-10-19")

    def test_the_three_price_tables_agree_on_every_model_they_share(self) -> None:
        for model, (cost_in, cost_out) in PRICE_PER_MILLION.items():
            if model in cost.PRICES:
                self.assertEqual(cost.PRICES[model], (cost_in, cost_out), model)
            a, b = price_of(model)
            self.assertEqual((a.dollars, b.dollars), (cost_in, cost_out), model)

    def test_a_plan_cost_with_cents_rounds_up_to_a_whole_number(self) -> None:
        from dmbot.memory import story_plan

        micro = story_plan.micro_usd(NEW, 1_000, 200)
        self.assertIsInstance(micro, int)
        self.assertEqual(micro, 200)  # 1,000 in at 0.10 and 200 out at 0.50, in millionths
        self.assertEqual(story_plan.micro_usd("claude-sonnet-5-5", 1_000, 200), 4_000)  # unchanged

    def test_the_baseline_defaults_to_the_model_before_the_move(self) -> None:
        args = fast_compare.parse_args([])
        self.assertEqual(args.baseline, PREVIOUS_FAST)
        self.assertEqual(args.candidate, NEW)
        self.assertEqual(fast_compare.parse_args(["--baseline", "m-old"]).baseline, "m-old")

    def test_the_default_fast_model_is_haiku_5_5(self) -> None:
        self.assertEqual(DEFAULT_MODELS.fast, "claude-haiku-5-5")  # the owner's decision, #2152
        models = parse_ai_models(lambda name: "")[0]
        self.assertEqual(models.fast, "claude-haiku-5-5")
        self.assertEqual(PREVIOUS_FAST, "claude-haiku-4-5-20251001")  # kept for the rollback
        self.assertEqual(with_fast_model(models, None), models)  # no option: nothing changes
        self.assertEqual(with_fast_model(models, NEW).fast, NEW)
        self.assertEqual(with_fast_model(models, NEW).careful, models.careful)


class TheOptionReachesTheClient(unittest.IsolatedAsyncioTestCase):
    def test_every_tool_has_the_option_and_defaults_to_none(self) -> None:
        self.assertIsNone(story_measure.parse_args([]).fast_model)
        self.assertEqual(story_measure.parse_args(["--fast-model", NEW]).fast_model, NEW)

    async def test_story_measure_builds_its_client_for_that_model(self) -> None:
        built: list[AIModels] = []

        class Recording:
            def __init__(self, key: str, models: AIModels) -> None:
                built.append(models)

            def tier(self, tier: Any) -> FakeClient:
                return FakeClient(built[-1].fast)

            async def close(self) -> None:
                return None

        args = story_measure.parse_args(
            ["--extractor", "anthropic", "--fast-model", NEW, "--max-usd", "50"]
        )
        env = {"ANTHROPIC_API_KEY": "k"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(story_measure, "AnthropicClient", Recording),
        ):
            await story_measure.main_async(args)
        self.assertTrue(built)
        self.assertEqual({m.fast for m in built}, {NEW})

    async def test_recap_measure_builds_its_client_for_that_model(self) -> None:
        built: list[AIModels] = []

        class Recording:
            def __init__(self, key: str, models: AIModels) -> None:
                built.append(models)

            def tier(self, tier: Any) -> FakeClient:
                return FakeClient(built[-1].fast)

            async def close(self) -> None:
                return None

        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=False),
            patch.object(recap_measure, "AnthropicClient", Recording),
        ):
            args = type("A", (), {"writer": "anthropic", "fast_model": NEW})()
            await recap_measure.main_async(args)
        self.assertEqual([m.fast for m in built], [NEW])

    async def test_sidebar_check_builds_its_client_for_that_model(self) -> None:
        built: list[AIModels] = []

        class Recording:
            def __init__(self, key: str, models: AIModels) -> None:
                built.append(models)

            def tier(self, tier: Any) -> FakeClient:
                return FakeClient(built[-1].fast)

            async def close(self) -> None:
                return None

        async def nothing(sidebar: Any, cases: Any, **kw: Any) -> sidebar_check.Checked:
            return sidebar_check.Checked(0, 0, [])

        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=False),
            patch.object(sidebar_check, "AnthropicClient", Recording),
            patch.object(sidebar_check, "check_cases", nothing),
        ):
            await sidebar_check.run(sidebar_check.DEFAULT_CASES, NEW)
        self.assertEqual([m.fast for m in built], [NEW])


class TheComparison(unittest.IsolatedAsyncioTestCase):
    async def test_each_tool_runs_on_both_models_and_the_tables_are_counts(self) -> None:
        made: list[str] = []

        async def make_client(model: str) -> tuple[Any, Any]:
            made.append(model)

            async def close() -> None:
                return None

            return FakeClient(model), close

        tables = await fast_compare.compare(
            ["claude-haiku-4-5-20251001", NEW], ["story", "recap", "sidebar"], make_client
        )
        self.assertEqual([t.tool for t in tables], ["story", "recap", "sidebar"])
        self.assertEqual(made, ["claude-haiku-4-5-20251001", NEW] * 3)  # per tool, per model
        for table in tables:
            old, new = table.rows
            self.assertGreater(old.tokens_in, 0)
            if table.tool != "sidebar":  # (its own timing can change how many calls it makes)
                self.assertEqual((old.tokens_in, old.tokens_out), (new.tokens_in, new.tokens_out))
                assert old.dollars is not None and new.dollars is not None
                self.assertLess(new.dollars, old.dollars)  # the same tokens cost less at 5.5
        text = "\n".join(fast_compare.render(tables))
        self.assertIn(f"{NEW}: ", text)
        self.assertIn("tokens", text)
        # each call's time, with the median and the slowest, for every model of every tool (#2263)
        for table in tables:
            for row in table.rows:
                self.assertTrue(row.seconds, table.tool)
        self.assertEqual(text.count("time per call: median "), 6)
        self.assertEqual(text.count("each (s): "), 6)

    async def test_the_cap_stops_new_runs(self) -> None:
        async def make_client(model: str) -> tuple[Any, Any]:
            async def close() -> None:
                return None

            return FakeClient(model), close

        tables = await fast_compare.compare(
            ["claude-haiku-4-5-20251001", NEW], ["story"], make_client, max_usd=0.0
        )
        self.assertTrue(all("cap was reached" in r.note for r in tables[0].rows))

    async def test_a_model_with_no_price_is_refused_before_any_call(self) -> None:
        args = fast_compare.parse_args(["--candidate", "some-model", "--yes"])
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=False):
            self.assertEqual(await fast_compare.main_async(args), 2)

    async def test_an_ai_error_in_the_sidebar_is_named_in_its_own_words(self) -> None:
        from dmbot.ai import AIError

        class Refusing(FakeClient):
            async def complete(self, system: str, text: str, **kwargs: Any) -> Reply:
                raise AIError("The AI service is busy. Try again.")

        async def make_client(model: str) -> tuple[Any, Any]:
            async def close() -> None:
                return None

            return Refusing(model), close

        (table,) = await fast_compare.compare(
            ["claude-haiku-4-5-20251001"], ["sidebar"], make_client
        )
        (row,) = table.rows
        self.assertIn("AI error: The AI service is busy", row.note)
        self.assertGreaterEqual(row.failed, 1)  # the case failed; the run did not crash
        self.assertEqual(row.tokens_in, 0)

    async def test_a_pause_is_taken_between_runs_not_before_the_first(self) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        async def make_client(model: str) -> tuple[Any, Any]:
            async def close() -> None:
                return None

            return FakeClient(model), close

        with patch.object(asyncio, "sleep", fake_sleep):
            await fast_compare.compare(["a-model", NEW], ["recap"], make_client, pause=7.0)
        self.assertEqual(sleeps, [7.0])  # two runs: one wait

    async def test_without_yes_nothing_is_called(self) -> None:
        args = fast_compare.parse_args([])
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=False):
            self.assertEqual(await fast_compare.main_async(args), 0)

    def test_the_wrapper_adds_up_tokens_and_keeps_the_model(self) -> None:
        async def go() -> Metered:
            metered = Metered(FakeClient("m"))
            await metered.complete("s", "t", max_tokens=5)
            await metered.complete("s", "t")
            return metered

        metered = asyncio.run(go())
        self.assertEqual((metered.calls, metered.input_tokens, metered.output_tokens), (2, 200, 40))
        self.assertEqual(metered.model, "m")


class TheTimes(unittest.IsolatedAsyncioTestCase):
    def test_the_line_gives_the_median_and_the_slowest(self) -> None:
        from dmbot.devtools.common import timing_line

        self.assertEqual(timing_line([1.0, 3.0, 2.0]), "median 2.0 s, slowest 3.0 s over 3 calls")
        self.assertEqual(timing_line([0.44]), "median 0.4 s, slowest 0.4 s over 1 call")
        self.assertEqual(timing_line([]), "")

    async def test_the_wrapper_times_a_call_that_fails_too(self) -> None:
        class Failing:
            async def complete(self, *a: Any, **k: Any) -> Reply:
                raise RuntimeError("down")

        metered = Metered(Failing())
        with self.assertRaises(RuntimeError):
            await metered.complete("s", "t")
        self.assertEqual(len(metered.seconds), 1)

    async def test_sidebar_check_prints_each_answers_time_and_the_median_and_slowest(self) -> None:
        import contextlib
        import io

        result = await sidebar_check.check_cases(
            sidebar_check.new_sidebar(FakeClient("m")), sidebar_check.load_cases(
                sidebar_check.DEFAULT_CASES
            ).CASES[:3], quiet=True
        )  # fmt: skip
        self.assertEqual(len(result.seconds), 3)
        self.assertTrue(
            all(" s, " in line for line in result.lines if "→" not in line and " s," in line)
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            await sidebar_check.check_cases(
                sidebar_check.new_sidebar(FakeClient("m")),
                sidebar_check.load_cases(sidebar_check.DEFAULT_CASES).CASES[:2],
            )
        self.assertEqual(out.getvalue().count(" s, "), 2)  # one time line for each answer

    def test_the_cases_and_the_golden_sets_are_found_from_another_folder(self) -> None:
        import tempfile
        from pathlib import Path

        from dmbot.devtools.recap_measure.golden import load_golden as recap_golden
        from dmbot.devtools.story_measure.golden import load_golden as story_golden

        here = Path.cwd()
        with tempfile.TemporaryDirectory() as elsewhere:
            os.chdir(elsewhere)
            try:
                self.assertTrue(sidebar_check.DEFAULT_CASES.is_file())
                self.assertTrue(sidebar_check.load_cases(sidebar_check.DEFAULT_CASES).CASES)
                self.assertTrue(story_golden())
                self.assertTrue(recap_golden())
            finally:
                os.chdir(here)

    def test_a_missing_golden_folder_is_named_plainly(self) -> None:
        from pathlib import Path

        from dmbot.devtools.recap_measure.golden import GoldenError as RecapError
        from dmbot.devtools.recap_measure.golden import load_golden as recap_golden
        from dmbot.devtools.story_measure.golden import GoldenError as StoryError
        from dmbot.devtools.story_measure.golden import load_golden as story_golden

        nowhere = Path("/nonexistent-golden")
        with self.assertRaisesRegex(StoryError, "golden data folder isn't there: /nonexistent"):
            story_golden(nowhere)
        with self.assertRaisesRegex(RecapError, "golden data folder isn't there: /nonexistent"):
            recap_golden(nowhere)


if __name__ == "__main__":
    unittest.main()
