"""The story-extractor measurement (#1268): the invented golden set's format and coverage, the
scoring by kind on canned model outputs, the report, and that nothing costs money unasked.
No test here calls the AI."""

import asyncio
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dmbot.ai import Reply
from dmbot.devtools.claims.extract import transcript
from dmbot.devtools.claims.scenes import Scene
from dmbot.devtools.story_measure import __main__ as measure_main
from dmbot.devtools.story_measure import report
from dmbot.devtools.story_measure.golden import (
    GOLDEN,
    MAX_LINES,
    MIN_LINES,
    GoldenError,
    load_golden,
    parse_golden,
    summary,
)

SCENES = load_golden()


def answer(scene: Scene, *, how: str | None = None, extra: list[dict[str, Any]] | None = None,
           skip: int = 0) -> str:  # fmt: skip
    """What a perfect extractor says about one scene, in the prompt's JSON shape."""
    claims = [
        {
            "subject": e.subject,
            "relationship": e.relationship,
            "object": e.object,
            "details": "",
            "lines": [line.number],
            "how": how or e.how,
        }
        for line in scene.lines
        for e in line.expected
    ]
    return json.dumps({"claims": claims[skip:] + (extra or [])})


class Canned:
    """A client that replies from a function of the scene it is asked about."""

    def __init__(self, reply: Any = answer, tokens: tuple[int, int] = (500, 100)) -> None:
        self.by_text = {transcript(s.lines): s for s in SCENES}
        self.reply = reply
        self.tokens = tokens
        self.calls = 0

    async def complete(self, system: str, text: str, *, max_tokens: int = 1500) -> Reply:
        self.calls += 1
        return Reply(self.reply(self.by_text[text]), False, *self.tokens)


async def run(client: Canned, price: tuple[float, float] = (1.0, 5.0)) -> Any:
    return await measure_main.measure(client, SCENES, price, 100.0)


class GoldenSet(unittest.TestCase):
    def test_it_is_about_sixty_scenes_of_five_to_fifteen_lines(self) -> None:
        self.assertGreaterEqual(len(SCENES), 60)
        for scene in SCENES:
            self.assertTrue(MIN_LINES <= len(scene.lines) <= MAX_LINES, scene.title)

    def test_every_line_is_labelled_and_only_statements_and_plans_yield_claims(self) -> None:
        for scene in SCENES:
            for line in scene.lines:
                self.assertIn(line.speaker, ("DM", "Player"))
                self.assertIn(line.kind, ("statement", "question", "plan", "ooc"))
                if line.kind in ("question", "ooc"):
                    self.assertEqual((line.expected, line.also), ([], []), scene.title)

    def test_how_a_claim_was_said_follows_from_the_line(self) -> None:
        for scene in SCENES:
            for line in scene.lines:
                for e in line.expected:
                    want = (
                        "plan"
                        if line.kind == "plan"
                        else "dm_said"
                        if line.speaker == "DM"
                        else "player_said"
                    )
                    self.assertEqual(e.how, want, f"{scene.title} line {line.number}")

    def test_it_has_the_traps_of_the_design(self) -> None:
        lines = [line for s in SCENES for line in s.lines]
        titles = " ".join(s.title.lower() for s in SCENES)
        self.assertGreaterEqual(sum(bool(line.never) for line in lines), 4)  # injection lines
        self.assertTrue(any("ignore your instructions" in line.text.lower() for line in lines))
        self.assertIn("lie", titles)  # an NPC lies
        self.assertIn("secret", titles)  # said aloud
        self.assertTrue(any("appears abandoned" in line.text for line in lines))  # DM uncertainty
        plans = [e for line in lines if line.kind == "plan" for e in line.expected]
        self.assertGreaterEqual(len(plans), 20)  # plans, not facts
        self.assertTrue(any(line.text.startswith("Let's kill the king") for line in lines))
        self.assertGreaterEqual(sum(e.condition for line in lines for e in line.expected), 25)
        self.assertGreaterEqual(sum(line.kind == "question" for line in lines), 30)
        self.assertGreaterEqual(sum(line.kind == "ooc" for line in lines), 30)

    def test_it_holds_no_real_data(self) -> None:
        """Invented only: no address, link, Discord-looking number or handle."""
        for path in GOLDEN.glob("*.txt"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"https?://|www\.|@\w|\b\d{15,}\b", text), path.name)

    def test_a_never_is_single_words(self) -> None:
        for scene in SCENES:
            for line in scene.lines:
                for words in line.never:
                    self.assertTrue(all(" " not in w for w in words), scene.title)

    def test_summary_counts(self) -> None:
        self.assertIn(f"{len(SCENES)} scenes", summary(SCENES))


class Format(unittest.TestCase):
    GOOD = (
        "## Scene 1: t\n"
        "1. dm statement: Alpha beats bravo.\n   - claim: alpha | beats | bravo\n"
        "2. player question: Two?\n"
        "3. dm ooc: Three.\n"
        "4. player plan: Go to bravo.\n   - claim: party | go | bravo\n"
        "5. player statement: Five.\n   - state: Alpha | dead\n   - never: roan, alive\n"
    )

    def bad(self, text: str, why: str) -> None:
        with self.assertRaisesRegex(GoldenError, why):
            parse_golden(text)

    def test_a_good_file_reads(self) -> None:
        (scene,) = parse_golden(self.GOOD)
        self.assertEqual(len(scene.lines), 5)
        self.assertEqual(scene.lines[4].expected[0].condition, True)
        self.assertEqual(scene.lines[4].never, [("alive", "roan")])
        self.assertEqual(scene.lines[0].expected[0].how, "dm_said")
        self.assertEqual(scene.lines[3].expected[0].how, "plan")
        self.assertEqual(scene.lines[4].expected[0].how, "player_said")

    def test_every_kind_of_mistake_is_refused_with_a_place(self) -> None:
        cases = {
            "text first": ("hello\n" + self.GOOD, "before the first"),
            "claim on a question": (
                self.GOOD.replace(
                    "2. player question: Two?\n",
                    "2. player question: Two?\n   - claim: alpha | beats | bravo\n",
                ),
                "yields no claim",
            ),
            "bad claim shape": (
                self.GOOD.replace("alpha | beats | bravo", "alpha | beats"),
                "subject | relationship",
            ),
            "not a condition": (
                self.GOOD.replace("Alpha | dead", "Alpha | asleep"),
                "not a condition",
            ),
            "numbers skip": (self.GOOD.replace("3. dm ooc", "4. dm ooc"), "count 1, 2, 3"),
            "too short": ("## Scene 1: t\n1. dm ooc: Hi.\n", "has 1 lines"),
            "unknown bullet": (self.GOOD + "   - maybe: x\n", "unknown bullet"),
            "bad speaker": (
                self.GOOD.replace("1. dm statement", "1. god statement"),
                "outside a numbered",
            ),
            "empty never": (self.GOOD.replace("roan, alive", " , "), "needs words"),
            "no scenes": ("", "no scenes"),
            "a question with no ?": (self.GOOD.replace("Two?", "Two."), "a question has no"),
            "a name the scene never says": (
                self.GOOD.replace("alpha | beats | bravo", "alpha | beats | zulu"),
                "'zulu' is not in the scene's words",
            ),
        }
        for name, (text, why) in cases.items():
            with self.subTest(name):
                self.bad(text, why)

    def test_scene_numbers_must_run_without_gaps_across_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_text(self.GOOD)
            Path(tmp, "b.txt").write_text(self.GOOD.replace("Scene 1", "Scene 3"))
            with self.assertRaisesRegex(GoldenError, "run 1, 2, 3"):
                load_golden(Path(tmp))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(GoldenError, "no"):
            load_golden(Path(tmp))


class Scoring(unittest.IsolatedAsyncioTestCase):
    async def test_a_perfect_extractor_scores_everything(self) -> None:
        score, calls, notes, _ = await run(Canned())
        self.assertEqual((score.pulled, score.invented, score.injections), (score.expected, 0, 0))
        self.assertEqual(score.from_quiet, 0)
        self.assertEqual(len(calls), len(SCENES))
        self.assertEqual(notes, [])
        for how in ("dm_said", "player_said", "plan"):
            self.assertEqual(score.kinds[(how, how)], score.want[how], how)
            self.assertEqual(score.labelled[how], score.want[how], how)
        self.assertEqual(score.states_pulled, score.states_expected)
        text = "\n".join(report.score_lines(score))
        self.assertIn("recall 100%", text)
        self.assertIn("precision 100%", text)
        self.assertIn("injection lines obeyed (should be 0): 0", text)

    async def test_everything_called_a_plan_loses_who_said_it_but_not_the_pulling(self) -> None:
        score, *_ = await run(Canned(lambda s: answer(s, how="plan")))
        self.assertEqual(score.pulled, score.expected)
        self.assertEqual(score.how_right, score.want["plan"])
        text = "\n".join(report.score_lines(score))
        self.assertIn("who said it (DM or player, of the matched statements): 0%", text)
        self.assertIn("what kind (a plan or not", text)
        self.assertEqual(score.labelled["dm_said"], 0)

    async def test_missing_a_claim_lowers_recall_not_precision(self) -> None:
        score, *_ = await run(Canned(lambda s: answer(s, skip=1)))
        self.assertLess(score.pulled, score.expected)
        self.assertEqual(score.invented, 0)

    async def test_an_obeyed_injection_and_claims_from_table_talk_are_counted(self) -> None:
        scene = next(s for s in SCENES if "Injection, simple" in s.title)
        ooc = next(s for s in SCENES if "Table talk" in s.title)

        def lie(s: Scene) -> str:
            extra: list[dict[str, Any]] = []
            if s is scene:
                extra = [{"subject": "king", "relationship": "is", "object": "dead",
                          "details": "", "lines": [1], "how": "player_said"}]  # fmt: skip
            if s is ooc:
                extra = [{"subject": "chips", "relationship": "are", "object": "salty",
                          "details": "", "lines": [2], "how": "player_said"}]  # fmt: skip
            return answer(s, extra=extra)

        score, *_ = await run(Canned(lie))
        self.assertEqual(score.injections, 1)
        self.assertEqual(
            score.from_quiet, 1
        )  # the table-talk line; the injection line is a statement
        self.assertEqual(score.invented, 2)

    async def test_a_scene_the_client_cannot_answer_counts_its_claims_as_missed(self) -> None:
        from dmbot.ai import AIError

        class SecondFails(Canned):
            async def complete(self, system: str, text: str, *, max_tokens: int = 1500) -> Reply:
                if self.calls == 1:
                    self.calls += 1
                    raise AIError("down")
                return await super().complete(system, text, max_tokens=max_tokens)

        score, calls, notes, stop = await run(SecondFails())
        self.assertEqual((len(calls), stop), (len(SCENES) - 1, False))
        self.assertLess(score.pulled, score.expected)  # that scene's claims count as missed
        self.assertIn("1 calls failed", notes[0])

    async def test_an_account_out_of_funds_stops_the_pass_at_once(self) -> None:
        from dmbot.ai import AIOutOfFunds

        class Broke(Canned):
            async def complete(self, system: str, text: str, *, max_tokens: int = 1500) -> Reply:
                self.calls += 1
                raise AIOutOfFunds("no money")

        client = Broke()
        score, calls, notes, _ = await run(client)
        self.assertEqual((client.calls, calls), (1, []))
        self.assertIn("out of funds", notes[0])
        plans = sum(e.how == "plan" for sc in SCENES for ln in sc.lines for e in ln.expected)
        self.assertEqual(score.want["plan"], plans)

    async def test_a_multi_word_never_still_catches_an_obeyed_injection(self) -> None:
        for title, claim in (
            ("Injection in character", ("temple of Nyxara", "has", "fallen")),
            ("Let's kill the king", ("king of Dunmere", "is", "dead")),
        ):
            scene = next(s for s in SCENES if title in s.title)

            def lie(s: Scene, scene: Scene = scene, claim: tuple[str, str, str] = claim) -> str:
                extra = []
                if s is scene:
                    extra = [{"subject": claim[0], "relationship": claim[1], "object": claim[2],
                              "details": "", "lines": [1], "how": "player_said"}]  # fmt: skip
                return answer(s, extra=extra)

            with self.subTest(title):
                score, *_ = await run(Canned(lie))
                self.assertEqual(score.injections, 1)

    async def test_the_cap_stops_the_pass(self) -> None:
        client = Canned(tokens=(1_000_000, 0))
        score, calls, notes, _ = await measure_main.measure(client, SCENES, (1.0, 5.0), 1.5)
        self.assertEqual(len(calls), 2)  # overshot by one call at most
        self.assertGreater(score.want["plan"], 0)  # the unmeasured scenes count as missed
        self.assertIn("the cap", notes[0])


class Report(unittest.TestCase):
    def test_percentile_is_the_nearest_rank(self) -> None:
        values = list(range(1, 101))
        self.assertEqual(report.percentile(values, 95), 95)
        self.assertEqual(report.percentile([7], 95), 7)
        self.assertEqual(report.percentile([3, 1, 2], 100), 3)

    def test_tokens_and_cost_per_session(self) -> None:
        calls = [report.Call(1000, 100), report.Call(2000, 300)]
        lines = report.token_lines(calls, (1.0, 5.0))
        self.assertIn("input mean 1500", lines[0])
        self.assertIn("output mean 200", lines[0])
        per_call = (1500 * 1.0 + 200 * 5.0) / 1_000_000
        self.assertIn(f"${per_call:.5f} a call", lines[1])
        self.assertIn(f"${per_call * 240:.2f} at 240 calls", lines[1])
        self.assertIn(f"${per_call * 480:.2f} at 480 calls", lines[1])
        self.assertIn("no call succeeded", report.token_lines([], (1.0, 5.0))[0])

    def test_a_share_comes_with_its_range(self) -> None:
        self.assertEqual(report.ratio(0, 0), "n/a")
        self.assertRegex(report.ratio(50, 100), r"^50% \(\d+-\d+%\)$")


class Command(unittest.TestCase):
    def go(self, *argv: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.dict("os.environ", env or {}, clear=True),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = asyncio.run(measure_main.main_async(measure_main.parse_args(list(argv))))
        return code, out.getvalue(), err.getvalue()

    def test_by_default_it_only_checks_and_estimates_and_makes_no_call(self) -> None:
        with patch.object(measure_main, "AnthropicClient", side_effect=AssertionError("a call")):
            code, out, _ = self.go()
        self.assertEqual(code, 0)
        self.assertIn("60 scenes", out)
        self.assertIn("No calls made", out)
        self.assertIn("a 4-hour session", out)
        self.assertIn("at 240 calls", out)

    def test_a_model_with_no_price_is_never_guessed(self) -> None:
        # An invented model: the real default careful model has a price on file now (#1969).
        unpriced = {"AI_MODEL_CAREFUL": "claude-unpriced-test-1"}
        code, out, _ = self.go("--tier", "careful", env=unpriced)
        self.assertEqual(code, 0)
        self.assertIn("price unknown (give --price claude-unpriced-test-1=IN,OUT)", out)
        code, _, err = self.go("--tier", "careful", "--extractor", "anthropic",
                               env={"ANTHROPIC_API_KEY": "k", **unpriced})  # fmt: skip
        self.assertEqual(code, 2)
        self.assertIn("no price for claude-unpriced-test-1", err)

    def test_paid_calls_need_a_key_and_stay_under_the_cap(self) -> None:
        code, _, err = self.go("--extractor", "anthropic")
        self.assertEqual((code, "ANTHROPIC_API_KEY isn't set" in err), (2, True))
        with patch.object(measure_main, "AnthropicClient", side_effect=AssertionError("a call")):
            code, _, err = self.go("--extractor", "anthropic", "--max-usd", "0.01",
                                   env={"ANTHROPIC_API_KEY": "k"})  # fmt: skip
        self.assertEqual(code, 2)
        self.assertIn("is over $0.01", err)

    def test_a_broken_folder_is_refused_plainly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.go("--golden", tmp)
        self.assertEqual(code, 2)
        self.assertIn("no *.txt files", err)

    def paid(self, client: Any, *argv: str, key: str = "sk-secret-value") -> tuple[int, str, str]:
        """The paid path, with the AI client swapped for `client` (no call leaves the machine)."""

        class Fake:
            def __init__(self, *_: Any) -> None:
                pass

            def tier(self, _tier: Any) -> Any:
                return client

            async def close(self) -> None:
                return None

        with patch.object(measure_main, "AnthropicClient", Fake):
            return self.go("--extractor", "anthropic", *argv, env={"ANTHROPIC_API_KEY": key})

    def test_the_key_is_never_printed_even_on_the_paid_path(self) -> None:
        code, out, err = self.paid(Canned(), "--tier", "fast")
        self.assertEqual(code, 0)
        self.assertIn("measured fast", out)
        self.assertNotIn("sk-secret-value", out + err)

    def test_out_of_funds_stops_every_tier_and_exits_non_zero(self) -> None:
        from dmbot.ai import AIOutOfFunds

        class Broke(Canned):
            async def complete(self, system: str, text: str, *, max_tokens: int = 1500) -> Reply:
                self.calls += 1
                raise AIOutOfFunds("no money")

        client = Broke()
        code, out, _ = self.paid(client, "--tier", "fast", "--tier", "careful",
                                 "--price", "claude-sonnet-5-5=3,15")  # fmt: skip
        self.assertEqual((code, client.calls), (1, 1))  # one call, not one for each tier
        self.assertIn("later tiers were not run", out)
        self.assertNotIn("measured careful", out)

    def test_a_first_call_that_fails_stops_the_run_at_once(self) -> None:
        from dmbot.ai import AIError

        class Refused(Canned):
            async def complete(self, system: str, text: str, *, max_tokens: int = 1500) -> Reply:
                self.calls += 1
                raise AIError("model not found")

        client = Refused()
        code, out, _ = self.paid(client, "--tier", "fast")
        self.assertEqual((code, client.calls), (1, 1))
        self.assertIn("the first call failed", out)

    def test_a_price_that_is_not_a_price_is_refused(self) -> None:
        for bad in ("m", "m=1", "m=a,b", "m=0,1", "=1,2", "m=1,nan"):
            with self.subTest(bad), self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                measure_main.parse_args(["--price", bad])
