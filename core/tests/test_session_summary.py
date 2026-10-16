"""The end-of-session summary in the DM screen (#109), without Discord."""

import unittest

from dmbot.audio.segmenter import Utterance
from dmbot.capture_log import SessionTotals
from dmbot.dm_screen.messages import Spoke, names_line, session_summary, summary_problems

START = 1_700_000_000


def summary(spoke: list[Spoke], problems: list[str] = [], sent: int = 0) -> str:  # noqa: B006
    return session_summary("Frostmaiden", START, START + 8040, spoke, problems, downloads_sent=sent)


class Summary(unittest.TestCase):
    def test_who_spoke_how_long_and_where_the_transcript_is(self) -> None:
        text = summary(
            [
                Spoke("Mia", 42 * 60, 99, False),
                Spoke("Sam", 65 * 60, None, False),
                Spoke("*Dee*", 30, 82, True),
            ],
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
            "⚠️ \\*Dee\\*'s voice cut out at times, so some of their words may be missing "
            "from the transcript.",
        )
        self.assertNotIn("✅", text)  # no all-clear under Dee's ⚠️
        clean = summary([Spoke("Mia", 60, 99, False)], sent=0)
        self.assertIn("✅ Everything DMbot heard was written down.", clean)
        self.assertIn("private message to download the transcript", text)

    def test_problems_are_listed_in_plain_words(self) -> None:
        text = summary([Spoke("Mia", 30, None, False)], summary_problems(3, 1, caught_up=False))
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
        self.assertIn("<@8> 1 min", summary([Spoke("<@8>", 60, None, False)]))


class NamesLine(unittest.TestCase):
    def test_mixed_counts_make_one_plain_sentence(self) -> None:
        self.assertEqual(
            names_line(3, 2, 1, 1, 2, 1),
            "✏️ Names: DMbot fixed 3 by itself, asked you about 2 (1 not answered), "
            "you corrected 1, players suggested 2 (1 still waiting).",
        )

    def test_parts_at_zero_are_left_out(self) -> None:
        self.assertEqual(names_line(4, 0, 0, 0, 0, 0), "✏️ Names: DMbot fixed 4 by itself.")
        self.assertEqual(
            names_line(0, 2, 0, 0, 1, 0), "✏️ Names: asked you about 2, players suggested 1."
        )

    def test_all_zeros_say_nothing(self) -> None:
        self.assertEqual(names_line(0, 0, 0, 0, 0, 0), "")

    def test_the_summary_is_otherwise_unchanged(self) -> None:
        spoke = [Spoke("Mia", 60, 99, False)]
        plain = session_summary("F", START, START + 60, spoke, [], downloads_sent=1)
        line = names_line(1, 0, 0, 0, 0, 0)
        with_names = session_summary(
            "F", START, START + 60, spoke, [], downloads_sent=1, names=line
        )
        self.assertEqual(
            session_summary("F", START, START + 60, spoke, [], downloads_sent=1, names=""), plain
        )
        self.assertEqual(with_names.splitlines().count(line), 1)
        self.assertEqual([x for x in with_names.splitlines() if x != line], plain.splitlines())
        # under the all-clear, above the download note
        lines = with_names.splitlines()
        self.assertEqual(
            lines.index(line), lines.index("✅ Everything DMbot heard was written down.") + 1
        )


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


class OnlyFlaggedPeopleCutOut(unittest.TestCase):
    """#699: the summary says "cut out at times" only for people the DM was warned about
    (their lines read garbled, or the loss was large)."""

    def test_a_low_percent_alone_is_no_alarm(self) -> None:
        text = summary([Spoke("Mia", 120, 60, False)], sent=0)
        self.assertNotIn("cutting out", text)

    def test_a_flagged_person_is_named(self) -> None:
        text = summary([Spoke("Mia", 120, 60, True)], sent=0)
        self.assertIn("Mia's voice cut out at times", text)

    def test_the_percent_counts_only_pieces_worth_writing_down(self) -> None:
        # dev1's Test A, a TV in the room; and a 0.2 s blip, which doesn't count.
        totals = SessionTotals()
        for received, expected in ((52, 52), (53, 53), (155, 169), (4, 23), (1, 10)):
            totals.add_health(7, received, expected)
        self.assertEqual(totals.speakers[7].percent, 88)
