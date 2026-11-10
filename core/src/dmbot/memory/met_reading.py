"""Who and what the party met in play that the story never named (#3172 slice 5). Pure: no
database, no AI call, nothing written.

A wolf on the road, two ambushers, the sled dogs: the owner's rank 0, "anyone or anything met;
kept so they can be raised later". The session reader is asked for them in the same call as its
claims (`MET_ADDENDUM`, so the switch costs no extra call), and the store keeps each as a quiet
unnamed entry at rank 0 (`MemoryStore.add_met`). Lore uploads keep their own rule
(`unnamed_read`); this is for play only (Supervisor's ruling on #3172, 2026-11-01).

Rules held in code, not trust (Supervisor's ruling, 2026-11-10):
- **Only the DM's own words.** Every item cites at least one line the DM spoke, and its words are
  found in those DM lines: a player's guess ("there's a yeti near") never makes an entry and never
  says what something is.
- **One entry per encounter.** A group is one item with a count, never one per goblin; the same
  words twice in a session are one item (the count is the larger, the lines both).
- **A cap per session** (`MAX_PER_SESSION`, counting a group as one): what is past it is counted,
  never made.
- **The transcript is data.** Words that read like an instruction are refused.

Counts only leave this module; the words of a transcript are never logged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from dmbot.memory import unnamed as unnamed_mod
from dmbot.memory.claims import LINES_MAX, reads_like_instruction
from dmbot.memory.readings import PRONOUNS

MAX_PER_ANSWER = 10  # one batch of ten lines meets this many at most
MAX_PER_SESSION = 10  # Supervisor, 2026-11-10: the sample session met 4 (11 if counted one by one)
MAX_COUNT = 999  # a count past this is "several"
WHAT_WORDS = 8  # "the barbarian with the maul" is a description; a sentence is not
SEVERAL = 0  # the count when a line says only "some" or "several"
PROMPT_SUFFIX = "+met-1"  # added to the claims' prompt version when the addendum is sent

MET_ADDENDUM = """

Also list who and what the party met in this stretch that is never named in it: a creature, a
person or a group the DM's own lines say is there with the party (the party talks with it, fights
it, travels with it, or the DM says the party senses it). Only what a DM line says: a player's
guess or fear ("there's a yeti near") never counts, and never says what something is. Leave out
anyone with a name, anyone only talked about, remembered or imagined, and the people at the table.
A group is one item ("two barbarians", "the sled dogs"), never one for each.
Give them as "met", beside "claims":
{"claims": [...], "met": [{"what": "barbarian", "count": 2, "lines": [4]}]}
"what" is the DM's own word for them, as short as still tells them apart; "count" is how many
(0 when the line says only "some" or "several"). Give "met": [] if there are none."""

_WORD = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")
_SKIP_WORDS = frozenset({"a", "an", "the", "of", "with", "and", "in", "on", "at", "from"})


@dataclass(frozen=True)
class Met:
    """One unnamed being or group the DM's lines show the party meeting. `count` 1 is one being;
    anything else is a group (`SEVERAL` when no number was said)."""

    what: str
    count: int
    lines: tuple[str, ...]

    @property
    def group(self) -> bool:
        return self.count != 1


@dataclass
class Parsed:
    met: list[Met] = field(default_factory=list)
    refused: int = 0  # not readable, a bad line, no DM line, or not in the DM's words
    not_dm: int = 0  # of those, what only a player's line said (counted on its own)


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold().replace("’", "'"))


def _stem(word: str) -> str:
    """Enough to find "barbarians" for "barbarian" and "wolves" for "wolf": a quote check, not a
    dictionary."""
    for end, put in (("ves", "f"), ("ies", "y"), ("es", ""), ("s", "")):
        if word.endswith(end) and len(word) > len(end) + 2:
            return word[: -len(end)] + put
    return word


def in_words_of(what: str, said: Iterable[str]) -> bool:
    """Every word of `what` that tells something apart is in `said` (the DM's lines). Pure."""
    heard = {_stem(w) for text in said for w in _words(text)}
    wanted = [_stem(w) for w in _words(what) if w not in _SKIP_WORDS]
    return bool(wanted) and all(w in heard for w in wanted)


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None  # (a JSON true is not a number)
    if isinstance(value, int):
        return value if 0 <= value <= MAX_COUNT else (SEVERAL if value > MAX_COUNT else None)
    if value is None:
        return SEVERAL
    return None


def _lines(value: Any, known: Collection[str]) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not value:
        return None
    out = tuple(dict.fromkeys(str(n) for n in value if not isinstance(n, bool)))
    if not out or any(n not in known for n in out):
        return None
    return out[:LINES_MAX]


def parse_met(
    text: str,
    said: Mapping[str, str],
    *,
    dm_lines: Collection[str],
) -> Parsed:
    """The "met" items in one answer of the claims call. `said` is the batch's lines as sent (line
    number to words); `dm_lines` (required, so the rule cannot be left out) are those the DM spoke.
    An answer with no "met" key, or one that can't be read, gives nothing and counts nothing: the
    claims parser already counts an unreadable answer."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return Parsed()
    items = data.get("met") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return Parsed()
    out = Parsed(refused=max(0, len(items) - MAX_PER_ANSWER))
    dm = {str(n) for n in dm_lines}
    for item in items[:MAX_PER_ANSWER]:
        if not isinstance(item, dict):
            out.refused += 1
            continue
        raw = item.get("what")
        what = unnamed_mod.tidy(raw) if isinstance(raw, str) else ""
        count = _count(item.get("count"))
        lines = _lines(item.get("lines"), said.keys())
        if (
            not what
            or count is None
            or lines is None
            or len(what.split()) > WHAT_WORDS
            or what.casefold() in PRONOUNS
            or reads_like_instruction(what)
        ):
            out.refused += 1
            continue
        from_dm = [n for n in lines if n in dm]
        if not from_dm or not in_words_of(what, (said[n] for n in from_dm)):
            out.refused += 1
            out.not_dm += 1  # (a player's guess never makes an entry, nor names its kind)
            continue
        out.met.append(Met(what, count, tuple(from_dm)))
    return out


def key(what: str) -> str:
    """Two items with this key are the same encounter's: the store's own same-person key."""
    return unnamed_mod.same_person_key(what, None)


def gathered(found: Iterable[Met], cap: int = MAX_PER_SESSION) -> tuple[list[Met], int]:
    """The session's items, one per key in the order first met (the larger count, every line, up
    to `LINES_MAX`), and how many were past `cap`. Pure."""
    by_key: dict[str, Met] = {}
    for m in found:
        k = key(m.what)
        had = by_key.get(k)
        if had is None:
            by_key[k] = m
            continue
        numbers = [c for c in (had.count, m.count) if c != SEVERAL]  # ("several" said no number)
        count = max(numbers) if numbers else SEVERAL
        lines = tuple(dict.fromkeys((*had.lines, *m.lines)))[:LINES_MAX]
        by_key[k] = Met(had.what, count, lines)
    items = list(by_key.values())
    return items[:cap], max(0, len(items) - cap)
