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

from dmbot import consent as consent_mod
from dmbot.devtools import accept_all
from dmbot.devtools.sample_run import __main__ as cli
from dmbot.devtools.sample_run import map_step
from dmbot.devtools.sample_run import steps as sample_steps
from dmbot.devtools.sample_run.plan import SampleError


class _Consent:
    def __init__(self, db: Any) -> None:
        pass

    async def consenting(self, guild_id: int) -> frozenset[int]:
        return frozenset({2})

    def has_image_consent(self, guild_id: int, user_id: int) -> bool:
        return user_id == 2


async def _sample(db: Any, guild: int, campaign: str) -> None:
    return None


class Skips(unittest.TestCase):
    def go(
        self, ready: bool, path: Path, dm: int = 2, **patches: Any
    ) -> tuple[dict[str, Any], list[str]]:
        said: list[str] = []
        with (
            mock.patch.object(accept_all, "check_campaign", _sample),
            mock.patch.object(consent_mod, "ConsentStore", _Consent),
        ):
            got = asyncio.run(
                map_step.run(
                    None, None, 1, "c", dm, path, path.parent, say=said.append,
                    settings=SimpleNamespace(pictures_ready=ready), **patches,
                )
            )  # fmt: skip
        return dict(got), said

    def test_pictures_off_is_said_and_skipped(self) -> None:
        got, said = self.go(False, Path("/nowhere/map.jpg"))
        self.assertEqual(got, {"skipped": 1})
        self.assertEqual(said, ["map: skipped (kept pictures are off on this server)"])

    def test_a_dm_who_hasnt_agreed_to_pictures_is_said_and_skipped(self) -> None:
        got, said = self.go(True, Path("/nowhere/map.jpg"), dm=3)
        self.assertEqual(got, {"skipped": 1})
        self.assertIn("hasn't agreed", said[0])

    def test_a_map_file_that_cant_be_read_is_said_without_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            got, said = self.go(True, Path(d, "missing.jpg"))
        self.assertEqual(got, {"skipped": 1})
        self.assertNotIn("missing.jpg", said[0])

    def test_any_campaign_but_the_sample_is_refused_before_anything_is_written(self) -> None:
        async def other(db: Any, guild: int, campaign: str) -> None:
            raise accept_all.WrongCampaign("not the sample")

        with (
            mock.patch.object(accept_all, "check_campaign", other),
            self.assertRaises(SampleError),
        ):
            asyncio.run(
                map_step.run(
                    None, None, 1, "c", 2, Path("/x"), Path("/"), say=lambda _: None,
                    settings=SimpleNamespace(pictures_ready=True),
                )
            )  # fmt: skip


class OnTheKeptRun(unittest.TestCase):
    def test_repeat_adds_the_map_once_after_the_recap(self) -> None:
        order: list[str] = []

        async def run(args: Any, **kw: Any) -> int:
            kw["kept"].update(guild=1, campaign=f"c{len(order)}", started=1)
            kw["metrics"]["spend: the bot's AI ($)"] = 0.1
            order.append("run")
            return 0

        async def recap(guild: int, campaign: str, started: int, **_: Any) -> int:
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

    def test_a_failed_run_says_the_map_was_skipped_and_why(self) -> None:
        # (#4646) a step that does not run never reads as "nothing found"
        said: list[str] = []

        async def run(args: Any, **kw: Any) -> int:
            return 1  # the run failed: no run is kept

        with tempfile.TemporaryDirectory() as d:
            args = cli.parse_args(
                ["--guild", "1", "--dm", "2", "--lore", "l", "--session", "s", "--backup-dir", d,
                 "--commit", "abc1234", "--yes", "--repeat", "2", "--map", "/srv/map.jpg"]
            )  # fmt: skip
            with mock.patch.object(cli, "run", run):
                asyncio.run(cli.run_many(args, say=said.append))
        skipped = [line for line in said if line.startswith("map: skipped")]
        self.assertEqual(len(skipped), 1)
        self.assertNotIn("/srv", skipped[0])  # (never the path)


class TheSkipLine(unittest.TestCase):
    def test_it_is_said_and_logged_with_the_reason(self) -> None:
        said: list[str] = []
        with self.assertLogs("dmbot.devtools.sample_run.map_step", level="INFO") as logs:
            map_step.skipped(said.append, "only the kept run reads the map")
        self.assertEqual(said, ["map: skipped (only the kept run reads the map)"])
        self.assertIn("only the kept run reads the map", logs.output[0])


if __name__ == "__main__":
    unittest.main()
