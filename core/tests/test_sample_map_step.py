"""The sample map step (#4423): it never fails a run. Pictures off, or a map file that can't be
read, are said and skipped; `--repeat` adds the map once, to the kept campaign, after its recap.
Stand-ins only."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from dmbot.devtools.sample_run import __main__ as cli
from dmbot.devtools.sample_run import map_step
from dmbot.devtools.sample_run import steps as sample_steps


class Skips(unittest.TestCase):
    def go(self, ready: bool, path: Path) -> tuple[dict[str, Any], list[str]]:
        said: list[str] = []
        got = asyncio.run(
            map_step.run(
                None, None, 1, "c", 2, path, path.parent, say=said.append,
                settings=SimpleNamespace(pictures_ready=ready),
            )
        )  # fmt: skip
        return dict(got), said

    def test_pictures_off_is_said_and_skipped(self) -> None:
        got, said = self.go(False, Path("/nowhere/map.jpg"))
        self.assertEqual(got, {"skipped": 1})
        self.assertEqual(said, ["map: skipped (kept pictures are off on this server)"])

    def test_a_map_file_that_cant_be_read_is_said_without_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            got, said = self.go(True, Path(d, "missing.jpg"))
        self.assertEqual(got, {"skipped": 1})
        self.assertNotIn("missing.jpg", said[0])


class OnTheKeptRun(unittest.TestCase):
    def test_repeat_adds_the_map_once_after_the_recap(self) -> None:
        order: list[str] = []

        async def run(args: Any, **kw: Any) -> int:
            kw["kept"].update(guild=1, campaign=f"c{len(order)}", started=1)
            kw["metrics"]["spend: the bot's AI ($)"] = 0.1
            order.append("run")
            return 0

        async def recap(guild: int, campaign: str, started: int) -> int:
            order.append("recap")
            return 0

        async def map_on(args: Any, kept: Any, folder: Path, say: Any) -> None:
            order.append(f"map {kept['campaign']}")

        with tempfile.TemporaryDirectory() as d:
            args = cli.parse_args(
                ["--guild", "1", "--dm", "2", "--lore", "l", "--session", "s", "--backup-dir", d,
                 "--commit", "abc1234", "--yes", "--repeat", "2", "--map", "/srv/map.jpg"]
            )  # fmt: skip
            with (
                mock.patch.object(cli, "run", run),
                mock.patch.object(sample_steps, "write_recap", recap),
                mock.patch.object(cli, "_map_on", map_on),
            ):
                asyncio.run(cli.run_many(args, say=lambda _: None))
        self.assertEqual(order, ["run", "run", "recap", "map c1"])


if __name__ == "__main__":
    unittest.main()
