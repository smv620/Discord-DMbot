import io
import random
import struct
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dmbot.audio.segmenter import Utterance
from dmbot.devtools.replay import __main__ as replay_main
from dmbot.devtools.replay import audio
from dmbot.devtools.replay.bakeoff import (
    BakeoffScore,
    bakeoff_record,
    is_bakeoff,
    load_bakeoff,
    misheard,
    parse_bakeoff,
    score_bakeoff,
)
from dmbot.devtools.replay.score import words
from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

SCRIPTS = Path(__file__).resolve().parents[2] / "docs" / "test-scripts"
SAID = 55  # names said in lines 1-48, counted by hand


class BakeoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = load_bakeoff(SCRIPTS / "stt-bakeoff.md")
        self.perfect = " ".join(self.script.lines[n] for n in sorted(self.script.lines))

    def scored(self, *pieces: str) -> BakeoffScore:
        return score_bakeoff(self.script, list(pieces))

    def test_the_script_is_read(self) -> None:
        self.assertEqual(len(self.script.lines), 65)
        self.assertEqual(len(self.script.names), 25)  # the 24 people and places, and Ashen Crown
        self.assertEqual(self.script.nickname.spellings, ("Bell", "Belle"))
        self.assertEqual(len(self.script.rules), 12)
        dravenmoor = next(t for t in self.script.names if t.name == "Dravenmoor")
        self.assertIn("Draven Moor", dravenmoor.spellings)
        self.assertTrue(is_bakeoff((SCRIPTS / "stt-bakeoff.md").read_text()))
        self.assertFalse(is_bakeoff((SCRIPTS / "dm-only.md").read_text()))

    def test_a_rules_line_without_a_colon_is_refused(self) -> None:
        text = (SCRIPTS / "stt-bakeoff.md").read_text().replace("scored separately):", "x")
        with self.assertRaises(ValueError):
            parse_bakeoff(text)

    def test_a_perfect_reading(self) -> None:
        result = self.scored(self.perfect)
        total = BakeoffScore.total(result.names)
        self.assertEqual((total.right, total.said, total.missing, total.cut), (SAID, SAID, 0, 0))
        self.assertEqual((result.nickname.right, result.nickname.said), (2, 2))
        self.assertEqual(BakeoffScore.total(result.rules).right, 12)
        self.assertEqual(result.false_names, [])
        self.assertEqual(result.wer_errors, 0)

    def test_a_name_inside_quotes_counts(self) -> None:
        # Line 18: Ka'zeth says, "Orrin will find you ..."
        self.assertEqual(self.scored(self.perfect).names["Orrin"].said, 2)

    def test_cuts_between_names_change_nothing(self) -> None:
        cut = self.perfect.split()
        pieces = [" ".join(cut[i : i + 37]) for i in range(0, len(cut), 37)]
        total = BakeoffScore.total(self.scored(*pieces).names)
        self.assertEqual(total.right + total.cut, SAID)
        self.assertEqual((len(total.wrong), total.missing), (0, 0))

    def test_a_name_cut_in_half_between_pieces_is_not_right(self) -> None:
        before, _, after = self.perfect.partition("Vhalzimar is the wizard")
        result = self.scored(before + "Vhal", "zimar is the wizard" + after)
        self.assertEqual(result.names["Vhalzimar"].cut, 1)
        self.assertEqual(result.names["Vhalzimar"].right, 2)

    def test_wrong_missing_and_accepted_spellings(self) -> None:
        text = (
            self.perfect.replace("Cerric, you're", "Derek, you're")  # wrong
            .replace("Hrothgar the", "the")  # missing
            .replace("Dravenmoor", "Draven Moor")  # an accepted spelling, as two words
            .replace("Oskar Vane meet", "Oscar Vane meet")  # accepted
        )
        result = self.scored(text)
        self.assertEqual(result.names["Cerric"].wrong, ["derek"])
        self.assertEqual(result.names["Hrothgar"].missing, 1)
        self.assertEqual(result.names["Dravenmoor"].right, 1)
        self.assertEqual(result.names["Oskar Vane"].right, 2)
        self.assertIn('Cerric: written as "derek"; missing 0; cut 0', misheard(result))

    def test_a_name_heard_as_several_words_shows_them_all(self) -> None:
        result = self.scored(self.perfect.replace("Who is Vhalzimar?", "Who is Vel zim or?"))
        self.assertEqual(result.names["Vhalzimar"].wrong, ["vel zim or"])

    def test_the_nickname_must_stay_bell(self) -> None:
        expanded = self.scored(self.perfect.replace("Bell, you're", "Belleros, you're"))
        self.assertEqual((expanded.nickname.right, expanded.expanded), (1, 1))
        spelt = self.scored(self.perfect.replace("Bell, you're", "Bellaros, you're"))
        self.assertEqual(spelt.expanded, 1)  # an accepted Belleros spelling counts too
        kept = self.scored(self.perfect.replace("Bell, you're", "Belle, you're"))
        self.assertEqual(kept.nickname.right, 2)

    def test_ring_the_bell_is_not_the_nickname(self) -> None:
        self.assertEqual(self.scored(self.perfect).nickname.said, 2)  # lines 38-39 only

    def test_a_name_where_none_was_said(self) -> None:
        text = (
            self.perfect.replace("The kale salad", "The Kael salad")
            .replace("cousin Eric", "cousin Kazeth")  # two spellings, one form: once
            .replace("the boards", "the Thorne Wick")  # a two-word spelling
            .replace("Ring the bell", "Ring the Bell")  # the nickname, with a capital
        )
        self.assertEqual(
            sorted(self.scored(text).false_names), ["Bell", "Ka'zeth", "Kael", "Thornewick"]
        )

    def test_the_everyday_lines_word_error_rate(self) -> None:
        result = self.scored(self.perfect.replace("the snacks", "the snakes"))
        self.assertEqual((result.wer_errors, result.wer_words), (1, 68))
        split = self.scored(self.perfect.replace("Thursday", "Thurs day"))
        self.assertEqual(split.wer_errors, 1)  # one word heard as two is one error

    def test_the_record_holds_counts_and_the_scripts_names_only(self) -> None:
        lines = bakeoff_record(self.scored(self.perfect.replace("Cerric", "Zzqx")))
        self.assertIn(f"{SAID - 3} of {SAID} right (3 wrong, 0 missing, 0 cut", lines[0])
        self.assertNotIn("zzqx", " ".join(lines).casefold())  # what was heard stays off it


class WordRuleTests(unittest.TestCase):
    def test_numbers_dice_and_ordinals(self) -> None:
        self.assertEqual(words("twenty-three"), words("23"))
        self.assertEqual(words("fifty gold"), words("50 gold"))
        self.assertEqual(words("a d20"), words("a D 20"))
        self.assertEqual(words("two d6"), ["two", "d", "six"])
        self.assertEqual(words("the 1st and 10th"), ["the", "1st", "and", "10th"])
        self.assertEqual(words("x²"), ["x²"])  # no crash


def noisy_speech(floor: int = 120, speech_s: float = 0.5, gap_s: float = 1.5) -> bytes:
    """Speech with room noise between lines, not muted."""
    rng = random.Random(1)
    out = b""
    for _ in range(3):
        tone = [int(8000 * ((i // 18) % 2 * 2 - 1)) for i in range(int(SAMPLE_RATE * speech_s))]
        noise = [rng.randint(-floor, floor) for _ in range(int(SAMPLE_RATE * gap_s))]
        out += struct.pack(f"<{len(tone) + len(noise)}h", *tone, *noise)
    return out


class SilenceThresholdTests(unittest.TestCase):
    def test_room_noise_counts_as_silence(self) -> None:
        pcm = noisy_speech()
        self.assertEqual(len(list(audio.pieces(pcm))), 1)  # at -60 dBFS the noise is "speech"
        threshold = audio.silence_dbfs_for(audio.frame_levels(pcm))
        self.assertGreater(threshold, audio.SILENCE_DBFS)
        self.assertEqual(len(list(audio.pieces(pcm, silence_dbfs=threshold))), 3)

    def test_a_muted_recording_keeps_the_usual_threshold(self) -> None:
        levels = audio.frame_levels(noisy_speech(floor=0))
        self.assertEqual(audio.silence_dbfs_for(levels), audio.SILENCE_DBFS)

    def test_a_recording_that_is_nearly_all_speech_is_capped(self) -> None:
        levels = audio.frame_levels(noisy_speech(speech_s=3.0, gap_s=0.1))
        self.assertEqual(audio.silence_dbfs_for(levels), audio.SILENCE_MAX_DBFS)

    def test_the_real_recordings(self) -> None:
        try:
            import av  # noqa: F401
        except ImportError:
            self.skipTest('needs PyAV: pip install -e ".[twin]"')
        pcm = audio.decode(SCRIPTS / "DMOnlyAudio.m4a")
        levels = audio.frame_levels(pcm)
        threshold = audio.silence_dbfs_for(levels)
        self.assertEqual(threshold, audio.SILENCE_DBFS)  # muted: unchanged
        self.assertEqual(len(list(audio.pieces(pcm, silence_dbfs=threshold, levels=levels))), 9)
        bakeoff = SCRIPTS / "stt-bakeoff.m4a"
        if not bakeoff.exists():
            self.skipTest("stt-bakeoff.m4a not in this checkout yet (#354)")
        pcm = audio.decode(bakeoff)
        levels = audio.frame_levels(pcm)
        threshold = audio.silence_dbfs_for(levels)
        self.assertAlmostEqual(threshold, -41.8, delta=0.5)
        self.assertEqual(len(list(audio.pieces(pcm, silence_dbfs=threshold, levels=levels))), 12)


class ScriptedTranscriber:
    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.hints: list[str] = []

    async def warm_up(self) -> None:
        return None

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        self.hints = hints
        return self.lines.pop(0) if self.lines else None

    async def close(self) -> None:
        return None


class CommandLineTests(unittest.TestCase):
    def setUp(self) -> None:
        script = load_bakeoff(SCRIPTS / "stt-bakeoff.md")
        self.perfect = " ".join(script.lines[n] for n in sorted(script.lines))

    def run_main(self, folder: Path, *extra: str) -> tuple[str, ScriptedTranscriber]:
        path = folder / "speech.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(BYTES_PER_SAMPLE)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(noisy_speech()[: SAMPLE_RATE * 2 * 2])  # one piece
        argv = [str(path), "--script", str(SCRIPTS / "stt-bakeoff.md")]
        argv += ["--transcriber", "whisper-local", *extra]
        engine = ScriptedTranscriber([self.perfect.replace("Cerric", "Zzqx")])
        with (
            patch.object(replay_main, "build_transcriber", return_value=engine),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(replay_main.main(argv), 0)
        return out.getvalue(), engine

    def test_the_bakeoff_script_gets_the_names_record_and_its_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text, engine = self.run_main(Path(tmp))
        self.assertIn(f"{SAID - 3} of {SAID} right", text)
        self.assertIn("pieces before core's 15 s cut", text)
        self.assertIn("Vhalzimar", engine.hints)
        self.assertIn("Bell", engine.hints)
        self.assertIn("name hints: 38", text)

    def test_no_hints_and_a_chosen_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text, engine = self.run_main(Path(tmp), "--no-hints", "--silence-db", "-50")
        self.assertEqual(engine.hints, [])
        self.assertIn("quieter than -50 dBFS", text)

    def test_the_log_never_holds_what_was_heard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "history.log"
            text, _ = self.run_main(Path(tmp), "--log", "--history", str(history))
            logged = history.read_text()
        self.assertIn("zzqx", text.casefold())  # on screen
        self.assertNotIn("zzqx", logged.casefold())
        self.assertIn("names (25, Ashen Crown included)", logged)
