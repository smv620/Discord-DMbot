"""The linker's matching rules (docs/STORY_MEMORY.md, section 9; #1364): when a claim names
someone DMbot does not know yet, is it a spelling of a name that is already waiting for the DM?

Pure: no database, no AI. A name that matches an entry exactly is not the linker's business (the
claim rules already resolve it). Everything else becomes a *proposed* entry for the DM, who says
whether it is new or the same as a known name, or not a name; the linker never confirms one and
never joins a name to a confirmed entry by itself (a likely match is only shown to the DM, by the
names review that already does it). The one thing it matches on its own is a second spelling of a
name already waiting, so the DM is asked once, not twice.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

MIN_FUZZY_LEN = 5  # shorter names are never matched loosely: "Bryn" and "Bran" are two people
NEAR_LEN = 9  # from here a name may differ by two letters, below it by one


def allowed_distance(key: str) -> int:
    """How many single-letter changes (add, drop, change) still count as the same name, for a
    name key of this length: none below `MIN_FUZZY_LEN`, one up to eight letters, two beyond."""
    if len(key) < MIN_FUZZY_LEN:
        return 0
    return 1 if len(key) < NEAR_LEN else 2


def within(a: str, b: str, limit: int) -> bool:
    """Whether the two are at most `limit` single-letter changes apart (Levenshtein), quickly:
    names are short, and a gap in length beyond the limit settles it at once."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        row = [i]
        for j, y in enumerate(b, start=1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (x != y)))
        if min(row) > limit:
            return False
        previous = row
    return previous[-1] <= limit


def alike(key: str, candidates: Mapping[str, str]) -> str | None:
    """The one entry a name key is near, among `candidates` (name key → entry id): its id when
    exactly one entry has a key within `allowed_distance` of it, None when none or more than one
    (two entries that close are the DM's to tell apart). The distance is the shorter key's."""
    found: set[str] = set()
    for other, entry in candidates.items():
        if within(key, other, allowed_distance(min(key, other, key=len))):
            found.add(entry)
    return next(iter(found)) if len(found) == 1 else None


def most_mentioned(names: Iterable[str], limit: int) -> tuple[list[str], int]:
    """The `limit` name keys said most often (ties: the one first said), and how many were left
    for next time. The input is every mention, in the order said."""
    counts = Counter(names)
    order = {k: i for i, k in enumerate(dict.fromkeys(names))}
    ranked = sorted(counts, key=lambda k: (-counts[k], order[k]))
    return ranked[:limit], max(0, len(ranked) - limit)


def spellings(keys: Sequence[str]) -> dict[str, list[str]]:
    """Group the name keys that are near each other (a name said two ways in one session), so the
    DM is asked once: first-said key → the other keys near it. Each key lands in one group."""
    groups: dict[str, list[str]] = {}
    for key in keys:
        home = alike(key, {k: k for k in groups})
        if home is None:
            groups[key] = []
        elif key != home:
            groups[home].append(key)
    return groups
