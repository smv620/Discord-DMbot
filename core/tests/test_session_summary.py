"""The end-of-session summary in the DM screen (#109), without Discord."""

import unittest

from dmbot.audio.segmenter import Utterance
from dmbot.capture_log import SessionTotals
from dmbot.dm_screen.messages import Spoke, session_summary, summary_problems

START = 1_700_000_000


def summary(spoke: list[Spoke], problems: list[str] = [], sent: int = 0) -> str:  # noqa: B006
    return session_summary("Frostmaiden", START, START + 8040, spoke, problems, downloads_sent=sent)


class Summary(unittest.TestCase):
    def test_who_spoke_how_long_and_where_the_transcript_is(self) -> None:
        text = summary(
            [Spoke("Mia", 42 * 60, 99), Spoke("Sam", 65 * 60, None), Spoke("*Dee*", 30, 82)],
            sent=3,
        )
        lines = text.splitlines()
        self.assertEqual(lines[0], "📋 **Session ended: Frostmaiden**")
        self.assertEqual(lines[1], f"Started <t:{START}:t>, ran 2 h 14 min.")
        self.assertEqual(
            lines[2], "🎙 **Who spoke:** Sam 1 h 5 min, Mia 42 min, \\*Dee\\* under a minute."
        )
        self.assertEqual(
            lines[3],
            "⚠️ \\*Dee\\*'s voice kept cutting out (82% got through), so some of their words "
            "may be missing.",
        )
        self.assertIn("✅ Everything DMbot heard was written down.", text)
        self.assertIn("private message to download the transcript", text)

    def test_problems_are_listed_in_plain_words(self) -> None:
        text = summary([Spoke("Mia", 30, None)], summary_problems(3, 1, caught_up=False))
        self.assertIn("missed 3 bits of speech. They aren't in the transcript.", text)
        self.assertIn("couldn't write down speech once, so the transcript has gaps", text)
        self.assertIn("The last few words before the stop may not be in the transcript", text)
        self.assertIn("tell whoever runs DMbot", text)
        self.assertNotIn("✅", text)
        self.assertIn("Anyone in the server can get the transcript with `/transcript`", text)
        self.assertNotIn("private message", text)  # none were sent
        self.assertEqual(summary_problems(0, 0, True), [])

    def test_nobody_recorded(self) -> None:
        text = summary([])
        self.assertIn("Nobody was recorded. DMbot only records people who said yes", text)
        self.assertNotIn("/transcript", text)

    def test_a_mention_stays_a_mention(self) -> None:
        self.assertIn("<@8> 1 min", summary([Spoke("<@8>", 60, None)]))


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
