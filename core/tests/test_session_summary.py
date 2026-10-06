"""The end-of-session summary in the DM screen (#109), without Discord."""

import unittest

from dmbot.audio.segmenter import Utterance
from dmbot.capture_log import SessionTotals
from dmbot.dm_screen.messages import Spoke, session_summary, summary_problems

START = 1_700_000_000


class Summary(unittest.TestCase):
    def test_who_spoke_how_long_and_a_pointer_to_the_transcript(self) -> None:
        text = session_summary(
            "Frostmaiden",
            START,
            START + 2 * 3600 + 14 * 60,
            [Spoke("Mia", 42 * 60, 99), Spoke("Sam", 65 * 60, None), Spoke("*Dee*", 90, 82)],
            [],
            downloads=True,
        )
        lines = text.splitlines()
        self.assertEqual(lines[0], "📋 **Session over: Frostmaiden**")
        self.assertEqual(lines[1], f"Started <t:{START}:t>, ran 2 h 14 min.")
        self.assertEqual(
            lines[2],
            "🎙 **Recorded:** Sam (1 h 5 min of speech), Mia (42 min of speech), "
            "\\*Dee\\* (1 min of speech, ⚠️ only 82% of their voice got through).",
        )
        self.assertIn("No problems", text)
        self.assertIn("private message to download the transcript", text)

    def test_problems_are_listed_in_plain_words(self) -> None:
        problems = summary_problems(missed=3, failed=1, caught_up=False)
        text = session_summary(
            "X", START, START + 60, [Spoke("Mia", 30, None)], problems, downloads=False
        )
        self.assertIn("missed 3 pieces of speech because writing things down fell behind", text)
        self.assertIn("failed once, so the transcript has gaps", text)
        self.assertIn("before it finished writing down the last few words", text)
        self.assertNotIn("No problems", text)
        self.assertNotIn("/transcript", text)
        self.assertEqual(summary_problems(0, 0, True), [])

    def test_nobody_recorded(self) -> None:
        text = session_summary("X", START, START + 600, [], [], downloads=True)
        self.assertIn("Nobody was recorded", text)
        self.assertNotIn("/transcript", text)

    def test_a_mention_stays_a_mention(self) -> None:
        text = session_summary(
            "X", START, START + 60, [Spoke("<@8>", 60, None)], [], downloads=False
        )
        self.assertIn("<@8> (1 min of speech)", text)


class Totals(unittest.TestCase):
    def test_the_whole_session_is_counted(self) -> None:
        totals = SessionTotals()
        totals.add_utterance(Utterance(1, 8, 0, 0, bytes(32000)))  # one second
        totals.add_utterance(Utterance(1, 8, 0, 0, bytes(64000)))
        totals.add_health(8, 80, 100)
        totals.add_health(8, 200, 100)  # capped at what was expected
        self.assertAlmostEqual(totals.speakers[8].seconds, 3.0)
        self.assertEqual(totals.speakers[8].percent, 90)
        totals.add_health(9, 0, 0)
        self.assertIsNone(totals.speakers[9].percent)
