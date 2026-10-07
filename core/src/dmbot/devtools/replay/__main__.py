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
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from dmbot.devtools.replay import audio
from dmbot.devtools.replay import names as name_scan
from dmbot.devtools.replay.bakeoff import (
    Bakeoff,
    is_bakeoff,
    misheard,
    parse_bakeoff,
    score_bakeoff,
)
from dmbot.devtools.replay.report import heard_lines, history_entry, record, record_bakeoff
from dmbot.devtools.replay.run import replay
from dmbot.devtools.replay.score import score
from dmbot.devtools.replay.script import Script, parse_script
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


_SHA = re.compile(r"[0-9a-f]{4,40}")


def _commit_id(value: str | None) -> str | None:
    """A commit id, shortened, or None: nothing else may go in the public log."""
    if not value:
        return None
    value = value.strip().casefold()
    return value[:7] if _SHA.fullmatch(value) else None


def commit(given: str | None = None) -> str:
    """Which code was replayed, for the record: `--commit`, then GIT_COMMIT (a container
    has no git checkout to ask), then git itself. Only a commit id goes in the public log."""
    if found := _commit_id(given):
        return found
    if found := _commit_id(env := os.environ.get("GIT_COMMIT")):
        return found
    if env:
        print("replay: GIT_COMMIT isn't a commit id; ignoring it", file=sys.stderr)
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


def _milliseconds(text: str) -> int:
    """A whole number of milliseconds, 0 or more. Raises ArgumentTypeError, which argparse
    reports as a usage error before anything else runs."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("needs a whole number of milliseconds") from None
    if value < 0:
        raise argparse.ArgumentTypeError("can't be negative")
    return value


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="replay", description="Replay a recording through core and score it."
    )
    parser.add_argument("recording", type=Path, help="audio file (m4a, mp3, wav, ...)")
    parser.add_argument("--script", type=Path, required=True, help="docs/test-scripts/*.md")
    parser.add_argument("--transcriber", choices=ENGINES, help="overrides TRANSCRIBER")
    parser.add_argument("--hint", action="append", default=[], help="a name hint (repeat)")
    parser.add_argument(
        "--names",
        type=Path,
        help="a names list (Add many format, or a setup note): the names the campaign "
        "already knows, for the name scan and as hints",
    )
    parser.add_argument(
        "--no-hints",
        action="store_true",
        help="the bake-off script sends its names as hints unless this is given",
    )
    parser.add_argument(
        "--silence-db",
        type=float,
        default=None,
        help="quieter than this (dBFS) is silence (default: from the recording's own noise)",
    )
    parser.add_argument(
        "--speech-end-ms",
        type=int,
        default=audio.SPEECH_END_MS,
        help=f"this much silence ends a piece of speech (default {audio.SPEECH_END_MS})",
    )
    parser.add_argument(
        "--lead-in-ms",
        type=_milliseconds,
        default=audio.LEAD_IN_MS,
        help=f"also send this much audio before each piece (default {audio.LEAD_IN_MS})",
    )
    parser.add_argument(
        "--hangover-ms",
        type=_milliseconds,
        default=audio.HANGOVER_MS,
        help=f"quiet inside a piece kept up to this long (default {audio.HANGOVER_MS})",
    )
    parser.add_argument(
        "--realtime", action="store_true", help="send audio as it was spoken, to time the delay"
    )
    parser.add_argument("--log", action="store_true", help="append to docs/testing-history.log")
    parser.add_argument(
        "--commit", help="the commit being replayed (default: GIT_COMMIT, then git)"
    )
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
    if args.commit and _commit_id(args.commit) is None:
        print(
            "replay: --commit takes a commit id: 4 to 40 of the digits and letters a-f "
            "(see git rev-parse HEAD)",
            file=sys.stderr,
        )
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
        text = args.script.read_text(encoding="utf-8")
        script: Script | Bakeoff = (
            parse_bakeoff(text) if is_bakeoff(text) else parse_script(text, args.script.stem)
        )
        pcm = audio.decode(args.recording)
    except (OSError, ValueError, audio.DecodeError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    levels = audio.frame_levels(pcm)
    silence = args.silence_db if args.silence_db is not None else audio.silence_dbfs_for(levels)
    pieces = list(
        audio.pieces(
            pcm,
            silence_dbfs=silence,
            speech_end_ms=args.speech_end_ms,
            hangover_ms=args.hangover_ms,
            lead_in_ms=args.lead_in_ms,
            levels=levels,
        )
    )
    # As used: whole 20 ms frames, and quiet as long as speech_end_ms ends a piece.
    lead_in = args.lead_in_ms // audio.FRAME_MS * audio.FRAME_MS
    hangover = min(args.hangover_ms, args.speech_end_ms - audio.FRAME_MS)
    hangover = hangover // audio.FRAME_MS * audio.FRAME_MS
    left_out_s = sum(p.end_ms - p.start_ms - len(p.frames) * audio.FRAME_MS for p in pieces)
    cut = (
        f"{args.speech_end_ms / 1000:g} s quieter than {silence:.0f} dBFS ends a piece "
        f"(lead-in {lead_in} ms, quiet kept inside up to {hangover} ms): "
        f"{len(pieces)} pieces before core's 15 s cut, {left_out_s / 1000:.0f} s of quiet "
        "inside them left out"
    )
    try:
        known = name_scan.load_known(args.names) if args.names else name_scan.Known.none()
    except (OSError, ValueError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    hints = list(args.hint)
    if not args.no_hints and not hints:
        if args.names:
            hints = known.hints()  # what the live bot sends for these names
        elif isinstance(script, Bakeoff):
            # No campaign given: every name and rules word, as if the campaign had them.
            hints = [t.name for t in (*script.names, script.nickname, *script.rules)]
    sent_s = sum(len(piece.frames) for piece in pieces) * audio.FRAME_MS / 1000
    cost = cost_line(settings, sent_s)
    if cost:
        print(f"replay: {cost}", file=sys.stderr)
    try:
        result = await replay(
            pieces,
            build_transcriber(settings),
            hints=hints,
            realtime=args.realtime,
            outside=settings.sends_audio_out,
            end_delay_ms=args.speech_end_ms,
        )
    except TranscriberUnavailable as exc:
        print(f"replay: the speech-to-text engine can't start: {exc}", file=sys.stderr)
        return 2
    heard = [h.text or "" for h in result.heard]
    details: list[str] = []
    if isinstance(script, Bakeoff):
        names = score_bakeoff(script, heard)
        details = misheard(names)
        lines = record_bakeoff(
            name=args.script.stem,
            recording=public_name(args.recording),
            engine=describe(settings),
            commit=commit(args.commit),
            result=result,
            score=names,
            cut=cut,
        )
    else:
        lines = record(
            script=script,
            recording=public_name(args.recording),
            engine=describe(settings),
            commit=commit(args.commit),
            result=result,
            score=score(script, heard),
            cut=cut,
        )
    if cost:
        lines.insert(2, cost)
    scan_story = isinstance(script, Bakeoff) or args.script.stem == "bakeoff-story"
    if args.names and not scan_story:
        details.append("name scan: scored only for stt-bakeoff.md and bakeoff-story.md")
    elif args.names:
        # Only with a campaign's names: the live bot never hints names it doesn't know,
        # so hinting every story name and then counting them found would flatter it.
        timed = [(h.end_ms / 1000, h.text) for h in result.heard if h.text]
        story = name_scan.story_names()
        perfect = (
            [script.lines[n] for n in sorted(script.lines)]
            if isinstance(script, Bakeoff)
            else [" ".join(w.text for w in script.words)]
        )
        cleaned_lines = name_scan.clean_lines(known, timed)
        unlimited = name_scan.score_scan(cleaned_lines, known, story, limit=False)
        lines += name_scan.scan_record(
            script=name_scan.score_scan(perfect, known, story),
            as_heard=name_scan.score_scan([t for _, t in timed], known, story),
            cleaned=name_scan.score_scan(cleaned_lines, known, story),
            unlimited=unlimited,
            script_unlimited=name_scan.score_scan(perfect, known, story, limit=False),
            known=known.count,
        )
        details += ["name scan, cleaned, without the limit:"]
        details += [f"  {line}" for line in name_scan.scan_details(unlimited)]
    print("\n".join(lines))
    if details:
        print("\non screen only, never logged:")
        print("\n".join(f"  {line}" for line in details))
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
