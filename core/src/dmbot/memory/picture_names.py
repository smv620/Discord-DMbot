"""Checking a name read from a picture against the whole document (#4003, Supervisor's ruling of
2026-11-10). Pure: no database, no AI, no Discord.

A name the vision read gives from a picture page (a map label, a table cell, a handout) is one of:
1. **Matched**: it is, by `name_key`, one of the document's own names or one of the campaign's
   (confirmed, public) names, or it is written word for word in the document's text. It is
   treated like the text's names, with no question to the DM.
2. **Corrected**: a simple misreading that the document plainly resolves. **Exactly one** of the
   document's own names passes one of three narrow tests (a spelling slip after folding the
   letters a picture read confuses, a name cut short, initials before a whole last word), and no
   other name passes any of them. The correction always points at this document's own name, never
   at another of the campaign's (the ruling): it is text the same upload has.
3. **ToCheck**: anything else, a name for the DM to check, with a link to the picture. When one
   name came close but not plainly, it is offered as a likely match ("Is this …?"), never applied.

**Never a guess:** two candidates, in one test or across tests, is case 3. A candidate of another
kind (a place against a person) is not one. The caller passes only public names: a secret spelling
is never a target, since a correction is shown on the upload's notice.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from dmbot.memory.models import name_key, spelling_key
from dmbot.memory.scan import NEAR, NEAR_ONE_WORD, TIE, alike, sound_keys

# A cut-short name must keep at least this many letters before it can resolve (#4003 note).
MIN_CUT = 4
# How far under the likeness threshold a single near name may be and still be offered as a likely
# match on the DM's question (never applied).
LIKELY_BELOW = 0.1

# Letters a picture read confuses, folded the same way on both sides before comparing: "rn" read
# for "m" and the reverse, "cl" for "d", "vv" for "w", and l / 1 / I, 0 / O, 5 / S. Order matters:
# the pairs go first, then the single letters.
_PAIRS = (("rn", "m"), ("cl", "d"), ("vv", "w"))
_SINGLES = str.maketrans({"1": "i", "l": "i", "|": "i", "0": "o", "5": "s"})
_CUT_MARK = re.compile(r"(?:\.\.\.|…|-)\s*$")
_INITIAL = re.compile(r"^[A-Za-z]\.?$")


@dataclass(frozen=True, slots=True)
class Known:
    """A name the picture's name is checked against, and its kind when known (an entry's type:
    character, place, faction …). None: not known, so it never rules a candidate out."""

    name: str
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class Matched:
    """Read correctly: the document's or the campaign's own spelling of it."""

    name: str


@dataclass(frozen=True, slots=True)
class Corrected:
    """A misreading the document plainly resolves: `name` is the document's spelling, `how` is
    which test resolved it ("slip", "cut", "initials"), for the counts."""

    name: str
    how: str


@dataclass(frozen=True, slots=True)
class ToCheck:
    """For the DM. `likely`: one of the document's names that came close but not plainly, to offer
    as "Is this …?"; never applied without the DM."""

    likely: str | None = None


Checked = Matched | Corrected | ToCheck


def fold(text: str) -> str:
    """A spelling with the letters a picture read confuses folded alike, on top of `name_key`."""
    key = name_key(text).replace(" ", "")
    for pair, single in _PAIRS:
        key = key.replace(pair, single)
    return key.translate(_SINGLES)


def check(
    name: str,
    kind: str | None,
    document: Sequence[Known],
    campaign: Iterable[Known] = (),
    text: str = "",
) -> Checked:
    """Which of the three cases a name read from a picture is (see the module's note).
    `document`: the names the reader found in this document's text pages (before and after the
    picture). `campaign`: the campaign's confirmed, public names (they count for a match only).
    `text`: the document's plain text, where a name the reader didn't list still counts."""
    key = name_key(name)
    if not key:
        return ToCheck()
    for known in (*document, *campaign):
        if spelling_key(known.name) == spelling_key(name):
            return Matched(known.name)
    if text and _written_in(name, text):
        return Matched(name.strip())
    fits = [k for k in document if _same_kind(kind, k.kind)]
    found: dict[str, str] = {}  # the document's name -> the test it passed
    for test, how in ((_slip, "slip"), (_cut, "cut"), (_initials, "initials")):
        for known in fits:
            if test(name, known.name, fits):
                found.setdefault(known.name, how)
    if len(found) == 1:
        ((only, how),) = found.items()
        return Corrected(only, how)
    if found:
        return ToCheck()  # two candidates: never a guess
    return ToCheck(_likely(name, fits))


def _same_kind(a: str | None, b: str | None) -> bool:
    return a is None or b is None or a == b


def _written_in(name: str, text: str) -> bool:
    """The name, word for word (case and spacing aside), in the document's text."""
    words = name.split()
    if not words:
        return False
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _need(a: str, b: str) -> float:
    return NEAR_ONE_WORD if len(a.split()) == 1 and len(b.split()) == 1 else NEAR


def _slip(name: str, candidate: str, fits: Sequence[Known]) -> bool:
    """(a) A spelling slip: after folding, as alike as the table's near-name test needs, sharing a
    sound code, and no other name of the document within the tie margin of it."""
    need = _need(name, candidate)
    score = _folded_alike(name, candidate)
    if score < need or not _sounds_alike(name, candidate):
        return False
    return not any(k.name != candidate and _folded_alike(name, k.name) > score - TIE for k in fits)


def _folded_alike(a: str, b: str) -> float:
    return max(alike(a, b), alike(fold(a), fold(b)))


def _sounds_alike(a: str, b: str) -> bool:
    return bool(
        set(sound_keys(a)) & set(sound_keys(b))
        or set(sound_keys(fold(a))) & set(sound_keys(fold(b)))
    )


def _cut(name: str, candidate: str, fits: Sequence[Known]) -> bool:
    """(b) Cut short: the start of the document's name, or of one of its words, with at least
    `MIN_CUT` letters, and the start of no other name of the document."""
    stem = name_key(_CUT_MARK.sub("", name)).replace(" ", "")
    if len(stem) < MIN_CUT:
        return False

    def starts(full: str) -> bool:
        k = name_key(full)
        return k.replace(" ", "").startswith(stem) or any(w.startswith(stem) for w in k.split())

    if not starts(candidate) or name_key(candidate).replace(" ", "") == stem:
        return False
    return not any(k.name != candidate and starts(k.name) for k in fits)


def _initials(name: str, candidate: str, fits: Sequence[Known]) -> bool:
    """(c) Shortened: one or more initials, then one whole last word ("S. Kaltro"). The last word
    must be the candidate's last word and each initial the first letter of the words before it, in
    order; initials alone never resolve."""
    parts = name.split()
    if len(parts) < 2 or not all(_INITIAL.match(p) for p in parts[:-1]):
        return False

    def fits_it(full: str) -> bool:
        words = name_key(full).split()
        last = name_key(parts[-1])
        if not words or words[-1] != last or len(words) - 1 < len(parts) - 1:
            return False
        firsts = [w[0] for w in words[: len(parts) - 1]]
        return firsts == [p[0].casefold() for p in parts[:-1]]

    if not fits_it(candidate):
        return False
    return not any(k.name != candidate and fits_it(k.name) for k in fits)


def _likely(name: str, fits: Sequence[Known]) -> str | None:
    """One document name that came close (within `LIKELY_BELOW` of the threshold and sharing a
    sound code), when exactly one did: offered on the DM's question, never applied."""
    close = [
        k.name
        for k in fits
        if _folded_alike(name, k.name) >= _need(name, k.name) - LIKELY_BELOW
        and _sounds_alike(name, k.name)
    ]
    return close[0] if len(close) == 1 else None
