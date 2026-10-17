"""The story-plan measuring tool (#1714): the free estimate, and the paid path with a canned
AI that never goes over its cap. No key, no network."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from dmbot.ai import Reply
from dmbot.devtools.plan_measure import __main__ as tool
from dmbot.memory.plan_reader import MAX_TOKENS

TEXT = "# Chapter 1\n\n" + "Captain Varn guards the harbour town of Saltmere. " * 40


class Canned:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
        self.calls += 1
        answer = json.dumps(
            {"people": [{"name": "Varn", "summary": "", "chunks": [1], "quote": "Captain Varn"}]}
        )
        return Reply(answer, False, 10_000, 2_000, "claude-sonnet-5-5")


class Free(unittest.TestCase):
    def test_the_estimate_counts_batches_and_stays_under_the_worst_case(self) -> None:
        got = tool.estimate(TEXT * 30, "claude-sonnet-5-5")
        self.assertGreaterEqual(got["batches"], 1)
        self.assertGreater(got["tokens_in"], len(TEXT * 30) / 3)
        self.assertLessEqual(got["dollars"], got["dollars_worst"])

    def test_a_file_is_read_and_nothing_of_it_is_printed(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "open.txt"
            path.write_text(TEXT)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(tool.main([str(path)]), 0)
            self.assertIn("estimate, not measured", out.getvalue())
            self.assertNotIn("Varn", out.getvalue() + err.getvalue())
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(tool.main([str(Path(folder) / "missing.txt")]), 2)
                self.assertEqual(tool.main([str(path), "--reader", "anthropic"]), 2)  # no key


class Paid(unittest.IsolatedAsyncioTestCase):
    async def test_it_reads_with_the_bots_checks_and_stops_before_its_cap(self) -> None:
        client = Canned()
        big = TEXT * 200
        done = await tool.measure(client, big, "claude-sonnet-5-5", 150_000)
        # $0.15 allows only a few calls of $0.04 each: it stops before the next could cross.
        self.assertEqual(done["calls"], client.calls)
        self.assertGreaterEqual(client.calls, 1)
        self.assertLessEqual(done["dollars"], 0.40)
        self.assertEqual(done["entries"], client.calls)
        self.assertEqual(done["dropped"], 0)
        none = await tool.measure(Canned(), big, "claude-sonnet-5-5", 1)
        self.assertEqual(none["calls"], 0)
        self.assertGreater(MAX_TOKENS, 0)


if __name__ == "__main__":
    unittest.main()
