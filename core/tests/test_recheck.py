"""Re-checking rules cards and sidebar answers in a library run (#1026): a fake AI client, a
scripted speech engine, no network."""

import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dmbot.ai import Reply
from dmbot.devtools import recheck
from dmbot.devtools import test_library as lib
from dmbot.devtools.session_replay import Shown
from dmbot.dm_screen.rules_cards import spotter_for
from tests.sidebar_brevity_cases import reply
from tests.test_replay import ScriptedTranscriber
from tests.test_session_replay import make_case


class Cards(unittest.TestCase):
    def setUp(self) -> None:
        self.spotter = spotter_for("2024", "2014")

    def test_a_card_gained_lost_or_changed(self) -> None:
        then = recheck.cards(["the wizard casts fireball", "he is grappled"], self.spotter)
        now = recheck.cards(
            ["the wizard casts firebell", "he is grappled", "a goblin warrior attacks"],
            self.spotter,
        )
        counts, detail = recheck.compare_cards(then, now)
        self.assertEqual((counts.same, counts.lost, counts.gained), (1, 1, 1))
        self.assertTrue(any("lost" in d and "fireball" in d for d in detail))
        self.assertTrue(any("gained" in d for d in detail))

    def test_a_name_is_one_card_however_often_it_is_said(self) -> None:
        shown = recheck.cards(["fireball", "again fireball"], self.spotter)
        self.assertEqual(len(shown), 1)


class Answers(unittest.TestCase):
    def test_the_source_and_sure_are_read_from_the_suffix(self) -> None:
        self.assertEqual(
            recheck.split_answer("No. A point you choose. (SRD 5.2.1 p. 131, sure)"),
            ("No. A point you choose.", "SRD 5.2.1 p. 131", "sure"),
        )
        self.assertEqual(
            recheck.split_answer("Yes. (not in DMbot's rules, check your book)"),
            ("Yes.", "not in DMbot's rules, check your book", None),
        )
        self.assertEqual(recheck.split_answer("Plain."), ("Plain.", None, None))

    def test_losing_sure_is_worse_and_the_same_words_are_same(self) -> None:
        then = "No. It starts at a point you choose. (SRD 5.2.1 p. 131, sure)"
        self.assertEqual(recheck.compare_answer(then, then).verdict, "same")
        lost = "No. It starts at a point you choose. (SRD 5.2.1 p. 131, not sure)"
        self.assertEqual(recheck.compare_answer(then, lost).verdict, "worse")
        other = "Yes. Walls matter a lot here. (the scene)"
        self.assertEqual(recheck.compare_answer(then, other).verdict, "worse")

    def test_a_broken_length_rule_is_worse_and_a_fixed_one_better(self) -> None:
        short = "Yes. (SRD 5.2.1 p. 131, sure)"
        long = " ".join(["Yes. It goes on and on."] * 20) + " (SRD 5.2.1 p. 131, sure)"
        self.assertEqual(recheck.compare_answer(short, long).verdict, "worse")
        self.assertEqual(recheck.compare_answer(long, short).verdict, "better")

    def test_pairs_follow_each_question_with_its_answer(self) -> None:
        shown = [
            Shown(1001, "sidebar question", "is it fire", 100),
            Shown(1001, "sidebar answer", "Yes.", 200),
            Shown(1001, "sidebar question", "what about walls", 300),
        ]
        self.assertEqual(
            recheck.pairs(shown), [(100, "is it fire", "Yes."), (300, "what about walls", "")]
        )

    def test_failed_questions_are_counted_and_the_rest_go_on(self) -> None:
        async def answer(question: str, scene: str) -> str | None:
            return None if "walls" in question else "Yes. (the scene)"

        done = asyncio.run(
            recheck.recheck_sidebar(
                [
                    (
                        0,
                        "line",
                    )
                ],
                [(100, "is it fire", "Yes. (the scene)"), (300, "what about walls", "No.")],
                answer,
            )
        )
        self.assertEqual((done.counts.same, done.counts.failed), (1, 1))

    def test_the_scene_is_the_table_lines_before_the_question(self) -> None:
        seen: list[str] = []

        async def answer(question: str, scene: str) -> str | None:
            seen.append(scene)
            return "Yes."

        asyncio.run(
            recheck.recheck_sidebar([(0, "before"), (500, "after")], [(100, "q", "Yes.")], answer)
        )
        self.assertEqual(seen, ["before"])


class Budget(unittest.TestCase):
    def test_tokens_are_added_up_per_call(self) -> None:
        class Inner:
            model = "claude-haiku-4-5-20251001"

            async def complete(self, system: str, text: str, *, max_tokens: int = 8000) -> Reply:
                return Reply("x", False, 100, 20, "claude-haiku-4-5-20251001")

        ai = recheck.CountingAI(Inner())
        asyncio.run(ai.complete("s", "t"))
        asyncio.run(ai.complete("s", "t"))
        self.assertIn("2 calls, 200 tokens in, 40 out", ai.budget())
        self.assertIn("$", ai.budget())

    def test_an_unpriced_model_says_so(self) -> None:
        ai = recheck.CountingAI(type("I", (), {"model": "mystery"})())
        self.assertIn("no price on file", ai.budget())


class FakeAnthropic:
    """Answers every sidebar question the same, with tokens."""

    model = "claude-haiku-4-5-20251001"

    def __init__(self, text: str) -> None:
        self.text = text
        self.closed = False

    async def complete(self, system: str, text: str, *, max_tokens: int = 8000) -> Reply:
        return Reply(self.text, False, 300, 30, self.model)

    async def close(self) -> None:
        self.closed = True


class LibraryFlags(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "rec"
        self.root.mkdir()
        self.history = Path(self._tmp.name) / "history.log"
        self.history.write_text("", encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)
        folder = make_case(
            self.root,
            "2026-10-10-0900-aaaa",
            transcript=((1001, 0, "cast fireball"), (1002, 400, "hi")),
            kept={"name": "two-speaker-live"},
        )
        path = folder / "session.json"
        data = json.loads(path.read_text())
        data["produced"]["shown"] = [
            {"speaker": 1001, "at_ms": 900, "kind": "sidebar question", "text": "is fireball fire"},
            {
                "speaker": 1001,
                "at_ms": 905,
                "kind": "sidebar answer",
                "text": "Yes. It deals fire damage. (SRD 5.2.1 p. 131, sure)",
            },
        ]
        path.write_text(json.dumps(data), encoding="utf-8")

    def run_library(self, *flags: str, fake: FakeAnthropic | None = None) -> tuple[int, str, str]:
        env = {k: v for k, v in os.environ.items() if k != "DISCORD_TOKEN"}
        env["TRANSCRIBER"] = "none"
        env["ANTHROPIC_API_KEY"] = "test-key"
        engine = ScriptedTranscriber(["cast firebell", "hi"])
        out, err = io.StringIO(), io.StringIO()
        client = fake or FakeAnthropic(reply("Yes. It deals fire damage.", "SRD 5.2.1 p. 131"))
        with (
            patch.dict(os.environ, env, clear=True),
            patch("dmbot.transcription.factory.build_transcriber", lambda settings: engine),
            patch("dmbot.devtools.recheck.AnthropicClient") as anthropic,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            anthropic.return_value.tier.return_value = client
            anthropic.return_value.close = client.close
            code = lib.main(
                [
                    "--dir",
                    str(self.root),
                    "run",
                    "--no-timing",
                    "--history",
                    str(self.history),
                    *flags,
                ]
            )
        return code, out.getvalue(), err.getvalue()

    def test_a_plain_run_does_no_rechecks(self) -> None:
        code, out, _ = self.run_library()
        self.assertEqual(code, 0)
        self.assertNotIn("rules cards", out)
        self.assertNotIn("sidebar", out)
        self.assertIn("AI tokens 0", out)

    def test_with_rules_compares_the_cards(self) -> None:
        _, out, _ = self.run_library("--with-rules")
        self.assertIn("rules cards: 1 lost", out)  # "firebell" is no card
        self.assertIn("card lost: fireball", out)
        self.assertNotIn("sidebar", out)

    def test_with_sidebar_asks_again_and_prints_the_budget(self) -> None:
        fake = FakeAnthropic(reply("Yes. It deals fire damage.", "SRD 5.2.1 p. 131", "sure"))
        _, out, _ = self.run_library("--with-sidebar", fake=fake)
        self.assertIn("sidebar: 1 same", out)
        self.assertIn("AI 1 calls, 300 tokens in, 30 out", out)

    def test_with_sidebar_notices_a_lost_sure(self) -> None:
        fake = FakeAnthropic(reply("Yes. It deals fire damage.", "SRD 5.2.1 p. 131", "not sure"))
        _, out, _ = self.run_library("--with-sidebar", fake=fake)
        self.assertIn("sidebar: 1 worse", out)

    def test_the_log_has_counts_only_never_text(self) -> None:
        self.run_library("--with-rules", "--with-sidebar", "--log")
        logged = self.history.read_text(encoding="utf-8")
        self.assertIn("rules cards:", logged)
        self.assertIn("sidebar:", logged)
        for words in ("fireball", "Fireball", "firebell", "fire damage", "hi"):
            self.assertNotIn(words, logged.replace("two-speaker-live", ""), words)

    def test_with_sidebar_needs_the_key(self) -> None:
        env = {
            k: v for k, v in os.environ.items() if k not in ("DISCORD_TOKEN", "ANTHROPIC_API_KEY")
        }
        env["TRANSCRIBER"] = "none"
        err = io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(err):
            code = lib.main(["--dir", str(self.root), "run", "--with-sidebar"])
        self.assertEqual(code, 2)
        self.assertIn("ANTHROPIC_API_KEY", err.getvalue())


if __name__ == "__main__":
    unittest.main()
