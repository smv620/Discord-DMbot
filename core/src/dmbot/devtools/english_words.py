"""Build `names/data/english_words.txt.gz` from a SCOWL release (#5154), so the list in the repo
can be made again and checked.

    python -m dmbot.devtools.english_words /path/to/scowl-2020.12.07 [--out FILE]

It reads the release's `final/` lists: the English, American and British word lists of every size
up to 60 (the usual spell-checker size). It keeps only entries of lower-case letters a to z, so
proper nouns, abbreviations, contractions and words with accents are left out. It writes them
sorted, one a line, gzipped with no timestamp and no file name, so the same release always gives
the same bytes.
The release's `Copyright` file goes beside the list as `SCOWL-Copyright.txt`, word for word.
Never run by the bot.
"""

from __future__ import annotations

import argparse
import gzip
import re
import sys
from collections.abc import Sequence
from pathlib import Path

SIZES = (10, 20, 35, 40, 50, 55, 60)
KINDS = ("english", "american", "british")
_WORD = re.compile(r"[a-z]+")
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "names" / "data" / "english_words.txt.gz"


def words_of(release: Path) -> list[str]:
    """The lower-case words of the release's lists up to size 60, sorted, each once."""
    found: set[str] = set()
    for kind in KINDS:
        for size in SIZES:
            path = release / "final" / f"{kind}-words.{size}"
            if path.exists():  # (not every kind has every size)
                text = path.read_text(encoding="latin-1")  # (SCOWL's lists are ISO-8859-1)
                found.update(w for w in text.split("\n") if _WORD.fullmatch(w))
    return sorted(found)


def write(words: Sequence[str], out: Path) -> None:
    data = ("\n".join(words) + "\n").encode("ascii")
    # (no name or time in the header: the bytes depend on the words only)
    with out.open("wb") as raw, gzip.GzipFile("", "wb", 9, raw, mtime=0) as file:
        file.write(data)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="english_words", description=__doc__)
    parser.add_argument("release", type=Path, help="the unpacked SCOWL release folder")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    words = words_of(args.release)
    if not words:
        print("english_words: no word lists in that folder", file=sys.stderr)
        return 2
    write(words, args.out)
    print(f"{len(words)} words written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
