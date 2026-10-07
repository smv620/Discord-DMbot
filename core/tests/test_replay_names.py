import io
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dmbot.audio.segmenter import Utterance
from dmbot.devtools.replay import __main__ as replay_main
from dmbot.devtools.replay.names import (
    Known,
    clean_lines,
    known_from_text,
    load_known,
    scan_details,
    scan_record,
    score_scan,
    story_names,
)
from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE
from dmbot.memory import scan

SCRIPTS = Path(__file__).resolve().parents[2] / "docs" / "test-scripts"
SETUP = SCRIPTS / "bakeoff-story-names-setup.md"


def timed(*lines: str) -> list[tuple[float, str]]:
    return [(i * 5.0, line) for i, line in enumerate(lines)]


class KnownNamesTests(unittest.TestCase):
    def test_the_setup_note_seeds_ten_names_and_their_other_names(self) -> None:
        known = load_known(SETUP)
        self.assertEqual(len(known.names), 10)
        self.assertIn("bell", known.keys)
        self.assertIn("vane", known.keys)
        self.assertIn("ashen crown", known.keys)

    def test_a_bad_names_file_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            known_from_text("A | b | c | d | e\n")

    def test_lines_are_cleaned_by_the_real_cleaner(self) -> None:
        known = known_from_text("Belleros | NPC | Bell\nKa'zeth | NPC\n")
        cleaned = clean_lines(known, timed("Then Belle Ross casts a spell.", "Kazeth laughs."))
        self.assertEqual(cleaned, ["Then Belleros casts a spell.", "Ka'zeth laughs."])


class ScanScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.known = known_from_text("Belleros | NPC | Bell\nGorrak | NPC\n")
        self.names = story_names()

    def test_found_missed_false_and_suggested_again(self) -> None:
        lines = [
            "We meet Hrothgar at dawn. Then we ask Hrothgar again.",  # unknown: found
            "The orc Gorak shouts. We fight Gorak.",  # known, misheard: suggested again
            "We see Zzqx today. Later, Zzqx returns.",  # not a story name
        ]
        score = score_scan(lines, self.known, self.names)
        self.assertIn("Hrothgar", score.found)
        self.assertEqual(score.again, ["Gorak"])
        self.assertEqual(score.false, ["Zzqx"])
        self.assertNotIn("Belleros", score.missed)  # known, so not counted as missed
        self.assertIn("Vhalzimar", score.missed)
        self.assertEqual(score.unknown, len(self.names) - 3)  # Belleros, Bell, Gorrak known

    def test_cleaning_stops_a_misheard_known_name_coming_back(self) -> None:
        heard = timed("Then Belle Ross casts a spell.", "We cheer as Belle Ross wins.")
        as_heard = score_scan([t for _, t in heard], self.known, self.names)
        cleaned = score_scan(clean_lines(self.known, heard), self.known, self.names)
        self.assertEqual(as_heard.again, ["Belle Ross"])  # what the bot does today (#394)
        self.assertEqual(cleaned.again, [])

    def test_the_bots_limit_and_without_it(self) -> None:
        many = [f"We meet {n.name} today. Later we see {n.name} again." for n in self.names]
        limited = score_scan(many, Known.none(), self.names)
        unlimited = score_scan(many, Known.none(), self.names, limit=False)
        self.assertEqual(limited.offered, scan.MAX_SUGGESTIONS)
        self.assertGreater(unlimited.offered, scan.MAX_SUGGESTIONS)
        self.assertEqual(scan.MAX_SUGGESTIONS, 10)  # put back afterwards

    def test_the_record_is_numbers_only(self) -> None:
        score = score_scan(["We see Zzqx today. Later, Zzqx returns."], self.known, self.names)
        lines = scan_record(score, score, score, len(self.known.names))
        self.assertNotIn("Zzqx", " ".join(lines))
        self.assertIn("not story names: Zzqx", scan_details(score))


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
    def test_names_seed_the_campaign_the_hints_and_the_scan(self) -> None:
        heard = "We meet Hrothgar at dawn. Then Hrothgar and Zzqx talk. Zzqx leaves."
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speech.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(BYTES_PER_SAMPLE)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(b"\x40\x1f" * (SAMPLE_RATE // 2))  # half a second of sound
            history = Path(tmp) / "history.log"
            argv = [str(path), "--script", str(SCRIPTS / "bakeoff-story.md")]
            argv += ["--transcriber", "whisper-local", "--names", str(SETUP)]
            argv += ["--log", "--history", str(history)]
            engine = ScriptedTranscriber([heard])
            with (
                patch.object(replay_main, "build_transcriber", return_value=engine),
                redirect_stdout(io.StringIO()) as out,
            ):
                self.assertEqual(replay_main.main(argv), 0)
            logged = history.read_text()
        self.assertEqual(len(engine.hints), 10)  # the campaign's names, as the bot sends
        self.assertIn("name scan (campaign knows 10 names", logged)
        self.assertIn("1 of 19 new story names found", logged)
        self.assertNotIn("Zzqx", logged)  # what was heard stays off the log
        self.assertIn("not story names: Zzqx", out.getvalue())
