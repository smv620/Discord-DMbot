"""Where two copies of one lore document break into paragraphs differently, in counts (#4307).

    python -m dmbot.devtools.lore_shape lore.txt lore.pdf

The owner's PDF of the sample lore gave far fewer connections than the same text read from its
Google Doc. The lore reader works paragraph by paragraph (`lore_read.paragraphs`: each line, a long
one cut at a sentence end), so a PDF whose paragraphs come out cut in two, or run together, is read
differently from the Doc even with every word the same. This reads both files exactly as an upload
does (`name_documents.text_of`; a PDF page by page through `name_documents.page_text`, so each break
and the line-joining's reason for each break it left are known), lines their words up, and
counts:

- each file: characters, words, pages, paragraphs (with how many words each holds), headings
  (`lore_knowledge.Sections`);
- the words: how many are the same in both, and how many only one file has (a PDF's page numbers
  or running headers), in how many places;
- the breaks: a paragraph of the first file that the second cuts (`cut`), and two of the first
  that the second runs together (`joined`). Each cut gives the reason the PDF's line-joining
  (`name_documents.reflow`) left that line as it is: `page_end`, `short_line` (under `RUNS_ON` of
  its page's widest line), `few_words` (under `PROSE_WORDS` words), `sentence_end` (it ends with
  . ! ? : ; or a closing quote or bracket), `next_is_item`, `page_number`, `blank`, `not_joined`
  (a page still read a word a line), or `reader_split` (the reader's own cut of a long line).
  The joins are counted by why the first's paragraph ended (`line_break`, or `reader_split`).
  Places are paragraph numbers and word counts, never words.

No AI, no database, no network, nothing stored. What it never prints: a name, a quote or a word of
either file, so its output can go on a public issue as it is. Free to run as often as wanted.
"""

from __future__ import annotations

import argparse
import difflib
import itertools
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dmbot.memory import lore_knowledge, lore_read
from dmbot.memory import name_documents as nd

SIZES = ((1, 3), (4, 9), (10, 29), (30, 79), (80, 10**9))  # words in a paragraph, for the table
SHOWN = 15  # the first so many cuts are listed one by one; the rest are only counted
# The tool measures shape, not size: a document an upload would refuse as too long is still worth
# measuring (and the lore upload's own limit is not the names screen's default), so no limit here.
NO_LIMIT = 10**9


@dataclass
class Shape:
    """One file as the reader gets it: its paragraphs' words, and why each paragraph ends."""

    chars: int
    pages: int
    paragraphs: list[str]
    headings: int
    words: list[str] = field(default_factory=list)  # each word, squashed (letters and digits)
    where: list[int] = field(default_factory=list)  # each word's paragraph number
    ends: list[str] = field(default_factory=list)  # why each paragraph ends where it does


def _squash(word: str) -> str:
    """A word as letters and digits only, so the Doc's curly quotes and dashes match the PDF's
    plain ones, and a lone dash or "***" is no word at all (in the places listed, too)."""
    return re.sub(r"\W", "", word.casefold())


def _page(text: str, why: list[str] | None) -> tuple[list[str], list[str]]:
    """One page's (or a whole text's) paragraphs as the reader cuts them, each with why it ends:
    the PDF line-joining's reason for its line (`why`, one for each line, as `reflow` gives them),
    `reader_split` where the reader itself cut a line (a long one, or at a line break other than
    "\n"), `page_end` for the page's last; a text that is no PDF has its lines (`line_break`)."""
    paragraphs: list[str] = []
    ends: list[str] = []
    lines = text.split("\n")
    if why is not None and len(why) != len(lines):  # (never: a mismatch would mislabel every one)
        raise ValueError("one reason for each line")
    reasons = why if why is not None else ["line_break"] * len(lines)
    for line, reason in zip(lines, reasons, strict=True):
        own = lore_read.paragraphs(line)
        paragraphs += own
        ends += ["reader_split"] * (len(own) - 1) + [reason] * bool(own)
    if ends and why is not None:
        ends[-1] = "page_end"
    return paragraphs, ends


def shape_of(name: str, raw: bytes) -> Shape:
    """The file read as an upload reads it (a PDF page by page, so its pages' ends are known)."""
    pages: list[str] = []
    paragraphs: list[str] = []
    ends: list[str] = []
    pdf = Path(name).suffix.casefold() == ".pdf"
    if pdf:
        for page in nd.open_pdf(raw).pages:
            why: list[str] = []
            pages.append(nd.page_text(page, why))
            own, own_ends = _page(pages[-1], why)
            paragraphs += own
            ends += own_ends
    else:
        pages.append(nd.text_of(name, raw, max_chars=NO_LIMIT))
        paragraphs, ends = _page(pages[0], None)
    text = "\n".join(pages)
    out = Shape(len(text), len(pages) if pdf else 0, paragraphs, 0, ends=ends)
    out.headings = len(lore_knowledge.Sections(text).heads)
    for n, para in enumerate(paragraphs):
        for word in para.split():
            if squashed := _squash(word):
                out.words.append(squashed)
                out.where.append(n)
    return out


@dataclass
class Breaks:
    """Where the second file's paragraph breaks differ from the first's."""

    cuts: Counter[str] = field(default_factory=Counter)  # by the second's reason
    joined: Counter[str] = field(default_factory=Counter)  # by the first's reason
    listed: list[str] = field(default_factory=list)  # the first few cuts, as places


def compare_breaks(a: Shape, b: Shape) -> tuple[Breaks, Counter[str]]:
    """Line the two files' words up and count where their paragraph breaks differ, and the words
    only one of them has (`only_first`, `only_second`, `places`).

    Breaks are compared between each two words both files have, next to each other in the first,
    whatever lies between them in either: a page number or a running header only the PDF has sits
    between the two halves of a cut paragraph, and must not hide the cut (the reason is then the
    first break in the second file between the two words)."""
    found = Breaks()
    words: Counter[str] = Counter()
    pairs: list[tuple[int, int]] = []  # (word in a, word in b), both files' own, in order
    matcher = difflib.SequenceMatcher(None, a.words, b.words, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            words["same"] += i2 - i1
            pairs += zip(range(i1, i2), range(j1, j2), strict=True)
        else:
            words["places"] += 1
            words["only_first"] += i2 - i1
            words["only_second"] += j2 - j1
    for (i, j), (i2, j2) in itertools.pairwise(pairs):
        a_breaks = a.where[i] != a.where[i2]
        b_break = next((k for k in range(j, j2) if b.where[k] != b.where[k + 1]), None)
        if b_break is not None and not a_breaks:
            reason = b.ends[b.where[b_break]]
            found.cuts[reason] += 1
            if len(found.listed) < SHOWN:
                para = a.where[i]
                into = i - a.where.index(para) + 1
                found.listed.append(
                    f"first's paragraph {para + 1} (after word {into} of "
                    f"{a.where.count(para)}): {reason}"
                )
        elif a_breaks and b_break is None:
            # (by why the first's paragraph ends: a `reader_split` there is the reader's own cut
            # of a long line, which moves when the second's breaks do, not a difference in shape)
            found.joined[a.ends[a.where[i]]] += 1
    return found, words


def _sizes(s: Shape) -> str:
    counts = Counter(s.where)  # (words as `_squash` keeps them, as in the places listed)
    counts = Counter(counts.get(n, 0) for n in range(len(s.paragraphs)))
    parts = []
    for low, high in SIZES:
        n = sum(v for k, v in counts.items() if low <= k <= high)
        parts.append(f"{low}+: {n}" if high > 10**8 else f"{low}-{high}: {n}")
    return ", ".join(parts)


def report(a: Shape, b: Shape) -> list[str]:
    out = []
    for label, s in (("first", a), ("second", b)):
        pages = f", {s.pages} pages" if s.pages else ""
        out.append(
            f"{label}: {s.chars} characters, {len(s.words)} words{pages}, {len(s.paragraphs)} "
            f"paragraphs (words each: {_sizes(s)}), {s.headings} headings"
        )
    found, words = compare_breaks(a, b)
    out.append(
        f"words: {words['same']} the same; only the first has {words['only_first']}, only the "
        f"second {words['only_second']}, in {words['places']} places"
    )
    reasons = ", ".join(f"{k} {v}" for k, v in found.cuts.most_common()) or "none"
    joins = ", ".join(f"{k} {v}" for k, v in found.joined.most_common()) or "none"
    out.append(
        f"breaks: the second cuts {sum(found.cuts.values())} of the first's paragraphs "
        f"({reasons}); runs {sum(found.joined.values())} of the first's breaks together ({joins})"
    )
    out += [f"  {line}" for line in found.listed]
    if sum(found.cuts.values()) > len(found.listed):
        out.append(f"  (and {sum(found.cuts.values()) - len(found.listed)} more, counted above)")
    return out


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lore_shape", description=__doc__)
    parser.add_argument("first", type=Path, help="the copy to compare against (the Doc's text)")
    parser.add_argument("second", type=Path, help="the other copy (the PDF)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    shapes = []
    for path in (args.first, args.second):
        try:
            shapes.append(shape_of(path.name, path.read_bytes()))
        except Exception as exc:  # (a damaged file too; the type only: a message may hold words)
            print(f"lore_shape: can't read a file ({type(exc).__name__})", file=sys.stderr)
            return 2
    print("\n".join(report(*shapes)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
