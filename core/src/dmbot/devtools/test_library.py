"""The library of kept live tests: replay every one and say what changed (#1020).

    python -m dmbot.devtools.test_library run [--dir FOLDER] [--transcriber ENGINE] [--log]

Replays every kept, complete case in the recordings folder (default
DMBOT_TEST_RECORDINGS_DIR, else /var/lib/dmbot/test-recordings) one after another at low
priority, prints one line per case (same, better, worse or changed) and the total cost:
speech-to-text minutes at the twin's price list. The replay makes no AI calls, so it uses no
AI tokens. `--log` appends a "Library run" entry to docs/testing-history.log with case names
and counts only: transcript text is players' words and the repo is public.

Never runs inside the bot's container or during a live session, reads only the recordings
folder, and refuses a case whose manifest holds a Discord-id-shaped number. Keeping and
listing cases is the recorder's side (#1019).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from dmbot.devtools.common import HISTORY, commit, history_entry
from dmbot.devtools.costs import model
from dmbot.devtools.session_replay import (
    Diff,
    Session,
    SessionError,
    compare,
    load,
    refuse_in_bot,
    run,
    summary,
    today_lines,
)

DEFAULT_DIR = Path("/var/lib/dmbot/test-recordings")
NICE = 10
# Only a plain name goes in the public log: it is typed by hand, and a stray one could be
# a person's name.
_PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,59}")


def recordings_dir(given: Path | None) -> Path:
    return given or Path(os.environ.get("DMBOT_TEST_RECORDINGS_DIR") or DEFAULT_DIR)


def cases(folder: Path) -> list[Path]:
    """The session folders, oldest first (their names start with the date)."""
    return sorted(p for p in folder.iterdir() if p.is_dir() and (p / "session.json").is_file())


def public_name(session: Session) -> str:
    return session.name if _PLAIN_NAME.fullmatch(session.name) else "(unnamed case)"


def dollars_line(speech_s: float, sends_out: bool) -> str:
    minutes = speech_s / 60
    cost = model.stt_dollars(minutes) if sends_out else 0.0
    return f"speech-to-text {minutes:.2f} min (${cost:.3f}); AI tokens 0 (the replay calls no AI)"


async def main_async(args: argparse.Namespace) -> int:
    from dmbot.transcription.base import TranscriberUnavailable
    from dmbot.transcription.config import TranscriptionConfigError, load_transcription_settings
    from dmbot.transcription.factory import build_transcriber

    if why := refuse_in_bot():
        print(f"test_library: {why}", file=sys.stderr)
        return 2
    env = dict(os.environ)
    if args.transcriber:
        env["TRANSCRIBER"] = args.transcriber
    try:
        settings = load_transcription_settings(env)
    except TranscriptionConfigError as exc:
        print(f"test_library: {exc}", file=sys.stderr)
        return 2
    if settings.sends_audio_out and args.transcriber != settings.engine:
        print(
            f"test_library: TRANSCRIBER={settings.engine} sends audio to an outside company "
            f"and costs money; say so with --transcriber {settings.engine}",
            file=sys.stderr,
        )
        return 2
    folder = recordings_dir(args.dir)
    if not folder.is_dir():
        print(f"test_library: no recordings folder at {folder}", file=sys.stderr)
        return 2
    if args.log and not args.history.parent.is_dir():
        print(f"test_library: no {args.history.parent} here; give --history", file=sys.stderr)
        return 2
    with_nice(NICE)
    lines: list[str] = []
    verdicts: dict[str, int] = {}
    speech_s = 0.0
    for path in cases(folder):
        try:
            session = load(path)
        except SessionError as exc:
            lines.append(f"{path.name}: refused ({exc})")
            continue
        if not session.kept:
            continue  # unkept sessions are not part of the library
        label = public_name(session)
        if not session.complete:
            lines.append(f"{label}: skipped (incomplete)")
            continue
        try:
            result = await run(
                session,
                build_transcriber(settings),
                realtime=not args.no_timing,
                outside=settings.sends_audio_out,
            )
        except (TranscriberUnavailable, OSError) as exc:
            lines.append(f"{label}: could not run ({exc})")
            continue
        speech_s += result.sent_s
        diff: Diff = compare(session.produced, today_lines(result), result.alerts, session.expected)
        verdicts[diff.verdict] = verdicts.get(diff.verdict, 0) + 1
        lines.append(f"{label}: {summary(diff)}")
    total = ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items())) or "no cases run"
    lines.append(f"total: {total}")
    lines.append(dollars_line(speech_s, settings.sends_audio_out))
    print("\n".join(lines))
    if args.log:
        header = [f"commit: {commit(args.commit, tool='test_library')}   engine: {settings.engine}"]
        entry = history_entry(header + lines, title="saved live tests", kind="Library run")
        with args.history.open("a", encoding="utf-8") as history:
            history.write(entry)
        print(f"\nAppended to {args.history}")
    return 0


def with_nice(increment: int) -> None:
    with contextlib.suppress(AttributeError, OSError):  # else: normal priority
        os.nice(increment)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="test_library", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="replay every kept, complete case")
    runner.add_argument("--dir", type=Path, help="the recordings folder")
    runner.add_argument("--transcriber", help="overrides TRANSCRIBER")
    runner.add_argument("--no-timing", action="store_true", help="queue at once, not in real time")
    runner.add_argument("--log", action="store_true", help="append to docs/testing-history.log")
    runner.add_argument("--commit", help="the commit being replayed (default: GIT_COMMIT, git)")
    runner.add_argument("--history", type=Path, default=HISTORY, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
