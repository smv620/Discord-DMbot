"""The lore comparison harness (#4382): it calls nothing without --yes, and prints counts only,
never a name or a word of the text. Invented names. No AI, no database."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from dmbot.devtools import lore_compare
from dmbot.devtools.common import Metered
from dmbot.memory import lore_read


class _Fake:
    model = "claude-test-model"


class Harness(unittest.TestCase):
    def test_without_yes_nothing_is_called(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lore = Path(d, "lore.txt")
            lore.write_text("Tamsin Vell keeps the lighthouse at Gullcove.", encoding="utf-8")
            out = io.StringIO()
            with (
                mock.patch.object(lore_read, "read_lore") as read,
                contextlib.redirect_stdout(out),
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-not-real"}),
            ):
                code = asyncio.run(
                    lore_compare.main_async(lore_compare.parse_args(["--text", str(lore)]))
                )
            read.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("text: 7 words", out.getvalue())
        self.assertNotIn("Tamsin", out.getvalue())

    def test_counts_only(self) -> None:
        stats = lore_read.Stats(names_listed=3, links_listed=2, read_calls=1, shape_calls=1)
        stats.dropped = Counter({"no_quote": 1})
        read = lore_read.LoreRead(names=[], links=[], cut=False, stats=stats)
        lines = "\n".join(lore_compare.render(read, Metered(_Fake()), Metered(_Fake())))
        self.assertIn("pass 1 listed 3", lines)
        self.assertIn("dropped: no_quote 1", lines)
        self.assertIn("claude-test-model: 0 calls, 0 in / 0 out", lines)


if __name__ == "__main__":
    unittest.main()
