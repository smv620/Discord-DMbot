"""The recap test run on a transcript file (#1969): each format's reader on invented lines, the
refusals (an unknown format, no DM, consent not answered yes, --keep), the names file, the cue map
and the audio's pronunciation rules and kept files. A fake AI and a fake provider: nothing is paid
for. Every name here is made up."""

from __future__ import annotations

import contextlib
import io
import json
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from dmbot.devtools.recap_run import __main__ as run
from dmbot.devtools.recap_run import transcript_file as tf
from dmbot.devtools.recap_run.ephemeral import blank
from dmbot.recaps import pipeline as pl
from dmbot.recaps.audio import script as sc
from dmbot.recaps.audio.eleven import ElevenLabs
from dmbot.recaps.audio.files import AudioFiles
from dmbot.recaps.audio.names import SpokenName
from tests import recap_fixtures as fx
from tests.test_recap_audio_step import Fake, Mixer

DMBOT_FILE = """DMbot transcript: The Test Table, session 3
Started 2026-10-01 19:00 UTC
Cleaned: DMbot fixed the spelling of names it was sure about.
Each line: [time since start] (person) {their character}: what they said.

[0:00:05] (Sam) {narrating}: The party reaches the keep at dusk.
[0:00:20] (Mia) {Mira}: Mira draws her blade.
[0:00:25] (Sam) [DM Sidebar id=a1b2c3 via=voice]: what is the AC of a guard
[0:00:26] (DMbot) [DM Sidebar reply-to=a1b2c3]: A guard has AC 16.
[0:00:40] (Mia) [1m 22s of off-topic chat skipped]
[0:01:02] (Ben) {Oskar} (typed): Oskar lights a torch.
[0:01:10] (Ben): We follow her.
"""
PLAIN_FILE = """[00:01] Sam: The gate stands open.
[00:09] Mia: Mira steps inside, and says: hello.
Ben: Oskar follows.
"""
VTT_FILE = """WEBVTT

NOTE made up

00:00:01.000 --> 00:00:04.000
Sam: The gate stands open.

00:00:05.500 --> 00:00:08.000
Mia: Mira steps
inside.
"""
SRT_FILE = """1
00:00:01,000 --> 00:00:04,000
Sam: The gate stands open.

2
00:01:05,500 --> 00:01:08,000
<i>Mia: Mira steps inside.</i>
"""


class TheFormats(unittest.TestCase):
    def test_dmbots_own_download_keeps_the_table_and_leaves_out_the_sidebar_and_notes(self) -> None:
        got = tf.parse(DMBOT_FILE)
        self.assertEqual(got.format, tf.DMBOT)
        self.assertEqual(
            [(u.speaker, u.at_ms) for u in got.utterances],
            [("Sam", 5000), ("Mia", 20000), ("Ben", 62000), ("Ben", 70000)],
        )
        self.assertEqual(got.characters, ("Mira", "Oskar"))  # {narrating} is not a character
        self.assertEqual(got.speakers, ["Sam", "Mia", "Ben"])

    def test_plain_lines_with_and_without_a_time(self) -> None:
        got = tf.parse(PLAIN_FILE)
        self.assertEqual(got.format, tf.PLAIN)
        self.assertEqual([u.speaker for u in got.utterances], ["Sam", "Mia", "Ben"])
        self.assertEqual(got.utterances[0].at_ms, 1000)
        self.assertEqual(got.utterances[1].text, "Mira steps inside, and says: hello.")
        self.assertEqual(got.utterances[2].at_ms, 10000)  # no time: a second after the line before

    def test_webvtt_and_srt_cues_start_with_the_name(self) -> None:
        vtt = tf.parse(VTT_FILE)
        self.assertEqual(vtt.format, tf.VTT)
        self.assertEqual(
            [(u.speaker, u.at_ms) for u in vtt.utterances], [("Sam", 1000), ("Mia", 5500)]
        )
        self.assertEqual(vtt.utterances[1].text, "Mira steps inside.")  # a cue of two lines
        srt = tf.parse(SRT_FILE)
        self.assertEqual(srt.format, tf.SRT)
        self.assertEqual(srt.utterances[1].at_ms, 65500)
        self.assertEqual(srt.utterances[1].text, "Mira steps inside.")  # the styling is not words

    def test_a_cue_with_no_name_is_refused_without_quoting_it(self) -> None:
        with self.assertRaises(tf.TranscriptError) as caught:
            tf.parse("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nno name here at all\n")
        self.assertIn("cue 1", str(caught.exception))
        self.assertNotIn("no name here", str(caught.exception))

    def test_an_unknown_format_is_refused_naming_the_formats_that_work(self) -> None:
        for text in ("", "just some words\nwith no names\n", '{"json": true}'):
            with self.assertRaises(tf.TranscriptError) as caught:
                tf.parse(text)
            if text:
                self.assertIn("WebVTT", str(caught.exception))
                self.assertIn("DMbot's own", str(caught.exception))

    def test_the_lines_are_numbered_in_the_order_spoken_with_a_made_up_number_per_speaker(
        self,
    ) -> None:
        got = tf.parse(PLAIN_FILE)
        ids = tf.speaker_ids(got)
        self.assertEqual(sorted(ids.values()), [100, 101, 102])
        lines = tf.lines_of(got)
        self.assertEqual([line.user_id for line in lines], [100, 101, 102])
        self.assertEqual(lines[0].text, "The gate stands open.")

    def test_the_dm_is_found_by_name_and_a_missing_one_names_who_is_there(self) -> None:
        got = tf.parse(PLAIN_FILE)
        self.assertEqual(tf.dm_id(got, " sam "), 100)
        with self.assertRaises(tf.TranscriptError) as caught:
            tf.dm_id(got, "Zed")
        self.assertIn("Ben, Mia, Sam", str(caught.exception))
        self.assertNotIn("gate", str(caught.exception))  # a name, never a line


class TheNamesFile(unittest.TestCase):
    def test_names_with_how_they_are_said_and_secret_ones(self) -> None:
        got = tf.parse_names(
            "# the table's names\n\nKa'zeth = Kah-zeth\nGreywater Keep\n"
            "secret: Vaelith\nSecret: Orm = Orrum\n"
        )
        self.assertEqual(
            got,
            [
                SpokenName("Ka'zeth", "Kah-zeth"),
                SpokenName("Greywater Keep"),
                SpokenName("Vaelith", secret=True),
                SpokenName("Orm", "Orrum", secret=True),
            ],
        )

    def test_a_line_with_no_name_is_refused(self) -> None:
        with self.assertRaises(tf.TranscriptError):
            tf.parse_names("= Kah-zeth\n")


class TheCueMap(unittest.TestCase):
    def test_every_cue_is_listed_with_its_voice_tag_or_music_only(self) -> None:
        summary = (
            "《Tense》 The gate stands open. ((pause)) Mira steps in.\n\n"
            "《whisper》 A voice speaks. ((slow)) And ((long pause)) it goes."
        )
        script = sc.build("The Gate", None, summary)
        rows = tf.cue_map(summary, script)
        self.assertEqual(
            rows,
            [
                "  《Tense》 -> music only",
                "  ((pause)) -> [short pause]",
                "  《whisper》 -> [whispers]",
                "  ((slow)) -> music only",
                "  ((long pause)) -> [long pause]",
            ],
        )

    def test_the_canned_recap_lists_all_its_cues(self) -> None:
        script = sc.build("The Silver Lock", 4, fx.SUMMARY)
        rows = tf.cue_map(fx.SUMMARY, script)
        self.assertEqual(len(rows), 3)  # 《Tense》, ((slow)), 《Uneasy》


class TheRefusals(unittest.TestCase):
    def refused(self, *argv: str) -> str:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            run.parse_args(list(argv))
        return err.getvalue()

    def test_a_transcript_needs_a_dm(self) -> None:
        self.assertIn("--dm", self.refused("--transcript", "f.txt"))

    def test_keep_is_refused_with_a_transcript(self) -> None:
        self.assertIn(
            "never stored", self.refused("--transcript", "f.txt", "--dm", "Sam", "--keep")
        )

    def test_the_other_flags_go_with_a_transcript_or_audio(self) -> None:
        self.assertIn("--transcript", self.refused("--golden", "--dm", "Sam"))
        self.assertIn("--audio", self.refused("--transcript", "f.txt", "--dm", "Sam", "--out", "d"))

    def test_the_flags_are_accepted_together(self) -> None:
        args = run.parse_args(
            ["--transcript", "f.txt", "--dm", "Sam", "--names", "n.txt", "--audio", "--out", "d"]
        )
        self.assertEqual(
            (args.transcript, args.dm, args.names, args.out), ("f.txt", "Sam", "n.txt", "d")
        )

    def test_only_the_word_yes_is_consent(self) -> None:
        self.assertTrue(tf.consent_given(" Yes "))
        for answer in ("", "y", "no", "yes please", "ok"):
            self.assertFalse(tf.consent_given(answer), answer)


class TheRun(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def file(self, name: str, text: str) -> str:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def story(self) -> str:
        # The canned session as plain lines: the DM is Sam, the players Mia and Ben.
        speakers = {fx.DM: "Sam", fx.ALICE: "Mia", fx.BOB: "Ben"}
        return "\n".join(f"{speakers[user]}: {text}" for user, text in fx.LINES) + "\n"

    async def go(
        self, answers: list[str | Exception] | None, *argv: str, ask: str = "yes"
    ) -> tuple[int, str, list[str]]:
        asked: list[str] = []

        def reply(question: str) -> str:
            asked.append(question)
            return ask

        args = run.parse_args(list(argv))
        fast = fx.Canned(*(answers or []))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = await run._transcript(
                args, fast, fx.Canned(), ("m-fast", "m-careful"), {}, ask=reply
            )
        self.fast = fast
        return code, out.getvalue(), asked

    def happy(self) -> list[str | Exception]:
        return [fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER]

    async def test_it_runs_the_recap_on_the_file_and_prints_the_recap_cues_and_never_the_file(
        self,
    ) -> None:
        story = self.file("story.txt", self.story())
        names = self.file("names.txt", "Mira\nOskar\nGreywater Keep\n")
        code, text, asked = await self.go(
            self.happy(), "--transcript", story, "--dm", "Sam", "--names", names, "--yes"
        )
        self.assertEqual(code, 0, text)
        self.assertEqual(asked, [tf.CONSENT_QUESTION])  # once
        self.assertIn("The Silver Lock", text)
        self.assertIn("Cues (3):", text)
        self.assertIn("《Tense》 -> music only", text)
        for _, said in fx.LINES:
            self.assertNotIn(said, text)  # the report never holds the transcript itself

    async def test_nothing_is_asked_or_run_without_yes(self) -> None:
        story = self.file("story.txt", self.story())
        code, text, asked = await self.go(None, "--transcript", story, "--dm", "Sam")
        self.assertEqual(code, 0)
        self.assertIn("Nothing was run", text)
        self.assertEqual(asked, [])
        self.assertEqual(self.fast.sent, [])

    async def test_yes_does_not_answer_the_consent_question_and_no_stops_before_any_call(
        self,
    ) -> None:
        story = self.file("story.txt", self.story())
        for answer in ("no", "", "y"):
            code, text, asked = await self.go(
                self.happy(), "--transcript", story, "--dm", "Sam", "--yes", ask=answer
            )
            self.assertEqual(code, 4)
            self.assertEqual(asked, [tf.CONSENT_QUESTION])  # --yes did not skip it
            self.assertIn("Nothing was sent", text)
            self.assertEqual(self.fast.sent, [], answer)  # no paid call

    async def test_an_unknown_format_or_a_missing_dm_or_file_stops_with_a_plain_line(self) -> None:
        odd = self.file("odd.txt", "some words\nwith no names\n")
        code, text, asked = await self.go(None, "--transcript", odd, "--dm", "Sam", "--yes")
        self.assertEqual((code, asked), (2, []))
        self.assertIn("WebVTT", text)
        story = self.file("story.txt", self.story())
        code, text, asked = await self.go(None, "--transcript", story, "--dm", "Zed", "--yes")
        self.assertEqual((code, asked), (2, []))
        self.assertIn("Ben, Mia, Sam", text)
        code, text, _ = await self.go(None, "--transcript", str(self.dir / "none.txt"), "--dm", "S")
        self.assertEqual(code, 2)

    async def test_the_names_file_sets_the_names_the_recap_may_and_may_not_use(self) -> None:
        story = self.file("story.txt", self.story())
        names = self.file("names.txt", "Oskar = Oss-kar\nsecret: Vaelith\n")
        args = run.parse_args(["--transcript", story, "--dm", "Sam", "--names", names, "--yes"])
        inputs, transcript, spoken = run.read_transcript_inputs(args)
        self.assertEqual(inputs.secret_names, ["Vaelith"])
        self.assertIn("Oskar", inputs.allowed_names)
        self.assertNotIn("Vaelith", inputs.allowed_names)
        self.assertEqual(inputs.dm_ids, {tf.dm_id(transcript, "Sam")})
        self.assertEqual([n.said_as for n in spoken], ["Oss-kar", None])

    async def test_a_recap_naming_a_secret_from_the_names_file_is_refused_not_printed(self) -> None:
        job = replace(
            blank(1, 1, 0), state="ready", title="Vaelith", summary="《Calm》 Vaelith walked in.",
            housekeeping=("Vaelith drew a blade.",),
        )  # fmt: skip
        story = self.file("story.txt", self.story())
        names = self.file("names.txt", "secret: Vaelith\n")
        args = run.parse_args(["--transcript", story, "--dm", "Sam", "--names", names, "--yes"])
        inputs, _, _ = run.read_transcript_inputs(args)
        out = run.Outcome(
            job, inputs, run.Counting(fx.Canned(), "m"), run.Counting(fx.Canned(), "m")
        )
        lines = run.report(out, {})
        self.assertEqual(lines, [run.REFUSED])
        self.assertNotIn("Vaelith", "\n".join(lines))


class TheAudio(unittest.IsolatedAsyncioTestCase):
    async def test_names_with_a_spelling_make_the_dictionary_and_a_secret_is_never_sent(
        self,
    ) -> None:
        out = await run.run_text(
            pl.Inputs(
                lines=fx.lines(),
                dm_ids={fx.DM},
                allowed_names=list(fx.NAMES),
                secret_names=[],
                previous="",
                day=4,
            ),
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(),
        )
        fake = Fake()
        names = [SpokenName("Oskar", "Oss-kar"), SpokenName("Vaelith", "Vay-lith", secret=True)]
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as keep:
            kept = Path(keep) / "mine"
            lines = await run.run_audio(
                out.job, out.inputs, ElevenLabs(fake, voice_id="v"), AudioFiles(Path(tmp)),
                runner=Mixer(), names=names, keep_in=kept,
            )  # fmt: skip
            text = "\n".join(lines)
            self.assertIn("Pronunciation rules: 1", text)
            self.assertIn("Kept in", text)
            self.assertEqual(
                sorted(p.name for p in kept.iterdir()),
                ["recap-mix.mp3", "recap-music.mp3", "recap-voice.mp3"],
            )
            self.assertEqual(stat.S_IMODE(kept.stat().st_mode), 0o700)  # private to this user
        sent = fake.everything_sent()
        self.assertIn("Oss-kar", sent)
        self.assertNotIn("Vaelith", sent)
        self.assertNotIn("Vay-lith", sent)
        self.assertEqual(len(fake.paths("pronunciation-dictionaries")), 1)
        json.dumps(sent)  # (a plain string: nothing to decode)
