"""Replay a recording through core and score it against its script (#299).

    python -m dmbot.devtools.replay RECORDING --script SCRIPT.md [--transcriber ENGINE]
    python -m dmbot.devtools.replay --speakers DM.m4a:1001,PLAYER.m4a:1002 --script ...

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

from dmbot.devtools.replay import audio, voices
from dmbot.devtools.replay import names as name_scan
from dmbot.devtools.replay.bakeoff import (
    Bakeoff,
    is_bakeoff,
    misheard,
    parse_bakeoff,
    score_bakeoff,
)
from dmbot.devtools.replay.report import (
    heard_lines,
    history_entry,
    record,
    record_bakeoff,
    speaker_lines,
)
from dmbot.devtools.replay.run import ConsentChange, replay
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


def _speakers(text: str) -> list[tuple[Path, int]]:
    """FILE:1001,FILE:1002: the DM's file (1001) and the player's (1002)."""
    out: list[tuple[Path, int]] = []
    for item in text.split(","):
        path, _, speaker = item.rpartition(":")
        if not path or not speaker.isdecimal() or int(speaker) not in voices.ROLES:
            raise argparse.ArgumentTypeError(
                f"needs FILE:{audio.TWIN_SPEAKER} (the DM's) and FILE:{audio.TWIN_PLAYER} "
                "(the player's), with a comma between"
            )
        out.append((Path(path), int(speaker)))
    if sorted(s for _, s in out) != sorted(voices.ROLES):
        raise argparse.ArgumentTypeError(
            f"needs one file for {audio.TWIN_SPEAKER} and one for {audio.TWIN_PLAYER}"
        )
    return out


_AT = re.compile(r"^(\d+)@(?:(\d+):)?(\d+(?:\.\d+)?)$")


def _at(text: str) -> tuple[int, int]:
    """USER_ID@M:SS (or @SS): who, and when in the replay, in milliseconds."""
    match = _AT.match(text)
    if match is None or int(match.group(1)) not in voices.ROLES:
        raise argparse.ArgumentTypeError(
            f"needs a speaker and a time, like {audio.TWIN_PLAYER}@0:25"
        )
    minutes = int(match.group(2) or 0)
    if match.group(2) is not None and float(match.group(3)) >= 60:
        raise argparse.ArgumentTypeError("seconds go up to 59 (1:15, not 0:75)")
    return int(match.group(1)), round((minutes * 60 + float(match.group(3))) * 1000)


def _signed_ms(text: str) -> int:
    """A whole number of milliseconds, negative allowed (--answer-ms talks over)."""
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("needs a whole number of milliseconds") from None


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="replay", description="Replay a recording through core and score it."
    )
    parser.add_argument("recording", type=Path, nargs="?", help="audio file (m4a, mp3, wav, ...)")
    parser.add_argument(
        "--speakers",
        type=_speakers,
        help=f"two voices recorded alone instead of one recording: DM.m4a:"
        f"{audio.TWIN_SPEAKER},PLAYER.m4a:{audio.TWIN_PLAYER} (two-voices.md)",
    )
    parser.add_argument(
        "--stop",
        type=_at,
        action="append",
        default=[],
        help=f"a speaker presses Stop recording me then, like {audio.TWIN_PLAYER}@0:25",
    )
    parser.add_argument(
        "--agree",
        type=_at,
        action="append",
        default=[],
        help="a speaker agrees to be recorded only then (the first-time question)",
    )
    parser.add_argument(
        "--turn-quiet-ms",
        type=_milliseconds,
        default=voices.TURN_QUIET_MS,
        help=f"with --speakers: quiet this long ends a turn (default {voices.TURN_QUIET_MS})",
    )
    parser.add_argument(
        "--answer-ms",
        type=_signed_ms,
        default=voices.ANSWER_MS,
        help="with --speakers: the next turn starts this long after one ends; negative "
        f"talks over its end (default {voices.ANSWER_MS})",
    )
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
    args = parser.parse_args(argv)
    if (args.recording is None) == (args.speakers is None):
        parser.error("give one recording, or --speakers with two")
    known = {s for _, s in args.speakers} if args.speakers else {audio.TWIN_SPEAKER}
    changed = [speaker for speaker, _ in (*args.stop, *args.agree)]
    for speaker in changed:
        if speaker not in known:
            parser.error(f"--stop and --agree: {speaker} isn't speaking in this replay")
    if len(changed) != len(set(changed)):
        parser.error("--stop and --agree: one change per speaker")
    return args


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
        files = args.speakers or [(args.recording, audio.TWIN_SPEAKER)]
    except (OSError, ValueError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    if isinstance(script, Bakeoff) and (args.stop or args.agree):
        print("replay: --stop and --agree need a [DM]/[Player] script", file=sys.stderr)
        return 2
    if args.speakers and (isinstance(script, Bakeoff) or set(script.order) != set(voices.SPEAKERS)):
        print("replay: --speakers needs a script with [DM] and [Player] lines", file=sys.stderr)
        return 2
    by_role: dict[str, list[audio.Piece]] = {}
    silences: list[str] = []
    recording_s = 0.0
    for path, speaker in files:
        # One file at a time: a long recording is big, and only its pieces are kept.
        try:
            pcm = audio.decode(path)
        except (OSError, audio.DecodeError) as exc:
            print(f"replay: {exc}", file=sys.stderr)
            return 2
        recording_s = audio.seconds(pcm)
        levels = audio.frame_levels(pcm)
        silence = args.silence_db if args.silence_db is not None else audio.silence_dbfs_for(levels)
        silences.append(f"{silence:.0f} dBFS")
        by_role[voices.ROLES[speaker]] = list(
            audio.pieces(
                pcm,
                silence_dbfs=silence,
                speech_end_ms=args.speech_end_ms,
                hangover_ms=args.hangover_ms,
                lead_in_ms=args.lead_in_ms,
                levels=levels,
            )
        )
        del pcm, levels
    lone = 0
    if args.speakers:
        assert isinstance(script, Script)
        file_names = {voices.ROLES[s]: public_name(path) for path, s in files}
        try:
            pieces = voices.mix(
                script.order,
                by_role,
                file_names,
                turn_quiet_ms=args.turn_quiet_ms,
                answer_ms=args.answer_ms,
                speech_end_ms=args.speech_end_ms,
            )
        except ValueError as exc:
            print(f"replay: {exc}", file=sys.stderr)
            return 2
        lone = sum(voices.lone_sounds(p, args.turn_quiet_ms) for p in by_role.values())
        recording_s = max((p.end_ms for p in pieces), default=0) / 1000
    else:
        pieces = by_role["DM"]
    recording_name = " + ".join(public_name(path) for path, _ in files)
    del by_role
    changes = [ConsentChange(at, speaker, False) for speaker, at in args.stop]
    changes += [ConsentChange(at, speaker, True) for speaker, at in args.agree]
    # As used: whole 20 ms frames, and quiet as long as speech_end_ms ends a piece.
    lead_in = args.lead_in_ms // audio.FRAME_MS * audio.FRAME_MS
    hangover = min(args.hangover_ms, args.speech_end_ms - audio.FRAME_MS)
    hangover = hangover // audio.FRAME_MS * audio.FRAME_MS
    left_out_s = sum(p.end_ms - p.start_ms - len(p.frames) * audio.FRAME_MS for p in pieces)
    cut = (
        f"{args.speech_end_ms / 1000:g} s quieter than {' / '.join(silences)} ends a piece "
        f"(lead-in {lead_in} ms, quiet kept inside up to {hangover} ms): "
        f"{len(pieces)} pieces before core's 15 s cut, {left_out_s / 1000:.0f} s of quiet "
        "inside them left out"
    )
    if args.speakers:
        cut += (
            f"; two voices, a turn ends at {args.turn_quiet_ms / 1000:g} s of quiet and the "
            f"next starts {args.answer_ms} ms later"
        )
        if lone:
            alone = "short sound on its own" if lone == 1 else "short sounds on their own"
            cut += f"; {lone} {alone} left out"
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
            changes=changes,
        )
    except TranscriberUnavailable as exc:
        print(f"replay: the speech-to-text engine can't start: {exc}", file=sys.stderr)
        return 2
    heard = [h.text or "" for h in result.heard]
    if isinstance(script, Script) and (args.speakers or changes):
        speakers = speaker_lines(script, result)
    else:
        speakers = []
    details: list[str] = []
    if isinstance(script, Bakeoff):
        names = score_bakeoff(script, heard)
        details = misheard(names)
        lines = record_bakeoff(
            name=args.script.stem,
            recording=recording_name,
            engine=describe(settings),
            commit=commit(args.commit),
            result=result,
            score=names,
            cut=cut,
            recording_s=recording_s,
        )
    else:
        lines = record(
            script=script,
            recording=recording_name,
            engine=describe(settings),
            commit=commit(args.commit),
            result=result,
            score=score(script, heard),
            cut=cut,
            recording_s=recording_s,
        )
    lines += speakers
    if cost:
        # What was sent, now it's known: the estimate above also counted pieces too short
        # to send.
        lines.insert(2, cost_line(settings, result.sent_s) or cost)
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
        title = f"{args.script.name}, {recording_name}, {describe(settings)}"
        with args.history.open("a", encoding="utf-8") as history:
            history.write(history_entry(lines, title=title))
        print(f"\nAppended to {args.history}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
