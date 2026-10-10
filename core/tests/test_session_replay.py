"""Replaying saved live tests and the library run (#1020): hand-made fixtures in the
saved format (tone-only FLAC files, made-up speakers), a scripted engine, no network."""

import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import av

from dmbot.devtools import session_replay as sr
from dmbot.devtools import test_library as lib
from dmbot.ears.protocol import SAMPLE_RATE
from tests.test_replay import ScriptedTranscriber, tone


def write_flac(path: Path, pcm: bytes) -> None:
    """A mono 16 kHz FLAC file holding `pcm` (s16le)."""
    with av.open(str(path), "w", format="flac") as out:
        stream = out.add_stream("flac", rate=SAMPLE_RATE)
        stream.layout = "mono"
        frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
        frame.sample_rate = SAMPLE_RATE
        frame.time_base = Fraction(1, SAMPLE_RATE)
        frame.planes[0].update(pcm)
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


def make_case(
    root: Path,
    name: str,
    *,
    utterances: tuple[tuple[int, int, int], ...] = ((1001, 0, 700), (1002, 400, 700)),
    consent: tuple[dict[str, object], ...] = (),
    transcript: tuple[tuple[int, int, str], ...] = ((1001, 0, "hello there"), (1002, 400, "hi")),
    alerts: tuple[str, ...] = (),
    kept: dict[str, object] | None = None,
    manifest_extra: dict[str, object] | None = None,
    skip_files: tuple[str, ...] = (),
) -> Path:
    folder = root / name
    folder.mkdir()
    files = []
    for i, (speaker, start, length) in enumerate(utterances):
        file = f"u{i:04d}.flac"
        if file not in skip_files:
            write_flac(folder / file, tone(length))
        files.append(
            {"speaker": speaker, "start_ms": start, "end_ms": start + length, "file": file}
        )
    manifest: dict[str, object] = {
        "version": 1,
        "speakers": {"1001": "DM", "1002": "player"},
        "utterances": files,
        "consent": list(consent),
        "produced": {
            "transcript": [{"speaker": s, "start_ms": t, "text": x} for s, t, x in transcript],
            "cards": ["Fireball card"],
            "sidebar": [],
            "alerts": list(alerts),
        },
        "commit": "abc1234",
        "settings": {},
    }
    manifest.update(manifest_extra or {})
    (folder / "session.json").write_text(json.dumps(manifest), encoding="utf-8")
    if kept is not None:
        (folder / "kept.json").write_text(json.dumps(kept), encoding="utf-8")
    return folder


def run_session(folder: Path, lines: list[str], *, realtime: bool = False) -> sr.Diff:
    session = sr.load(folder)
    engine = ScriptedTranscriber(lines)
    result = asyncio.run(sr.run(session, engine, realtime=realtime))
    return sr.compare(session.produced, sr.today_lines(result), result.alerts, session.expected)


class Replaying(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_each_utterance_keeps_its_recorded_time_and_overlaps(self) -> None:
        folder = make_case(self.root, "2026-10-10-0100-aaaa")
        session = sr.load(folder)
        engine = ScriptedTranscriber(["hello there", "hi"])
        result = asyncio.run(sr.run(session, engine, realtime=False))
        starts = {h.speaker: (h.start_ms, h.end_ms) for h in result.heard}
        self.assertEqual(starts[1001][0], 0)
        self.assertEqual(starts[1002][0], 400)
        self.assertLess(starts[1002][0], starts[1001][1])  # the overlap is still an overlap

    def test_real_time_replay_waits_for_the_recorded_times(self) -> None:
        folder = make_case(self.root, "2026-10-10-0101-bbbb", utterances=((1001, 600, 400),))
        session = sr.load(folder)
        result = asyncio.run(sr.run(session, ScriptedTranscriber(["x"]), realtime=True))
        self.assertGreaterEqual(result.took_s, 0.6)  # nothing is sent before its time
        self.assertEqual(result.heard[0].start_ms, 600)

    def test_a_consent_stop_is_kept(self) -> None:
        folder = make_case(
            self.root,
            "2026-10-10-0102-cccc",
            utterances=((1001, 0, 700), (1002, 0, 700), (1002, 2500, 700)),
            consent=({"at_ms": 1800, "speaker": 1002, "agrees": False},),
            transcript=((1001, 0, "a"), (1002, 0, "b"), (1002, 2500, "c")),
        )
        diff = run_session(folder, ["a", "b", "c"])
        self.assertEqual([x.text for x in diff.lost], ["c"])  # nothing after the stop

    def test_the_diff_counts_same_changed_lost_and_added(self) -> None:
        folder = make_case(
            self.root,
            "2026-10-10-0103-dddd",
            utterances=((1001, 0, 700),),
            transcript=((1001, 0, "the red door"), (1001, 9000, "gone line")),
            alerts=("old alert",),
        )
        diff = run_session(folder, ["the blue door"])
        self.assertEqual(diff.same, 0)
        self.assertEqual(
            [(c.before, c.after) for c in diff.changed], [("the red door", "the blue door")]
        )
        self.assertEqual([x.text for x in diff.lost], ["gone line"])
        self.assertEqual(diff.alerts_lost, ["old alert"])
        self.assertEqual(diff.verdict, "changed")
        self.assertIn("alert lost", "\n".join(sr.report(diff, sr.load(folder))))
        text = "\n".join(sr.report(diff, sr.load(folder)))
        self.assertIn("then: the red door", text)
        self.assertIn("alert lost (rules alerts are not replayed): old alert", text)
        self.assertIn("not compared", text)

    def test_same_when_nothing_changed(self) -> None:
        folder = make_case(self.root, "2026-10-10-0104-eeee")
        diff = run_session(folder, ["Hello there!", "hi"])  # case and punctuation don't count
        self.assertEqual((diff.same, diff.verdict), (2, "same"))

    def test_better_and_worse_are_judged_against_the_kept_expected_lines(self) -> None:
        expected = {
            "expected": {"transcript": [{"speaker": 1001, "start_ms": 0, "text": "the red door"}]}
        }
        for said, verdict in (("the red door", "better"), ("the bed floor", "worse")):
            with self.subTest(said):
                folder = make_case(
                    self.root,
                    f"2026-10-10-0105-{verdict}",
                    utterances=((1001, 0, 700),),
                    transcript=(
                        (1001, 0, "the red door" if verdict == "worse" else "the bed door"),
                    ),
                    kept={"name": "case", **expected},
                )
                self.assertEqual(run_session(folder, [said]).verdict, verdict)


class Guards(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_manifest_with_a_discord_id_is_refused(self) -> None:
        folder = make_case(
            self.root,
            "2026-10-10-0200-ffff",
            manifest_extra={"settings": {"channel": "123456789012345678"}},
        )
        with self.assertRaisesRegex(sr.SessionError, "Discord id"):
            sr.load(folder)

    def test_made_up_ids_and_timestamps_pass_the_guard(self) -> None:
        sr.load(make_case(self.root, "2026-10-10-0201-gggg"))  # 1001, 1002, ms values

    def test_odd_manifests_are_a_session_error_not_a_crash(self) -> None:
        for i, text in enumerate(("[]", '{"version": 1, "speakers": []}', "not json")):
            with self.subTest(text):
                folder = self.root / f"odd-{i}"
                folder.mkdir()
                (folder / "session.json").write_text(text, encoding="utf-8")
                with self.assertRaises(sr.SessionError):
                    sr.load(folder)
        folder = make_case(self.root, "2026-10-10-0203-iiii")
        (folder / "kept.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(sr.SessionError):
            sr.load(folder)

    def test_a_file_name_cannot_leave_the_folder(self) -> None:
        folder = make_case(self.root, "2026-10-10-0202-hhhh")
        path = folder / "session.json"
        path.write_text(path.read_text().replace("u0000.flac", "../x.flac"), encoding="utf-8")
        with self.assertRaisesRegex(sr.SessionError, "plain file name"):
            sr.load(folder)

    def test_it_will_not_run_where_the_bot_runs(self) -> None:
        self.assertIsNotNone(sr.refuse_in_bot({"DISCORD_TOKEN": "x"}))
        self.assertIsNone(sr.refuse_in_bot({}))
        err = io.StringIO()
        with (
            patch.dict(os.environ, {"DISCORD_TOKEN": "x"}),
            contextlib.redirect_stderr(err),
        ):
            code = sr.main(["--session", str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("bot's own container", err.getvalue())


class Library(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "recordings"
        self.root.mkdir()
        self.history = Path(self._tmp.name) / "history.log"
        self.addCleanup(self._tmp.cleanup)

    def run_library(self, *extra: str) -> tuple[int, str]:
        env = {k: v for k, v in os.environ.items() if k != "DISCORD_TOKEN"}
        env["TRANSCRIBER"] = "none"
        out = io.StringIO()
        fake = ScriptedTranscriber(["hello there", "hi", "hello there", "hi", "hello there", "hi"])
        with (
            patch.dict(os.environ, env, clear=True),
            patch("dmbot.transcription.factory.build_transcriber", lambda settings: fake),
            contextlib.redirect_stdout(out),
        ):
            code = lib.main(
                [
                    "run",
                    "--dir",
                    str(self.root),
                    "--no-timing",
                    "--history",
                    str(self.history),
                    *extra,
                ]
            )
        return code, out.getvalue()

    def test_one_line_per_kept_case_and_a_total(self) -> None:
        make_case(self.root, "2026-10-10-0300-aaaa", kept={"name": "two-speaker-live"})
        make_case(self.root, "2026-10-10-0301-bbbb")  # not kept: not in the library
        code, out = self.run_library()
        self.assertEqual(code, 0)
        self.assertIn("two-speaker-live [2026-10-10-0300-aaaa]: same: 2 same", out)
        self.assertNotIn("0301", out)
        self.assertIn("total: 1 same", out)
        self.assertIn("speech-to-text", out)

    def test_an_incomplete_case_is_skipped(self) -> None:
        make_case(
            self.root, "2026-10-10-0302-cccc", kept={"name": "lost-a-speaker", "incomplete": True}
        )
        make_case(
            self.root,
            "2026-10-10-0303-dddd",
            kept={"name": "file-gone"},
            skip_files=("u0001.flac",),
        )
        _, out = self.run_library()
        self.assertIn("lost-a-speaker [2026-10-10-0302-cccc]: skipped, marked incomplete", out)
        self.assertIn("file-gone [2026-10-10-0303-dddd]: skipped, 1 audio files are missing", out)
        self.assertIn("no cases run", out)

    def test_a_case_with_a_discord_id_is_refused_and_the_rest_still_run(self) -> None:
        make_case(
            self.root,
            "2026-10-10-0304-eeee",
            kept={"name": "bad"},
            manifest_extra={"note": "999999999999999999"},
        )
        make_case(self.root, "2026-10-10-0305-ffff", kept={"name": "good"})
        _, out = self.run_library()
        self.assertIn("2026-10-10-0304-eeee: refused", out)
        self.assertIn("good [2026-10-10-0305-ffff]: same", out)

    def test_the_history_entry_has_names_and_counts_never_transcript_text(self) -> None:
        make_case(self.root, "2026-10-10-0306-gggg", kept={"name": "two-speaker-live"})
        self.history.write_text("", encoding="utf-8")
        code, _ = self.run_library("--log", "--commit", "abc1234")
        self.assertEqual(code, 0)
        logged = self.history.read_text(encoding="utf-8")
        self.assertIn("Library run: saved live tests", logged)
        self.assertIn("two-speaker-live: same: 2 same", logged)
        for words in ("hello there", "Fireball card"):
            self.assertNotIn(words, logged)

    def test_the_log_never_holds_a_folder_name_or_an_error_text(self) -> None:
        make_case(
            self.root,
            "player-name-folder",
            kept={"name": "bad"},
            manifest_extra={"n": "999999999999999999"},
        )
        make_case(
            self.root, "2026-10-10-0307-hhhh", kept={"name": "gone"}, skip_files=("u0000.flac",)
        )
        make_case(self.root, "2026-10-10-0308-iiii", kept={"name": "corrupt"})
        (self.root / "2026-10-10-0308-iiii" / "u0000.flac").write_bytes(b"not audio")
        self.history.write_text("", encoding="utf-8")
        code, out = self.run_library("--log")
        self.assertEqual(code, 1)  # a refused or unrunnable case is noticed by a script
        self.assertIn("player-name-folder: refused", out)  # on screen, for dev1
        logged = self.history.read_text(encoding="utf-8")
        self.assertNotIn("player-name-folder", logged)
        self.assertNotIn("2026-10-10-0308", logged)
        self.assertIn("corrupt: could not run", logged)
        self.assertIn("a saved session was refused", logged)

    def test_a_hand_typed_name_that_is_not_plain_stays_out_of_the_log(self) -> None:
        session = sr.Session(
            Path("."), {}, (), (), sr.Produced(), name="Aleksandra's table", kept=True
        )
        self.assertEqual(lib.public_name(session), "(unnamed case)")

    def test_it_stays_out_of_an_outside_engine_unless_asked(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "DISCORD_TOKEN"}
        env.update({"TRANSCRIBER": "deepgram", "DEEPGRAM_API_KEY": "k"})
        err = io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(err):
            code = lib.main(["run", "--dir", str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("costs money", err.getvalue())


if __name__ == "__main__":
    unittest.main()
