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
    score_bakeoff,
)
from dmbot.devtools.replay.score import words
from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

SCRIPTS = Path(__file__).resolve().parents[2] / "docs" / "test-scripts"


class BakeoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = load_bakeoff(SCRIPTS / "stt-bakeoff.md")
        self.perfect = " ".join(self.script.lines[n] for n in sorted(self.script.lines))

    def scored(self, text: str) -> BakeoffScore:
        return score_bakeoff(self.script, [text])

    def test_the_script_is_read(self) -> None:
        self.assertEqual(len(self.script.lines), 65)
        self.assertEqual(len(self.script.names), 25)  # the 24 people and places, and Ashen Crown
        self.assertEqual(self.script.nickname.spellings, ("Bell", "Belle"))
        self.assertEqual(len(self.script.rules), 12)
        self.assertIn(
            "Draven Moor", next(t for t in self.script.names if t.name == "Dravenmoor").spellings
        )
        self.assertTrue(is_bakeoff((SCRIPTS / "stt-bakeoff.md").read_text()))
        self.assertFalse(is_bakeoff((SCRIPTS / "dm-only.md").read_text()))

    def test_a_perfect_reading(self) -> None:
        result = self.scored(self.perfect)
        total = BakeoffScore.total(result.names)
        self.assertEqual((total.right, total.said, total.missing), (54, 54, 0))
        self.assertEqual((result.nickname.right, result.nickname.said), (2, 2))
        self.assertEqual(BakeoffScore.total(result.rules).right, 12)
        self.assertEqual(result.false_names, [])
        self.assertEqual(result.wer_errors, 0)

    def test_wherever_the_pieces_are_cut(self) -> None:
        cut = self.perfect.split()
        pieces = [" ".join(cut[i : i + 37]) for i in range(0, len(cut), 37)]  # mid-line cuts
        self.assertEqual(BakeoffScore.total(score_bakeoff(self.script, pieces).names).right, 54)

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
        self.assertIn('Cerric: written as "derek"; missing 0', misheard(result))

    def test_the_nickname_must_stay_bell(self) -> None:
        result = self.scored(self.perfect.replace("Bell, you're", "Belleros, you're"))
        self.assertEqual((result.nickname.right, result.expanded), (1, 1))

    def test_ring_the_bell_is_not_the_nickname(self) -> None:
        self.assertEqual(self.scored(self.perfect).nickname.said, 2)  # lines 38-39 only

    def test_a_name_where_none_was_said(self) -> None:
        text = self.perfect.replace("The kale salad", "The Kael salad").replace(
            "cousin Eric", "cousin Cerric"
        )
        self.assertEqual(sorted(self.scored(text).false_names), ["Cerric", "Kael"])

    def test_the_everyday_lines_word_error_rate(self) -> None:
        result = self.scored(self.perfect.replace("the snacks", "the snakes"))
        self.assertEqual(result.wer_errors, 1)
        self.assertEqual(result.wer_words, 68)

    def test_the_record_holds_counts_and_the_scripts_names_only(self) -> None:
        lines = bakeoff_record(self.scored(self.perfect.replace("Cerric", "Zzqx")))
        self.assertTrue(lines[0].startswith("names: 51 of 54 right (3 wrong, 0 missing)"))
        self.assertNotIn("zzqx", " ".join(lines).casefold())  # what was heard stays off the log


class WordRuleTests(unittest.TestCase):
    def test_numbers_and_dice(self) -> None:
        self.assertEqual(words("twenty-three"), words("23"))
        self.assertEqual(words("fifty gold"), words("50 gold"))
        self.assertEqual(words("a d20"), words("a D 20"))
        self.assertEqual(words("two d6"), ["two", "d", "six"])


def noisy_speech(floor: int = 120) -> bytes:
    """Speech with room noise between lines, not muted: a 440 Hz tone, then noise."""
    rng = random.Random(1)
    out = b""
    for _ in range(3):
        tone = [int(8000 * ((i // 18) % 2 * 2 - 1)) for i in range(SAMPLE_RATE // 2)]
        noise = [rng.randint(-floor, floor) for _ in range(SAMPLE_RATE * 3 // 2)]
        out += struct.pack(f"<{len(tone) + len(noise)}h", *tone, *noise)
    return out


class SilenceThresholdTests(unittest.TestCase):
    def test_room_noise_counts_as_silence(self) -> None:
        pcm = noisy_speech()
        self.assertEqual(len(list(audio.pieces(pcm))), 1)  # -60 dBFS: the noise is "speech"
        threshold = audio.silence_dbfs_for(pcm)
        self.assertGreater(threshold, audio.SILENCE_DBFS)
        self.assertEqual(len(list(audio.pieces(pcm, silence_dbfs=threshold))), 3)

    def test_a_muted_recording_keeps_the_usual_threshold(self) -> None:
        pcm = noisy_speech(floor=0)
        self.assertEqual(audio.silence_dbfs_for(pcm), audio.SILENCE_DBFS)


class ScriptedTranscriber:
    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)

    async def warm_up(self) -> None:
        return None

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        return self.lines.pop(0) if self.lines else None

    async def close(self) -> None:
        return None


class CommandLineTests(unittest.TestCase):
    def test_the_bakeoff_script_gets_the_names_record(self) -> None:
        script = load_bakeoff(SCRIPTS / "stt-bakeoff.md")
        perfect = " ".join(script.lines[n] for n in sorted(script.lines))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speech.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(BYTES_PER_SAMPLE)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(noisy_speech()[: SAMPLE_RATE * 2 * 2])  # one piece
            argv = [str(path), "--script", str(SCRIPTS / "stt-bakeoff.md")]
            argv += ["--transcriber", "whisper-local"]
            engine = ScriptedTranscriber([perfect])
            with (
                patch.object(replay_main, "build_transcriber", return_value=engine),
                redirect_stdout(io.StringIO()) as out,
            ):
                self.assertEqual(replay_main.main(argv), 0)
        text = out.getvalue()
        self.assertIn("names: 54 of 54 right (0 wrong, 0 missing)", text)
        self.assertIn("cut: 1 s quieter than", text)
