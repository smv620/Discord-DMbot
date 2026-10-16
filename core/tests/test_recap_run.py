"""The recap test run (#1424): the real worker on a session, in memory; the report, the refusal on a
secret, what `--keep` writes, the flags, and the golden set through the same path. A fake AI and a
fake provider: nothing is paid for."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dmbot.ai import AIError
from dmbot.campaigns.store import CampaignStore
from dmbot.devtools.recap_run import __main__ as run
from dmbot.devtools.recap_run.ephemeral import EphemeralStore, blank
from dmbot.recaps import pipeline as pl
from dmbot.recaps.audio.eleven import ElevenLabs
from dmbot.recaps.audio.files import AudioFiles
from dmbot.recaps.store import RecapStore
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest
from tests.test_recap_audio_step import Fake, Mixer


def inputs(**kw: Any) -> pl.Inputs:
    base: dict[str, Any] = {
        "lines": fx.lines(), "dm_ids": {fx.DM}, "allowed_names": list(fx.NAMES),
        "secret_names": [], "previous": "", "day": 4,
    }  # fmt: skip
    return pl.Inputs(**{**base, **kw})


def leftovers() -> set[str]:
    return {str(p) for p in Path(tempfile.gettempdir()).glob("recap-run-*")}


def happy() -> fx.Canned:
    return fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)


class TheRun(unittest.IsolatedAsyncioTestCase):
    async def test_it_runs_the_worker_and_the_report_says_everything_asked_for(self) -> None:
        out = await run.run_text(inputs(), happy(), fx.Canned(), models=("m-fast", "m-careful"))
        self.assertEqual(out.job.state, "ready")
        text = "\n".join(run.report(out, {"m-fast": (1.0, 5.0)}))
        self.assertIn("The Silver Lock", text)  # the title
        self.assertIn("As the table reads it (no cues):", text)
        self.assertIn("Housekeeping:", text)
        self.assertIn("- Mira drew her blade.", text)
        self.assertIn("PASS  beats cite real lines", text)  # every check's result
        self.assertIn("PASS  prose:", text)
        self.assertIn("Grader:", text)
        self.assertIn("below the bar: nothing", text)
        self.assertIn("Calls: 4", text)  # beats, prose, grader, finish
        self.assertIn("spend $", text)

    async def test_a_model_with_no_price_is_said_not_guessed(self) -> None:
        out = await run.run_text(inputs(), happy(), fx.Canned())
        self.assertIn("no price on file", "\n".join(run.report(out, {})))

    async def test_a_failed_call_stops_the_run_and_says_so_instead_of_waiting(self) -> None:
        out = await run.run_text(inputs(), fx.Canned(AIError("down")), fx.Canned())
        text = "\n".join(run.report(out, {}))
        self.assertNotEqual(out.job.state, "ready")
        self.assertIn("did not finish", text)
        self.assertIn("wasn't available", text)  # the live job's own reason

    async def test_a_player_who_has_not_agreed_is_left_out_like_in_the_live_job(self) -> None:
        out = await run.run_text(inputs(), happy(), fx.Canned(), lambda: {fx.DM, fx.ALICE, fx.BOB})
        self.assertEqual(out.job.state, "ready")
        self.assertEqual(set(out.job.speakers), {fx.DM, fx.ALICE, fx.BOB})

    async def test_a_recap_that_names_a_secret_is_refused_and_not_printed(self) -> None:
        job = replace(
            blank(1, 1, 0), state="ready", title="Mira", summary="《Calm》 Mira walked in.",
            housekeeping=("Mira drew a blade.",),
        )  # fmt: skip
        fast = run.Counting(fx.Canned(), "m")
        out = run.Outcome(job, inputs(secret_names=["Mira"]), fast, run.Counting(fx.Canned(), "m"))
        lines = run.report(out, {})
        self.assertEqual(lines, [run.REFUSED])
        self.assertNotIn("Mira", "\n".join(lines))

    async def test_the_estimate_counts_the_windows_of_a_long_session(self) -> None:
        small = run.estimate(inputs())
        big = run.estimate(inputs(window_chars=100))
        self.assertEqual(small[0], pl.MAX_CALLS)
        self.assertGreater(big[0], small[0])


class TheMemoryStore(unittest.IsolatedAsyncioTestCase):
    async def test_it_holds_one_job_applies_the_same_rules_and_writes_nothing_else(self) -> None:
        store = EphemeralStore(100, 7)
        await store.update(0, "c", 100, title="T", problems=["a"], state="beats")
        job = await store.get(0, "c", 100)
        self.assertEqual((job.title, job.problems, job.state), ("T", ("a",), "beats"))
        with self.assertRaises(ValueError):
            await store.update(0, "c", 100, status="posted")  # only the columns a stage writes
        self.assertTrue(await store.lease(0, "c", 100))


class TheFlags(unittest.TestCase):
    def test_one_of_session_or_golden_and_golden_takes_no_audio_or_keep(self) -> None:
        args = run.parse_args(["--session", "1", "c" * 32, "100", "--audio", "--keep", "--yes"])
        self.assertTrue(args.audio and args.keep and args.yes)
        for bad in (
            [],
            ["--golden", "--session", "1", "c", "100"],
            ["--golden", "--audio"],
            ["--golden", "--keep"],
        ):
            with self.assertRaises(SystemExit):
                run.parse_args(bad)
        self.assertTrue(run.parse_args(["--golden"]).golden)

    def test_prices_are_given_or_on_file_and_never_guessed(self) -> None:
        got = run.prices_of(["m=3,15"], ["m", "claude-haiku-4-5-20251001", "unknown-model"])
        self.assertEqual(got["m"], (3.0, 15.0))
        self.assertEqual(got["claude-haiku-4-5-20251001"], (1.0, 5.0))
        self.assertNotIn("unknown-model", got)


class TheGoldenSet(unittest.IsolatedAsyncioTestCase):
    async def test_nothing_runs_without_yes_and_the_table_has_a_row_for_each_session(self) -> None:
        with patch.object(run, "clients_from_env", return_value=(happy(), fx.Canned(), ("a", "b"))):
            self.assertEqual(await run.main_async(run.parse_args(["--golden"])), 0)
        rows, clean = await run.run_golden(fx.Canned(AIError("x")), fx.Canned(), ("a", "b"), {})
        self.assertFalse(clean)
        self.assertEqual(rows[0].split()[0], "session")
        self.assertGreaterEqual(len(rows), 4)  # the header and at least three invented sessions


class TheAudio(unittest.IsolatedAsyncioTestCase):
    async def test_it_makes_the_files_in_a_temporary_folder_and_prints_lengths_and_credits(
        self,
    ) -> None:
        out = await run.run_text(inputs(), happy(), fx.Canned())
        fake = Fake()
        before = leftovers()
        with tempfile.TemporaryDirectory() as tmp:
            lines = await run.run_audio(
                out.job, out.inputs, ElevenLabs(fake, voice_id="v"), AudioFiles(Path(tmp)),
                runner=Mixer(),
            )  # fmt: skip
            text = "\n".join(lines)
            self.assertIn("Audio:", text)
            self.assertIn("credits", text)
            self.assertIn("recap-voice.mp3", text)
        self.assertEqual(leftovers(), before)  # the temporary folder is gone
        self.assertTrue(fake.calls)


class TheKeep(DatabaseTest):
    async def test_keep_writes_one_draft_row_and_nothing_else_and_never_over_an_existing_one(
        self,
    ) -> None:
        campaigns = CampaignStore(self.db)
        camp = await campaigns.create(1, "One", fx.DM)
        recaps = RecapStore(self.db)
        out = await run.run_text(inputs(), happy(), fx.Canned(), session_at=100)
        # A test run without --keep wrote nothing.
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM recap_jobs")
            self.assertEqual((await cur.fetchone())["n"], 0)
        self.assertTrue(await run.keep(recaps, 1, camp.id, out, fx.DM))
        job = await recaps.get(1, camp.id, 100)
        assert job is not None
        self.assertEqual((job.state, job.status, job.title), ("ready", "draft", "The Silver Lock"))
        self.assertEqual(job.summary, out.job.summary)
        self.assertEqual(job.speakers, out.job.speakers)
        self.assertIsNotNone(job.emailed_at)  # marked as told: the worker never emails it
        self.assertFalse(await run.keep(recaps, 1, camp.id, out, fx.DM))  # left as it is
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM recap_jobs")
            self.assertEqual((await cur.fetchone())["n"], 1)


class TheSharedLoader(DatabaseTest):
    async def test_the_loader_reads_the_session_the_job_would_and_none_for_a_missing_one(
        self,
    ) -> None:
        from dmbot.memory.lookup import LookupCache
        from dmbot.memory.store import MemoryStore
        from dmbot.recaps.inputs import load_inputs
        from dmbot.transcript.store import TranscriptStore

        campaigns = CampaignStore(self.db)
        camp = await campaigns.create(1, "One", fx.DM)
        transcripts = TranscriptStore(self.db)
        recaps = RecapStore(self.db)
        memory = MemoryStore(self.db)
        sid = await transcripts.open_session(1, camp.id, 1_700_000_000)
        await transcripts.add_lines(1, sid, fx.lines())

        async def load(guild: int, at: int) -> pl.Inputs | None:
            return await load_inputs(
                campaigns=campaigns, lookup=LookupCache(memory), transcripts=transcripts,
                recaps=recaps, clocks=None, guild_id=guild, campaign_id=camp.id, session_at=at,
            )  # fmt: skip

        got = await load(1, 1_700_000_000)
        assert got is not None
        self.assertEqual(len(got.lines), len(fx.lines()))
        self.assertEqual(got.dm_ids, {fx.DM})
        self.assertEqual(got.previous, "")
        self.assertIsNone(await load(1, 12345))  # no such session
        self.assertIsNone(await load(2, 1_700_000_000))  # another server sees nothing
