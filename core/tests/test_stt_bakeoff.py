"""Offline tests for the speech-to-text bake-off tool (#128): no keys, no network.

The service clients are tested against small fake WebSocket servers on localhost.
"""

import asyncio
import json
import re
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

from dmbot.devtools.stt_bakeoff import data
from dmbot.devtools.stt_bakeoff.audio import RATE, split
from dmbot.devtools.stt_bakeoff.normalize import normalize, normalize_words
from dmbot.devtools.stt_bakeoff.providers import (
    CHUNK_BYTES,
    Deepgram,
    Speechmatics,
    deepgram_url,
    deepgram_words,
    speechmatics_start,
    speechmatics_words,
    stream_audio,
)
from dmbot.devtools.stt_bakeoff.report import (
    Paired,
    Relisten,
    Speed,
    build,
    decide,
    paired,
    percentile,
    relisten_stats,
)
from dmbot.devtools.stt_bakeoff.runner import (
    MAIN,
    RELISTEN,
    LearnedSourceMissing,
    Record,
    Runner,
    shortlist,
)
from dmbot.devtools.stt_bakeoff.score import align, heard_as, misheard_forms, score

SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "test-scripts" / "stt-bakeoff.md"
Words = list[tuple[str, float | None]]


def words(text: str, conf: float | None = None) -> Words:
    return [(w, conf) for w in text.split()]


def record(
    line: int,
    text: str,
    *,
    setup: str = "sm-enhanced",
    reader: str = "reader-a",
    wordlist: str = data.SCENE,
    phase: str = MAIN,
    error: str | None = None,
    **extra: Any,
) -> Record:
    return Record(
        f"{phase}|{setup}|{wordlist}|{reader}|{line}|{extra.get('target', '')}",
        phase,
        setup,
        wordlist,
        reader,
        "discord",
        line,
        0,
        text,
        words(text),
        0.2,
        0.4,
        3.0,
        error,
        "",
        38,
        **extra,
    )


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
            normalize("Okay, a natural twenty, so twenty-three. Roll a D 20, it's Ka'zeth"),
            ["okay", "a", "natural", "20", "so", "23", "roll", "a", "d20", "it", "is", "kazeth"],
        )
        self.assertEqual(normalize("23"), normalize("twenty three"))
        self.assertEqual(normalize("d twenty"), ["d20"])
        self.assertEqual(normalize("OK"), ["okay"])
        self.assertEqual(normalize("2d6 plus 3"), normalize("two d6 plus three"))

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

    def test_confidence_on_missed_and_correct_names(self) -> None:
        s = score(5, [("Bell", 0.4), ("or", 0.5), ("us", 0.9), *words("will take the lead")])
        self.assertEqual(s.missed, ["Belleros"])
        self.assertEqual((s.missed_low_conf, s.missed_conf_known), (1, 1))
        ok = score(3, [("Cerric", 0.4), *words("you're at the front", 0.9)])
        self.assertEqual(
            (ok.correct, ok.correct_low_conf, ok.correct_conf_known), (["Cerric"], 1, 1)
        )

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
        def heard(n: int, text: str) -> dict[str, str]:
            return misheard_forms(data.LINE_BY_N[n], words(text))

        tail = " will take the lead while the others wait by the gate."
        self.assertEqual(heard(5, "Bell or us" + tail), {"Belleros": "bell or us"})
        self.assertEqual(heard(5, "Then the bell or us" + tail), {"Belleros": "bell or us"})
        self.assertEqual(heard(14, "who is val zimmer please"), {"Vhalzimar": "val zimmer"})
        self.assertEqual(
            heard(23, "The quill on, the halfling, wants to buy the amulet."),
            {"Quillon": "quill on"},
        )
        found = heard(45, "Ill Varis and Oscar vain meet in secret at Sorrowmere")
        self.assertEqual(found, {"Ilvaris": "ill varis", "Oskar Vane": "oscar vain"})
        self.assertEqual(heard(5, data.LINE_BY_N[5].text), {})
        self.assertEqual(heard_as(data.LINE_BY_N[3], words("Sara, you're at"), "Cerric"), "sara")

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
        big = data.word_list(data.BIG)
        self.assertEqual(len(big), len(scene) + data.DISTRACTOR_COUNT)
        self.assertEqual(big[: len(scene)], scene)  # distractors last, so cuts drop them
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

    def test_shortlist_comes_from_what_was_heard(self) -> None:
        sl = shortlist("bell or us", with_sounds_like=True)
        self.assertEqual(len(sl), 5)
        self.assertEqual(sl[0].content, "Belleros")
        self.assertTrue(sl[0].sounds_like)
        self.assertNotIn("Bell", [v.content for v in sl])
        self.assertIn("Kael", [v.content for v in shortlist("kale", with_sounds_like=False)])
        self.assertFalse(any(v.sounds_like for v in shortlist("kale", with_sounds_like=False)))


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
        empty = speechmatics_start([], "standard", 1.0)["transcription_config"]
        self.assertNotIn("additional_vocab", empty)

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
        self.assertIn("mip_opt_out=true", url)
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

    def test_stream_audio_chunks_and_stop(self) -> None:
        sent: list[bytes] = []

        async def send(chunk: bytes) -> None:
            sent.append(chunk)

        count, _t = asyncio.run(stream_audio(send, bytes(CHUNK_BYTES * 3 + 10), realtime=False))
        self.assertEqual(count, 4)
        self.assertEqual(len(sent[-1]), 10)
        count, _t = asyncio.run(
            stream_audio(send, bytes(CHUNK_BYTES * 3), realtime=False, stop=lambda: len(sent) >= 5)
        )
        self.assertEqual(count, 1)


Handler = Callable[[ServerConnection], Awaitable[None]]


async def with_server(
    handler: Handler, body: Callable[[str], Awaitable[Any]], process_request: Any = None
) -> Any:
    async with serve(handler, "127.0.0.1", 0, process_request=process_request) as server:
        port = server.sockets[0].getsockname()[1]
        return await body(f"ws://127.0.0.1:{port}")


class FakeSpeechmatics(unittest.TestCase):
    def run_sm(self, error_after: int | None = None) -> tuple[Any, dict[str, Any]]:
        seen: dict[str, Any] = {"chunks": 0, "messages": []}

        async def handler(ws: ServerConnection) -> None:
            start = json.loads(await ws.recv())
            seen["start"] = start
            await ws.send(json.dumps({"message": "RecognitionStarted", "id": "x"}))
            async for raw in ws:
                if isinstance(raw, bytes):
                    seen["chunks"] += 1
                    if error_after is not None and seen["chunks"] == error_after:
                        await ws.send(
                            json.dumps(
                                {
                                    "message": "Error",
                                    "type": "quota_exceeded",
                                    "reason": "too many sessions",
                                }
                            )
                        )
                        return
                    continue
                msg = json.loads(raw)
                seen["messages"].append(msg)
                if msg["message"] == "ForceEndOfUtterance":
                    await ws.send(
                        json.dumps(
                            {
                                "message": "AddTranscript",
                                "results": [
                                    {
                                        "type": "word",
                                        "alternatives": [{"content": "Cerric", "confidence": 0.8}],
                                    }
                                ],
                            }
                        )
                    )
                elif msg["message"] == "EndOfStream":
                    await ws.send(json.dumps({"message": "EndOfTranscript"}))
                    return

        async def body(url: str) -> Any:
            sm = Speechmatics("enhanced", url=url, key="test")
            return await sm.transcribe(
                bytes(CHUNK_BYTES * 10), [data.VocabEntry("Cerric")], realtime=False
            )

        return asyncio.run(with_server(handler, body)), seen

    def test_full_flow(self) -> None:
        res, seen = self.run_sm()
        self.assertIsNone(res.error)
        self.assertEqual(res.text, "Cerric")
        self.assertEqual(seen["chunks"], 10)
        kinds = [m["message"] for m in seen["messages"]]
        self.assertEqual(kinds, ["ForceEndOfUtterance", "EndOfStream"])
        self.assertEqual(seen["messages"][1]["last_seq_no"], 10)
        self.assertEqual(
            seen["start"]["transcription_config"]["additional_vocab"], [{"content": "Cerric"}]
        )
        self.assertGreaterEqual(res.final_s, 0)
        self.assertLess(res.final_s, 1.0)
        self.assertGreater(res.total_s, 0)

    def test_error_mid_stream_keeps_the_real_reason(self) -> None:
        res, seen = self.run_sm(error_after=3)
        self.assertEqual(res.error, "quota_exceeded: too many sessions")
        self.assertLess(seen["chunks"], 10)  # stopped sending


class FakeDeepgram(unittest.TestCase):
    def run_dg(
        self,
        *,
        reply_finalize: bool = True,
        max_terms: int | None = None,
        vocab: list[data.VocabEntry] | None = None,
    ) -> tuple[Any, list[int]]:
        sizes: list[int] = []

        def process_request(conn: ServerConnection, request: Request) -> Response | None:
            n = len(parse_qs(urlparse(request.path).query).get("keyterm", []))
            sizes.append(n)
            if max_terms is not None and n > max_terms:
                return conn.respond(HTTPStatus.BAD_REQUEST, "too many keyterms\n")
            return None

        async def handler(ws: ServerConnection) -> None:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg["type"] == "Finalize":
                    reply: dict[str, Any] = {
                        "type": "Results",
                        "is_final": True,
                        "channel": {
                            "alternatives": [
                                {
                                    "transcript": "Hi Kael",
                                    "words": [
                                        {"punctuated_word": "Hi", "confidence": 0.9},
                                        {"punctuated_word": "Kael", "confidence": 0.5},
                                    ],
                                }
                            ]
                        },
                    }
                    if reply_finalize:
                        reply["from_finalize"] = True
                    await ws.send(json.dumps(reply))
                elif msg["type"] == "CloseStream":
                    return

        async def body(url: str) -> Any:
            dg = Deepgram(key="test", base_url=url, min_terms=38, finalize_timeout=0.3)
            return await dg.transcribe(bytes(CHUNK_BYTES * 5), vocab or [], realtime=False)

        return asyncio.run(with_server(handler, body, process_request)), sizes

    def test_finalize_flow(self) -> None:
        res, _ = self.run_dg()
        self.assertIsNone(res.error)
        self.assertEqual(res.text, "Hi Kael")
        self.assertEqual(res.words, [("Hi", 0.9), ("Kael", 0.5)])
        self.assertLess(res.final_s, 0.3)

    def test_no_finalize_reply_uses_last_final(self) -> None:
        res, _ = self.run_dg(reply_finalize=False)
        self.assertIn("no from_finalize reply", res.note)
        self.assertLess(res.final_s, 0.3)  # not the 10 s wait, not the close

    def test_long_list_is_cut_to_distractors_only(self) -> None:
        res, sizes = self.run_dg(max_terms=100, vocab=data.word_list(data.BIG))
        self.assertIsNone(res.error)
        self.assertEqual(sizes[0], 308)
        self.assertGreaterEqual(sizes[-1], 38)  # never below the Scene list
        self.assertLessEqual(sizes[-1], 100)
        self.assertIn(f"keyterms {sizes[-1]}/308", res.note)


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
        self.assertAlmostEqual(segments[0].tail_s, 0.3, delta=0.03)


class Reporting(unittest.TestCase):
    def test_percentile(self) -> None:
        self.assertIsNone(percentile([], 50))
        self.assertEqual(percentile([1.0, 2.0, 3.0], 50), 2.0)
        self.assertAlmostEqual(percentile([0.0, 1.0], 95) or 0, 0.95)

    def test_paired_only_counts_shared_clips(self) -> None:
        a = [record(3, "Cerric, you're at"), record(4, "Sara draws his sword")]
        b = [
            record(3, "Sara, you're at", setup="dg-nova3"),
            record(4, "x", setup="dg-nova3", error="HTTP 500 when connecting"),
        ]
        p = paired(a, b)
        self.assertEqual((p.clips, p.names), (1, 1))
        self.assertEqual(p.gap, 100.0)

    def test_decision_rule(self) -> None:
        clear = Paired(100, 100, 1.0, -1.0, 3.0, 1, 1)
        fast = Speed(40, 0.5, 0.9, 0.2, 0.3)
        quick = Relisten(10, 0, 8, 5, 0, 1.2)
        ok = (0.0, 0.0)
        self.assertEqual(decide(clear, ok, fast, quick, "sm-enhanced").choice, "sm-enhanced")
        slow = Speed(40, 0.5, 1.4, 0.2, 0.3)
        self.assertEqual(decide(clear, ok, slow, quick, "sm-enhanced").choice, "dg-nova3")
        worse = Paired(100, 100, -4.0, -6.0, -2.5, 1, 1)
        self.assertEqual(decide(worse, ok, fast, quick, "sm-enhanced").choice, "dg-nova3")
        noisy = Paired(100, 100, 1.0, -1.0, 3.0, 4, 1)
        self.assertEqual(decide(noisy, ok, fast, quick, "sm-enhanced").choice, "dg-nova3")
        wide = Paired(100, 100, -3.0, -9.0, 3.0, 1, 1)
        d = decide(wide, ok, fast, quick, "sm-enhanced")
        self.assertEqual(d.choice, "sm-enhanced")
        self.assertIn("too close to call", d.reasons[0])
        self.assertEqual(decide(clear, (0.2, 0.0), fast, quick, "sm-enhanced").choice, "undecided")
        self.assertEqual(
            decide(clear, ok, Speed(0, None, None, None, None), quick, "sm-enhanced").choice,
            "undecided",
        )
        failed = Relisten(0, 5, 0, 0, 0, None)
        self.assertEqual(decide(clear, ok, fast, failed, "sm-enhanced").choice, "undecided")

    def test_relisten_counts(self) -> None:
        recs = [
            record(
                5,
                "Belleros will take the lead",
                phase=RELISTEN,
                wordlist="shortlist",
                target="Belleros",
                shortlist_hit=True,
            ),
            record(
                3,
                "Sara, you're at",
                phase=RELISTEN,
                wordlist="shortlist",
                target="Cerric",
                shortlist_hit=False,
            ),
            record(50, "Ring the Belleros", phase=RELISTEN, wordlist="control"),
        ]
        st = relisten_stats(recs)
        self.assertEqual((st.tried, st.shortlist_hits, st.recovered), (2, 1, 1))

    def test_report_has_numbers_not_speech(self) -> None:
        recs = [
            record(5, "SECRET TABLE TALK", wordlist=data.SCENE_SL),
            record(5, "SECRET TABLE TALK", setup="dg-nova3"),
        ]
        report = build(recs)
        self.assertIn("sm-enhanced", report)
        self.assertNotIn("SECRET", report)


class Learning(unittest.TestCase):
    def setup_out(self, tmp: Path, readers: list[str]) -> None:
        manifest = {
            "readers": {
                r: {
                    "variants": ["clean", "discord"],
                    "lines": {
                        "5": {
                            "clean": f"clips/{r}/clean/05.wav",
                            "discord": f"clips/{r}/discord/05.wav",
                            "tail_s": 0.3,
                        }
                    },
                }
                for r in readers
            }
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest))

    def test_hints_come_from_the_other_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.setup_out(out, ["reader-a", "reader-b"])
            heard = "Bell or us will take the lead while the others wait by the gate."
            rec = record(5, heard)
            (out / "results.jsonl").write_text(json.dumps(asdict(rec)) + "\n")
            runner = Runner(out)
            self.assertEqual(runner.learned_hints("reader-b"), {"Belleros": {"bell or us"}})
            # reader-b has no Scene results yet, so reader-a's list can't be built.
            with self.assertRaises(LearnedSourceMissing):
                runner.learned_hints("reader-a")
