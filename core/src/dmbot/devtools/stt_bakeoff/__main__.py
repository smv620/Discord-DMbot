"""Command line for the speech-to-text bake-off. See docs/STT_BAKEOFF.md.

python -m dmbot.devtools.stt_bakeoff split  --audio DIR --out DIR
python -m dmbot.devtools.stt_bakeoff check
python -m dmbot.devtools.stt_bakeoff run    --out DIR [--phase all] [--setups ...]
python -m dmbot.devtools.stt_bakeoff report --out DIR
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from dmbot.devtools.stt_bakeoff import data

AUDIO_SUFFIXES = {".wav", ".m4a", ".mp3", ".ogg", ".flac", ".aac", ".webm"}


def inside_repo(path: Path) -> bool:
    """True if `path` is inside a git checkout (recordings must never land in the repo)."""
    full = path.resolve()
    return any((p / ".git").exists() for p in [full, *full.parents])


def _refuse_repo_paths(*paths: Path) -> bool:
    for p in paths:
        if inside_repo(p):
            print(
                f"{p} is inside a git checkout. Keep recordings and results outside the "
                "repo (the repo is public), e.g. ~/bakeoff-audio and ~/bakeoff-results."
            )
            return True
    return False


def cmd_split(args: argparse.Namespace) -> int:
    from dmbot.devtools.stt_bakeoff import audio

    out: Path = args.out
    if _refuse_repo_paths(Path(args.audio), out):
        return 1
    manifest: dict[str, dict[str, object]] = {}
    ok = True
    files = sorted(p for p in Path(args.audio).iterdir() if p.suffix.lower() in AUDIO_SUFFIXES)
    if not files:
        print(f"No recordings found in {args.audio}.", file=sys.stderr)
        return 1
    mapping = json.loads(Path(args.map).read_text()) if args.map else {}
    for path in files:
        reader = path.stem
        clean = audio.decode(path, audio.RATE)
        discord = audio.discord_like(audio.decode(path, audio.DISCORD_RATE))
        if discord is None and not args.allow_clean:
            print(
                "This PyAV can't encode Opus, so Discord-like audio can't be made. Use "
                "--allow-clean to go on with clean audio only (the report will show it)."
            )
            return 1
        segments = audio.split(clean, min_silence_s=args.min_silence)
        lines = mapping.get(reader) or list(range(1, len(data.LINES) + 1))
        print(f"{reader}: {len(segments)} pieces of speech, {len(data.LINES)} script lines")
        if len(segments) != len(lines):
            ok = False
            for i, seg in enumerate(segments):
                at = seg.start / audio.RATE
                print(f"  piece {i + 1}: at {at:7.1f} s, {seg.seconds():.1f} s long")
            print(
                "  Counts differ. Try a different --min-silence, or give --map a JSON file "
                'like {"reader-a": [1, 2, null, 3, ...]} (null skips a piece).'
            )
            continue
        entry: dict[str, object] = {"variants": ["clean"], "lines": {}}
        if discord is not None:
            entry["variants"] = ["clean", "discord"]
        files_by_line: dict[str, dict[str, object]] = {}
        for seg, line in zip(segments, lines, strict=True):
            if line is None:
                continue
            rel: dict[str, object] = {"tail_s": round(seg.tail_s, 3)}
            for variant, pcm in (("clean", clean), ("discord", discord)):
                if pcm is None:
                    continue
                name = f"clips/{reader}/{variant}/{int(line):02d}.wav"
                audio.write_wav(out / name, pcm[seg.start : seg.end])
                rel[variant] = name
            files_by_line[str(line)] = rel
        entry["lines"] = files_by_line
        manifest[reader] = entry
        print(
            f"  saved {len(files_by_line)} clips"
            + ("" if discord is not None else " (clean only: no Opus encoder)")
        )
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps({"readers": manifest}, indent=1))
    return 0 if ok else 2


def cmd_check(_args: argparse.Namespace) -> int:
    """Send one second of silence to each service, to check the keys and network."""
    from dmbot.devtools.stt_bakeoff.providers import Deepgram, MissingKey, Speechmatics

    silence = bytes(32_000)
    # One custom word with a sounds-like hint, so a bad dictionary format shows up now.
    vocab = [data.VocabEntry("Cerric", ("serik",))]
    worst = 0
    for provider in (Speechmatics("enhanced"), Deepgram("nova-3")):
        try:
            res = asyncio.run(provider.transcribe(silence, vocab, realtime=False))
        except MissingKey as exc:
            print(f"{provider.name}: {exc}")
            worst = 1
            continue
        status = "OK" if not res.error else f"FAILED ({res.error})"
        print(f"{provider.name}: {status}, connect {res.connect_s:.2f} s")
        worst = worst or (1 if res.error else 0)
    return worst


def cmd_run(args: argparse.Namespace) -> int:
    from dmbot.devtools.stt_bakeoff import runner

    setups: list[str] = args.setups or list(runner.DEFAULT_SETUPS)
    unknown = [s for s in setups if s not in runner.SETUPS]
    if unknown:
        print(f"Unknown setup(s): {unknown}. Choose from {list(runner.SETUPS)}.")
        return 1
    phases = list(runner.PHASES) if args.phase == "all" else [args.phase]
    if _refuse_repo_paths(args.out):
        return 1

    async def go() -> None:
        r = runner.Runner(args.out, concurrency=args.concurrency)
        for phase in phases:
            logging.info("Phase: %s", phase)
            if phase == runner.MAIN:
                await r.main(setups, limit=args.limit)
            elif phase == runner.CLEAN:
                await r.clean(setups, limit=args.limit)
            elif phase == runner.LEARNED:
                await r.learned(setups, limit=args.limit)
            elif phase == runner.RELISTEN:
                await r.relisten(setups)
            elif phase == runner.TIMING:
                await r.timing(setups)

    try:
        asyncio.run(go())
    except runner.LearnedSourceMissing as exc:
        print(exc)
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from dmbot.devtools.stt_bakeoff.report import build
    from dmbot.devtools.stt_bakeoff.runner import load

    print(build(load(args.out / "results.jsonl")))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(
        prog="stt_bakeoff",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split", help="cut recordings into one clip per script line")
    s.add_argument("--audio", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--min-silence", type=float, default=1.2)
    s.add_argument("--map", help="JSON file mapping each reader's pieces to line numbers")
    s.add_argument(
        "--allow-clean",
        action="store_true",
        help="go on without Discord-like audio if Opus isn't available",
    )
    s.set_defaults(fn=cmd_split)
    c = sub.add_parser("check", help="check the API keys with one second of silence")
    c.set_defaults(fn=cmd_check)
    r = sub.add_parser("run", help="run a phase (or all of them)")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument(
        "--phase", default="all", choices=["all", "main", "clean", "learned", "relisten", "timing"]
    )
    r.add_argument("--setups", nargs="*")
    r.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="requests at once (timing always runs one at a time)",
    )
    r.add_argument("--limit", type=int, help="only the first N lines per reader (a trial)")
    r.set_defaults(fn=cmd_run)
    rep = sub.add_parser("report", help="print the results summary (numbers only)")
    rep.add_argument("--out", type=Path, required=True)
    rep.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    code: int = args.fn(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
