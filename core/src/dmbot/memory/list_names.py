"""Tells a list of names offered as one name ("Caer-Dineval, Caer-Konig, Lonelywood") from a real
name, and splits the list into its parts (#3383).

Pure: no database, no AI. The rule is "more likely than not" (the owner's standard): a comma,
semicolon or slash between parts that each look like a name on their own is a list. A bare "and",
"or" or "&" is usually inside a real name ("Salt and Iron Company"), so it splits only when every
part is already known to the campaign, or the same read also offered each part alone. When any
part is not name-like, the text is kept whole.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from dmbot.memory.models import name_key

MAX_PART_WORDS = 4
MAX_PARTS = 8  # a longer list is more likely a sentence than names: keep it whole
# Small words allowed inside a name ("Tower of Joy", "Hans de Vries").
_JOINERS = frozenset({"of", "the", "de", "del", "la", "le", "von", "van", "du", "da", "al", "el"})
_HARD = re.compile(r"\s*[,;/]\s*")
_SOFT = re.compile(r"\s+(?:and|or|&)\s+", re.IGNORECASE)
_LEADING_JOIN = re.compile(r"^(?:and|or|&)\s+", re.IGNORECASE)
_WORD = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’.-])*$")


def _name_like(part: str) -> bool:
    """One to four words, each a capitalised word (or a small joining word between them)."""
    words = part.split()
    if not 1 <= len(words) <= MAX_PART_WORDS:
        return False
    for i, w in enumerate(words):
        if not _WORD.match(w):
            return False
        if w[0].isupper():
            continue
        if not (0 < i < len(words) - 1 and w in _JOINERS):
            return False
    return True


def split_list(text: str, known: Collection[str] = (), offered: Collection[str] = ()) -> list[str]:
    """The parts of `text` when it is a list of names, or `[text]` when it is one name.

    `known` and `offered` are name keys: names the campaign already holds, and names the same
    read offered on their own. A name held whole (`text` itself is in `known`) is never split."""
    whole = " ".join(text.split())
    if not whole or name_key(whole) in {name_key(k) for k in known}:
        return [whole] if whole else []
    parts = [p for p in _HARD.split(whole) if p]
    hard = len(parts) > 1
    if not hard:
        parts = [p for p in _SOFT.split(whole) if p]
    else:  # "A, B, and C" is three: drop the joiner in front of the last part
        parts = [_LEADING_JOIN.sub("", p) for p in parts]
    if len(parts) < 2 or len(parts) > MAX_PARTS:
        return [whole]
    if not all(_name_like(p) for p in parts):
        return [whole]
    if not hard:
        keys = {name_key(k) for k in (*known, *offered)}
        if not all(name_key(p) in keys for p in parts):
            return [whole]
    # an "and" list that has a comma too ("A, B and C"): split the last pair as well
    out: list[str] = []
    for p in parts:
        sub = [q for q in _SOFT.split(p) if q]
        if hard and len(sub) > 1 and all(_name_like(q) for q in sub):
            keys = {name_key(k) for k in (*known, *offered)}
            out.extend(sub if all(name_key(q) in keys for q in sub) else [p])
        else:
            out.append(p)
    unique: dict[str, str] = {}
    for p in out:
        unique.setdefault(name_key(p), p)
    return list(unique.values())
