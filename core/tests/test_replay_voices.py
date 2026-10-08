import asyncio
import io
import itertools
import math
import struct
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.devtools.replay import __main__ as replay_main
from dmbot.devtools.replay import audio, voices
from dmbot.devtools.replay.audio import TWIN_PLAYER, TWIN_SPEAKER, Piece
from dmbot.devtools.replay.report import clock, speaker_lines
from dmbot.devtools.replay.run import END_DELAY_MS, TWIN_GUILD, ConsentChange, Heard, Replay, replay
from dmbot.devtools.replay.script import load_script
from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE, AudioFrame

SCRIPTS = Path(__file__).resolve().parents[2] / "docs" / "test-scripts"
TWO_VOICES = SCRIPTS / "two-voices.md"


def tone(ms: int) -> bytes:
    n = SAMPLE_RATE * ms // 1000
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)))
        for i in range(n)
    )


def silence(ms: int) -> bytes:
    return bytes(SAMPLE_RATE * ms // 1000 * BYTES_PER_SAMPLE)


def piece(start_ms: int, ms: int, speaker: int = TWIN_SPEAKER) -> Piece:
    frames = tuple(
        (at, b"\x40\x1f" * (audio.FRAME_BYTES // 2))
        for at in range(start_ms, start_ms + ms, audio.FRAME_MS)
    )
    return Piece(frames, speaker)


class BySpeaker:
    """A perfect engine for two voices: each speaker's next line, in turn."""

    def __init__(self, lines: dict[int, list[str]]) -> None:
        self.lines = {speaker: list(said) for speaker, said in lines.items()}
        self.heard: list[Utterance] = []

    async def warm_up(self) -> None:
        return None

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        self.heard.append(utterance)
        said = self.lines.get(utterance.user_id, [])
        return said.pop(0) if said else None

    async def close(self) -> None:
        return None


def turn_texts(path: Path) -> dict[int, list[str]]:
    """Each speaker's turns, as read: what a perfect engine would write."""
    script = load_script(path)
    out: dict[int, list[str]] = {TWIN_SPEAKER: [], TWIN_PLAYER: []}
    for role, said in itertools.groupby(script.words, key=lambda w: w.speaker):
        out[voices.SPEAKERS[role]].append(" ".join(w.text for w in said))
    return out


class ScriptTests(unittest.TestCase):
    def test_two_voices_is_dm_and_player_cut_by_cue(self) -> None:
        two = load_script(TWO_VOICES)
        same = load_script(SCRIPTS / "dm-and-player.md")
        self.assertEqual(two.words, same.words)
        self.assertEqual(two.order, ("DM", "Player") * 4)
        self.assertEqual(two.order, same.order)
        cues = [line for line in TWO_VOICES.read_text().splitlines() if line.startswith("### Cue")]
        self.assertEqual(len(cues), len(two.order))
        for cue, role in zip(cues, two.order, strict=True):
            self.assertIn(f"({role})", cue)


class MixTests(unittest.TestCase):
    def test_turns_are_cut_at_long_quiet(self) -> None:
        pieces = [piece(0, 500), piece(2_000, 500), piece(10_000, 500)]
        self.assertEqual([len(t) for t in voices.turns(pieces, 5_000)], [2, 1])

    def test_turns_go_in_the_scripts_order_each_with_its_speaker(self) -> None:
        dm = [piece(0, 1_000), piece(2_000, 500), piece(20_000, 1_000)]  # turns: 2, 1 pieces
        player = [piece(10_000, 700)]
        mixed = voices.mix(("DM", "Player", "DM"), {"DM": dm, "Player": player}, {}, answer_ms=800)
        self.assertEqual(
            [(p.speaker, p.start_ms, p.end_ms) for p in mixed],
            [
                (TWIN_SPEAKER, 0, 1_000),
                (TWIN_SPEAKER, 2_000, 2_500),  # inside a turn, timing is kept
                (TWIN_PLAYER, 3_300, 4_000),
                (TWIN_SPEAKER, 4_800, 5_800),
            ],
        )

    def test_a_negative_answer_talks_over_the_end(self) -> None:
        mixed = voices.mix(
            ("DM", "Player"),
            {"DM": [piece(0, 2_000)], "Player": [piece(9_000, 1_000)]},
            {},
            answer_ms=-500,
        )
        self.assertEqual(mixed[1].start_ms, 1_500)

    def test_talking_over_someone_never_over_oneself(self) -> None:
        mixed = voices.mix(
            ("DM", "Player", "DM"),
            {"DM": [piece(0, 2_000), piece(9_000, 1_000)], "Player": [piece(9_000, 200)]},
            {},
            answer_ms=-1_500,
        )
        self.assertEqual([p.start_ms for p in mixed], [0, 500, 3_000])  # DM waits for itself
        self.assertTrue(all(p.start_ms >= 0 for p in mixed))

    def test_a_turn_count_that_doesnt_match_says_which_file(self) -> None:
        with self.assertRaises(ValueError) as caught:
            voices.mix(
                ("DM", "Player", "DM"),
                {"DM": [piece(0, 500)], "Player": [piece(9_000, 500)]},
                {"DM": "two-voices-dm.m4a"},
            )
        self.assertIn(
            "two-voices-dm.m4a: 1 turns found, but the script has 2", str(caught.exception)
        )
        self.assertIn("count to ten", str(caught.exception))


class TwoSpeakerReplayTests(unittest.TestCase):
    def run_replay(self, pieces: list[Piece], *changes: ConsentChange) -> tuple[Replay, BySpeaker]:
        engine = BySpeaker(
            {
                TWIN_SPEAKER: [f"dm {i}" for i in range(9)],
                TWIN_PLAYER: [f"pl {i}" for i in range(9)],
            }
        )
        return asyncio.run(replay(pieces, engine, changes=changes)), engine

    def test_each_speaker_is_heard_on_their_own(self) -> None:
        # Talking over each other: two pieces at once, never joined.
        result, engine = self.run_replay([piece(0, 2_000), piece(1_000, 2_000, TWIN_PLAYER)])
        self.assertEqual(
            sorted((h.speaker, h.start_ms, h.text) for h in result.heard),
            [(TWIN_SPEAKER, 0, "dm 0"), (TWIN_PLAYER, 1_000, "pl 0")],
        )
        self.assertEqual({u.user_id for u in engine.heard}, {TWIN_SPEAKER, TWIN_PLAYER})

    def test_one_speakers_lead_in_never_joins_their_last_piece(self) -> None:
        # The second piece's first frame is earlier than the first piece's end is heard.
        result, _ = self.run_replay([piece(0, 500), piece(1_400, 500)])
        self.assertEqual([h.start_ms for h in result.heard], [0, 1_400])

    def test_nothing_after_a_stop_is_written_down(self) -> None:
        pieces = [piece(0, 1_000, TWIN_PLAYER), piece(3_000, 2_000, TWIN_PLAYER)]
        pieces += [piece(6_000, 1_000, TWIN_PLAYER), piece(6_000, 1_000)]
        result, engine = self.run_replay(pieces, ConsentChange(4_000, TWIN_PLAYER, False))
        player = [h for h in result.heard if h.speaker == TWIN_PLAYER]
        self.assertEqual([h.start_ms for h in player], [0])  # the piece being said is dropped
        self.assertEqual([h.speaker for h in result.heard], [TWIN_PLAYER, TWIN_SPEAKER])
        self.assertTrue(all(u.start_ms < 4_000 for u in engine.heard if u.user_id == TWIN_PLAYER))

    def test_nothing_before_a_first_yes_is_heard(self) -> None:
        pieces = [piece(0, 1_000, TWIN_PLAYER), piece(3_000, 1_000, TWIN_PLAYER)]
        result, _ = self.run_replay(pieces, ConsentChange(2_000, TWIN_PLAYER, True))
        self.assertEqual([h.start_ms for h in result.heard], [3_000])

    def test_a_stop_at_the_same_moment_as_a_frame_comes_first(self) -> None:
        result, _ = self.run_replay(
            [piece(1_000, 1_000, TWIN_PLAYER)], ConsentChange(1_000, TWIN_PLAYER, False)
        )
        self.assertEqual(result.heard, [])

    def test_a_late_lead_in_from_before_a_yes_is_not_sent(self) -> None:
        # The second piece's lead-in (1400 ms) is played at 1500, after the first piece
        # ends; a yes at 1450 must not let in what was said before it.
        pieces = [piece(0, 500, TWIN_PLAYER), piece(1_400, 800, TWIN_PLAYER)]
        result, _ = self.run_replay(pieces, ConsentChange(1_450, TWIN_PLAYER, True))
        self.assertEqual(len(result.heard), 1)
        self.assertGreaterEqual(result.heard[0].start_ms, 1_450)

    def test_one_voice_plays_exactly_as_before(self) -> None:
        # The old loop, one piece after another; lead-ins and a 15 s cut included.
        pcm = tone(300) + silence(1_040) + tone(16_000) + silence(1_200) + tone(400)
        pieces = list(audio.pieces(pcm, lead_in_ms=100))
        segmenter, before = Segmenter(TWIN_GUILD), []
        for p in pieces:
            for at_ms, frame in p.frames:
                if full := segmenter.add(AudioFrame(TWIN_GUILD, TWIN_SPEAKER, at_ms, frame)):
                    before.append(full)
            if (end := segmenter.end(TWIN_SPEAKER)) is not None:
                before.append(end)
        _, engine = self.run_replay(pieces)
        self.assertEqual(len(before), 4)
        self.assertEqual(
            [(u.start_ms, u.end_ms, u.pcm) for u in engine.heard],
            [(u.start_ms, u.end_ms, u.pcm) for u in before],
        )

    def test_live_a_stop_throws_away_words_still_being_written(self) -> None:
        class Slow(BySpeaker):
            async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
                self.heard.append(utterance)
                await asyncio.sleep(0.6)
                return "too late"

        engine = Slow({})
        pieces = [piece(0, 300, TWIN_PLAYER), piece(2_000, 300, TWIN_PLAYER)]
        # Heard to end at 1.3 s, still being written at the stop at 1.5 s: lost, as live.
        stop = ConsentChange(1_500, TWIN_PLAYER, False)
        result = asyncio.run(
            replay(pieces, engine, realtime=True, changes=[stop], end_delay_ms=END_DELAY_MS)
        )
        self.assertEqual(result.heard, [])
        self.assertEqual([u.start_ms for u in engine.heard], [0])  # never sent after it

    def test_speaker_lines_check_the_consent(self) -> None:
        script = load_script(TWO_VOICES)
        result = Replay(
            heard=[
                Heard(0, 3_000, "Your story starts", 0.0, 3.0),
                Heard(4_000, 6_000, "I knock", 0.0, 2.0, TWIN_PLAYER),
                Heard(30_000, 32_000, "late words", 0.0, 2.0, TWIN_PLAYER),  # after the stop
            ],
            changes=(ConsentChange(25_000, TWIN_PLAYER, False),),
        )
        lines = speaker_lines(script, result)
        self.assertTrue(lines[0].startswith("DM (1001): 1 pieces, 3.0 s; part 1:"))
        self.assertTrue(lines[1].startswith("Player (1002): 2 pieces, 4.0 s; part 1:"))
        self.assertEqual(
            lines[2], "  Player stopped at 0:25: written down after it: 1 pieces (should be 0)"
        )
        self.assertNotIn("late words", "\n".join(lines))
        self.assertEqual(clock(83_500), "1:23")


class CommandLineTests(unittest.TestCase):
    def wav(self, folder: Path, name: str, pcm: bytes) -> Path:
        path = folder / name
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(BYTES_PER_SAMPLE)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        return path

    def voices_wavs(self, folder: Path) -> tuple[Path, Path]:
        """Four turns each, as the script asks: one's lines, the other's count to ten."""
        turn, wait = tone(600), silence(8_000)
        dm = (turn + wait) * 4
        player = wait + (turn + wait) * 4
        return self.wav(folder, "dm.wav", dm), self.wav(folder, "player.wav", player)

    def run_main(self, *extra: str) -> tuple[int, str, str, BySpeaker]:
        with tempfile.TemporaryDirectory() as tmp:
            dm, player = self.voices_wavs(Path(tmp))
            argv = ["--speakers", f"{dm}:{TWIN_SPEAKER},{player}:{TWIN_PLAYER}"]
            argv += ["--script", str(TWO_VOICES), "--transcriber", "whisper-local", *extra]
            engine = BySpeaker(turn_texts(TWO_VOICES))
            with (
                patch.object(replay_main, "build_transcriber", return_value=engine),
                redirect_stdout(io.StringIO()) as out,
                redirect_stderr(io.StringIO()) as err,
            ):
                code = replay_main.main(argv)
            return code, out.getvalue(), err.getvalue(), engine

    def test_two_voices_replay_as_a_table_of_two(self) -> None:
        code, out, _, engine = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("part 1: 0 wrong, 0 missing, 0 added (of 35", out)
        self.assertIn("part 2: 12 of 12 right", out)
        self.assertIn("whisper: all", out)
        self.assertIn("recording: a recording + a recording", out)  # not the repo's own files
        self.assertIn("two voices, a turn ends at 5 s of quiet", out)
        self.assertIn("DM (1001): 4 pieces", out)
        self.assertIn("Player (1002): 4 pieces", out)
        self.assertIn("  DM: ", out)  # heard lines say who
        self.assertEqual([u.user_id for u in engine.heard], [TWIN_SPEAKER, TWIN_PLAYER] * 4)

    def test_a_player_who_stops(self) -> None:
        # Each turn starts 800 ms after the last ends: the player's cues 2, 4, 6 and 8
        # start at 1.4, 4.4, 7.4 and 10.4 s, and cue 4 is heard to end at 6.1 s. So a
        # stop at 0:07 keeps the whisper (cue 4) and loses cues 6 and 8.
        code, out, _, _ = self.run_main("--stop", f"{TWIN_PLAYER}@0:07")
        self.assertEqual(code, 0)
        self.assertIn("Player stopped at 0:07: written down after it: 0 pieces (should be 0)", out)
        self.assertIn("whisper: all", out)
        self.assertIn("part 2: 8 of 12 right", out)  # Detect Magic, longsword, ... lost

    def test_a_first_time_yes(self) -> None:
        code, out, _, _ = self.run_main("--agree", f"{TWIN_PLAYER}@0:03")
        self.assertEqual(code, 0)
        self.assertIn("Player agreed at 0:03: written down before it: 0 pieces (should be 0)", out)
        self.assertIn("Player (1002): 3 pieces", out)  # cue 2 was before the yes

    def test_a_turn_count_that_doesnt_match_is_refused(self) -> None:
        code, _, err, _ = self.run_main("--turn-quiet-ms", "20000")
        self.assertEqual(code, 2)
        self.assertIn("1 turns found, but the script has 4 [DM] turns", err)

    def test_bad_options(self) -> None:
        for argv, says in (
            (["x.wav", "--speakers", "a:1001,b:1002"], "give one recording, or --speakers"),
            (["--speakers", "a:1001,b:1003"], "needs FILE:1001"),
            (["--speakers", "a:1001,b:1001"], "one file for 1001 and one for 1002"),
            (["x.wav", "--stop", "1002@0:10"], "1002 isn't speaking in this replay"),
            (["x.wav", "--stop", "1001@soon"], "like 1002@0:25"),
            (["x.wav", "--stop", "1001@0:75"], "seconds go up to 59"),
            (["x.wav", "--stop", "1001@1", "--agree", "1001@2"], "one change per speaker"),
        ):
            with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                replay_main.parse_args([*argv, "--script", str(TWO_VOICES)])
            self.assertIn(says, err.getvalue(), argv)

    def test_times_read_as_minutes_and_seconds(self) -> None:
        self.assertEqual(replay_main._at("1001@1:02.5"), (1001, 62_500))
        self.assertEqual(replay_main._at("1002@7"), (1002, 7_000))
        self.assertEqual(replay_main._at("1002@75"), (1002, 75_000))  # seconds alone: any

    def test_one_recording_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.wav(Path(tmp), "one.wav", tone(400) + silence(1_200) + tone(400))
            argv = [str(path), "--script", str(SCRIPTS / "dm-only.md")]
            argv += ["--transcriber", "whisper-local"]
            engine = BySpeaker({TWIN_SPEAKER: ["Your story starts", "here"]})
            with (
                patch.object(replay_main, "build_transcriber", return_value=engine),
                redirect_stdout(io.StringIO()) as out,
            ):
                self.assertEqual(replay_main.main(argv), 0)
        self.assertNotIn("DM (1001)", out.getvalue())
        self.assertNotIn("  DM: ", out.getvalue())
