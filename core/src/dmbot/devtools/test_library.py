"""The saved test sessions (#1019): `python -m dmbot.devtools.test_library list` and `keep`.

    python -m dmbot.devtools.test_library list
    python -m dmbot.devtools.test_library keep FOLDER --name two-speaker-live --note "what passed"

Run it where the recordings are (the core container, or a shell with
DMBOT_TEST_RECORDINGS_DIR set). Nothing it prints holds a Discord id or a name. A session
nobody keeps is deleted after 7 days; a kept one stays until its speakers ask to be deleted.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from dmbot.test_recording import library

DEFAULT_DIR = "/var/lib/dmbot/test-recordings"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="test_library", description="Saved test sessions.")
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(os.environ.get("DMBOT_TEST_RECORDINGS_DIR") or DEFAULT_DIR),
        help="the recordings folder (default: DMBOT_TEST_RECORDINGS_DIR)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="the saved sessions, their sizes and kept names")
    keep = commands.add_parser("keep", help="keep a finished session as a replayable case")
    keep.add_argument("folder", help="a folder name from `list`")
    keep.add_argument("--name", required=True, help="a short name, like two-speaker-live")
    keep.add_argument("--note", default="", help="what passed, in a few words")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "list":
        infos = library.listing(args.dir)
        if not infos:
            print("No saved test sessions.")
        for info in infos:
            print(library.describe(info))
        return 0
    try:
        library.keep(args.dir, args.folder, args.name, args.note)
    except library.LibraryError as exc:
        print(f"test_library: {exc}", file=sys.stderr)
        return 1
    print(f"Kept {args.folder} as {args.name!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
