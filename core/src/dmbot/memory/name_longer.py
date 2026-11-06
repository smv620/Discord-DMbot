"""Longer names a suggested name may really be part of (#2570).

Speech-to-text often writes a place with a capital for its first word and lower case for the rest
("Harrow's keep"), so the scan offers "Harrow" and the card has no way to say "the name is longer".
These find the likely longer names in the card's own quotes, for the DM to pick from and fix. Pure:
no database, no AI. Only suggestions: nothing counts until the DM saves a name.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from dmbot.memory.models import NAME_MAX

MAX_OFFERED = 3
MAX_EXTRA_WORDS = 2  # "Harrow's keep", "the Hall of Harrow" are two words around it at most
# Words that join a name to its sentence rather than extend it: never the edge of a longer name.
_EDGE_SKIP = frozenset(
    [
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "from",
        "he",
        "her",
        "his",
        "i",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "she",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "with",
        "you",
        "your",
    ]
)
# Words a place's name often ends with ("Harrow's Keep"). A short list, kept here with the
# reason: only a place-like second word makes the scan suggest the longer name itself, so a
# capitalized word followed by any other lower-case word is never widened on a guess.
PLACE_WORDS = frozenset(
    [
        "keep",
        "hold",
        "vale",
        "pass",
        "ford",
        "tower",
        "gate",
        "bridge",
        "fort",
        "fortress",
        "castle",
        "abbey",
        "hall",
        "hill",
        "hills",
        "wood",
        "woods",
        "forest",
        "mountain",
        "mountains",
        "lake",
        "river",
        "falls",
        "city",
        "town",
        "village",
        "isle",
        "island",
        "marsh",
        "reach",
        "crossing",
        "harbor",
        "harbour",
        "port",
        "temple",
        "shrine",
        "cave",
        "caves",
        "mine",
        "mines",
    ]
)
_WORD = r"[^\W\d_](?:[^\W\d_]|['’-](?=[^\W\d_]))*"


def _plain(text: str) -> str:
    return text.replace("**", "").replace("*", "").replace("_", " ")


def longer_names(texts: Iterable[str], name: str, limit: int = MAX_OFFERED) -> list[str]:
    """Up to `limit` longer names the quotes suggest for `name`: the name with the one or two
    words after it (after a possessive too: "Harrow's keep"), or the one or two before it, as
    heard; most said first, shorter first. A candidate never begins or ends on a joining word, so
    "Harrow and" is never offered."""
    own = " ".join(name.split())
    if not own:
        return []
    found: Counter[str] = Counter()
    pattern = re.compile(
        rf"(?P<before>(?:{_WORD}\s+){{0,{MAX_EXTRA_WORDS}}})(?<!\w)(?P<name>{re.escape(own)})"
        rf"(?P<poss>['’]s)?(?P<after>(?:\s+{_WORD}){{0,{MAX_EXTRA_WORDS}}})",
        re.IGNORECASE,
    )
    for text in texts:
        for match in pattern.finditer(_plain(text)):
            heard = match.group("name")
            poss = (match.group("poss") or "").replace("’", "'")
            after = match.group("after").split()
            for take in range(1, len(after) + 1):
                words = after[:take]
                if _joins(words[0]) or _joins(words[-1]):
                    continue
                found[f"{heard}{poss} {' '.join(words)}"] += 1
            before = match.group("before").split()
            for take in range(1, len(before) + 1):
                words = before[-take:]
                if _joins(words[0]) or _joins(words[-1]):
                    continue
                found[f"{' '.join(words)} {heard}{poss}"] += 1
    ranked = sorted(
        (c for c in found if len(c) <= NAME_MAX and c.casefold() != own.casefold()),
        key=lambda c: (-found[c], len(c.split()), c),
    )
    if ranked and found[ranked[0]] >= 2:  # said more than once: a one-off pairing is noise
        ranked = [c for c in ranked if found[c] >= 2]
    out: list[str] = []
    seen: set[str] = set()
    for candidate in ranked:  # one spelling of each (case-insensitive)
        if candidate.casefold() not in seen:
            seen.add(candidate.casefold())
            out.append(candidate)
        if len(out) >= limit:
            break
    return out


def _joins(word: str) -> bool:
    return word.casefold() in _EDGE_SKIP


def widened(lines: Iterable[str], name: str, min_times: int) -> str:
    """The longer name the lines say at least `min_times` times for a one-word `name` (the same
    capitalized word followed by the same place word, after a possessive or not), in its heard
    form with the place word capitalized ("Harrow's Keep"), else "". Only a place-like word
    widens a name by itself (`PLACE_WORDS`)."""
    own = " ".join(name.split())
    if not own or " " in own:
        return ""
    pattern = re.compile(
        rf"\b{re.escape(own)}(?P<poss>['’]s)?\s+(?P<place>{'|'.join(sorted(PLACE_WORDS))})\b",
        re.IGNORECASE,
    )
    forms: Counter[str] = Counter()
    for line in lines:
        if own.casefold() not in line.casefold():
            continue  # (most lines never say it)
        for match in pattern.finditer(_plain(line)):
            poss = (match.group("poss") or "").replace("’", "'")
            # A place word after a bare name is a place only when it is written as one ("Harrow
            # Keep"): "Kira falls" or "Ana holds" are a person doing something.
            if not poss and not match.group("place")[:1].isupper():
                continue
            forms[f"{own}{poss} {match.group('place').capitalize()}"] += 1
    if not forms:
        return ""
    best, times = forms.most_common(1)[0]
    return best if times >= min_times and len(best) <= NAME_MAX else ""
