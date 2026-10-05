"""Offline tests for the speech-to-text bake-off tool (#128): no keys, no network."""

import asyncio
import json
import re
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import numpy as np

from dmbot.devtools.stt_bakeoff import data
from dmbot.devtools.stt_bakeoff.audio import RATE, split
from dmbot.devtools.stt_bakeoff.normalize import normalize, normalize_words
from dmbot.devtools.stt_bakeoff.providers import (
    CHUNK_BYTES,
    Deepgram,
    Result,
    deepgram_url,
    deepgram_words,
    speechmatics_start,
    speechmatics_words,
    stream_audio,
)
from dmbot.devtools.stt_bakeoff.report import (
    Relisten,
    Speed,
    Summary,
    build,
    decide,
    percentile,
)
from dmbot.devtools.stt_bakeoff.runner import MAIN, Record, Runner, focused_list
from dmbot.devtools.stt_bakeoff.score import align, misheard_forms, score

SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "test-scripts" / "stt-bakeoff.md"


def words(text: str, conf: float | None = None) -> list[tuple[str, float | None]]:
    return [(w, conf) for w in text.split()]


class ScriptMatchesDocs(unittest.TestCase):
    def test_lines_match_the_script(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")
        section = body.split("## The lines", 1)[1].split("## Name list", 1)[0]
        doc = {int(m[1]): m[2] for m in re.finditer(r"^(\d+)\. (.+)$", section, re.M)}
        self.assertEqual(doc, {line.n: line.text for line in data.LINES})

    def test_names_match_the_name_list(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8").split("## Name list", 1)[1]
        table = body.split("**Rules words**", 1)[0]
        rows = [r.split("|")[1].strip() for r in table.splitlines() if r.startswith("| ")]
        doc = {re.sub(r"\s*\(.*\)", "", r) for r in rows[1:]}  # skip the header row
        self.assertEqual(doc, {n.canonical for n in data.NAMES})

    def test_expected_words_are_in_their_lines(self) -> None:
        for line in data.LINES:
            tokens = " ".join(normalize(line.text))
            for item in (*line.names, *line.rules):
                self.assertIn(" ".join(normalize(item)), tokens, (line.n, item))


class Normalizing(unittest.TestCase):
    def test_numbers_dice_contractions(self) -> None:
        self.assertEqual(
            normalize("I rolled a natural twenty, so twenty-three. Roll a D 20, it's Ka'zeth"),
            [
                "i",
                "rolled",
                "a",
                "natural",
                "20",
                "so",
                "23",
                "roll",
                "a",
                "d20",
                "it",
                "is",
                "kazeth",
            ],
        )
        self.assertEqual(normalize("23"), normalize("twenty three"))
        self.assertEqual(normalize("d twenty"), ["d20"])

    def test_confidence_follows_tokens(self) -> None:
        toks = normalize_words([("twenty", 0.9), ("three", 0.4), ("Ka'zeth", 0.8)])
        self.assertEqual(toks, [("23", 0.4), ("kazeth", 0.8)])


class Scoring(unittest.TestCase):
    def test_reference_scores_perfectly(self) -> None:
        for line in data.LINES:
            s = score(line.n, words(line.text))
            self.assertEqual(s.names_correct, s.names_expected, line.n)
            self.assertEqual(s.rules_correct, s.rules_expected, line.n)
            self.assertEqual(s.false_names, [], line.n)
            self.assertEqual(s.word_errors, 0, line.n)

    def test_missed_name_and_low_confidence(self) -> None:
        s = score(5, [("Bell", 0.4), ("or", 0.5), ("us", 0.9), *words("will take the lead")])
        self.assertEqual(s.missed, ["Belleros"])
        self.assertEqual((s.missed_low_conf, s.missed_conf_known), (1, 1))

    def test_trap_lines(self) -> None:
        # A name forced onto a real word is a false name ...
        forced = score(50, words("Ring the Belleros, or else they'll hear us."))
        self.assertEqual(forced.false_names, ["Belleros"])
        self.assertEqual(score(49, words("He has a Cerric blade")).false_names, ["Cerric"])
        # ... but real words that sound like names are fine, and "bell" is never one.
        self.assertEqual(score(52, words("Sarah from work says hi to everyone.")).false_names, [])
        self.assertEqual(score(50, words("Ring the bell, or else")).false_names, [])

    def test_nickname_must_stay(self) -> None:
        s = score(38, words("Belleros, you're up first."))
        self.assertEqual(s.missed, ["Bell"])
        self.assertEqual(s.false_names, ["Belleros"])

    def test_learning_what_was_heard(self) -> None:
        line5 = data.LINE_BY_N[5]
        heard = words("Bell or us will take the lead while the others wait by the gate.")
        self.assertEqual(misheard_forms(line5, heard), {"Belleros": "bell or us"})
        line45 = data.LINE_BY_N[45]
        heard45 = words("Ill Varis and Oscar vain meet in secret at Sorrowmere")
        found = misheard_forms(line45, heard45)
        self.assertEqual(found["Ilvaris"], "ill varis")
        self.assertEqual(found["Oskar Vane"], "oscar vain")
        self.assertEqual(misheard_forms(line5, words(line5.text)), {})

    def test_align(self) -> None:
        dist, pairs = align(["a", "b", "c"], ["a", "x", "c", "d"])
        self.assertEqual(dist, 2)
        self.assertEqual(pairs, [(0, 0), (1, 1), (2, 2), (None, 3)])


class WordLists(unittest.TestCase):
    def test_sizes_and_contents(self) -> None:
        self.assertEqual(data.word_list(data.NONE), [])
        scene = data.word_list(data.SCENE)
        self.assertEqual(len(scene), len(data.NAMES) + len(data.RULES_WORDS))
        self.assertTrue(all(not v.sounds_like for v in scene))
        self.assertTrue(any(v.sounds_like for v in data.word_list(data.SCENE_SL)))
        self.assertEqual(len(data.word_list(data.BIG)), len(scene) + data.DISTRACTOR_COUNT)
        with self.assertRaises(ValueError):
            data.word_list("nope")

    def test_learned_adds_hints(self) -> None:
        learned = data.word_list(data.LEARNED, {"Belleros": {"bella ross"}})
        entry = next(v for v in learned if v.content == "Belleros")
        self.assertIn("bella ross", entry.sounds_like)
        self.assertIn("bell or us", entry.sounds_like)

    def test_entries_fit_speechmatics(self) -> None:
        for v in data.word_list(data.BIG):
            self.assertLessEqual(len(v.content.split()), 6)

    def test_distractors(self) -> None:
        d = data.distractors()
        self.assertEqual(d, data.distractors())  # the same every run
        self.assertEqual(len(set(d)), data.DISTRACTOR_COUNT)
        script = " ".join(normalize(" ".join(line.text for line in data.LINES))).split()
        names = {f.casefold().replace("'", "") for n in data.NAMES for f in n.forms}
        for w in d:
            self.assertNotIn(w.casefold(), names)
            self.assertNotIn(w.casefold(), script)

    def test_focused_list(self) -> None:
        f = focused_list("Belleros", with_sounds_like=True)
        self.assertEqual(len(f), 5)
        self.assertEqual(f[0].content, "Belleros")
        self.assertTrue(f[0].sounds_like)
        self.assertNotIn("Bell", [v.content for v in f])
        self.assertFalse(any(v.sounds_like for v in focused_list("Kael", with_sounds_like=False)))


class Messages(unittest.TestCase):
    def test_speechmatics_start(self) -> None:
        msg = speechmatics_start(
            [data.VocabEntry("Cerric", ("serik",)), data.VocabEntry("Kael")], "enhanced", 1.0
        )
        cfg = msg["transcription_config"]
        self.assertEqual(cfg["operating_point"], "enhanced")
        self.assertEqual(
            cfg["additional_vocab"],
            [{"content": "Cerric", "sounds_like": ["serik"]}, {"content": "Kael"}],
        )
        self.assertEqual(msg["audio_format"]["sample_rate"], 16_000)
        self.assertNotIn(
            "additional_vocab", speechmatics_start([], "standard", 1.0)["transcription_config"]
        )

    def test_speechmatics_words(self) -> None:
        results = [
            {"type": "word", "alternatives": [{"content": "Hello", "confidence": 0.9}]},
            {"type": "punctuation", "alternatives": [{"content": ","}]},
            {"type": "word", "alternatives": [{"content": "Cerric", "confidence": 0.5}]},
        ]
        self.assertEqual(
            speechmatics_words(results), ("Hello, Cerric", [("Hello", 0.9), ("Cerric", 0.5)])
        )

    def test_deepgram(self) -> None:
        url = deepgram_url(["Ka'zeth", "Oskar Vane"])
        self.assertIn("keyterm=Ka%27zeth", url)
        self.assertIn("keyterm=Oskar%20Vane", url)
        self.assertIn("model=nova-3", url)
        msg = {
            "channel": {
                "alternatives": [
                    {
                        "transcript": "Hi Kael",
                        "words": [
                            {"word": "hi", "punctuated_word": "Hi", "confidence": 0.9},
                            {"word": "kael", "punctuated_word": "Kael", "confidence": 0.4},
                        ],
                    }
                ]
            }
        }
        self.assertEqual(deepgram_words(msg), ("Hi Kael", [("Hi", 0.9), ("Kael", 0.4)]))

    def test_deepgram_halves_rejected_keyterms(self) -> None:
        dg = Deepgram(key="unused")
        seen: list[int] = []

        async def fake_once(pcm: bytes, terms: list[str], *, realtime: bool) -> Result:
            seen.append(len(terms))
            return Result(error="HTTP 400 when connecting") if len(terms) > 50 else Result()

        dg._once = fake_once  # type: ignore[method-assign]
        res = asyncio.run(dg.transcribe(b"", data.word_list(data.BIG)))
        self.assertIsNone(res.error)
        self.assertLessEqual(seen[-1], 50)
        self.assertTrue(res.note.startswith(f"keyterms {seen[-1]}/"))
        asyncio.run(dg.transcribe(b"", data.word_list(data.BIG)))
        self.assertEqual(seen[-1], seen[-2])  # remembered: no second round of rejections

    def test_stream_audio_chunks(self) -> None:
        sent: list[bytes] = []

        async def send(chunk: bytes) -> None:
            sent.append(chunk)

        count, _t = asyncio.run(stream_audio(send, bytes(CHUNK_BYTES * 3 + 10), realtime=False))
        self.assertEqual(count, 4)
        self.assertEqual(len(sent[-1]), 10)


class Splitting(unittest.TestCase):
    def test_cuts_at_long_pauses_only(self) -> None:
        rng = np.random.default_rng(1)
        t = np.arange(RATE) / RATE
        tone = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
        short_gap = np.zeros(RATE // 2, dtype=np.int16)  # 0.5 s: inside a line
        long_gap = np.zeros(2 * RATE, dtype=np.int16)  # 2 s: between lines
        click = (np.ones(RATE // 50) * 20000).astype(np.int16)  # 20 ms bump
        pcm = np.concatenate(
            [
                long_gap,
                tone,
                short_gap,
                tone,
                long_gap,
                tone,
                long_gap,
                click,
                long_gap,
                tone,
                long_gap,
            ]
        )
        pcm = (pcm + rng.normal(0, 30, len(pcm))).astype(np.int16)
        segments = split(pcm)
        self.assertEqual(len(segments), 3)
        self.assertGreater(segments[0].seconds(), 2.4)  # both halves of the first line


class Reporting(unittest.TestCase):
    def test_percentile(self) -> None:
        self.assertIsNone(percentile([], 50))
        self.assertEqual(percentile([1.0, 2.0, 3.0], 50), 2.0)
        self.assertAlmostEqual(percentile([0.0, 1.0], 95) or 0, 0.95)

    def test_decision_rule(self) -> None:
        sm = Summary(names_expected=100, names_correct=90, false_names=1)
        dg = Summary(names_expected=100, names_correct=91, false_names=1)
        fast = Speed(0.5, 0.9, 0.2, 0.3)
        quick = Relisten(10, 5, 1.2)
        self.assertEqual(decide(sm, dg, fast, quick, "sm-enhanced").choice, "sm-enhanced")
        slow = Speed(0.5, 1.4, 0.2, 0.3)
        self.assertEqual(decide(sm, dg, slow, quick, "sm-enhanced").choice, "dg-nova3")
        worse = Summary(names_expected=100, names_correct=85, false_names=1)
        self.assertEqual(decide(worse, dg, fast, quick, "sm-enhanced").choice, "dg-nova3")
        noisy = Summary(names_expected=100, names_correct=95, false_names=3)
        self.assertEqual(decide(noisy, dg, fast, quick, "sm-enhanced").choice, "dg-nova3")

    def test_report_has_numbers_not_speech(self) -> None:
        rec = Record(
            "k",
            MAIN,
            "sm-enhanced",
            data.SCENE_SL,
            "reader-a",
            "discord",
            5,
            0,
            "SECRET TABLE TALK",
            words("SECRET TABLE TALK"),
            0.2,
            0.6,
            3.0,
            None,
            "",
            38,
        )
        report = build([rec])
        self.assertIn("sm-enhanced", report)
        self.assertNotIn("SECRET", report)


class Learning(unittest.TestCase):
    def test_hints_come_from_the_other_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            heard = words("Bell or us will take the lead while the others wait by the gate.")
            rec = Record(
                "k1",
                MAIN,
                "sm-enhanced",
                data.SCENE,
                "reader-a",
                "discord",
                5,
                0,
                "",
                heard,
                0.1,
                0.5,
                3.0,
                None,
                "",
                38,
            )
            (out / "results.jsonl").write_text(json.dumps(asdict(rec)) + "\n")
            runner = Runner(out)
            self.assertEqual(runner.learned_hints("reader-b"), {"Belleros": {"bell or us"}})
            self.assertEqual(runner.learned_hints("reader-a"), {})
