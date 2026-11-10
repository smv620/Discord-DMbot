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

# A cut-short name must keep at least this many letters before it can resolve (#4003 note), with a
# mark that it was cut ("…", "...", "-"). With no mark, it needs `MIN_UNMARKED` letters and at least
# `MIN_SHARE` of the word it starts, and must not be a whole word itself: "Mara" is a name, not a
# cut "Marabel" (the reviewer); a label clipped at the page edge ("Sparkwalk") still resolves.
MIN_CUT = 4
MIN_UNMARKED = 6
MIN_SHARE = 0.6
# How far under the likeness threshold a single near name may be and still be offered as a likely
# match on the DM's question (never applied).
LIKELY_BELOW = 0.1

# Letters a picture read confuses, folded the same way on both sides before comparing: "rn" read
# for "m" and the reverse, "cl" for "d", "vv" for "w", and l / 1 / I, 0 / O, 5 / S. Order matters:
# the pairs go first, then the single letters.
_PAIRS = (("rn", "m"), ("cl", "d"), ("vv", "w"))
_SINGLES = str.maketrans({"1": "i", "l": "i", "0": "o", "5": "s"})
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
    """A spelling with the letters a picture read confuses folded alike, on top of `name_key`,
    word by word: a pair is never made across a space ("Eric Lane" is not "Eridane")."""
    words = []
    for word in name_key(text).split():
        for pair, single in _PAIRS:
            word = word.replace(pair, single)
        words.append(word.translate(_SINGLES))
    return " ".join(words)


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
    if text:
        written = _written_in(name, text)
        if written is not None:
            return Matched(written)  # (the text's own spelling, not an all-capitals label's)
    fits = _one_each(k for k in document if _same_kind(kind, k.kind))
    # A campaign name of the same kind is never a target, but it is a rival: a correction is only
    # made when nothing else the campaign knows fits about as well (never a guess, Supervisor).
    rivals = _one_each([*fits, *(k for k in campaign if _same_kind(kind, k.kind))])
    found: dict[str, str] = {}  # the document's name -> the test it passed
    for test, how in ((_slip, "slip"), (_cut, "cut"), (_initials, "initials")):
        for known in fits:
            if test(name, known.name, rivals):
                found.setdefault(known.name, how)
    if len(found) == 1:
        ((only, how),) = found.items()
        return Corrected(only, how)
    if found:
        return ToCheck()  # two candidates: never a guess
    return ToCheck(_likely(name, fits))


def _one_each(names: Iterable[Known]) -> list[Known]:
    """One per spelling key, the first kept: two spellings of one name are not two candidates."""
    seen: dict[str, Known] = {}
    for k in names:
        seen.setdefault(spelling_key(k.name).replace(" ", ""), k)  # ("Sephek-Kaltro" too)
    return list(seen.values())


def _same_kind(a: str | None, b: str | None) -> bool:
    return a is None or b is None or a == b


def _written_in(name: str, text: str) -> str | None:
    """The name as the document's text writes it, when it is there word for word (case and spacing
    aside). Lower-case prose counts too ("a blue clam shell"): the name is in the document."""
    words = name.split()
    if not words:
        return None
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)"
    found = re.search(pattern, text, re.IGNORECASE)
    return " ".join(found.group(0).split()) if found else None


def _need(a: str, b: str) -> float:
    return NEAR_ONE_WORD if len(a.split()) == 1 and len(b.split()) == 1 else NEAR


def _slip(name: str, candidate: str, fits: Sequence[Known]) -> bool:
    """(a) A spelling slip: as alike as the table's near-name test needs, and either sharing a
    sound code as written, or the same after folding with one word unchanged as its anchor
    ("Torrga Lcevein"). A one-word name the fold alone makes equal is never enough: "Clara" is
    not "Dara" (the reviewer). No other name within the tie margin of it."""
    need = _need(name, candidate)
    score = _folded_alike(name, candidate)
    if score < need or not (_sounds_alike(name, candidate) or _anchored(name, candidate)):
        return False
    return not any(k.name != candidate and _folded_alike(name, k.name) > score - TIE for k in fits)


def _folded_alike(a: str, b: str) -> float:
    return max(alike(a, b), alike(fold(a), fold(b)))


def _sounds_alike(a: str, b: str) -> bool:
    """A sound code shared as written: never after folding, which makes equal spellings of
    different names (the reviewer)."""
    return bool(set(sound_keys(a)) & set(sound_keys(b)))


def _anchored(a: str, b: str) -> bool:
    """The same words after folding, word by word, with at least one word the same as written."""
    wa, wb = name_key(a).split(), name_key(b).split()
    return (
        len(wa) == len(wb) > 1
        and fold(a) == fold(b)
        and any(x == y for x, y in zip(wa, wb, strict=True))
    )


def _cut(name: str, candidate: str, fits: Sequence[Known]) -> bool:
    """(b) Cut short: the start of the document's name, or of one of its words, with at least
    `MIN_CUT` letters, and the start of no other name of the document."""
    marked = _CUT_MARK.search(name) is not None
    stem = name_key(_CUT_MARK.sub("", name)).replace(" ", "")
    if len(stem) < (MIN_CUT if marked else MIN_UNMARKED):
        return False

    def starts(full: str) -> bool:
        k = name_key(full)
        return k.replace(" ", "").startswith(stem) or any(w.startswith(stem) for w in k.split())

    words = name_key(candidate).split()
    if not starts(candidate) or stem in words or "".join(words) == stem:
        return False  # (a whole word or the whole name is not cut short)
    if (
        not marked
        and not any(w.startswith(stem) and len(stem) >= MIN_SHARE * len(w) for w in words)
        and not "".join(words).startswith(stem)
    ):
        return False
    return not any(k.name != candidate and starts(k.name) for k in fits)


def _initials(name: str, candidate: str, fits: Sequence[Known]) -> bool:
    """(c) Shortened: one or more initials, then one whole last word ("S. Kaltro"). The last word
    must be the candidate's last word (a possessive aside) and each initial the first letter of the
    candidate's first words, in order ("S. Kaltro" fits "Sephek Vel Kaltro" too); initials alone
    never resolve."""
    parts = name.split()
    if len(parts) < 2 or not all(_INITIAL.match(p) for p in parts[:-1]):
        return False

    def fits_it(full: str) -> bool:
        words = name_key(full).split()
        last = spelling_key(parts[-1])
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
