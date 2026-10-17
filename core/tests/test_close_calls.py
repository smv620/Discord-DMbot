"""Counting the Cleaner's close calls (#1577): the pure counter, the Cleaner feeding it, the one
line at a session's end, and the offline devtool. Made-up names; counts only in the bot."""

from __future__ import annotations

import io
import logging
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dmbot.bot import DMBot
from dmbot.devtools import cleaner_close_calls as tool
from dmbot.memory.scene import SceneTracker
from dmbot.transcript import cleaner
from dmbot.transcript.cleaner import Vocabulary, clean
from dmbot.transcript.close_calls import BAND, NEAREST_KEPT, CloseCalls
from tests.test_transcript_cleaner import lookup

T = 0.7  # a threshold to weigh against


class Counter(unittest.TestCase):
    def test_a_score_within_the_band_counts_on_either_side(self) -> None:
        c = CloseCalls()
        c.start_line()
        c.note(T + 0.03, T, one_word=False, person=False, at=1)  # just above: fixed
        c.note(T - 0.03, T, one_word=False, person=False, at=2)  # just below: left
        c.note(T + BAND, T, one_word=False, person=False, at=3)  # the edge counts
        c.note(T - BAND, T, one_word=False, person=False, at=4)
        self.assertEqual((c.fixed_near, c.left_near), (2, 2))

    def test_a_score_outside_the_band_is_not_a_close_call(self) -> None:
        c = CloseCalls()
        c.start_line()
        c.note(T + 0.06, T, one_word=False, person=False, at=1)
        c.note(T - 0.06, T, one_word=False, person=False, at=2)
        self.assertEqual((c.fixed_near, c.left_near), (0, 0))

    def test_the_threshold_itself_is_fixed_not_left(self) -> None:
        c = CloseCalls()
        c.start_line()
        c.note(T, T, one_word=False, person=False, at=1)
        self.assertEqual((c.fixed_near, c.left_near), (1, 0))

    def test_the_one_word_case_and_people_are_counted_apart(self) -> None:
        c = CloseCalls()
        c.start_line()
        c.note(0.82, 0.8, one_word=True, person=False, at=1)
        c.note(0.78, 0.8, one_word=True, person=True, at=5)
        c.note(0.72, T, one_word=False, person=True, at=9)
        self.assertEqual(c.one_word, 2)
        self.assertEqual((c.people_fixed_near, c.people_left_near), (1, 1))
        self.assertEqual((c.fixed_near, c.left_near), (2, 1))

    def test_the_same_place_weighed_again_in_a_line_counts_once_and_a_new_line_counts_again(
        self,
    ) -> None:
        c = CloseCalls()
        c.start_line()
        c.note(0.71, T, one_word=False, person=False, at=4)  # tried as three words, then two...
        c.note(0.71, T, one_word=False, person=False, at=4)
        self.assertEqual(c.fixed_near, 1)
        c.start_line()
        c.note(0.71, T, one_word=False, person=False, at=4)
        self.assertEqual((c.fixed_near, c.lines), (2, 2))

    def test_the_report_is_one_line_of_counts_with_no_words(self) -> None:
        c = CloseCalls()
        for n in range(412):
            c.start_line()
            if n < 3:
                c.note(0.71, T, one_word=False, person=False, at=0, heard="Zed", candidate="Zeth")
        self.assertEqual(
            c.report(),
            "Cleaner close calls: fixed-near 3, left-near 0, one-word 0 of 412 lines",
        )

    def test_words_are_kept_only_when_asked_and_only_the_ten_nearest_misses(self) -> None:
        quiet = CloseCalls()
        quiet.start_line()
        quiet.note(0.69, T, one_word=False, person=False, at=0, heard="Zed", candidate="Zeth")
        self.assertEqual(quiet.nearest, [])  # the bot never keeps text
        c = CloseCalls(keep_words=True)
        for n in range(15):
            c.start_line()
            c.note(T - 0.001 * (n + 1), T, one_word=False, person=False, at=0,
                   heard=f"w{n}", candidate="Zeth")  # fmt: skip
        self.assertEqual(len(c.nearest), NEAREST_KEPT)
        self.assertEqual(c.nearest[0].heard, "w0")  # the nearest first


class TheCleaner(unittest.TestCase):
    def counted(self, line: str, **patches: Any) -> CloseCalls:
        calls = CloseCalls()
        with patch.multiple(cleaner, **patches) if patches else nullcontext():
            clean(lookup(), line, close_calls=calls)
        return calls

    # "Beleros" and Belleros are alike by 0.933. One word alone is held to the one-word bar.
    def test_a_fix_just_above_the_threshold_is_counted_as_fixed_near(self) -> None:
        calls = self.counted("We met Beleros today", MIN_LIKENESS_ONE_WORD=0.93)
        self.assertEqual((calls.fixed_near, calls.left_near, calls.lines), (1, 0, 1))

    def test_words_left_just_below_the_threshold_are_counted_as_left_near(self) -> None:
        calls = self.counted("We met Beleros today", MIN_LIKENESS_ONE_WORD=0.96)
        self.assertEqual((calls.fixed_near, calls.left_near), (0, 1))

    def test_far_from_the_threshold_nothing_is_counted(self) -> None:
        calls = self.counted("We met Beleros today")  # 0.933 against 0.7
        self.assertEqual((calls.fixed_near, calls.left_near, calls.lines), (0, 0, 1))

    def test_one_word_against_the_one_word_threshold_is_counted(self) -> None:
        calls = self.counted("We met Beleros today", MIN_LIKENESS_ONE_WORD=0.95)
        self.assertEqual((calls.one_word, calls.left_near), (1, 1))

    def test_one_word_is_not_judged_by_the_joined_name_bar(self) -> None:
        """0.933 sits by MIN_LIKENESS here, but a single word is decided by the one-word bar,
        so it must not be counted against the other (it would read as 'fixed' when not)."""
        calls = self.counted("We met Beleros today", MIN_LIKENESS=0.93)
        self.assertEqual((calls.fixed_near, calls.left_near), (0, 0))

    def test_a_joined_name_is_judged_by_the_joined_name_bar(self) -> None:
        calls = self.counted("The mage Ka Zeth came", MIN_LIKENESS=1.0)
        self.assertEqual((calls.fixed_near, calls.left_near, calls.one_word), (1, 0, 0))

    def test_counting_never_changes_what_is_cleaned(self) -> None:
        lines = ["We met Beleros today", "Beleros again", "The mage Ka Zeth came", "Cerric spoke"]
        for ones in (0.5, 0.93, 0.96):
            with patch.multiple(cleaner, MIN_LIKENESS_ONE_WORD=ones):
                counted = [clean(lookup(), x, close_calls=CloseCalls()) for x in lines]
                plain = [clean(lookup(), x) for x in lines]
            self.assertEqual(counted, plain)

    def test_no_counter_means_nothing_changes(self) -> None:
        with_calls = clean(lookup(), "We met Beleros today", close_calls=CloseCalls())
        without = clean(lookup(), "We met Beleros today")
        self.assertEqual(with_calls, without)


class SessionEnd(unittest.IsolatedAsyncioTestCase):
    def table(self) -> Any:
        return SimpleNamespace(
            guild_id=1,
            campaign_id="c" * 32,
            close_calls=CloseCalls(),
            name_lookup=lookup(),
            vocabulary=Vocabulary(),
            people=(),
            scene=SceneTracker(),
        )

    async def test_table_speech_is_counted_and_the_session_end_line_is_said_once(self) -> None:
        table = self.table()
        fake = SimpleNamespace(_clean_failed_at=0.0)
        with patch.object(cleaner, "MIN_LIKENESS_ONE_WORD", 0.93):
            for _ in range(3):
                DMBot._clean(fake, table, "We met Beleros today", unsure=True, count=True)  # type: ignore[arg-type]
        DMBot._clean(fake, table, "Beleros again", unsure=True)  # type: ignore[arg-type]  # not counted
        self.assertEqual(table.close_calls.lines, 3)
        with self.assertLogs("dmbot.bot", logging.INFO) as logged:
            await DMBot.log_close_calls(SimpleNamespace(), table)  # type: ignore[arg-type]
        (line,) = [r.getMessage() for r in logged.records]
        self.assertEqual(
            line, "Cleaner close calls: fixed-near 3, left-near 0, one-word 3 of 3 lines"
        )
        self.assertNotIn("eleros", line)  # counts only: no words, names or speakers
        with self.assertNoLogs("dmbot.bot", logging.INFO):  # said once
            await DMBot.log_close_calls(SimpleNamespace(), table)  # type: ignore[arg-type]

    async def test_a_session_with_no_lines_logs_nothing(self) -> None:
        with self.assertNoLogs("dmbot.bot", logging.INFO):
            await DMBot.log_close_calls(SimpleNamespace(), self.table())  # type: ignore[arg-type]


class Devtool(unittest.TestCase):
    def run_tool(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), patch.dict("os.environ", {}, clear=False):
            code = tool.main(list(argv))
        return code, out.getvalue()

    def test_it_counts_a_made_up_transcript_and_lists_the_nearest_misses(self) -> None:
        with TemporaryDirectory() as folder:
            names = Path(folder, "names.txt")
            names.write_text("Belleros | NPC\n", encoding="utf-8")
            transcript = Path(folder, "t.txt")
            transcript.write_text("We met Beleros today\n\nBeleros again\n", encoding="utf-8")
            with patch.object(cleaner, "MIN_LIKENESS_ONE_WORD", 0.96):
                code, text = self.run_tool("--names", str(names), str(transcript))
        self.assertEqual(code, 0)
        self.assertIn("Cleaner close calls: fixed-near 0, left-near 2", text)
        self.assertIn("of 2 lines", text)
        self.assertIn("Beleros / Belleros / 0.93, 0.96", text)

    def test_it_says_when_nothing_is_near(self) -> None:
        with TemporaryDirectory() as folder:
            names = Path(folder, "names.txt")
            names.write_text("Belleros | NPC\n", encoding="utf-8")
            transcript = Path(folder, "t.txt")
            transcript.write_text("Nothing here\n", encoding="utf-8")
            code, text = self.run_tool("--names", str(names), str(transcript))
        self.assertEqual(code, 0)
        self.assertIn("No misses within 0.05", text)

    def test_a_missing_file_is_a_plain_message_not_a_crash(self) -> None:
        code, _ = self.run_tool("--names", "/nonexistent/n.txt", "/nonexistent/t.txt")
        self.assertEqual(code, 1)

    def test_it_will_not_run_in_the_bots_own_container(self) -> None:
        with patch.dict("os.environ", {"DISCORD_TOKEN": "x"}):
            code, _ = self.run_tool("--names", "n", "t")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
