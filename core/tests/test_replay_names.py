import io
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dmbot.audio.segmenter import Utterance
from dmbot.devtools.replay import __main__ as replay_main
from dmbot.devtools.replay import names as name_scan
from dmbot.devtools.replay.bakeoff import load_bakeoff
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
from dmbot.devtools.replay.script import load_script
from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE
from dmbot.memory import scan

SCRIPTS = Path(__file__).resolve().parents[2] / "docs" / "test-scripts"
SETUP = SCRIPTS / "bakeoff-story-names-setup.md"


def timed(*lines: str) -> list[tuple[float, str]]:
    return [(i * 5.0, line) for i, line in enumerate(lines)]


class KnownNamesTests(unittest.TestCase):
    def test_the_setup_note_seeds_ten_names_and_their_other_names(self) -> None:
        known = load_known(SETUP)
        self.assertEqual(known.count, 10)
        self.assertEqual(len(known.keys), 12)
        self.assertIn("bell", known.keys)
        self.assertIn("vane", known.keys)

    def test_the_hints_are_the_bots_own(self) -> None:
        hints = load_known(SETUP).hints()
        self.assertEqual(len(hints), 12)  # other names too: Bell, Vane
        self.assertIn("Bell", hints)

    def test_saved_as_add_many_saves_them(self) -> None:
        # The real Add many decides (#574): held only if near a name above (spelled at
        # least 0.9 alike) or with no kind; a look-alike by sound alone is confirmed.
        known = known_from_text(
            "Kaelen | NPC\nQuillon | NPC\nQuilon | NPC | Kwilo\nZanthor\nQuillon | NPC | Kwil\n"
        )
        self.assertEqual(known.count, 4)  # the repeated Quillon folds into the first
        statuses = {e.name: e.status for e in known.lookup.entities.values()}
        self.assertEqual(statuses["Quillon"], "confirmed")
        self.assertEqual(statuses["Quilon"], "proposed")  # spelled almost like Quillon
        self.assertEqual(statuses["Kaelen"], "confirmed")  # sounds like Quillon (KLN): fine
        self.assertEqual(statuses["Zanthor"], "proposed")  # no kind
        names = {n.key: n for n in known.lookup.names}
        self.assertEqual((names["kwil"].kind, names["kwil"].confirmed), ("nickname", True))
        self.assertFalse(names["kwilo"].confirmed)  # a held name's other names wait too
        hints = known.hints()
        self.assertLess(hints.index("Quillon"), hints.index("Zanthor"))  # guesses go last

    def test_a_fenced_block_with_a_language_tag(self) -> None:
        known = known_from_text("Intro\n```text\nCerric | NPC\n```\n")
        self.assertEqual(known.count, 1)
        self.assertNotIn("text", known.keys)

    def test_a_bad_names_file_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            known_from_text("A | b | c | d | e\n")

    def test_lines_are_cleaned_by_the_real_cleaner(self) -> None:
        known = known_from_text("Belleros | NPC | Bell\nKa'zeth | NPC\n")
        cleaned = clean_lines(known, timed("Then Belle Ross casts a spell.", "Kazeth laughs."))
        self.assertEqual(cleaned, ["Then Belleros casts a spell.", "Ka'zeth laughs."])


class ScanScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.known = known_from_text(
            "Belleros | NPC | Bell\nGorrak | NPC\nOskar Vane | NPC | Vane\n"
        )
        self.names = story_names()

    def test_every_kind_of_suggestion(self) -> None:
        lines = [
            "We meet Hrothgar at dawn. Then we ask Hrothgar again.",  # unknown: found
            "The orc Gorak shouts. We fight Gorak.",  # known, misheard: again
            "We see Zzqx today. Later, Zzqx returns.",  # nothing: other
            "He casts Fireball now. Again he casts Fireball.",  # a spell
            "Then Oskar Vane leaves. We follow Oskar Vane.",  # a known name said whole (#399)
            "We meet Val Zimmer. Later Val Zimmer smiles.",  # misheard Vhalzimar
        ]
        score = score_scan(lines, self.known, self.names, limit=False)
        self.assertIn("Hrothgar", score.found)
        self.assertEqual(sorted(score.again), ["Gorak"])  # not "Oskar" any more (#399)
        self.assertEqual(score.rules, ["Fireball"])
        self.assertEqual(score.misheard, ["Val Zimmer"])
        self.assertEqual(score.junk, ["Zzqx"])
        self.assertIn("Vhalzimar", score.missed)
        self.assertNotIn("Belleros", score.missed)  # known, so not counted

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

    def test_the_limit_is_put_back_after_an_error(self) -> None:
        with (
            patch.object(scan, "find_new_names", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            score_scan(["x"], Known.none(), self.names, limit=False)
        self.assertEqual(scan.MAX_SUGGESTIONS, 10)

    def test_the_record_is_numbers_only(self) -> None:
        score = score_scan(["We see Zzqx today. Later, Zzqx returns."], self.known, self.names)
        lines = scan_record(
            script=score, as_heard=score, cleaned=score, unlimited=score,
            script_unlimited=score, known=self.known.count,
        )  # fmt: skip
        self.assertNotIn("Zzqx", " ".join(lines))
        self.assertIn("other: Zzqx", scan_details(score))


class CeilingTests(unittest.TestCase):
    """The most the scan can find when every word is heard right, with the setup note's
    names known. A change to the scan's rules moves these; that should be on purpose."""

    def setUp(self) -> None:
        self.known = load_known(SETUP)
        self.names = story_names()

    def test_the_bakeoff_script(self) -> None:
        script = load_bakeoff(SCRIPTS / "stt-bakeoff.md")
        lines = [script.lines[n] for n in sorted(script.lines)]
        score = score_scan(lines, self.known, self.names, limit=False)
        # Names said once, or only at the start of a sentence, can't be found.
        self.assertEqual((len(score.found), score.unknown), (9, 15))

    def test_the_story(self) -> None:
        script = load_script(SCRIPTS / "bakeoff-story.md")
        lines = [" ".join(w.text for w in script.words)]
        limited = score_scan(lines, self.known, self.names)
        unlimited = score_scan(lines, self.known, self.names, limit=False)
        self.assertEqual((len(unlimited.found), unlimited.unknown), (15, 15))
        self.assertEqual(len(limited.rules), 3)  # spells take 3 of the DM's 10 places
        self.assertEqual(len(limited.found), 7)


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
    def run_main(self, script: str, *extra: str) -> tuple[str, str, ScriptedTranscriber]:
        heard = "We meet Hrothgar at dawn. Then Hrothgar and Zzqx talk. Zzqx leaves."
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speech.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(BYTES_PER_SAMPLE)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(b"\x40\x1f" * (SAMPLE_RATE // 2))  # half a second of sound
            history = Path(tmp) / "history.log"
            argv = [str(path), "--script", str(SCRIPTS / script)]
            argv += ["--transcriber", "whisper-local", "--log", "--history", str(history), *extra]
            engine = ScriptedTranscriber([heard])
            with (
                patch.object(replay_main, "build_transcriber", return_value=engine),
                redirect_stdout(io.StringIO()) as out,
            ):
                self.assertEqual(replay_main.main(argv), 0)
            return out.getvalue(), history.read_text(), engine

    def test_names_seed_the_campaign_the_hints_and_the_scan(self) -> None:
        out, logged, engine = self.run_main("bakeoff-story.md", "--names", str(SETUP))
        self.assertEqual(engine.hints, load_known(SETUP).hints())
        self.assertIn("name scan (campaign knows 10 names)", logged)
        self.assertIn("cleaned: 1 of 15 new story names", logged)
        self.assertIn("(the most possible): 15 of 15", logged)
        self.assertNotIn("Zzqx", logged)  # what was heard stays off the log
        self.assertIn("other: Zzqx", out)

    def test_no_scan_without_names(self) -> None:
        _, logged, _ = self.run_main("stt-bakeoff.md")
        self.assertNotIn("name scan", logged)

    def test_no_scan_for_a_script_that_isnt_a_bakeoff(self) -> None:
        out, logged, _ = self.run_main("dm-only.md", "--names", str(SETUP))
        self.assertNotIn("name scan (", logged)
        self.assertIn("scored only for stt-bakeoff.md and bakeoff-story.md", out)


class IsolationTests(unittest.TestCase):
    def test_the_twins_rules_words_are_not_story_names(self) -> None:
        story = {n.name for n in story_names()}
        self.assertFalse(story & set(name_scan.RULES_WORDS))
