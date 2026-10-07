import ast
import asyncio
import datetime as dt
import io
import math
import os
import struct
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dmbot.audio.segmenter import MAX_UTTERANCE_MS, Utterance
from dmbot.devtools.replay import __main__ as replay_main
from dmbot.devtools.replay import audio
from dmbot.devtools.replay.report import history_entry, record
from dmbot.devtools.replay.run import (
    END_DELAY_MS,
    TWIN_GUILD,
    TWIN_SPEAKER,
    Replay,
    TwinConsent,
    replay,
)
from dmbot.devtools.replay.score import align, score, words
from dmbot.devtools.replay.script import Part, load_script, parse_script
from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE
from dmbot.transcription.pipeline import QUEUE_SIZE

SCRIPTS = Path(__file__).resolve().parents[2] / "docs" / "test-scripts"

DM_ONLY_PIECES = [
    # As read, split at each dramatic pause; the whisper is its own piece.
    "Your story starts in a small town at the edge of the woods. You knock on the front "
    "door three times. An old man opens the door.",
    '"Come inside," he says. "It\'s cold."',
    "He gives you hot food and a warm bed.",
    "Welcome to Bryn Shander, the biggest of the Ten-Towns. The road goes north to Targos "
    "and east to Easthaven.",
    "Your wizard casts Detect Magic on the frozen chest.",
    "It's a trap! Everyone, make a Dexterity saving throw.",
    "A frost giant walks out of the blizzard. Auril the Frostmaiden is watching.",
    'The fighter draws a longsword and shouts, "For Lonelywood and Caer-Dineval!"',
]


def tone(ms: int, amplitude: int = 8000) -> bytes:
    n = SAMPLE_RATE * ms // 1000
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)))
        for i in range(n)
    )


def silence(ms: int) -> bytes:
    return bytes(SAMPLE_RATE * ms // 1000 * BYTES_PER_SAMPLE)


class ScriptTests(unittest.TestCase):
    def test_the_scripts_score_the_words_the_readme_counts(self) -> None:
        dm_only = load_script(SCRIPTS / "dm-only.md")
        self.assertEqual(dm_only.count(Part.ONE), 33)
        self.assertEqual(dm_only.pauses, 5)
        whisper = [w.text for w in dm_only.words if w.part is Part.WHISPER]
        self.assertEqual(" ".join(whisper), "He gives you hot food and a warm bed.")
        both = load_script(SCRIPTS / "dm-and-player.md")
        self.assertEqual(both.count(Part.ONE), 35)
        self.assertEqual(both.pauses, 2)
        self.assertEqual({w.speaker for w in both.words}, {"DM", "Player"})

    def test_pauses_mark_the_next_word(self) -> None:
        script = load_script(SCRIPTS / "dm-only.md")
        after = [w.text for w in script.words if w.after_pause]
        self.assertEqual(after, ['"Come', "Your", "It's", "A", "The"])

    def test_directions_and_notes_are_not_read(self) -> None:
        script = parse_script(
            "## Part 1\n\n**[Don't read this out loud.]** Say it like this.\n\n"
            "**[DM]:** Hello (( slowly )) there.\n"
        )
        self.assertEqual([w.text for w in script.words], ["Hello", "there."])

    def test_a_file_without_turns_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            parse_script("# Nothing to read\n")


class WordTests(unittest.TestCase):
    def test_compared_the_readme_way(self) -> None:
        self.assertEqual(words("It's cold."), words("it is cold"))
        self.assertEqual(words("door 3 times"), ["door", "three", "times"])
        self.assertEqual(words("the 10 towns"), words("the Ten-Towns"))
        self.assertEqual(words("It’s"), words("it's"))

    def test_align_names_each_kind_of_error(self) -> None:
        kinds = [s.kind for s in align(["a", "b", "c"], ["a", "x", "c", "d"])]
        self.assertEqual(kinds, ["ok", "wrong", "ok", "added"])
        self.assertEqual([s.kind for s in align(["a", "b"], ["b"])], ["missing", "ok"])


class ScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = load_script(SCRIPTS / "dm-only.md")

    def test_a_perfect_reading(self) -> None:
        result = score(self.script, DM_ONLY_PIECES)
        self.assertEqual((result.part1.wrong, result.part1.missing, result.part1.added), (0, 0, 0))
        self.assertEqual((len(result.terms_right), result.terms_total), (12, 12))
        self.assertEqual(result.whisper, "all")
        self.assertEqual((result.pauses_split, result.pauses), (5, 5))
        self.assertEqual(result.first_word_errors, [])

    def test_run_7(self) -> None:
        # Run 7 missed Auril and Caer-Dineval and wrote "10 towns".
        pieces = [
            p.replace("Auril", "Oral")
            .replace("Caer-Dineval", "Care Dinner Val")
            .replace("Ten-Towns", "10 towns")
            for p in DM_ONLY_PIECES
        ]
        result = score(self.script, pieces)
        self.assertEqual(result.part1.errors, 0)
        self.assertEqual(result.terms_missed, ["Auril", "Caer-Dineval"])

    def test_a_first_word_cut_after_a_pause(self) -> None:
        pieces = list(DM_ONLY_PIECES)
        pieces[1] = 'inside," he says. "It\'s cold."'  # "Come" lost (#121)
        pieces[4] = "wizard casts Detect Magic on the frozen chest."  # "Your" lost
        result = score(self.script, pieces)
        self.assertEqual(result.part1.missing, 1)
        self.assertEqual(result.first_word_errors, ['"Come', "Your"])
        self.assertEqual(result.pauses_split, 5)

    def test_added_words_in_a_pause(self) -> None:
        pieces = [*DM_ONLY_PIECES[:1], "Thanks for watching.", *DM_ONLY_PIECES[1:]]
        self.assertEqual(score(self.script, pieces).part1.added, 3)

    def test_a_pause_that_didnt_split(self) -> None:
        pieces = [" ".join(DM_ONLY_PIECES[:2]), *DM_ONLY_PIECES[2:]]
        self.assertEqual(score(self.script, pieces).pauses_split, 4)

    def test_the_whisper(self) -> None:
        part = list(DM_ONLY_PIECES)
        part[2] = "He gives you hot food."
        self.assertEqual(score(self.script, part).whisper, "part")
        lost = DM_ONLY_PIECES[:2] + DM_ONLY_PIECES[3:]
        result = score(self.script, lost)
        self.assertEqual(result.whisper, "missing")
        self.assertEqual(result.part1.errors, 0)  # not counted in Part 1

    def test_nothing_heard(self) -> None:
        result = score(self.script, [])
        self.assertEqual(result.part1.missing, 33)
        self.assertEqual(result.terms_right, [])


class AudioTests(unittest.TestCase):
    def test_a_long_silence_ends_a_piece_and_a_short_one_doesnt(self) -> None:
        pcm = silence(400) + tone(500) + silence(1000) + tone(300) + silence(300) + tone(200)
        found = list(audio.pieces(pcm))
        self.assertEqual([p.start_ms for p in found], [400, 1900])
        self.assertEqual([p.end_ms - p.start_ms for p in found], [500, 800])

    def test_silence_alone_is_no_speech(self) -> None:
        self.assertEqual(list(audio.pieces(silence(3000))), [])
        self.assertEqual(audio.frame_dbfs(silence(20)), -math.inf)

    def test_wav_in_core_format_needs_nothing_extra(self) -> None:
        pcm = tone(100)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speech.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(BYTES_PER_SAMPLE)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(pcm)
            self.assertEqual(audio.decode(path), pcm)


class ScriptedTranscriber:
    """Says the next line each time, like a perfect engine."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.heard: list[Utterance] = []
        self.closed = False

    async def warm_up(self) -> None:
        return None

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        self.heard.append(utterance)
        return self.lines.pop(0) if self.lines else None

    async def close(self) -> None:
        self.closed = True


class ReplayTests(unittest.TestCase):
    def test_pieces_go_through_the_pipeline_in_order(self) -> None:
        pcm = tone(600) + silence(1000) + tone(700) + silence(1000)
        engine = ScriptedTranscriber(["first", "second"])
        result = asyncio.run(replay(list(audio.pieces(pcm)), engine))
        self.assertEqual([h.text for h in result.heard], ["first", "second"])
        self.assertEqual([h.start_ms for h in result.heard], [0, 1600])
        self.assertTrue(engine.closed)
        self.assertEqual({u.user_id for u in engine.heard}, {TWIN_SPEAKER})

    def test_a_long_piece_is_cut_at_15_seconds_by_the_segmenter(self) -> None:
        pcm = tone(MAX_UTTERANCE_MS + 2000)
        engine = ScriptedTranscriber(["a", "b"])
        result = asyncio.run(replay(list(audio.pieces(pcm)), engine))
        self.assertEqual(len(result.heard), 2)
        self.assertAlmostEqual(engine.heard[0].duration_s, MAX_UTTERANCE_MS / 1000)

    def test_the_twin_consents_only_for_itself(self) -> None:
        consent = TwinConsent()
        self.assertTrue(consent.has_consent(TWIN_GUILD, TWIN_SPEAKER))
        self.assertFalse(consent.has_consent(TWIN_GUILD, TWIN_SPEAKER + 1))
        self.assertFalse(consent.has_consent(TWIN_GUILD + 1, TWIN_SPEAKER))


class ReportTests(unittest.TestCase):
    def test_the_record_and_its_history_entry(self) -> None:
        script = load_script(SCRIPTS / "dm-only.md")
        lines = record(
            script=script,
            recording="DMOnlyAudio.m4a",
            engine="deepgram nova-3",
            commit="abc1234",
            result=Replay(),
            score=score(script, DM_ONLY_PIECES),
        )
        self.assertIn("part 1: 0 wrong, 0 missing, 0 added (of 33, whisper not included)", lines)
        self.assertIn("part 2: 12 of 12 right", lines)
        entry = history_entry(lines, title="dm-only.md", today=dt.date(2026, 10, 7))
        self.assertIn("2026-10-07  Twin run: dm-only.md\n", entry)
        self.assertIn("\n  part 2: 12 of 12 right\n", entry)


class IsolationTests(unittest.TestCase):
    def test_the_bot_never_imports_devtools(self) -> None:
        src = Path(__file__).resolve().parents[1] / "src" / "dmbot"
        for path in src.rglob("*.py"):
            if "devtools" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertNotIn("devtools", name, path)


class CutModelTests(unittest.TestCase):
    def test_quiet_inside_a_piece_is_sent_only_up_to_the_hangover(self) -> None:
        pcm = tone(200) + silence(600) + tone(200)
        (piece,) = audio.pieces(pcm)
        sent_ms = len(piece.frames) * audio.FRAME_MS
        self.assertEqual(sent_ms, 200 + audio.HANGOVER_MS + 200)
        self.assertEqual(piece.end_ms - piece.start_ms, 1000)  # times are kept

    def test_the_end_of_speech_is_exact(self) -> None:
        gap = audio.SPEECH_END_MS
        self.assertEqual(len(list(audio.pieces(tone(200) + silence(gap) + tone(200)))), 2)
        short = tone(200) + silence(gap - audio.FRAME_MS) + tone(200)
        self.assertEqual(len(list(audio.pieces(short))), 1)

    def test_edges(self) -> None:
        (piece,) = audio.pieces(tone(300) + silence(400))  # trailing quiet left out
        self.assertEqual(piece.end_ms, 300)
        (piece,) = audio.pieces(tone(300) + b"\x01")  # a part-frame at the end is ignored
        self.assertEqual(piece.end_ms, 300)

    def test_the_test_recording_cuts_like_run_7(self) -> None:
        # Pins the cut model: run 7 had 9 pieces live (8 here, plus a click at the start).
        try:
            import av  # noqa: F401
        except ImportError:
            self.skipTest('needs PyAV: pip install -e ".[twin]"')
        found = list(audio.pieces(audio.decode(SCRIPTS / "DMOnlyAudio.m4a")))
        self.assertEqual(len(found), 9)
        self.assertLess(found[0].end_ms - found[0].start_ms, 250)

    def test_without_pyav_other_recordings_say_what_to_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speech.m4a"
            path.write_bytes(b"not really audio")
            saved = sys.modules.get("av")
            sys.modules["av"] = None  # type: ignore[assignment]
            try:
                with self.assertRaisesRegex(audio.DecodeError, r"\.\[twin\]"):
                    audio.decode(path)
            finally:
                if saved is None:
                    del sys.modules["av"]
                else:
                    sys.modules["av"] = saved


class PauseScoreTests(unittest.TestCase):
    def test_a_split_after_an_added_word_still_counts(self) -> None:
        script = load_script(SCRIPTS / "dm-only.md")
        pieces = list(DM_ONLY_PIECES)
        pieces[1] = 'Um, inside," he says. "It\'s cold."'  # "Come" misheard as "Um,"
        result = score(script, pieces)
        self.assertEqual(result.pauses_split, 5)
        self.assertEqual(result.first_word_errors, ['"Come'])

    def test_the_bakeoff_script_is_not_a_read_aloud_script(self) -> None:
        with self.assertRaises(ValueError):
            load_script(SCRIPTS / "stt-bakeoff.md")


class FailingTranscriber(ScriptedTranscriber):
    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        raise RuntimeError("engine down")


class SlowTranscriber(ScriptedTranscriber):
    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        self.heard.append(utterance)
        self.hints = hints
        await asyncio.sleep(0.001)
        return "x"


def piece_at(start_ms: int, ms: int = 300) -> audio.Piece:
    pcm = tone(audio.FRAME_MS)
    return audio.Piece(tuple((start_ms + i, pcm) for i in range(0, ms, audio.FRAME_MS)))


class ReplayEdgeTests(unittest.TestCase):
    def test_a_full_queue_waits_and_drops_nothing(self) -> None:
        many = [piece_at(i * 2000) for i in range(QUEUE_SIZE + 10)]
        engine = SlowTranscriber([])
        result = asyncio.run(replay(many, engine, hints=["Auril"]))
        self.assertEqual(result.dropped, 0)
        self.assertEqual(len(result.heard), QUEUE_SIZE + 10)
        self.assertTrue(result.finished)
        self.assertEqual(engine.hints, ["Auril"])  # the hints reach the engine

    def test_failures_are_counted_and_still_delivered_without_text(self) -> None:
        result = asyncio.run(replay([piece_at(0), piece_at(2000)], FailingTranscriber([])))
        self.assertEqual(result.failed, 2)
        self.assertEqual([h.text for h in result.heard], [None, None])

    def test_realtime_times_the_delay_from_when_core_hears_the_end(self) -> None:
        result = asyncio.run(replay([piece_at(0)], ScriptedTranscriber(["hi"]), realtime=True))
        self.assertGreaterEqual(result.took_s, (300 + END_DELAY_MS) / 1000)
        lines = record(
            script=load_script(SCRIPTS / "dm-only.md"),
            recording="r",
            engine="e",
            commit="c",
            result=result,
            score=score(load_script(SCRIPTS / "dm-only.md"), ["hi"]),
        )
        self.assertTrue(any(line.startswith("delay: about") for line in lines), lines)

    def test_a_clip_too_short_to_write_down_is_reported_apart(self) -> None:
        result = asyncio.run(replay([piece_at(0, 100), piece_at(2000)], ScriptedTranscriber(["a"])))
        lines = record(
            script=load_script(SCRIPTS / "dm-only.md"),
            recording="r",
            engine="e",
            commit="c",
            result=result,
            score=score(load_script(SCRIPTS / "dm-only.md"), ["a"]),
        )
        self.assertIn(
            "pieces of speech: 1 (longest 0.3 s), plus 1 under 0.25 s (not written down)", lines
        )


class CommandLineTests(unittest.TestCase):
    def wav(self, folder: Path) -> Path:
        path = folder / "speech.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(BYTES_PER_SAMPLE)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(tone(400) + silence(1200) + tone(400))
        return path

    def test_log_appends_one_twin_run_and_names_no_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            history = folder / "history.log"
            history.write_text("earlier runs\n")
            engine = ScriptedTranscriber(["Your story starts", "in a small town"])
            argv = [
                str(self.wav(folder)),
                "--script",
                str(SCRIPTS / "dm-only.md"),
                "--transcriber",
                "whisper-local",
                "--log",
                "--history",
                str(history),
            ]
            with (
                patch.object(replay_main, "build_transcriber", return_value=engine),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(replay_main.main(argv), 0)
            text = history.read_text()
            self.assertTrue(text.startswith("earlier runs\n"))
            self.assertEqual(text.count("Twin run: dm-only.md, a recording, whisper-local"), 1)
            self.assertNotIn("speech.wav", text)

    def test_log_is_refused_without_an_engine_that_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            history = folder / "history.log"
            argv = [str(self.wav(folder)), "--script", str(SCRIPTS / "dm-only.md")]
            argv += ["--transcriber", "none", "--log", "--history", str(history)]
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(replay_main.main(argv), 2)
            self.assertIn("--log needs an engine", err.getvalue())
            self.assertFalse(history.exists())


class ReviewFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = load_script(SCRIPTS / "dm-only.md")

    def test_words_added_after_the_whisper_count_in_part_1(self) -> None:
        pieces = [*DM_ONLY_PIECES[:3], "Thanks for watching.", *DM_ONLY_PIECES[3:]]
        self.assertEqual(score(self.script, pieces).part1.added, 3)

    def test_two_words_heard_as_one_is_one_error(self) -> None:
        pieces = list(DM_ONLY_PIECES)
        pieces[0] = pieces[0].replace("front door", "frontdoor")
        result = score(self.script, pieces)
        self.assertEqual((result.part1.wrong, result.part1.missing), (1, 0))

    def test_one_word_heard_as_two_is_one_error(self) -> None:
        pieces = list(DM_ONLY_PIECES)
        pieces[1] = pieces[1].replace("inside", "in side")
        result = score(self.script, pieces)
        self.assertEqual((result.part1.wrong, result.part1.added), (1, 0))

    def test_a_note_right_after_a_turn_is_not_read(self) -> None:
        script = parse_script(
            "## Part 1\n**[DM]:** Hello there.\n**[Don't read this out loud.]** Shh.\n"
        )
        self.assertEqual([w.text for w in script.words], ["Hello", "there."])

    def test_realtime_ends_pieces_after_the_chosen_quiet(self) -> None:
        engine = ScriptedTranscriber(["hi"])
        result = asyncio.run(replay([piece_at(0)], engine, realtime=True, end_delay_ms=100))
        self.assertLess(result.took_s, (300 + END_DELAY_MS) / 1000)


class CostTests(unittest.TestCase):
    def wav(self, folder: Path) -> Path:
        return CommandLineTests.wav(CommandLineTests(), folder)

    def run_main(self, argv: list[str], env: dict[str, str]) -> tuple[int, str, list[object]]:
        built: list[object] = []

        def build(settings: object) -> ScriptedTranscriber:
            built.append(settings)
            return ScriptedTranscriber(["Your story starts"])

        err = io.StringIO()
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(replay_main, "build_transcriber", side_effect=build),
            redirect_stdout(io.StringIO()),
            redirect_stderr(err),
        ):
            code = replay_main.main(argv)
        return code, err.getvalue(), built

    def test_a_paid_engine_set_only_in_the_environment_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv = [str(self.wav(Path(tmp))), "--script", str(SCRIPTS / "dm-only.md")]
            env = {"TRANSCRIBER": "deepgram", "DEEPGRAM_API_KEY": "test-not-a-key"}
            code, err, built = self.run_main(argv, env)
        self.assertEqual(code, 2)
        self.assertIn("--transcriber deepgram", err)
        self.assertEqual(built, [])  # nothing was sent anywhere

    def test_a_named_paid_engine_runs_and_says_what_it_sends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv = [str(self.wav(Path(tmp))), "--script", str(SCRIPTS / "dm-only.md")]
            argv += ["--transcriber", "deepgram"]
            env = {"DEEPGRAM_API_KEY": "test-not-a-key"}
            code, err, built = self.run_main(argv, env)
        self.assertEqual(code, 0)
        self.assertRegex(err, r"sends \d+ s of audio to Deepgram \(about \$\d+\.\d{3} at nova-3")
        self.assertEqual(len(built), 1)

    def test_the_command_line_engine_beats_the_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv = [str(self.wav(Path(tmp))), "--script", str(SCRIPTS / "dm-only.md")]
            argv += ["--transcriber", "whisper-local"]
            code, _, built = self.run_main(argv, {"TRANSCRIBER": "deepgram"})
        self.assertEqual(code, 0)
        self.assertEqual([getattr(b, "engine", None) for b in built], ["whisper-local"])


class PublicLogTests(unittest.TestCase):
    def test_the_repos_own_recordings_keep_their_names(self) -> None:
        self.assertEqual(replay_main.public_name(SCRIPTS / "DMOnlyAudio.m4a"), "DMOnlyAudio.m4a")
        self.assertEqual(replay_main.public_name(Path("/tmp/Alice reading.m4a")), "a recording")

    def test_the_log_never_holds_what_was_heard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            history = folder / "history.log"
            engine = ScriptedTranscriber(["Zanzibarquux said this"])
            argv = [str(CommandLineTests.wav(CommandLineTests(), folder)), "--script"]
            argv += [str(SCRIPTS / "dm-only.md"), "--transcriber", "whisper-local"]
            argv += ["--log", "--history", str(history)]
            with (
                patch.object(replay_main, "build_transcriber", return_value=engine),
                redirect_stdout(io.StringIO()) as out,
            ):
                self.assertEqual(replay_main.main(argv), 0)
            self.assertIn("Zanzibarquux", out.getvalue())  # shown on screen
            self.assertNotIn("Zanzibarquux", history.read_text())  # never logged
