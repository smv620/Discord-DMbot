"""Replay a recording through core and score it against its script (#299).

    python -m dmbot.devtools.replay RECORDING --script SCRIPT.md [--transcriber ENGINE]

The engine and its settings come from the environment, as for the bot (TRANSCRIBER,
DEEPGRAM_API_KEY, ...); --transcriber overrides TRANSCRIBER. Prints the scored record;
--log also appends it to docs/testing-history.log as a "Twin run".
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from dmbot.devtools.replay import audio
from dmbot.devtools.replay.report import heard_lines, history_entry, record
from dmbot.devtools.replay.run import replay
from dmbot.devtools.replay.score import score
from dmbot.devtools.replay.script import load_script
from dmbot.transcription.base import TranscriberUnavailable
from dmbot.transcription.config import (
    ENGINES,
    TranscriptionConfigError,
    TranscriptionSettings,
    load_transcription_settings,
)
from dmbot.transcription.factory import build_transcriber

# Only from a checkout (pip install -e): an installed copy has no docs/ next to it.
REPO = Path(__file__).resolve().parents[5]
HISTORY = REPO / "docs" / "testing-history.log"
TEST_SCRIPTS = REPO / "docs" / "test-scripts"


# List prices per minute of audio, to tell whoever runs a replay roughly what it costs.
# Check the company's current prices before relying on them.
PRICE_PER_MINUTE = {"deepgram": 0.0043, "cloud": 0.006}


def cost_line(settings: TranscriptionSettings, seconds: float) -> str | None:
    """What an outside engine is sent, and about what it costs; None for local engines."""
    if not settings.sends_audio_out:
        return None
    company = settings.company or settings.engine
    model = settings.deepgram_model if settings.engine == "deepgram" else settings.cloud_model
    dollars = seconds / 60 * PRICE_PER_MINUTE.get(settings.engine, 0.0)
    return (
        f"sends {seconds:.0f} s of audio to {company} (about ${dollars:.3f} at {model} list prices)"
    )


def describe(settings: TranscriptionSettings) -> str:
    if settings.engine == "deepgram":
        return f"deepgram {settings.deepgram_model}"
    if settings.engine == "cloud":
        return f"cloud {settings.cloud_model}"
    if settings.engine == "whisper-local":
        return (
            f"whisper-local {settings.whisper_model} (device {settings.whisper_device}, "
            f"compute {settings.whisper_compute_type}, beam {settings.whisper_beam_size})"
        )
    return "none (no text)"


def commit() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def public_name(recording: Path) -> str:
    """The recording's name for the public log: only the repo's own test recordings are
    named, since a file of someone's own could be named after them."""
    try:
        recording.resolve().relative_to(TEST_SCRIPTS)
    except ValueError:
        return "a recording"
    return recording.name


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="replay", description="Replay a recording through core and score it."
    )
    parser.add_argument("recording", type=Path, help="audio file (m4a, mp3, wav, ...)")
    parser.add_argument("--script", type=Path, required=True, help="docs/test-scripts/*.md")
    parser.add_argument("--transcriber", choices=ENGINES, help="overrides TRANSCRIBER")
    parser.add_argument("--hint", action="append", default=[], help="a name hint (repeat)")
    parser.add_argument(
        "--silence-db",
        type=float,
        default=audio.SILENCE_DBFS,
        help=f"quieter than this (dBFS) is silence (default {audio.SILENCE_DBFS:g})",
    )
    parser.add_argument(
        "--speech-end-ms",
        type=int,
        default=audio.SPEECH_END_MS,
        help=f"this much silence ends a piece of speech (default {audio.SPEECH_END_MS})",
    )
    parser.add_argument(
        "--realtime", action="store_true", help="send audio as it was spoken, to time the delay"
    )
    parser.add_argument("--log", action="store_true", help="append to docs/testing-history.log")
    parser.add_argument("--history", type=Path, default=HISTORY, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    if args.transcriber:
        env["TRANSCRIBER"] = args.transcriber
    try:
        settings = load_transcription_settings(env)
    except TranscriptionConfigError as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    if settings.sends_audio_out and args.transcriber != settings.engine:
        # TRANSCRIBER in the environment (in the bot's container, say) must not send
        # audio out and run up a bill without being asked.
        print(
            f"replay: TRANSCRIBER={settings.engine} sends audio to an outside company and "
            f"costs money; say so with --transcriber {settings.engine}",
            file=sys.stderr,
        )
        return 2
    if args.log and settings.engine == "none":
        print("replay: --log needs an engine that writes text (not none)", file=sys.stderr)
        return 2
    if args.log and not args.history.parent.is_dir():
        print(f"replay: no {args.history.parent} here; give --history", file=sys.stderr)
        return 2
    try:
        script = load_script(args.script)
        pcm = audio.decode(args.recording)
    except (OSError, ValueError, audio.DecodeError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    pieces = list(audio.pieces(pcm, silence_dbfs=args.silence_db, speech_end_ms=args.speech_end_ms))
    sent_s = sum(len(piece.frames) for piece in pieces) * audio.FRAME_MS / 1000
    cost = cost_line(settings, sent_s)
    if cost:
        print(f"replay: {cost}", file=sys.stderr)
    try:
        result = await replay(
            pieces,
            build_transcriber(settings),
            hints=args.hint,
            realtime=args.realtime,
            outside=settings.sends_audio_out,
            end_delay_ms=args.speech_end_ms,
        )
    except TranscriberUnavailable as exc:
        print(f"replay: the speech-to-text engine can't start: {exc}", file=sys.stderr)
        return 2
    scored = score(script, [h.text or "" for h in result.heard])
    lines = record(
        script=script,
        recording=public_name(args.recording),
        engine=describe(settings),
        commit=commit(),
        result=result,
        score=scored,
    )
    if cost:
        lines.insert(2, cost)
    print("\n".join(lines))
    print("\nheard:")
    print("\n".join(f"  {line}" for line in heard_lines(result)))
    for text in result.alerts:
        print(f"\nDM screen: {text}")
    if result.alerts and not args.realtime:
        print("(Without --realtime every piece is queued at once, so falling-behind")
        print("warnings are the replay's doing, not the engine's.)")
    if args.log:
        title = f"{args.script.name}, {public_name(args.recording)}, {describe(settings)}"
        with args.history.open("a", encoding="utf-8") as history:
            history.write(history_entry(lines, title=title))
        print(f"\nAppended to {args.history}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
