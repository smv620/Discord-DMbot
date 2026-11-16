"""The two-stage sample run (#4523 slice 3, Supervisor's ruling): the four player characters first,
then each secret backstory read as its own character's, then the lore (stage 1, scored against the
total key), then the session (stage 2, the full key), each stage with its own cap of careful-model
decisions. Invented text only; the pure parts need no database."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dmbot.devtools import build_keys
from dmbot.devtools.sample_run import __main__ as cli
from dmbot.devtools.sample_run import plan, steps
from dmbot.devtools.sample_run.plan import SampleError
from tests.pg import DatabaseTest
from tests.test_key_knowers import backup
from tests.test_key_tiers import said
from tests.test_sample_run import DM, GUILD, NAME, NOW, SESSION

SLUGS = [slug for slug, _ in plan.BACKSTORIES]


class ThePlan(unittest.TestCase):
    def test_each_backstory_is_its_keys_owners(self) -> None:
        for slug, owner in plan.BACKSTORIES:
            with self.subTest(slug):
                key = json.loads((build_keys.KEYS / f"backstory_{slug}.json").read_text())
                self.assertEqual(key["speaker"], owner)
        self.assertEqual(set(SLUGS), set(build_keys.BACKSTORIES))

    def test_the_order_is_characters_backstories_lore_then_the_session(self) -> None:
        order = plan.STEPS_TWO.index
        self.assertLess(order("player characters corrected"), order("backstory read (cerric)"))
        last_story = max(order(f"backstory read ({s})") for s in SLUGS)
        self.assertLess(last_story, order("lore read"))
        self.assertLess(order("lore read"), order("sure (stage 1)"))
        self.assertLess(order("AI decides (stage 1)"), order("backup and score (stage 1)"))
        self.assertLess(order("backup and score (stage 1)"), order("session import"))
        self.assertLess(order("AI decides (stage 2)"), order("backup and score (stage 2)"))
        self.assertEqual(len(plan.STEPS_TWO), len(set(plan.STEPS_TWO)))

    def test_all_four_backstories_each_once(self) -> None:
        given = [f"{s}=/tmp/{s}.txt" for s in SLUGS]
        self.assertEqual(plan.parse_backstories(given), {s: f"/tmp/{s}.txt" for s in SLUGS})
        for bad in (given[:3], [*given, given[0]], [*given[:3], "wit=/tmp/w.txt"], ["cerric"]):
            with self.subTest(bad), self.assertRaises(SampleError):
                plan.parse_backstories(bad)

    def test_the_cap_line_is_a_count(self) -> None:
        self.assertEqual(plan.cap_line(7), "stopped at the cap: 7 still waiting")
        self.assertEqual(plan.DEFAULT_STAGE_CAP, 150)  # (ruling E)


class TheCommand(unittest.IsolatedAsyncioTestCase):
    def files(self, tmp: str) -> list[str]:
        lore, session = Path(tmp) / "lore.txt", Path(tmp) / "session.txt"
        lore.write_text("Mira was raised by the Ashen Crown.")
        session.write_text(SESSION)
        out = ["--guild", "1", "--dm", "2", "--deploy", "31",
               "--players", "ist=3,jac=4,eri=5,owe=6",
               "--lore", str(lore), "--session", str(session)]  # fmt: skip
        for slug in SLUGS:
            story = Path(tmp) / f"{slug}.txt"
            story.write_text("Tavin grew up by the sea.")
            out += ["--backstory", f"{slug}={story}"]
        return out

    async def test_without_yes_it_says_two_stages_of_decisions_and_runs_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            said_: list[str] = []
            args = cli.parse_args([*self.files(tmp), "--stages", "2", "--cap", "40"])
            self.assertEqual(await cli.run(args, say=said_.append), 0)
        self.assertTrue(any("at most 80 careful-model calls" in line for line in said_))
        self.assertIn("Add --yes", said_[-1])

    async def test_a_missing_backstory_stops_before_the_cost_is_printed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = self.files(tmp)
            said_: list[str] = []
            for argv in (
                [*base[: base.index("--backstory")], "--stages", "2"],  # (none of the four)
                [*base, "--stages", "2", "--backstory", f"cerric={tmp}/cerric.txt"],  # (twice)
                [*base, "--stages", "2", "--accept-all"],
                [*base],  # (backstories without --stages 2)
            ):
                with self.subTest(argv[-2:]), self.assertRaises(SampleError):
                    await cli.run(cli.parse_args(argv), say=said_.append)
            self.assertEqual(said_, [])

    async def test_an_unreadable_backstory_is_a_plain_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = self.files(tmp)
            (Path(tmp) / "claude.txt").write_text("")
            with self.assertRaises(SampleError):
                await cli.run(cli.parse_args([*base, "--stages", "2"]), say=lambda _l: None)


class TheStageScore(unittest.TestCase):
    def test_a_stage_is_scored_against_its_key_with_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, record = Path(tmp) / "b.json", Path(tmp) / "decisions.jsonl"
            path.write_text(json.dumps(backup([])))
            record.write_text(json.dumps(said("names", "no", "Dog sleds")) + "\n")
            key = steps.built_key_path(plan.STAGE_KEYS[1])
            lines = steps.score(path, "all", key=key, record=record)
            numbers = steps.score_numbers(path, "all", key=steps.built_key(plan.STAGE_KEYS[1]))
        text = "\n".join(lines)
        self.assertIn("Tiers, every key row and extra:", text)
        self.assertIn("Who knows found entries", text)
        total = json.loads(key.read_text())
        self.assertEqual(numbers["entries missing"], len(total["nodes"]) - 3)  # (by design, 3)


class TheOwners(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.store import MemoryStore

        self.memory = MemoryStore(self.db)
        self.sample = await steps.campaign_store(self.db).create(GUILD, NAME, DM)

    async def test_a_backstory_is_queued_as_its_owners_once_the_four_are_set(self) -> None:
        from dmbot.devtools.sample_run import characters

        with self.assertRaises(SampleError):  # (no owner yet: the four come first)
            await characters.ids_by_name(self.db, self.memory, GUILD, self.sample.id)
        await characters.correct_player_characters(
            self.db,
            self.memory,
            GUILD,
            self.sample.id,
            {"ist": 11, "jac": 12, "eri": 13, "owe": 14},
        )
        owners = await characters.ids_by_name(self.db, self.memory, GUILD, self.sample.id)
        self.assertEqual(set(owners), {name for name, _ in plan.PLAYER_CHARACTERS})
        up = await steps.queue_lore(
            self.db, GUILD, self.sample.id, DM, "Tavin grew up by the sea.", choice="spoilers",
            now=NOW, speaker=owners["Claude Ambere"],
        )  # fmt: skip
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT speaker, choice FROM lore_uploads WHERE id = %s", (up,)
            )
            row: Any = await cur.fetchone()
        self.assertEqual((row["speaker"], row["choice"]), (owners["Claude Ambere"], "spoilers"))


if __name__ == "__main__":
    unittest.main()
