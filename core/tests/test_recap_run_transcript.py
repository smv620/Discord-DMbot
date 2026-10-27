"""The recap test run on a transcript file (#1969): each format's reader on invented lines, the
refusals (an unknown format, no DM, consent not answered yes, --keep), the names file, the cue map
and the audio's pronunciation rules and kept files. A fake AI and a fake provider: nothing is paid
for. Every name here is made up."""

from __future__ import annotations

import contextlib
import io
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

    def test_a_byte_order_mark_does_not_change_any_format(self) -> None:
        for text, kind in (
            (DMBOT_FILE, tf.DMBOT), (PLAIN_FILE, tf.PLAIN), (VTT_FILE, tf.VTT), (SRT_FILE, tf.SRT),
        ):  # fmt: skip
            got = tf.parse("\ufeff" + text)
            self.assertEqual(got.format, kind)
            self.assertEqual(got.speakers[0], "Sam")  # not "\ufeffSam"

    def test_windows_line_endings_work_in_every_format(self) -> None:
        for text in (DMBOT_FILE, PLAIN_FILE, VTT_FILE, SRT_FILE):
            self.assertEqual(tf.parse(text.replace("\n", "\r\n")), tf.parse(text))

    def test_a_clock_time_inside_a_cue_is_words_not_a_speaker(self) -> None:
        got = tf.parse("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nSam: It was 10:30 by then.\n")
        self.assertEqual(
            (got.utterances[0].speaker, got.utterances[0].text), ("Sam", "It was 10:30 by then.")
        )
        with self.assertRaises(tf.TranscriptError):  # no name: refused, not a speaker "It was 10"
            tf.parse("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nIt was 10:30 by then.\n")

    def test_webvtt_identifiers_and_multi_line_notes_are_not_words(self) -> None:
        got = tf.parse(
            "WEBVTT\n\nNOTE a note\nthat runs on\n\nc1\n00:00:01.000 --> 00:00:02.000\n"
            "Sam: First.\n\nc2\n00:00:03.000 --> 00:00:04.000\nMia: Second.\n"
        )
        self.assertEqual(
            [(u.speaker, u.text) for u in got.utterances], [("Sam", "First."), ("Mia", "Second.")]
        )

    def test_a_file_that_is_over_a_quarter_wrapped_lines_is_refused_by_number_not_words(
        self,
    ) -> None:
        lines = [f"Sam: line {n}" for n in range(6)] + [
            f"a long continuation {n}" for n in range(4)
        ]
        with self.assertRaises(tf.TranscriptError) as caught:
            tf.parse("\n".join(lines))
        self.assertIn("line 7", str(caught.exception))
        self.assertNotIn("continuation", str(caught.exception))

    def test_a_few_wrapped_lines_are_joined_to_the_utterance_before_them(self) -> None:
        text = "\n".join(
            ["Sam: The gate stands", "open at dusk.", *[f"Mia: line {n}" for n in range(8)]]
        )
        got = tf.parse(text)
        self.assertEqual(got.format, tf.PLAIN)
        self.assertEqual(got.utterances[0].text, "The gate stands open at dusk.")
        self.assertEqual((got.skipped, got.joined), (0, 1))
        self.assertEqual(len(got.utterances), 9)

    def test_craigs_cut_out_placeholders_are_dropped_and_counted_apart(self) -> None:
        text = "\n".join(
            [
                "Sam: The gate stands open.",
                "{non-game context}",
                "Mia: Mira steps in.",
                "{non-game context}",
                "{non-game context}",
                "Sam: A torch is lit.",
            ]
        )
        got = tf.parse(text)
        self.assertEqual(got.format, tf.PLAIN)
        self.assertEqual((got.cut_out, got.joined), (3, 0))
        self.assertEqual(
            [(u.speaker, u.text) for u in got.utterances],
            [
                ("Sam", "The gate stands open."),
                ("Mia", "Mira steps in."),
                ("Sam", "A torch is lit."),
            ],
        )
        self.assertNotIn("non-game", "".join(u.text for u in got.utterances))

    def test_a_leading_placeholder_is_stripped_and_an_empty_utterance_dropped(self) -> None:
        text = "\n".join(
            [
                "Sam: {non-game context} then, at the dawn, we ride.",
                "Mia: {non-game context}",
                "Sam: Onward.",
            ]
        )
        got = tf.parse(text)
        self.assertEqual(
            [(u.speaker, u.text) for u in got.utterances],
            [("Sam", "then, at the dawn, we ride."), ("Sam", "Onward.")],
        )

    def test_a_wrapped_line_after_an_emptied_utterance_joins_that_speaker_not_the_one_before(
        self,
    ) -> None:
        text = "\n".join(
            [f"Sam: line {n}" for n in range(8)]
            + ["Mia: {non-game context}", "and then she spoke.", "Sam: Onward."]
        )
        got = tf.parse(text)
        self.assertEqual(got.utterances[-2].speaker, "Mia")
        self.assertEqual(got.utterances[-2].text, "and then she spoke.")

    def test_many_cut_outs_do_not_count_against_the_quarter_limit(self) -> None:
        lines: list[str] = []
        for n in range(10):
            lines += [f"Sam: line {n}", "{non-game context}", "{non-game context}"]
        got = tf.parse("\n".join(lines))
        self.assertEqual((got.cut_out, got.joined, len(got.utterances)), (20, 0, 10))

    def test_a_longer_braced_note_is_not_a_cut_out(self) -> None:
        long_note = "{" + "x" * 41 + "}"
        text = "\n".join([*[f"Sam: line {n}" for n in range(8)], long_note])
        got = tf.parse(text)
        self.assertEqual((got.cut_out, got.joined), (0, 1))

    def test_a_header_before_the_first_utterance_is_skipped_and_counted(self) -> None:
        text = (
            "Craig recording of the Test Table\nRecorded 2026-10-01\n\n"
            "Sam: The gate stands open.\nMia: Mira steps in.\n"
        )
        got = tf.parse(text)
        self.assertEqual(got.format, tf.PLAIN)
        self.assertEqual((got.skipped, got.joined), (2, 0))
        self.assertEqual([u.speaker for u in got.utterances], ["Sam", "Mia"])
        self.assertNotIn("Craig", "".join(u.text for u in got.utterances))

    def test_a_header_longer_than_ten_lines_is_not_a_plain_file(self) -> None:
        text = "\n".join([f"heading {n}" for n in range(11)] + ["Sam: Hello there."] * 3)
        with self.assertRaises(tf.TranscriptError):
            tf.parse(text)

    def test_names_may_hold_parentheses_hashes_dots_apostrophes_and_underscores(self) -> None:
        text = (
            "Rowan (rowan_v): The gate stands open.\nkai#0: Mira steps in.\n"
            "Mr. O'Neil: A torch is lit.\n"
        )
        got = tf.parse(text)
        self.assertEqual(got.speakers, ["Rowan (rowan_v)", "kai#0", "Mr. O'Neil"])

    def test_the_dm_may_be_given_as_the_whole_name_or_the_part_before_the_parenthesis(self) -> None:
        got = tf.parse("Rowan (rowan_v): Hello.\nMia (mia_x): Hi.\n")
        whole = tf.dm_id(got, "Rowan (rowan_v)")
        self.assertEqual(tf.dm_id(got, "Rowan"), whole)
        self.assertEqual(tf.dm_id(got, " rowan "), whole)
        with self.assertRaises(tf.TranscriptError):
            tf.dm_id(got, "rowan_v")  # the username alone is neither

    def test_two_speakers_with_the_same_short_name_need_the_whole_name(self) -> None:
        got = tf.parse("Rowan (a): Hello.\nRowan (b): Hi.\n")
        with self.assertRaises(tf.TranscriptError) as caught:
            tf.dm_id(got, "Rowan")
        self.assertIn("whole name", str(caught.exception))
        self.assertNotEqual(tf.dm_id(got, "Rowan (a)"), tf.dm_id(got, "Rowan (b)"))

    def test_dmbot_vtt_and_srt_are_still_found_before_plain(self) -> None:
        for text, kind in (
            (DMBOT_FILE, tf.DMBOT), (VTT_FILE, tf.VTT), (SRT_FILE, tf.SRT), (PLAIN_FILE, tf.PLAIN),
        ):  # fmt: skip
            self.assertEqual(tf.detect(text), kind)
        # a DMbot line with no character in braces has a name that looks like a plain one
        self.assertEqual(
            tf.detect("DMbot transcript: T, session 1\n\n[0:00:05] (Sam): Hello.\n"), tf.DMBOT
        )

    def test_the_sidebar_tag_is_read_where_it_stands_and_not_found_in_a_players_words(self) -> None:
        text = (
            DMBOT_FILE
            + "[0:02:00] (A Very Long Name That Goes On And On For Quite A While Indeed) "
            "[DM Sidebar id=x1]: a question\n"
            "[0:02:05] (Ben): She said [DM Sidebar] out loud.\n"
        )
        got = tf.parse(text)
        self.assertEqual(got.utterances[-1].text, "She said [DM Sidebar] out loud.")
        self.assertNotIn(
            "A Very Long Name That Goes On And On For Quite A While Indeed", got.speakers
        )

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


class TheLength(unittest.TestCase):
    """The recap's length follows the session's hours (#2013)."""

    def read(self, text: str, *extra: str) -> tuple[str, pl.Inputs]:
        with tempfile.TemporaryDirectory() as tmp:
            story = Path(tmp) / "story.txt"
            story.write_text(text, encoding="utf-8")
            args = run.parse_args(["--transcript", str(story), "--dm", "Sam", *extra])
            inputs, transcript, _ = run.read_transcript_inputs(args)
        return run._length_line(args, transcript, inputs), inputs

    def test_a_file_with_no_times_is_estimated_from_its_words(self) -> None:
        line, inputs = self.read("Sam: " + " ".join(["word"] * 14_000) + "\nMia: hi.\n")
        self.assertEqual(inputs.hours, 2.0)
        self.assertIn("about 2 hours (estimated from the words; give --hours to set it)", line)
        self.assertIn("about 230 words (a target, not a check)", line)

    def test_a_tiny_file_is_half_an_hour_at_least(self) -> None:
        _, inputs = self.read("Sam: hello there.\nMia: hi.\n")
        self.assertEqual(inputs.hours, 0.5)

    def test_hours_overrides_the_estimate(self) -> None:
        line, inputs = self.read("Sam: hello.\nMia: hi.\n", "--hours", "6")
        self.assertEqual(inputs.hours, 6)
        self.assertIn("about 6 hours (from --hours)", line)
        self.assertIn("about 450 words", line)

    def test_a_file_with_times_uses_them(self) -> None:
        text = "[00:00:01] Sam: hello.\n[01:00:00] Mia: hi.\n[02:00:01] Sam: bye.\n"
        line, inputs = self.read(text)
        self.assertIsNone(inputs.hours)  # the lines' own times are used
        self.assertIn("from the file's times", line)
        self.assertIn("about 2 hours", line)

    def test_the_hours_flag_is_checked(self) -> None:
        for bad in ("0", "0.1", "30", "-2"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                run.parse_args(["--transcript", "f.txt", "--dm", "Sam", "--hours", bad])
            self.assertIn("--hours", err.getvalue(), bad)
        args = run.parse_args(["--transcript", "f.txt", "--dm", "Sam", "--hours", "3.5"])
        self.assertEqual(args.hours, 3.5)


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
                "  《Tense》 -> [urgently]",
                "  ((pause)) -> [short pause]",
                "  《whisper》 -> [whispers]",
                "  ((slow)) -> music only",
                "  ((long pause)) -> [long pause]",
            ],
        )

    def test_the_canned_recap_lists_all_its_cues(self) -> None:
        script = sc.build("The Silver Lock", 4, fx.SUMMARY)
        rows = tf.cue_map(fx.SUMMARY, script)
        self.assertEqual(len(rows), 3)  # 《Tense》, ((slow)), 《Ominous》


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
        self.assertIn("--transcript", self.refused("--golden", "--out", "d"))
        args = run.parse_args(["--transcript", "f.txt", "--dm", "Sam", "--out", "d"])
        self.assertEqual(args.out, "d")  # the text is saved without --audio too

    def test_the_flags_are_accepted_together(self) -> None:
        args = run.parse_args(
            ["--transcript", "f.txt", "--dm", "Sam", "--names", "n.txt", "--audio", "--out", "d"]
        )
        self.assertEqual(
            (args.transcript, args.dm, args.names, args.out), ("f.txt", "Sam", "n.txt", "d")
        )

    def test_the_consent_question_says_the_words_go_to_an_ai_service(self) -> None:
        self.assertIn("AI service", tf.CONSENT_QUESTION)

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

        # The canned recap is a 3.5-hour session's length (#2013): a file this small is not.
        args = run.parse_args([*argv, *([] if "--hours" in argv else ["--hours", "3.5"])])
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
        self.assertIn("《Tense》 -> [urgently]", text)
        for _, said in fx.LINES:
            self.assertNotIn(said, text)  # the report never holds the transcript itself

    async def test_no_one_to_answer_is_a_no(self) -> None:
        story = self.file("story.txt", self.story())
        args = run.parse_args(["--transcript", story, "--dm", "Sam", "--yes"])
        fast = fx.Canned()

        def nobody(question: str) -> str:
            raise EOFError

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = await run._transcript(args, fast, fx.Canned(), ("a", "b"), {}, ask=nobody)
        self.assertEqual((code, fast.sent), (4, []))

    async def test_a_character_the_names_file_keeps_secret_is_not_offered_as_a_name(self) -> None:
        text = DMBOT_FILE
        story = self.file("dm.txt", text)
        names = self.file("names.txt", "secret: Oskar\n")
        args = run.parse_args(["--transcript", story, "--dm", "Sam", "--names", names, "--yes"])
        inputs, _, _ = run.read_transcript_inputs(args)
        self.assertIn("Mira", inputs.allowed_names)
        self.assertNotIn("Oskar", inputs.allowed_names)
        self.assertEqual(inputs.secret_names, ["Oskar"])

    async def test_the_names_file_may_start_with_a_byte_order_mark(self) -> None:
        story = self.file("story.txt", self.story())
        names = self.dir / "names.txt"
        names.write_bytes(b"\xef\xbb\xbfOskar = Oss-kar\n")
        args = run.parse_args(
            ["--transcript", story, "--dm", "Sam", "--names", str(names), "--yes"]
        )
        _, _, spoken = run.read_transcript_inputs(args)
        self.assertEqual(spoken, [SpokenName("Oskar", "Oss-kar")])

    async def test_the_summary_line_counts_the_cut_out_lines_apart_from_the_joined_ones(
        self,
    ) -> None:
        text = self.story().replace("\n", "\n{non-game context}\n", 2)
        story = self.file("craig2.txt", text)
        code, said, _ = await self.go(None, "--transcript", story, "--dm", "Sam")
        self.assertEqual(code, 0)
        self.assertIn("skipped 2 cut-out lines", said)
        self.assertNotIn("joined", said.split("skipped 2 cut-out lines")[0][-40:])

    async def test_the_summary_line_counts_what_the_plain_reader_let_through(self) -> None:
        text = "A header\nof two lines\n" + self.story().replace(
            "Mira draws her blade and steps inside.", "Mira draws her blade\nand steps inside."
        )
        story = self.file("craig.txt", text)
        code, said, _ = await self.go(None, "--transcript", story, "--dm", "Sam")
        self.assertEqual(code, 0)
        self.assertIn("(skipped 2 header lines, joined 1 wrapped lines)", said)
        self.assertNotIn("A header", said)  # a count, never the text

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


def open_up(folder: Path) -> None:
    folder.chmod(0o755)


def modes_in(folder: Path) -> tuple[int, set[int]]:
    """The folder's mode and the modes of what is in it."""
    return stat.S_IMODE(folder.stat().st_mode), {
        stat.S_IMODE(p.stat().st_mode) for p in folder.iterdir()
    }


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
        self.assertEqual(len(fake.paths("add-from-rules")), 1)
        deletes = [c for c in fake.calls if c[0] == "DELETE"]
        self.assertEqual(
            [c[1] for c in deletes], ["/v1/pronunciation-dictionaries/dict1"]
        )  # nothing left

    async def test_the_kept_folder_is_made_private_even_if_it_already_exists(self) -> None:
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
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as keep:
            kept = Path(keep)
            open_up(kept)
            await run.run_audio(
                out.job, out.inputs, ElevenLabs(Fake(), voice_id="v"), AudioFiles(Path(tmp)),
                runner=Mixer(), keep_in=kept,
            )  # fmt: skip
            self.assertEqual(modes_in(kept), (0o700, {0o600}))
