"""`python -m dmbot.devtools.srd PDF [--out DIR]`: see dmbot.devtools.srd.build."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from dmbot.devtools.srd import build
from dmbot.devtools.srd.parse import SrdError


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dmbot.devtools.srd",
        description="Build DMbot's spell and condition data from the SRD 5.2.1 or 5.1 PDF.",
    )
    parser.add_argument(
        "pdf", help=f"the PDF Wizards publishes at {build.SOURCE_URL} or {build.SOURCE_URL_51}"
    )
    parser.add_argument("--out", type=Path, help="where to write the files (default: its folder)")
    args = parser.parse_args(argv)
    try:
        files = build.build(args.pdf)
    except SrdError as exc:
        print(f"Stopped: {exc}")
        return 1
    for path in build.write(files, args.out or build.folder_for(files)):
        print(f"Wrote {path} ({len(files[path.name]['entries'])} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
