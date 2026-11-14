"""The lore comparison harness (#4382): it calls nothing without --yes, prints counts only (never a
name, a quote, a word of the text or the key), counts and prices cached input, and closes its
client even when the read fails. Invented names. No AI, no database."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from typing import Any
from unittest import mock

from dmbot.ai import Reply
from dmbot.devtools import lore_compare
from dmbot.devtools.costs.prices import price_of
from dmbot.memory import lore_read

KEY = "sk-test-not-real"
TEXT = "Tamsin Vell keeps the lighthouse at Gullcove."


class _Tier:
    model = "claude-opus-5-5"  # (a model with a price on file)

    async def complete(self, system: str, text: str, **kwargs: Any) -> Reply:
        return Reply(
            text="", cut=False, input_tokens=1000, output_tokens=100, cache_read_tokens=10_000,
            cache_write_tokens=2000,
        )  # fmt: skip


class _Client:
    closed = False
    made_with: dict[str, Any] = {}  # noqa: RUF012 (a stand-in's record, set per test)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _Client.made_with = kwargs

    def tier(self, *args: Any, **kwargs: Any) -> _Tier:
        return _Tier()

    async def close(self) -> None:
        _Client.closed = True


def _run(*argv: str, env: dict[str, str]) -> tuple[int, str]:
    out = io.StringIO()
    with tempfile.TemporaryDirectory() as d:
        lore = Path(d, "lore.txt")
        lore.write_text(TEXT, encoding="utf-8")
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(out),
            mock.patch.dict(os.environ, env, clear=True),
        ):
            code = asyncio.run(
                lore_compare.main_async(lore_compare.parse_args(["--text", str(lore), *argv]))
            )
    return code, out.getvalue()


def _found_read() -> lore_read.LoreRead:
    stats = lore_read.Stats(names_listed=3, links_listed=2, read_calls=1, shape_calls=1)
    stats.dropped = Counter({"no_quote": 1})
    # (stand-ins: only their numbers may be printed)
    return lore_read.LoreRead(
        names=["Tamsin Vell"], links=["Tamsin Vell keeps Gullcove"],  # type: ignore[list-item]
        cut=False, stats=stats, trait_lines=["Tamsin Vell: human"],
    )  # fmt: skip


class Harness(unittest.TestCase):
    def test_without_yes_nothing_is_called(self) -> None:
        with mock.patch.object(lore_read, "read_lore") as read:
            code, out = _run(env={"ANTHROPIC_API_KEY": KEY})
        read.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("text: 7 words", out)
        self.assertNotIn("Tamsin", out)

    def test_counts_only_and_cached_input_priced(self) -> None:
        meter = lore_compare.CachedMeter(_Tier())
        asyncio.run(meter.complete("s", "t"))
        lines = "\n".join(lore_compare.render(_found_read(), meter, meter))
        for private in ("Tamsin", "Gullcove", "human"):
            self.assertNotIn(private, lines)
        self.assertIn("names: 1 kept (pass 1 listed 3", lines)
        self.assertIn("dropped: no_quote 1", lines)
        self.assertIn("1000 in (+cache write 2000, cache read 10000) / 100 out", lines)
        cost_in, cost_out = price_of(_Tier.model)
        want = (
            cost_in.dollars * (1000 + 2000 * 1.25 + 10_000 * 0.10) + cost_out.dollars * 100
        ) / 1_000_000
        self.assertAlmostEqual(lore_compare.dollars(meter) or 0.0, want)

    def test_the_paid_run_closes_its_client_and_never_prints_the_key(self) -> None:
        async def read(*args: Any, **kwargs: Any) -> lore_read.LoreRead:
            await args[0].complete("s", "t")
            return _found_read()

        _Client.closed = False
        with (
            mock.patch.object(lore_compare, "AnthropicClient", _Client),
            mock.patch.object(lore_read, "read_lore", read),
        ):
            code, out = _run("--yes", env={"ANTHROPIC_API_KEY": KEY})
        self.assertEqual(code, 0)
        self.assertTrue(_Client.closed)
        self.assertEqual(_Client.made_with, {"fallback": False})
        self.assertNotIn(KEY, out)
        self.assertIn("pass 1: claude-opus-5-5: 1 calls", out)

    def test_a_failed_read_still_closes_the_client(self) -> None:
        async def read(*args: Any, **kwargs: Any) -> lore_read.LoreRead:
            raise RuntimeError("the service went away")

        _Client.closed = False
        with (
            mock.patch.object(lore_compare, "AnthropicClient", _Client),
            mock.patch.object(lore_read, "read_lore", read),
            self.assertRaises(RuntimeError),
        ):
            _run("--yes", env={"ANTHROPIC_API_KEY": KEY})
        self.assertTrue(_Client.closed)

    def test_no_key_stops(self) -> None:
        code, _ = _run("--yes", env={})
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
