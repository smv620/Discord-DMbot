"""The one clip decoder both voice readers share (#2162): each keeps its own words, both stop a
clip that expands past its limit."""

import unittest
from unittest import mock

from dmbot import audio_decode
from dmbot.recaps import voice
from dmbot.sidebar import memo
from tests.test_web_recap_voice import tone


class TheSharedDecoder(unittest.TestCase):
    def test_a_good_clip_comes_back_as_audio(self) -> None:
        pcm = audio_decode.decode_pcm(
            tone("ogg", 1.5), container="ogg", max_bytes=10**6, max_s=30, min_s=0.4
        )
        self.assertAlmostEqual(audio_decode.seconds(pcm), 1.5, delta=0.1)

    def test_each_check_has_its_own_reason(self) -> None:
        clip = tone("ogg", 1.5)
        cases: dict[audio_decode.Why, tuple[bytes, dict[str, float]]] = {
            audio_decode.Why.TOO_BIG: (b"", {}),
            audio_decode.Why.TOO_LONG: (clip, {"max_s": 0.5}),
            audio_decode.Why.TOO_SHORT: (clip, {"min_s": 5}),
            audio_decode.Why.UNREADABLE: (b"not audio at all" * 20, {}),
            audio_decode.Why.TOO_SLOW: (clip, {"budget_s": -1.0}),
        }
        for why, (data, over) in cases.items():
            limits: dict[str, float] = {"max_s": 30, "min_s": 0.4} | over
            with self.subTest(why=why), self.assertRaises(audio_decode.Refused) as caught:
                audio_decode.decode_pcm(
                    data,
                    container="ogg",
                    max_bytes=10**6,
                    max_s=limits["max_s"],
                    min_s=limits["min_s"],
                    budget_s=limits.get("budget_s"),
                )
            self.assertIs(caught.exception.why, why)


class TheTwoCallers(unittest.TestCase):
    def test_a_clip_that_expands_past_its_limit_is_refused_by_both(self) -> None:
        clip = tone("ogg", 1.5)
        with mock.patch.object(memo, "MAX_MEMO_S", 0.5), self.assertRaises(memo.MemoError) as m:
            memo.decode(clip)
        self.assertEqual(str(m.exception), memo.TOO_LONG)
        with mock.patch.object(voice, "MAX_CLIP_S", 0.5), self.assertRaises(voice.ClipError) as v:
            voice.decode(clip, "audio/ogg")
        self.assertEqual(v.exception.code, voice.TOO_LONG)

    def test_each_keeps_its_own_words_for_a_file_it_cannot_read(self) -> None:
        junk = b"definitely not an ogg file" * 10
        with self.assertRaises(memo.MemoError) as m:
            memo.decode(junk)
        self.assertIn("couldn't read", str(m.exception))
        with self.assertRaises(voice.ClipError) as v:
            voice.decode(junk, "audio/ogg")
        self.assertEqual(v.exception.code, voice.BAD_CLIP)
