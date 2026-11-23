"""How the session file writes the key's names (docs/design/session_names.md, section 8).

A description of the input, never a score of the readers: for each name the session key lists,
whether the file ever writes it with a capital in the middle of a sentence, only with a capital at
a sentence's start, only in lower case, or not at all. Printed beside the score for every version,
it proves the "no capitals" file is what it should be: there, no name should still carry a
capital mid-sentence, or the baseline is not the one we think it is (the Expert, 2026-11-23).

Pure: no database, no AI. Counts only, plus the names still capitalized mid-sentence: the public
sample's names, which may go on #3545, never a line of the session.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

MID = "capital mid-sentence"
START = "capital only at a sentence start"
LOWER = "lower case only"
ABSENT = "not in the file"
ORDER = (MID, START, LOWER, ABSENT)
# Before a sentence's first word: the line's start, or an end mark and spaces (a quote may open)
_SENTENCE_END = re.compile(r"(?:^|[.!?]['\"’”)]*)\s*['\"‘“(]*$")


@dataclass(slots=True)
class Shape:
    counts: Counter[str] = field(default_factory=Counter)
    # The key names written with a capital mid-sentence somewhere in the file
    capitalized: list[str] = field(default_factory=list)

    def line(self) -> str:
        total = sum(self.counts.values())
        parts = ", ".join(f"{label} {self.counts[label]}" for label in ORDER)
        return f"Session file, the key's {total} names: {parts}"


def key_names(key: Mapping[str, object]) -> dict[str, list[str]]:
    """Each named key entry with its wordings. Entries the story never names (`unnamed`,
    `described`) and those left out on purpose (`by_design`) are not names to look for."""
    out: dict[str, list[str]] = {}
    nodes = key.get("nodes", [])
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or node.get("unnamed") or node.get("described"):
            continue
        if node.get("by_design"):
            continue
        name = str(node["name"])
        out[name] = [name, *(str(a) for a in node.get("aliases", []))]
    return out


def _pattern(wording: str) -> re.Pattern[str]:
    # (whole words, any case; an apostrophe or a hyphen next to it is still part of a word)
    return re.compile(r"(?<![\w'’-])" + re.escape(wording) + r"(?![\w'’-])", re.IGNORECASE)


def shape(lines: Iterable[str], names: Mapping[str, Sequence[str]]) -> Shape:
    """How `lines` (the file's utterances) write each name in `names`."""
    text = list(lines)
    out = Shape()
    for name, wordings in names.items():
        mid = start = lower = False
        for wording in wordings:
            pattern = _pattern(wording)
            for line in text:
                for found in pattern.finditer(line):
                    written = found.group()
                    if not any(w[:1].isupper() for w in written.split()):
                        lower = True
                    elif _SENTENCE_END.search(line[: found.start()]):
                        start = True
                    else:
                        mid = True
        label = MID if mid else START if start else LOWER if lower else ABSENT
        out.counts[label] += 1
        if mid:
            out.capitalized.append(name)
    out.capitalized.sort()
    return out
