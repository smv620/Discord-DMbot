"""Lines for the live transcript channel (#124), without Discord."""

import unittest

from dmbot.transcript.stream import (
    MAX_WAITING,
    MESSAGE_MAX,
    UNKNOWN_SPEAKER,
    TranscriptStream,
    ended,
    escape,
    line,
    speaker_name,
    started,
)


def everyone(_: int) -> bool:
    return True


def drain(stream: TranscriptStream) -> list[str]:
    """Post everything (as if every post worked)."""
    out = []
    while (ready := stream.next_message(everyone)) is not None:
        text, count = ready
        out.append(text)
        stream.posted(count)
    return out


class Escaping(unittest.TestCase):
    def test_speech_can_never_ping_format_or_link(self) -> None:
        said = (
            "hi @everyone and @here <@123> <@&9> <#5> </cmd:1> <t:1:R> <:e:1> "
            "**bold** ||spoiler|| [x](y) https://example.com discord.gg/abc"
        )
        text = escape(said)
        for raw in ("@everyone", "@here", "**", "||", "https://"):
            self.assertNotIn(raw, text)
        # Every <, [ and * is escaped, so Discord shows it as plain text.
        self.assertNotRegex(text, r"(?<!\\)[<>\[*|]")

    def test_ordinary_punctuation_is_left_alone(self) -> None:
        said = "Ka-zeth (the goblin) is 2/3 done."
        self.assertEqual(escape(said), said)

    def test_line_shows_the_speaker_in_bold(self) -> None:
        self.assertEqual(line("Mia", "  I cast  Shield. "), "**Mia:** I cast Shield.")
        self.assertEqual(line("*Mia*", "hi"), "**\\*Mia\\*:** hi")

    def test_speaker_names_are_cleaned(self) -> None:
        self.assertEqual(speaker_name("Mia‮evil"), "Miaevil")  # no direction flips
        self.assertEqual(speaker_name(None), UNKNOWN_SPEAKER)  # never a raw <@id>
        self.assertEqual(speaker_name("​"), UNKNOWN_SPEAKER)

    def test_lines_full_of_escapes_still_fit_one_message(self) -> None:
        for char in "*_<|`~[":
            stream = TranscriptStream()
            stream.add(1, char * 300, char * 3000, 0)
            (message,) = drain(stream)
            self.assertLessEqual(len(message), MESSAGE_MAX, char)
            self.assertFalse(message.rstrip("…").endswith("\\") and not message.endswith("\\\\"))


class Batching(unittest.TestCase):
    def test_lines_are_grouped_into_messages_under_the_limit(self) -> None:
        stream = TranscriptStream()
        for i in range(250):
            stream.add(1, "Mia", f"line number {i} with a few more words in it", i)
        messages = drain(stream)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(m) <= MESSAGE_MAX for m in messages))
        self.assertEqual(sum(m.count("\n") + 1 for m in messages), 250)  # none lost
        self.assertIsNone(stream.next_message(everyone))

    def test_lines_are_in_speaking_order(self) -> None:
        stream = TranscriptStream()
        stream.add_divider(started("Frostmaiden", 1_700_000_000), at_ms=0)
        stream.add(1, "Mia", "a long question that took a while to write down", 1000)
        stream.add(2, "Dee", "Yes!", 1500)
        stream.add(2, "Dee", "said first, written down last", 500)
        stream.add_divider(ended("Frostmaiden"), at_ms=9999)
        (message,) = drain(stream)
        self.assertEqual(
            message.splitlines(),
            [
                "── 🔴 Session started · **Frostmaiden** · <t:1700000000:f> ──",
                "**Dee:** said first, written down last",
                "**Mia:** a long question that took a while to write down",
                "**Dee:** Yes!",
                "── ⏹ Session ended · **Frostmaiden** ──",
            ],
        )

    def test_a_failed_post_keeps_the_lines(self) -> None:
        stream = TranscriptStream()
        stream.add(1, "Mia", "hello", 0)
        first = stream.next_message(everyone)
        again = stream.next_message(everyone)  # not posted: still there
        self.assertEqual(first, again)

    def test_someone_who_stops_loses_their_unposted_lines(self) -> None:
        stream = TranscriptStream()
        stream.add(1, "Mia", "keep this", 0)
        stream.add(2, "Dee", "drop this", 1)
        stream.drop_speaker(2)
        self.assertEqual(drain(stream), ["**Mia:** keep this"])

    def test_consent_is_checked_as_each_message_is_built(self) -> None:
        # Dee stops while the first message is being posted: nothing more of hers goes.
        stream = TranscriptStream()
        for i in range(200):
            stream.add(1 + i % 2, "Mia" if i % 2 == 0 else "Dee", f"line {i} " * 10, i)
        text, count = stream.next_message(everyone) or ("", 0)
        self.assertIn("Dee", text)
        stream.posted(count)
        rest = stream.next_message(lambda uid: uid != 2)
        assert rest is not None
        self.assertNotIn("Dee", rest[0])

    def test_dividers_are_never_dropped_by_consent(self) -> None:
        stream = TranscriptStream()
        stream.add_divider(ended("X"), at_ms=0)
        self.assertIsNotNone(stream.next_message(lambda uid: False))

    def test_waiting_lines_are_capped(self) -> None:
        stream = TranscriptStream()
        for i in range(MAX_WAITING + 5):
            stream.add(1, "Mia", f"line {i}", i)
        self.assertEqual((len(stream), stream.dropped), (MAX_WAITING, 5))

    def test_empty_speech_is_ignored(self) -> None:
        stream = TranscriptStream()
        stream.add(1, "Mia", "   ", 0)
        self.assertEqual(len(stream), 0)

    def test_a_restart_has_its_own_divider(self) -> None:
        text = started("X", 1, resumed=True)
        self.assertIn("Back after a short break", text)
        self.assertIn("wasn't written down", text)
