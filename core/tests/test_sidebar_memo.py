"""Reading a Discord voice message (#935): Ogg Opus in, 16 kHz mono PCM out."""

import io
import math
import unittest

import av
import numpy as np

from dmbot.sidebar import memo

RATE = 48_000


def ogg_opus(seconds: float) -> bytes | None:
    """A tone as Discord sends voice messages (Ogg Opus), or None if this PyAV can't encode."""
    samples = (np.sin(np.arange(int(RATE * seconds)) * 2 * math.pi * 440 / RATE) * 12_000).astype(
        np.int16
    )
    buf = io.BytesIO()
    try:
        with av.open(buf, "w", format="ogg") as out:
            stream = out.add_stream("libopus", rate=RATE)
            step = RATE // 50
            pts = 0
            for start in range(0, len(samples), step):
                chunk = samples[start : start + step]
                chunk = np.pad(chunk, (0, step - len(chunk)))
                frame = av.AudioFrame.from_ndarray(
                    chunk.reshape(1, -1), format="s16", layout="mono"
                )
                frame.sample_rate = RATE
                frame.pts = pts
                pts += step
                for packet in stream.encode(frame):
                    out.mux(packet)
            for packet in stream.encode(None):
                out.mux(packet)
    except Exception:
        return None
    return buf.getvalue()


class Decode(unittest.TestCase):
    def test_a_voice_message_becomes_16k_mono_audio(self) -> None:
        data = ogg_opus(1.5)
        if data is None:
            self.skipTest("this PyAV can't encode Opus")
        pcm = memo.decode(data)
        self.assertAlmostEqual(memo.seconds(pcm), 1.5, delta=0.1)
        self.assertEqual(len(pcm) % 2, 0)

    def test_something_that_is_not_audio_is_refused_in_plain_words(self) -> None:
        with self.assertRaises(memo.MemoError) as caught:
            memo.decode(b"this is not a voice message at all" * 20)
        self.assertIn("voice message", str(caught.exception))

    def test_nothing_and_too_much_are_refused(self) -> None:
        for data in (b"", b"x" * (memo.MAX_MEMO_BYTES + 1)):
            with self.subTest(size=len(data)), self.assertRaises(memo.MemoError):
                memo.decode(data)

    def test_a_blip_is_too_short(self) -> None:
        data = ogg_opus(0.1)
        if data is None:
            self.skipTest("this PyAV can't encode Opus")
        with self.assertRaises(memo.MemoError):
            memo.decode(data)

    def test_a_voice_message_over_the_limit_by_a_second_is_refused(self) -> None:
        under = ogg_opus(memo.MAX_MEMO_S - 5)
        over = ogg_opus(memo.MAX_MEMO_S + 1)
        if under is None or over is None:
            self.skipTest("this PyAV can't encode Opus")
        self.assertAlmostEqual(memo.seconds(memo.decode(under)), memo.MAX_MEMO_S - 5, delta=0.2)
        with self.assertRaises(memo.MemoError):
            memo.decode(over)

    def test_a_very_long_one_is_refused(self) -> None:
        data = ogg_opus(memo.MAX_MEMO_S + 10)
        if data is None:
            self.skipTest("this PyAV can't encode Opus")
        with self.assertRaises(memo.MemoError):
            memo.decode(data)
