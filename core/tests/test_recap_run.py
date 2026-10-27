"""The recap test run (#1424): the real worker on a session, in memory; the report, the refusal on a
secret, what `--keep` writes, the flags, and the golden set through the same path. A fake AI and a
fake provider: nothing is paid for."""

from __future__ import annotations

import json
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
        "secret_names": [], "previous": "", "day": 4, "hours": 3.5,  # the canned recap's length
    }  # fmt: skip
    return pl.Inputs(**{**base, **kw})


def found(folder: str, name: str) -> list[Path]:
    return list(Path(folder).rglob(name))


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
        self.assertIn("Calls: 5", text)  # beats, prose, fact check, grader, finish
        self.assertIn("spend $", text)

    async def test_structured_asks_both_steps_the_two_pass_way_and_reports_counts_only(
        self,
    ) -> None:
        draft = "event | lines 1 | who | where | The party reached the keep at dusk"
        fast = fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        careful = fx.Canned(draft)
        out = await run.run_text(inputs(), fast, careful, structured=True)
        self.assertEqual(out.job.state, "ready")
        self.assertEqual(out.structured and out.structured["runs"], 1)
        text = "\n".join(run.report(out, {}))
        self.assertIn("Structured: runs 1, passes 2", text)
        self.assertNotIn(draft, text)
        plain = await run.run_text(inputs(), happy(), fx.Canned())
        self.assertIsNone(plain.structured)
        self.assertNotIn("Structured:", "\n".join(run.report(plain, {})))

    async def test_the_report_shows_how_the_opening_was_split_by_count_never_by_word(self) -> None:
        from tests import test_recap_split as st

        fast = fx.Canned(
            st.SPLIT_ANSWER,
            st.beats_after(len(st.TALK) + len(st.READ_ALOUD)),
            fx.PROSE_ANSWER,
            fx.GRADE_ANSWER,
            fx.FINISH_ANSWER,
        )
        lines = st.reading([st.TALK, st.READ_ALOUD, [t for _, t in fx.LINES]])
        out = await run.run_text(inputs(lines=lines, earlier=st.EARLIER), fast, fx.Canned())
        self.assertEqual(out.job.state, "ready")
        text = "\n".join(run.report(out, {}))
        self.assertIn("Split (ai): talk 2, recap 5, play 7.", text)
        self.assertIn("lines talk 1-2, recap 3-7, play 8-14", text)
        self.assertNotIn("Redfall", text)

    async def test_a_run_that_fails_its_beat_sheet_twice_says_why_for_each_try(self) -> None:
        # Both tries cite no real line, so the sheet is refused twice (#1997).
        bad = (
            '{"beats": [{"what": "A dragon came", "who": ["Varkus"], "lines": [99],'
            ' "kind": "event"}]}'
        )
        out = await run.run_text(inputs(), fx.Canned(bad), fx.Canned(bad))
        self.assertEqual(out.job.state, "failed")
        self.assertEqual(len(out.sheets), 2)
        text = "\n".join(run.report(out, {}))
        self.assertIn(
            "Beat sheet, try 1: 1 beats written, 0 kept (a recap needs at least 3).", text
        )
        self.assertIn("Beat sheet, try 2:", text)
        self.assertIn("problem: 1 beat(s) cut: no line", text)
        self.assertIn("problem: only 0 usable beat(s)", text)

    async def test_the_names_a_sheet_was_not_given_are_printed_for_the_owners_terminal(
        self,
    ) -> None:
        bad = (
            '{"beats": [{"what": "Lord Varkus met them", "who": ["Varkus"], "lines": [1],'
            ' "kind": "event"}]}'
        )
        out = await run.run_text(inputs(), fx.Canned(bad), fx.Canned(bad))
        text = "\n".join(run.report(out, {}))
        self.assertIn("names it did not accept:", text)
        self.assertIn("Varkus", text)
        for _, said in fx.LINES:
            self.assertNotIn(said, text)  # a line of the transcript never prints

    async def test_a_word_only_a_non_speaker_said_does_not_rescue_a_name(self) -> None:
        # The words come from the speakers the job used, like the live worker (consent).
        out = await run.run_text(inputs(), fx.Canned(), fx.Canned())
        stranger = fx.lines()[0].__class__(99000, 4242, "Okthandom waits at the gate.", "x")
        wide = replace(out.inputs, lines=[*out.inputs.lines, stranger])
        bad = (
            '{"beats": [{"what": "They reached Okthandom", "who": [], "lines": [1],'
            ' "kind": "event"}]}'
        )
        failed = replace(
            out, inputs=wide, sheets=[json.loads(bad)["beats"]] * 2,
            job=replace(out.job, state="failed", speakers=(fx.DM, fx.ALICE, fx.BOB)),
        )  # fmt: skip
        text = "\n".join(run.explain_sheets(failed))
        self.assertIn("names it did not accept: Okthandom", text)

    async def test_a_run_that_fails_its_prose_twice_says_why_for_each_try(self) -> None:
        # The beats pass; the draft is refused twice (#1969). The report names the checks, in
        # fixed sentences: no line of the transcript is printed.
        bad = fx.prose_answer("《Tense》 They walked on.", "1. soft strings")
        out = await run.run_text(inputs(), fx.Canned(fx.BEATS_ANSWER, bad), fx.Canned(bad, bad))
        self.assertEqual(out.job.state, "failed")
        text = "\n".join(run.report(out, {}))
        self.assertIn("Step prose, try 1:", text)
        # A short draft is never refused for its length (#2597); this one does not end where the
        # session stopped, so it is retried until the tries run out.
        self.assertIn("(check: ending)", text)
        self.assertIn("Step prose, try 3:", text)
        self.assertIn("problem: it must end exactly where the session stopped", text)
        self.assertNotIn("Make it longer", text)
        for _, said in fx.LINES:
            self.assertNotIn(said, text)

    async def test_a_failed_check_that_names_a_secret_prints_no_name(self) -> None:
        job = (await run.run_text(inputs(), happy(), fx.Canned())).job
        out = run.Outcome(
            replace(job, state="failed"),
            inputs(secret_names=["Zarathul"]),
            run.Counting(fx.Canned(), "f"),
            run.Counting(fx.Canned(), "c"),
            failures=[("checked", 2, ["names that are not in the beat sheet: Zarathul, Mira"])],
        )
        text = "\n".join(run.explain_failures(out))
        self.assertNotIn("Zarathul", text)
        self.assertIn("keeps secret", text)

    async def test_a_model_without_a_price_is_told_the_exact_flag(self) -> None:
        out = await run.run_text(inputs(), happy(), fx.Canned(), models=("m-fast", "m-careful"))
        self.assertIn("--price m-fast=IN,OUT", "\n".join(run.report(out, {})))

    def test_the_default_careful_model_has_a_price_on_file(self) -> None:
        from dmbot.ai import DEFAULT_MODELS

        self.assertIn(DEFAULT_MODELS.careful, run.prices_of([], [DEFAULT_MODELS.careful]))

    async def test_a_finished_run_prints_no_beat_sheet_diagnosis(self) -> None:
        out = await run.run_text(inputs(), happy(), fx.Canned())
        self.assertNotIn("Beat sheet, try", "\n".join(run.report(out, {})))

    async def test_the_clients_are_closed_when_the_run_ends(self) -> None:
        closed: list[bool] = []

        class Fake:
            async def close(self) -> None:
                closed.append(True)

        run._OPEN_CLIENTS.append(Fake())  # type: ignore[arg-type]  # a stand-in with close()
        with patch.object(run, "main_async", return_value=0):
            self.assertEqual(await run._run(run.parse_args(["--golden"])), 0)
        self.assertEqual(closed, [True])
        self.assertEqual(run._OPEN_CLIENTS, [])

        run._OPEN_CLIENTS.append(Fake())  # type: ignore[arg-type]
        boom = patch.object(run, "main_async", side_effect=RuntimeError("boom"))
        with boom, self.assertRaises(RuntimeError):
            await run._run(run.parse_args(["--golden"]))
        self.assertEqual(closed, [True, True])  # closed on a failure too

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
        async def agreed() -> set[int]:
            return {fx.DM, fx.ALICE, fx.BOB}

        out = await run.run_text(inputs(), happy(), fx.Canned(), agreed)
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

    def test_structured_goes_with_a_session_or_a_transcript_only(self) -> None:
        self.assertTrue(
            run.parse_args(["--session", "1", "c" * 32, "100", "--structured"]).structured
        )
        self.assertTrue(
            run.parse_args(["--transcript", "t.txt", "--dm", "Quill", "--structured"]).structured
        )
        for bad in (
            ["--golden", "--structured"],
            ["--transcript", "t.txt", "--dm", "Quill", "--compare", "--structured"],
            ["--review", "somewhere", "--structured"],
        ):
            with self.assertRaises(SystemExit, msg=bad):
                run.parse_args(bad)

    async def test_structured_with_out_writes_the_counts_line_into_the_report_file(self) -> None:
        draft = "event | lines 1 | who | where | The party reached the keep at dusk"
        fast = fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        out = await run.run_text(inputs(), fast, fx.Canned(draft), structured=True)
        lines = run.report(out, {})
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "run"
            run.save_text(folder, out, lines, names_given=False)
            report = (folder / run.rv.REPORT_FILE).read_text()
        self.assertIn("Structured: runs 1, passes 2", report)
        self.assertNotIn(draft, report)  # counts only

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


class TheSettingsStayWithTheRun(unittest.IsolatedAsyncioTestCase):
    async def test_a_run_leaves_the_writer_settings_as_it_found_them(self) -> None:
        # (#2496) `main_async` turned the critics on for the whole process, so a later test that
        # canned four answers got the critics' extra calls and failed, but only after this one.
        before = dict(run._WRITERS)
        with patch.object(run, "clients_from_env", return_value=(happy(), fx.Canned(), ("a", "b"))):
            await run.main_async(run.parse_args(["--golden"]))
        self.assertEqual(run._WRITERS, before)


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

    async def test_a_refused_text_says_which_rule_and_never_the_name(self) -> None:
        import dataclasses

        out = await run.run_text(inputs(), happy(), fx.Canned())
        job = dataclasses.replace(out.job, summary="《Calm》 Rae Quillon walked in.")
        secret = dataclasses.replace(out.inputs, secret_names=["Rae Quillon"])
        with tempfile.TemporaryDirectory() as tmp:
            lines = await run.run_audio(
                job, secret, ElevenLabs(Fake(), voice_id="v"), AudioFiles(Path(tmp)),
                runner=Mixer(),
            )  # fmt: skip
        self.assertEqual(lines, ["audio: not made (the text names a secret: whole-name)"])
        self.assertNotIn("Quillon", "\n".join(lines))

    async def test_an_audio_run_with_a_scene_reaches_the_ambience_and_makes_the_files(self) -> None:
        # #2062: the ambience loop is kept under the audio file store's ids, which refuse a
        # server number of 0; the test run's placeholders must be ones it accepts
        import dataclasses

        out = await run.run_text(inputs(), happy(), fx.Canned())
        job = dataclasses.replace(
            out.job, summary="《Calm》 Mira and Oskar walked into the dark forest at dusk."
        )
        fake = Fake()
        with tempfile.TemporaryDirectory() as tmp:
            lines = await run.run_audio(
                job, out.inputs, ElevenLabs(fake, voice_id="v"), AudioFiles(Path(tmp)),
                runner=Mixer(),
            )  # fmt: skip
            text = "\n".join(lines)
            self.assertNotIn("failed", text)
            self.assertIn("recap-mix.mp3", text)
            kept = found(tmp, "forest.mp3")  # the loop, kept under the placeholders
            self.assertEqual(len(kept), 1)
            self.assertIn(str(run.PLACEHOLDER_GUILD), kept[0].parts)
        self.assertTrue(fake.paths("sound-generation"))  # the provider was asked for it

    def test_the_placeholder_ids_are_ones_the_audio_file_store_accepts(self) -> None:
        files = AudioFiles(Path("/tmp/anywhere"))
        files.ambience_path(run.PLACEHOLDER_GUILD, run.PLACEHOLDER_CAMPAIGN, "forest")
        files.recap_dir(run.PLACEHOLDER_GUILD, run.PLACEHOLDER_CAMPAIGN, 1)
        with self.assertRaises(ValueError):  # and the store's own checks are unchanged
            files.ambience_path(0, run.PLACEHOLDER_CAMPAIGN, "forest")

    def test_a_value_error_says_its_message_but_no_other_error_does(self) -> None:
        self.assertEqual(
            run.audio_failure(ValueError("not a campaign")),
            ["audio: failed (ValueError: not a campaign); the text above is unchanged"],
        )
        long = run.audio_failure(ValueError("x" * 500))[0]
        self.assertLessEqual(len(long), 200)  # cut short
        other = run.audio_failure(KeyError("a line of the transcript"))
        self.assertEqual(other, ["audio: failed (KeyError); the text above is unchanged"])


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
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)
        self.assertTrue(await run.keep(recaps, 1, camp.id, out, fx.DM))
        job = await recaps.get(1, camp.id, 100)
        assert job is not None
        self.assertEqual((job.state, job.status, job.title), ("ready", "draft", "The Silver Lock"))
        self.assertEqual(job.summary, out.job.summary)
        self.assertEqual(job.speakers, out.job.speakers)
        self.assertIsNotNone(job.emailed_at)  # marked as told: the worker never emails it
        self.assertEqual(await recaps.due(1), [])  # and never offered to the live worker
        self.assertFalse(await run.keep(recaps, 1, camp.id, out, fx.DM))  # left as it is
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM recap_jobs")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 1)


class TheConsent(DatabaseTest):
    async def test_only_yeses_for_the_engine_in_use_count_like_in_the_live_bot(self) -> None:
        from dmbot.consent import ConsentStore
        from dmbot.transcription.config import load_transcription_settings

        local = ConsentStore(self.db)
        await local.grant(1, fx.ALICE, outside_to=None)  # a yes given for local transcription only
        await local.grant(1, fx.BOB, outside_to="deepgram")
        engine = load_transcription_settings({"TRANSCRIBER": "deepgram", "DEEPGRAM_API_KEY": "k"})
        live = ConsentStore(self.db, outside=engine.outside_engine)
        self.assertEqual(await live.consenting(1), {fx.BOB})  # what the bot, and now the tool, use


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


class TheToolsLookup(DatabaseTest):
    """`recap_run --session` finds the session and the names the way the bot does, on tables with
    forced row-level security, and says WHICH of the three things failed (#2680)."""

    async def setUp_one(self, lines: bool = True) -> tuple[str, str]:
        from dmbot.transcript.store import TranscriptStore

        camp = await CampaignStore(self.db).create(1, "One", fx.DM)
        transcripts = TranscriptStore(self.db)
        sid = await transcripts.open_session(1, camp.id, 1_700_000_000)
        if lines:
            await transcripts.add_lines(1, sid, fx.lines())
        return camp.id, sid

    async def test_the_session_and_the_names_load(self) -> None:
        from dmbot.devtools.recap_run.__main__ import inputs_for

        cid, _ = await self.setUp_one()
        got = await inputs_for(self.db, 1, cid, 1_700_000_000)
        self.assertEqual(len(got.lines), len(fx.lines()))

    async def test_each_failure_says_which_it_was(self) -> None:
        from unittest.mock import patch

        from dmbot.devtools.recap_run.__main__ import inputs_for, not_loaded_line
        from dmbot.memory.lookup import LookupCache
        from dmbot.recaps.inputs import NotLoaded

        cid, _ = await self.setUp_one()
        with self.assertRaises(NotLoaded) as no_campaign:
            await inputs_for(self.db, 1, "0" * 32, 1_700_000_000)
        self.assertEqual(no_campaign.exception.why, "campaign")
        with self.assertRaises(NotLoaded) as elsewhere:
            await inputs_for(self.db, 2, cid, 1_700_000_000)  # another server sees nothing
        self.assertEqual(elsewhere.exception.why, "campaign")
        with self.assertRaises(NotLoaded) as wrong_time:
            await inputs_for(self.db, 1, cid, 12345)
        self.assertEqual(wrong_time.exception.why, "session")
        self.assertEqual(wrong_time.exception.started, (1_700_000_000,))
        line = not_loaded_line(wrong_time.exception, 1, cid, 12345)
        self.assertIn("12345", line)
        self.assertIn("1700000000", line)  # the times that do exist, to see the mismatch
        with (
            patch.object(LookupCache, "get", side_effect=RuntimeError("boom")),
            self.assertRaises(NotLoaded) as names,
        ):
            await inputs_for(self.db, 1, cid, 1_700_000_000)
        self.assertEqual(names.exception.why, "names")
        self.assertNotEqual(
            not_loaded_line(names.exception, 1, cid, 1),
            not_loaded_line(wrong_time.exception, 1, cid, 1),
        )

    async def test_a_session_with_no_stored_lines_says_so(self) -> None:
        from dmbot.devtools.recap_run.__main__ import inputs_for, not_loaded_line
        from dmbot.recaps.inputs import NotLoaded

        cid, _ = await self.setUp_one(lines=False)
        with self.assertRaises(NotLoaded) as empty:
            await inputs_for(self.db, 1, cid, 1_700_000_000)
        self.assertEqual((empty.exception.why, empty.exception.lines_in_it), ("session", 0))
        self.assertIn("0 stored lines", not_loaded_line(empty.exception, 1, cid, 1_700_000_000))

    async def test_the_line_count_is_one_servers_own(self) -> None:
        from dmbot.transcript.store import TranscriptStore

        cid, _ = await self.setUp_one()
        transcripts = TranscriptStore(self.db)
        self.assertEqual(await transcripts.line_count_at(1, cid, 1_700_000_000), len(fx.lines()))
        self.assertIsNone(await transcripts.line_count_at(1, cid, 5))  # no such session
        self.assertIsNone(await transcripts.line_count_at(2, cid, 1_700_000_000))  # another server

    async def test_the_lines_name_what_to_look_at(self) -> None:
        from unittest.mock import patch

        from dmbot.devtools.recap_run.__main__ import inputs_for, not_loaded_line
        from dmbot.memory.lookup import LookupCache
        from dmbot.recaps.inputs import NotLoaded

        cid, _ = await self.setUp_one()
        self.assertEqual(
            not_loaded_line(NotLoaded("campaign"), 1, cid, 9), f"no campaign {cid} in server 1"
        )
        self.assertIn("names can't be read", not_loaded_line(NotLoaded("names"), 1, cid, 9))
        with (
            patch.object(LookupCache, "get", side_effect=RuntimeError("boom")),
            self.assertRaises(NotLoaded) as names,
        ):
            await inputs_for(self.db, 1, cid, 1_700_000_000)
        self.assertIsInstance(names.exception.__cause__, RuntimeError)
