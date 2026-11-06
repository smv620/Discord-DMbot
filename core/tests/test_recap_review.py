"""The recap test run's review files (#2011): recap.txt, report.txt and review.html, made from
invented text and empty stand-ins for the mp3s. No network and no paid call."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import re
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dmbot.devtools.recap_run import __main__ as run
from dmbot.devtools.recap_run import review as rv
from dmbot.recaps.audio.files import FILE_NAMES, MIX, MUSIC, VOICE
from tests import recap_fixtures as fx

SUMMARY = (
    "《Tense》 The gate stands open. ((pause)) Mira steps in.\n\n"
    "《Uneasy》 A cold wind follows. ((slow)) Nothing answers."
)


def folder_with(
    root: Path, *, text: bool = True, sound: tuple[str, ...] = (VOICE, MUSIC, MIX)
) -> Path:
    for kind in sound:
        (root / FILE_NAMES[kind]).write_bytes(b"")
    if text:
        (root / rv.RECAP_FILE).write_text(
            rv.recap_text(
                "The Gate",
                SUMMARY,
                ["Mira drew her blade."],
                ["Low strings.", "Soft drones."],
                voice_tags=True,
                names_given=True,
            ),
            encoding="utf-8",
        )
        (root / rv.REPORT_FILE).write_text(
            "Cues (2):\n  《Tense》 -> [urgently]\n  ((pause)) -> [short pause]\n", encoding="utf-8"
        )
    return root


class TheText(unittest.TestCase):
    def test_recap_txt_keeps_the_cues_and_has_a_copy_without_them(self) -> None:
        text = rv.recap_text("The Gate", SUMMARY, ["A bullet."], ["Strings."], voice_tags=False)
        _, parts = rv._sections(text)
        self.assertIn("《Tense》 The gate stands open.", parts[rv.S_CUES])
        self.assertIn("((pause))", parts[rv.S_CUES])
        self.assertNotIn("《", parts[rv.S_PLAIN])
        self.assertNotIn("((", parts[rv.S_PLAIN])
        self.assertIn("The gate stands open.", parts[rv.S_PLAIN])
        self.assertIn("Voice mood tags: off", text)

    def test_the_folder_reads_back_what_was_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            review = rv.read_folder(folder_with(Path(tmp)))
        self.assertEqual(review.title, "The Gate")
        self.assertEqual(review.housekeeping, ["Mira drew her blade."])
        self.assertEqual(review.music, ["Low strings.", "Soft drones."])
        self.assertEqual((review.voice_tags, review.names_given), (True, True))
        self.assertEqual(len(review.cue_map), 2)


class ThePage(unittest.TestCase):
    def page(self, **kw: object) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            folder = folder_with(Path(tmp), **kw)  # type: ignore[arg-type]
            return rv.page(rv.read_folder(folder), folder, with_names=True)

    def test_it_holds_the_three_players_with_relative_paths(self) -> None:
        page = self.page()
        for kind, ident in ((VOICE, "voice"), (MUSIC, "bed")):
            self.assertRegex(page, rf'<audio id="{ident}" src="{FILE_NAMES[kind]}"')
        self.assertRegex(page, rf'<audio controls[^>]* src="{FILE_NAMES[MIX]}"')
        self.assertEqual(page.count("<audio"), 3)

    def test_it_has_the_shared_transport_and_both_toggles_and_volumes(self) -> None:
        page = self.page()
        for ident in ("play", "seek", "clock", "voice-on", "voice-vol", "bed-on", "bed-vol"):
            self.assertIn(f'id="{ident}"', page)
        self.assertIn("Voice on", page)
        self.assertIn("Music on", page)
        self.assertIn("0.15", page)  # the drift limit, in seconds

    def test_each_cue_is_a_tag_and_the_words_are_escaped(self) -> None:
        review = rv.Review(title="<b>x</b>", with_cues="《Tense》 a < b & c ((pause)) d.")
        with tempfile.TemporaryDirectory() as tmp:
            page = rv.page(review, Path(tmp))
        self.assertIn('class="cue mood"', page)
        self.assertIn('class="cue perf"', page)
        self.assertIn("a &lt; b &amp; c", page)
        self.assertNotIn("<b>x</b>", page)

    def test_the_same_mood_always_has_the_same_colour(self) -> None:
        page = self.page()
        hues = re.findall(r"--hue:(\d+)", rv.tagged_html("《Tense》 a 《Tense》 b"))
        self.assertEqual(len(set(hues)), 1)
        self.assertIn("Tense", page)

    def test_it_loads_nothing_from_outside(self) -> None:
        page = self.page()
        self.assertNotIn("http", page)
        self.assertNotIn("//", re.sub(r"</?[a-z]+", "", page.replace("<!--", "")).replace("/*", ""))

    def test_it_says_whether_the_voice_tags_were_on(self) -> None:
        self.assertIn("Mood tags: on.", self.page())

    def test_it_says_which_file_is_which_in_plain_words(self) -> None:
        page = self.page()
        self.assertIn("Voice only", page)
        self.assertIn("Music and ambience", page)
        self.assertIn("Both mixed", page)
        self.assertNotIn(".mp3</b>", page)  # the file names are not the words a DM reads

    def test_a_cue_line_is_put_in_plain_words(self) -> None:
        self.assertIn("the voice sounds urgently", rv.cue_meaning("《Tense》 -> [urgently]"))
        self.assertIn("no voice change", rv.cue_meaning("《Uneasy》 -> music only"))
        self.assertIn("a short pause", rv.cue_meaning("((pause)) -> [short pause]"))
        self.assertNotIn("->", rv.cue_meaning("《Tense》 -> [urgently]"))

    def test_it_lists_what_to_listen_for(self) -> None:
        page = self.page()
        self.assertIn("no singing or words", page)
        self.assertIn("Names are said the way your table says them", page)

    def test_a_folder_with_no_text_says_so_and_still_has_the_players(self) -> None:
        page = self.page(text=False)
        self.assertIn(rv.NOT_SAVED, page)
        self.assertEqual(page.count("<audio"), 3)

    def test_a_folder_with_no_sound_says_so(self) -> None:
        self.assertIn("No sound was made for this test", self.page(sound=()))


class TheDetails(unittest.TestCase):
    def test_a_cue_inside_another_cue_does_not_repeat_words(self) -> None:
        out = rv.tagged_html("《a ((b)) c》 then words")
        self.assertEqual(out.count("then words"), 1)
        self.assertEqual(out.count("c》"), 1)

    def test_a_day_in_a_saved_reports_heading_is_not_part_of_the_title(self) -> None:
        printed = (
            "\n**Session Summary: The Gate (Day 4)**\n\n" + SUMMARY + "\n\n"
            "As the table reads it (no cues):\nPlain.\n\nHousekeeping:\n- B.\n\n"
            "Music prompts:\n1. M.\n"
        )
        review = rv.Review()
        rv._from_report(printed, review)
        self.assertEqual(review.title, "The Gate")

    def test_the_voice_tag_line_is_read_only_at_a_lines_start(self) -> None:
        review = rv.Review()
        rv._from_report("They said Voice mood tags: off, to be sure.\n", review)
        self.assertIsNone(review.voice_tags)

    def test_the_page_has_the_handlers_for_seek_toggles_and_trouble(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = folder_with(Path(tmp))
            page = rv.page(rv.read_folder(folder), folder)
        for needle in ("'input'", "'ended'", "'waiting'", "'error'", "tried.catch", "el.muted"):
            self.assertIn(needle, page)

    def test_voice_only_folder_disables_the_music_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = folder_with(Path(tmp), sound=(VOICE,))
            page = rv.page(rv.read_folder(folder), folder)
        self.assertRegex(page, r'id="bed-on" type="checkbox" checked disabled')
        self.assertNotIn('id="bed" src', page)
        self.assertIn("One of the two sound files is not in this folder", page)

    def test_old_files_are_cleared_but_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = folder_with(Path(tmp))
            (folder / "mine.txt").write_text("keep me")
            rv.clear_old(folder)
            left = sorted(p.name for p in folder.iterdir())
        self.assertEqual(left, ["mine.txt"])

    def test_a_private_file_is_never_wider_than_0600(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.txt"
            path.write_text("old")
            path.chmod(0o644)
            rv.write_private(path, "new")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_text(), "new")


class TheReviewFlag(unittest.TestCase):
    def go(self, folder: Path) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = run.main(["--review", str(folder)])
        return code, out.getvalue()

    def test_a_folder_with_only_mp3s_and_a_saved_report_builds_the_page(self) -> None:
        # What dev1's `tee` saves: the printed report, with the recap and the cue map in it.
        printed = (
            "\n**Session Summary: The Gate**\n\n" + SUMMARY + "\n\n"
            "As the table reads it (no cues):\nThe gate stands open. Mira steps in.\n\n"
            "Housekeeping:\n- Mira drew her blade.\n\nMusic prompts:\n1. Low strings.\n\n"
            "Checks:\n  PASS  353 words\n\nCalls: 4\n\nCues (2):\n"
            "  《Tense》 -> [urgently]\n  ((pause)) -> [short pause]\n"
            "Voice mood tags: on (DMBOT_RECAP_VOICE_TAGS)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            folder = folder_with(Path(tmp), text=False)
            (folder / rv.REPORT_FILE).write_text(printed, encoding="utf-8")
            code, said = self.go(folder)
            page = (folder / rv.PAGE_FILE).read_text(encoding="utf-8")
            mode = stat.S_IMODE((folder / rv.PAGE_FILE).stat().st_mode)
        self.assertEqual(code, 0, said)
        self.assertIn("review.html", said)
        self.assertIn('class="cue mood"', page)
        self.assertIn("The Gate", page)
        self.assertIn("Mira drew her blade.", page)
        self.assertIn("Low strings.", page)
        self.assertIn("[urgently]", page)
        self.assertIn("Mood tags: on.", page)
        self.assertEqual(mode, 0o600)

    def test_only_mp3s_gives_the_players_and_text_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = folder_with(Path(tmp), text=False)
            code, _ = self.go(folder)
            page = (folder / rv.PAGE_FILE).read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn(rv.NOT_SAVED, page)
        self.assertIn('id="voice"', page)

    def test_it_needs_no_key_no_yes_and_no_paid_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = folder_with(Path(tmp))
            with patch.dict("os.environ", {}, clear=True):  # no ANTHROPIC_API_KEY at all
                code, said = self.go(folder)
            self.assertTrue((folder / rv.PAGE_FILE).is_file())
        self.assertEqual(code, 0, said)

    def test_it_takes_no_flag_that_would_spend_money(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            run.parse_args(["--review", "d", "--audio"])
        self.assertIn("only rebuilds", err.getvalue())

    def test_a_folder_that_is_not_there_is_said_plainly(self) -> None:
        code, said = self.go(Path("/nonexistent-recap-folder"))
        self.assertEqual(code, 2)
        self.assertIn("is not a folder", said)


class TheOutFolder(unittest.IsolatedAsyncioTestCase):
    async def go(self, folder: Path) -> tuple[int, str]:
        speakers = {fx.DM: "Sam", fx.ALICE: "Mia", fx.BOB: "Ben"}
        story = "\n".join(f"{speakers[u]}: {t}" for u, t in fx.LINES) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            story_file = Path(tmp) / "story.txt"
            story_file.write_text(story, encoding="utf-8")
            argv = ["--transcript", str(story_file), "--dm", "Sam", "--out", str(folder), "--yes"]
            argv += ["--hours", "3.5"]  # the canned recap is a 3.5-hour session's length
            out = io.StringIO()
            answers = [fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = await run._transcript(
                    run.parse_args(argv),
                    fx.Canned(*answers),
                    fx.Canned(),
                    ("m-fast", "m-careful"),
                    {},
                    ask=lambda _: "yes",
                )
        return code, out.getvalue()

    async def test_out_saves_the_recap_the_report_and_the_page_privately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "run"
            code, said = await self.go(folder)
            names = sorted(p.name for p in folder.iterdir())
            modes = {stat.S_IMODE(p.stat().st_mode) for p in folder.iterdir()}
            folder_mode = stat.S_IMODE(folder.stat().st_mode)
            recap = (folder / rv.RECAP_FILE).read_text(encoding="utf-8")
            report = (folder / rv.REPORT_FILE).read_text(encoding="utf-8")
            page = (folder / rv.PAGE_FILE).read_text(encoding="utf-8")
        self.assertEqual(code, 0, said)
        self.assertEqual(names, ["recap.txt", "report.txt", "review.html"])
        self.assertEqual((folder_mode, modes), (0o700, {0o600}))
        self.assertIn("《Tense》", recap)  # the cues as the AI wrote them
        self.assertIn("Cues (3):", report)
        self.assertIn("Review page:", said)
        self.assertIn('class="cue mood"', page)
        for _, line in fx.LINES:
            self.assertNotIn(line, recap + report + page)  # the transcript is never saved

    async def test_a_second_run_into_the_same_folder_leaves_nothing_of_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "run"
            await self.go(folder)
            (folder / FILE_NAMES[VOICE]).write_bytes(b"old sound")
            (folder / rv.RECAP_FILE).write_text("OLD RECAP")
            await self.go(folder)
            names = sorted(p.name for p in folder.iterdir())
            recap = (folder / rv.RECAP_FILE).read_text(encoding="utf-8")
        self.assertEqual(names, ["recap.txt", "report.txt", "review.html"])  # the old sound went
        self.assertNotIn("OLD RECAP", recap)

    async def test_a_recap_that_failed_its_checks_is_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "run"
            with patch.object(run, "report", return_value=["The run did not finish"]):
                real = run.run_text

                async def failed(*a: object, **k: object) -> run.Outcome:
                    out = await real(*a, **k)  # type: ignore[arg-type]
                    return run.Outcome(
                        dataclasses.replace(out.job, state="failed"),
                        out.inputs, out.fast, out.careful, stopped=out.stopped,
                    )  # fmt: skip

                with patch.object(run, "run_text", failed):
                    code, _ = await self.go(folder)
            names = sorted(p.name for p in folder.iterdir())
        self.assertEqual(code, 1)
        self.assertEqual(names, ["report.txt", "review.html"])  # no recap.txt

    async def test_a_recap_that_names_a_secret_is_never_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "run"
            # The worker's own checks keep a secret out of a finished recap; this is the last
            # lock, the one that also guards the printing, so it is forced here.
            with patch.object(run, "names_a_secret_anywhere", return_value=True):
                code, said = await self.go(folder)
            written = list(folder.iterdir()) if folder.exists() else []
        self.assertEqual(code, 3)
        self.assertIn(run.REFUSED, said)
        self.assertEqual(written, [])
