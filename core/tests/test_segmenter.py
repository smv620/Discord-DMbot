import unittest

from dmbot.audio.segmenter import IDLE_TIMEOUT_MS, MAX_UTTERANCE_MS, Segmenter
from dmbot.ears.protocol import AudioFrame

FRAME_BYTES = 640  # 20 ms at 16 kHz mono s16le


def frame(user: int, ts: int, size: int = FRAME_BYTES) -> AudioFrame:
    return AudioFrame(guild_id=1, user_id=user, timestamp_ms=ts, pcm=bytes(size))


class SegmenterTests(unittest.TestCase):
    def test_end_emits_utterance(self) -> None:
        seg = Segmenter(1)
        for i in range(50):
            self.assertIsNone(seg.add(frame(7, 1000 + i * 20)))
        utt = seg.end(7)
        assert utt is not None
        self.assertEqual((utt.user_id, utt.start_ms, utt.end_ms), (7, 1000, 1980))
        self.assertAlmostEqual(utt.duration_s, 1.0)
        self.assertIsNone(seg.end(7))

    def test_speakers_are_separate(self) -> None:
        seg = Segmenter(1)
        seg.add(frame(1, 0))
        seg.add(frame(2, 0))
        seg.add(frame(2, 20))
        u1, u2 = seg.end(1), seg.end(2)
        assert u1 is not None and u2 is not None
        self.assertEqual(len(u1.pcm), FRAME_BYTES)
        self.assertEqual(len(u2.pcm), 2 * FRAME_BYTES)

    def test_max_length_splits(self) -> None:
        seg = Segmenter(1)
        frames_needed = MAX_UTTERANCE_MS // 20
        results = [seg.add(frame(1, i * 20)) for i in range(frames_needed)]
        self.assertTrue(all(r is None for r in results[:-1]))
        last = results[-1]
        assert last is not None
        self.assertAlmostEqual(last.duration_s, MAX_UTTERANCE_MS / 1000)
        self.assertEqual(seg.active_speakers, 0)

    def test_idle_flush(self) -> None:
        seg = Segmenter(1)
        seg.add(frame(1, 0))
        seg.add(frame(2, 5000))
        flushed = seg.flush_idle(now_ms=IDLE_TIMEOUT_MS + 1)
        self.assertEqual([u.user_id for u in flushed], [1])
        self.assertEqual(seg.active_speakers, 1)

    def test_drop_discards(self) -> None:
        seg = Segmenter(1)
        seg.add(frame(1, 0))
        seg.drop(1)
        self.assertIsNone(seg.end(1))
        self.assertEqual(seg.flush_all(), [])
