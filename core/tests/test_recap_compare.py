"""`recap_run --compare` (#2575): two blind drafts from the same beats, the key apart."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

from dmbot.devtools.recap_run import compare as cmp
from dmbot.devtools.recap_run.__main__ import parse_args


def job(summary: str, state: str = "ready", beats: Any = None) -> Any:
    return SimpleNamespace(
        title="The Door", summary=summary, housekeeping=(), music=("soft",), to_confirm=(),
        state=state, beats=beats if beats is not None else [{"what": "x"}], speakers=(1, 2),
        split={"m": 1}, grade={},
    )  # fmt: skip


def counted(model: str) -> Any:
    return SimpleNamespace(model=model, calls=2, input_tokens=1000, output_tokens=500)


def outcome(summary: str, model: str, state: str = "ready") -> Any:
    return SimpleNamespace(
        job=job(summary, state), stopped="", creative=counted(model), formatting=counted("fmt"),
        careful=counted("careful"), fast=counted("fast"),
    )  # fmt: skip


class Runner:
    """A stand-in for `run_text` that records how it was called."""

    def __init__(self, leak: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.leak = leak

    async def __call__(
        self, inputs: Any, fast: Any, careful: Any, consenting: Any, **kw: Any
    ) -> Any:
        self.calls.append(kw)
        which = len(self.calls) - 1
        text = "《Calm》 Tavin lit the fire." if which == 0 else "《Calm》 Tavin warmed his hands."
        return outcome(text, kw["creative_models"][0])


WRITERS = [
    cmp.Writer("Anthropic claude-writer", "c0", "f0", ("claude-writer", "fmt")),  # type: ignore[arg-type]
    cmp.Writer("Gemini gemini-writer", "c1", "f1", ("gemini-writer", "fmt")),  # type: ignore[arg-type]
]


class TheComparison(unittest.IsolatedAsyncioTestCase):
    async def run_it(
        self, runner: Runner, order: tuple[int, int], leaks: bool = False
    ) -> tuple[Path, list[str], int]:
        folder = Path(tempfile.mkdtemp()) / "cmp"
        said: list[str] = []
        code = await cmp.run_compare(
            runner, SimpleNamespace(), "fast", "careful", None,  # type: ignore[arg-type]
            session_at=1, models=("fast", "careful"), writers=WRITERS, folder=folder,
            prices={"claude-writer": (4, 20), "gemini-writer": (1, 5)},
            checks_of=lambda o: [("names ok", True, "")], leaks=lambda o: leaks,
            voice_tags=False, order=order, emit=said.append,
        )  # fmt: skip
        return folder, said, code

    async def test_two_blind_drafts_and_a_separate_key(self) -> None:
        runner = Runner()
        folder, said, code = await self.run_it(runner, (1, 0))
        self.assertEqual(code, 0)
        a, b = (folder / "A.txt").read_text(), (folder / "B.txt").read_text()
        self.assertIn("lit the fire", a)  # draft A is written first; here by the second writer
        self.assertIn("warmed his hands", b)
        key = (folder / "key.txt").read_text()
        self.assertEqual(key, "A = Gemini gemini-writer\nB = Anthropic claude-writer\n")
        for text in (a, b, *said):
            self.assertNotIn("gemini", text.lower())
            self.assertNotIn("claude", text.lower())
            self.assertNotIn("anthropic", text.lower())

    async def test_draft_a_runs_first_and_draft_b_starts_from_its_beat_sheet(self) -> None:
        for order, expect in (((0, 1), ["c0", "c1"]), ((1, 0), ["c1", "c0"])):
            runner = Runner()
            await self.run_it(runner, order)
            self.assertNotIn("start", runner.calls[0])
            state, fields = runner.calls[1]["start"]
            self.assertEqual(state, "checked")
            self.assertEqual(fields["beats"], [{"what": "x"}])
            self.assertEqual(fields["speakers"], (1, 2))
            self.assertEqual([c["creative"] for c in runner.calls], expect)

    async def test_the_two_reports_have_the_same_rows_whichever_writer_wrote_them(self) -> None:
        _, said, _ = await self.run_it(Runner(), (1, 0))
        text = "\n".join(said)
        a, b = text.split("== Draft B ==")

        def labels(block: str) -> list[str]:
            return [
                line.split(":")[0].strip() for line in block.splitlines() if line.startswith("  ")
            ]

        self.assertEqual(labels(a), labels(b))

    async def test_an_earlier_comparison_in_the_folder_is_cleared_first(self) -> None:
        folder = Path(tempfile.mkdtemp()) / "cmp"
        folder.mkdir(parents=True)
        for name in ("A.txt", "B.txt", "key.txt"):
            (folder / name).write_text("old")
        code = await cmp.run_compare(
            Runner(), SimpleNamespace(), "f", "c", None,  # type: ignore[arg-type]
            session_at=1, models=("f", "c"), writers=WRITERS, folder=folder, prices={},
            checks_of=lambda o: [], leaks=lambda o: True, voice_tags=False, order=(0, 1),
            emit=lambda line: None,
        )  # fmt: skip
        self.assertEqual(code, 3)
        self.assertEqual([p.name for p in folder.iterdir()], [])

    async def test_costs_and_checks_are_printed_by_letter(self) -> None:
        _, said, _ = await self.run_it(Runner(), (0, 1))
        text = "\n".join(said)
        self.assertIn("== Draft A ==", text)
        self.assertIn("== Draft B ==", text)
        self.assertIn("PASS  names ok", text)
        self.assertIn(
            "writer: 2 calls, in 1000, out 500, $0.014", text
        )  # 4*1000+20*500 per million

    async def test_a_draft_that_names_a_secret_writes_nothing(self) -> None:
        folder, _said, code = await self.run_it(Runner(), (0, 1), leaks=True)
        self.assertEqual(code, 3)
        self.assertFalse(folder.exists())

    async def test_a_first_run_with_no_beats_stops_plainly(self) -> None:
        class NoBeats(Runner):
            async def __call__(self, *a: Any, **kw: Any) -> Any:
                out = await super().__call__(*a, **kw)
                out.job.beats, out.job.state = [], "queued"
                return out

        _folder, said, code = await self.run_it(NoBeats(), (0, 1))
        self.assertEqual(code, 1)
        self.assertIn("nothing to compare", " ".join(said))

    def test_the_coin_gives_both_orders(self) -> None:
        self.assertEqual(cmp.blind_order(lambda n: 0), (0, 1))
        self.assertEqual(cmp.blind_order(lambda n: 1), (1, 0))


class TheArguments(unittest.TestCase):
    def test_compare_needs_a_folder_and_a_gemini_model(self) -> None:
        for argv in (
            ["--session", "1", "c", "2", "--compare"],
            ["--session", "1", "c", "2", "--compare", "--out", "x"],
            ["--session", "1", "c", "2", "--compare", "--gemini-model", "g"],
            ["--golden", "--compare", "--out", "x", "--gemini-model", "g"],
        ):
            with self.assertRaises(SystemExit):
                parse_args(argv)

    def test_two_claude_writers_need_no_gemini_model_or_key_confirmation(self) -> None:
        args = parse_args(
            ["--session", "1", "c", "2", "--compare", "--out", "x", "--writers", "claude,claude"]
        )
        self.assertTrue(args.compare)
        self.assertIsNone(args.gemini_model)

    def test_a_gemini_writer_still_needs_the_model_and_the_confirmation(self) -> None:
        for argv in (
            ["--writers", "claude,gemini"],
            ["--writers", "gemini,gemini"],
            ["--writers", "claude,gemini", "--gemini-model", "g"],
            ["--writers", "claude,gemini", "--paid-gemini-key"],
        ):
            with self.subTest(argv), self.assertRaises(SystemExit):
                parse_args(["--session", "1", "c", "2", "--compare", "--out", "x", *argv])

    def test_a_session_run_accepts_out_to_keep_its_draft(self) -> None:
        args = parse_args(["--session", "1", "c", "2", "--reads", "transcript", "--out", "x"])
        self.assertEqual(args.out, "x")
        with self.assertRaises(SystemExit):  # (still not on its own)
            parse_args(["--golden", "--out", "x"])

    def test_the_model_flags_go_only_with_compare(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--session", "1", "c", "2", "--gemini-model", "g"])

    def test_a_good_compare_line_is_accepted(self) -> None:
        args = parse_args(
            [
                "--session", "1", "c", "2", "--compare", "--out", "x", "--gemini-model", "g",
                "--paid-gemini-key",
            ]
        )  # fmt: skip
        self.assertTrue(args.compare)

    def test_compare_will_not_run_without_the_paid_key_confirmation(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(
                ["--session", "1", "c", "2", "--compare", "--out", "x", "--gemini-model", "g"]
            )


class TheReads(unittest.IsolatedAsyncioTestCase):
    """Each side of a comparison can read the beats or the cleaned transcript (#2590)."""

    COMPARE: ClassVar[list[str]] = [
        "--session",
        "1",
        "c",
        "2",
        "--compare",
        "--out",
        "x",
        "--gemini-model",
        "g",
    ]

    def test_reads_is_one_word_for_both_sides_or_one_for_each(self) -> None:
        paid = ["--paid-gemini-key"]
        self.assertEqual(parse_args([*self.COMPARE, *paid]).reads_each, ("beats", "beats"))
        args = parse_args([*self.COMPARE, *paid, "--reads", "transcript"])
        self.assertEqual(args.reads_each, ("transcript", "transcript"))
        args = parse_args([*self.COMPARE, *paid, "--reads", "beats,transcript"])
        self.assertEqual(args.reads_each, ("beats", "transcript"))

    def test_a_reads_word_that_is_not_known_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args([*self.COMPARE, "--paid-gemini-key", "--reads", "everything"])
        with self.assertRaises(SystemExit):
            parse_args([*self.COMPARE, "--paid-gemini-key", "--reads", "beats,beats,beats"])

    def test_writers_goes_only_with_compare(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--session", "1", "c", "2", "--writers", "gemini,gemini"])

    async def test_each_writer_is_run_with_what_it_reads(self) -> None:
        writers = [
            cmp.Writer("Anthropic a", "c0", "f0", ("a", "fmt"), "beats"),  # type: ignore[arg-type]
            cmp.Writer("Gemini b", "c1", "f1", ("b", "fmt"), "transcript"),  # type: ignore[arg-type]
        ]
        runner = Runner()
        folder = Path(tempfile.mkdtemp()) / "cmp"
        code = await cmp.run_compare(
            runner, SimpleNamespace(), "fast", "careful", None,  # type: ignore[arg-type]
            session_at=1, models=("fast", "careful"), writers=writers, folder=folder,
            prices={}, checks_of=lambda o: [], leaks=lambda o: False, voice_tags=False,
            order=(0, 1), emit=lambda line: None,
        )  # fmt: skip
        self.assertEqual(code, 0)
        self.assertEqual([c["reads"] for c in runner.calls], ["beats", "transcript"])
        # the key says what each read; the drafts and the report do not
        key = (folder / "key.txt").read_text()
        self.assertIn("A = Anthropic a", key)
        for name in ("A.txt", "B.txt"):
            self.assertNotIn("transcript", (folder / name).read_text().lower())

    def test_the_default_pair_is_one_of_each_and_gemini_twice_is_allowed(self) -> None:
        from dmbot.ai import DEFAULT_MODELS

        env = {"ANTHROPIC_API_KEY": "k" * 20, "GEMINI_API_KEY": "g" * 20}
        made: list[Any] = []
        for which, reads in (
            (("claude", "gemini"), ("beats", "beats")),
            (("gemini", "gemini"), ("beats", "transcript")),
        ):
            made.clear()
            writers = cmp.writers_from_env(
                lambda name: env.get(name, ""), DEFAULT_MODELS, "claude-x", "gemini-y",
                register=made.append, which=which, reads=reads,
            )  # fmt: skip
            assert writers is not None
            self.assertEqual([w.reads for w in writers], list(reads))
            self.assertEqual(len(made), 2)  # a client each, so nothing is shared
        assert writers is not None
        self.assertEqual([w.label.split()[0] for w in writers], ["Gemini", "Gemini"])
