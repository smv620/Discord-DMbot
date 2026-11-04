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
# A longer list is more likely a sentence than names: keep it whole. Twelve, because the sample lore
# lists the ten towns of one region in a row (each part must still look like a name).
MAX_PARTS = 12
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


def split_list(
    text: str, known: Collection[str] = (), offered: Collection[str] = (), *, dm: bool = False
) -> list[str]:
    """The parts of `text` when it is a list of names, or `[text]` when it is one name.

    `known` and `offered` are name keys: names the campaign already holds, and names the same
    read offered on their own. `dm`: the DM pressed "These are separate names", so the DM has
    decided: "and", "or" and "&" split like a comma, and a part with a joining word needs no
    proof; every part must still be name-like. A name held whole (`text` itself is in `known`)
    is never split."""
    whole = " ".join(text.split())
    if not whole or name_key(whole) in {name_key(k) for k in known}:
        return [whole] if whole else []
    keys = {name_key(k) for k in (*known, *offered)}
    hard_parts = [p for p in _HARD.split(whole) if p]
    hard = len(hard_parts) > 1
    if hard:  # "A, B, and C" and "A, B and C" are three: the last pair is one list too
        parts: list[str] = []
        for p in hard_parts:
            p = _LEADING_JOIN.sub("", p)
            sub = [q for q in _SOFT.split(p) if q]
            parts.extend(sub if len(sub) > 1 and all(_name_like(q) for q in sub) else [p])
    else:
        parts = [p for p in _SOFT.split(whole) if p]
    if len(parts) < 2 or len(parts) > MAX_PARTS:
        return [whole]
    if not all(_name_like(p) for p in parts):
        return [whole]
    if dm:
        pass
    elif not hard and not all(name_key(p) in keys for p in parts):
        return [whole]
    # A part with a small joining word in it ("City of Splendors" in "Waterdeep, City of
    # Splendors") is more likely a title or a description than a name of its own: it splits only
    # when the campaign or the same read holds that part on its own.
    for p in [] if dm else parts:
        if any(w in _JOINERS for w in p.split()) and name_key(p) not in keys:
            return [whole]
    unique: dict[str, str] = {}
    for p in parts:
        unique.setdefault(name_key(p), p)
    return list(unique.values())
