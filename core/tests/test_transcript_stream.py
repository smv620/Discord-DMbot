"""Lines for the live transcript channel (#124), without Discord."""

import unittest

from dmbot.transcript.stream import (
    LINE_MAX,
    MESSAGE_MAX,
    TranscriptStream,
    ended,
    escape,
    line,
    started,
)


class Escaping(unittest.TestCase):
    def test_speech_can_never_ping_or_format(self) -> None:
        text = escape("hi @everyone and @here <@123> <@&9> <#5> **bold** ||spoiler|| [x](y)")
        for raw in ("@everyone", "@here", "<@123>", "<@&9>", "<#5>", "**", "||", "[x]"):
            self.assertNotIn(raw, text)

    def test_ordinary_punctuation_is_left_alone(self) -> None:
        self.assertEqual(
            escape("Ka-zeth (the goblin) is 2/3 done."), "Ka-zeth (the goblin) is 2/3 done."
        )

    def test_line_shows_the_speaker_in_bold(self) -> None:
        self.assertEqual(line("Mia", "  I cast  Shield. "), "**Mia:** I cast Shield.")
        self.assertEqual(line("*Mia*", "hi"), "**\\*Mia\\*:** hi")

    def test_a_very_long_line_is_cut(self) -> None:
        self.assertLess(len(line("Mia", "word " * 1000)), LINE_MAX + 20)


class Batching(unittest.TestCase):
    def test_lines_are_grouped_into_messages_under_the_limit(self) -> None:
        stream = TranscriptStream()
        for i in range(300):
            stream.add(1, "Mia", f"line number {i} with a few more words in it")
        messages = stream.take()
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(m) <= MESSAGE_MAX for m in messages))
        self.assertEqual(sum(m.count("\n") + 1 for m in messages), 300)  # none lost
        self.assertEqual(stream.take(), [])  # taken once

    def test_order_is_kept(self) -> None:
        stream = TranscriptStream()
        stream.add_divider(started("Frostmaiden", 1_700_000_000))
        stream.add(1, "Mia", "first")
        stream.add(2, "Dee", "second")
        stream.add_divider(ended())
        (message,) = stream.take()
        self.assertEqual(
            message.splitlines(),
            [
                "── 🔴 Session started · **Frostmaiden** · <t:1700000000:f> ──",
                "**Mia:** first",
                "**Dee:** second",
                "── ⏹ Session ended ──",
            ],
        )

    def test_someone_who_stops_loses_their_unposted_lines(self) -> None:
        stream = TranscriptStream()
        stream.add(1, "Mia", "keep this")
        stream.add(2, "Dee", "drop this")
        stream.add_divider(ended())
        stream.drop_speaker(2)
        self.assertEqual(stream.take(), ["**Mia:** keep this\n── ⏹ Session ended ──"])

    def test_empty_speech_is_ignored(self) -> None:
        stream = TranscriptStream()
        stream.add(1, "Mia", "   ")
        self.assertEqual(len(stream), 0)

    def test_a_restart_has_its_own_divider(self) -> None:
        self.assertIn("Listening again after a restart", started("X", 1, resumed=True))
