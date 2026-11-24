"""Things and places a session singles out with no name of its own (#5173, session names slice 6;
docs/design/session_names.md, section 4; Supervisor's ruling with the Expert's check).

"The gemstone mine", "the azure tome": a particular thing or place the story treats as one of a kind
but never names. The name reader lists one with the reason `singled_out` (`name_reader`); this
module decides, in code and never on the reader's word, which are offered to the DM:

1. **It came back** (the ruling's first way in): heard in at least 3 lines, in 2 stretches at least
   `name_reader.BATCH_LINES` lines apart, so one scene's door or campfire never qualifies. The
   ruling's second way ("built on it": 2 lines when another reader used it) waits for the sample's
   count of which way finds each of its rows; a candidate it would be for is traced
   `two_lines_only`, so that count can be made.
2. **A shorter return** ("the mine" after "the gemstone mine") counts only when the reader cited the
   whole phrase in at least `WHOLE_LINES` lines (one "north gate" never makes every "the gate" its
   own) and no other singled-out phrase this session shares that head word (two mines are never
   one); counted per candidate for the trace (the Expert, on #5173).
3. **Never a name DMbot has:** refused when the whole phrase, or its head word, is a known name.
   "Garret's old sled" stays: the possessor tells this sled from others. No connection is written
   from the possessive.
4. **An item is one particular thing** (`unique_item.keeps`), and never a player character's own
   gear ("Cerric's spellbook").
5. **At most `SINGLED_MAX` a session,** most heard first; the rest are dropped, not held (the
   ruling's stated exception to section 9: these are not names, and one that matters comes back).

Kinds are an item or a place only (the reader's check refuses any other). The ordinary-word lists
don't refuse "the door": the two stretches, the limit and the reader do that work (the ruling).
Pure: no store, no Discord, no AI. Names only, so nothing here is logged.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from dmbot.memory import unique_item
from dmbot.memory.models import name_key
from dmbot.memory.name_reader import BATCH_LINES, KNOWN_NAME, SINGLED_OUT, Found

SINGLED_MAX = 3  # offered a session at most (the ruling: "a few")
CAME_BACK_LINES = 3  # lines holding it...
STRETCH_APART = BATCH_LINES  # ...the first and last at least this many lines apart
WHOLE_LINES = 2  # lines holding the whole phrase before its shorter returns count

# The fixed reasons a candidate ends with (the dev tools' trace words)
OFFERED = "offered"
TWO_LINES_ONLY = "two_lines_only"  # heard twice: what "built on it" would need (counted, 6a)
TOO_FEW = "too_few_hearings"
ONE_STRETCH = "one_stretch"  # heard 3 times or more, but all in one stretch of the session
EVERYDAY_ITEM = "everyday_item"  # an item that is not one particular thing, or a player's gear
OVER_SINGLED = "over_singled"  # past `SINGLED_MAX` this session: dropped, never held
SHORTER_RETURN = "shorter_return"  # a count row: lines counted by the head word alone

_SMALL = frozenset({"a", "an", "the", "this", "that", "of", "from", "in", "on", "at", "for", "and",
                    "to", "with", "by", "under", "upon"})  # fmt: skip
_LEADING = frozenset({"a", "an", "the", "this", "that"})
_POSSESSOR = re.compile(r"^(.+?)['’]s?\s+\S")


@dataclass(frozen=True, slots=True)
class Kept:
    """One singled-out thing or place offered to the DM: the name proposed (a capital on its
    first word only: a description, never "Gemstone Mine"), the words as heard, its kind, its
    hearings, the lines that hold it (the reader's numbers) and `said`: the words as a cited line
    says them, without an article ("gemstone mine", "Easthaven ferry"), kept as the entry's mark
    (`Entity.described`)."""

    name: str
    heard: str
    kind: str
    times: int
    lines: tuple[str, ...]
    said: str = ""


@dataclass(slots=True)
class Decided:
    kept: list[Kept] = field(default_factory=list)
    refused: list[tuple[str, str, int]] = field(default_factory=list)  # (heard, reason, times)
    # (heard, lines counted by its head word alone: "the mine"), per candidate that had any
    shorter_returns: list[tuple[str, int]] = field(default_factory=list)


def head_word(phrase: str) -> str:
    """The word the phrase is about: its last word that isn't small, before any "of" ("mine" of
    "gemstone mine", "sled" of "Garret's old sled", "tome" of "tome of ages")."""
    words = name_key(phrase).split()
    if "of" in words[1:]:
        words = words[: words.index("of", 1)]
    content = [w for w in words if w not in _SMALL]
    return content[-1] if content else ""


def proposed(heard: str, lines: Iterable[str] = ()) -> str:
    """A description takes a capital on its first word only ("Gemstone mine"): capitals on each
    word would claim a name the story never gave (the Expert, on #5173). A later word keeps a
    capital only where the lines give it one ("old Bryn Shander gate"); the reader's own capitals
    count for nothing."""
    words = " ".join(heard.split()).split()
    if not words:
        return ""
    said = {w for line in lines for w in re.findall(r"[^\W_]+(?:['’][^\W_]+)*", line)}
    out = [words[0][:1].upper() + words[0][1:]]
    for w in words[1:]:
        cap = w[:1].upper() + w[1:]
        out.append(cap if cap in said else w[:1].lower() + w[1:])
    return " ".join(out)


def _bare(phrase: str) -> str:
    words = " ".join(phrase.split()).split()
    while len(words) > 1 and words[0].casefold() in _LEADING:
        words = words[1:]
    return " ".join(words)


def _phrase(heard: str) -> re.Pattern[str]:
    """The whole phrase in a line, whatever its case and spacing ("Easthaven  ferry")."""
    words = [re.escape(w) for w in _bare(heard).split()]
    return re.compile(r"(?<![^\W_])" + r"\s+".join(words) + r"(?![^\W_])", re.IGNORECASE)


def as_said(heard: str, lines: Iterable[str]) -> str:
    """The words as the first cited line that holds them says them, without an article: its own
    capitals ("Easthaven ferry", "gemstone mine"), never DMbot's. None holds them (the reader
    reworded): the reader's words, without an article."""
    bare = _bare(heard)
    if not bare:
        return ""
    pattern = _phrase(bare)
    for line in lines:
        match = pattern.search(line)
        if match:
            return " ".join(match.group(0).split())
    return bare


def described_heads(described: Iterable[str]) -> set[str]:
    """The head words of the campaign's live descriptions (`Entity.described`) that no other live
    description shares: "mine" once "gemstone mine" is kept, so a later "the mine" is never offered
    as something new (the Expert, on #5173). Two descriptions with one head word ("north gate",
    "south gate") refuse neither word: which one a "the gate" means can't be told."""
    heads = [head_word(d) for d in described]
    return {h for h in heads if h and heads.count(h) == 1}


def player_keys(names: Iterable[str]) -> set[str]:
    """The keys a player character's own gear may be named by ("Cerric's", "Cerric Ambere's"): each
    of its names and aliases whole, and the first word of each (the reviewer, on #5173)."""
    out: set[str] = set()
    for n in names:
        key = name_key(n)
        if key:
            out |= {key, key.split()[0]}
    return out


def _possessor(phrase: str) -> str:
    found = _POSSESSOR.match(" ".join(phrase.split()))
    return name_key(found.group(1)) if found else ""


def _stretches(lines: Iterable[str]) -> bool:
    numbers = sorted(int(n) for n in lines if n.isdigit())
    return len(numbers) >= CAME_BACK_LINES and numbers[-1] - numbers[0] >= STRETCH_APART


def decide(
    found: Sequence[Found],
    texts: Mapping[str, str],
    known: Collection[str],
    players: Collection[str] = (),
    live: Collection[str] | None = None,
    is_item: Callable[[str, str], bool] = unique_item.keeps,
) -> Decided:
    """The session's singled-out candidates (`name_reader.tally(..., every=True)` output with the
    reason `singled_out`; any other is ignored) to the few offered. `texts`: every line the reader
    read, by its number (numbered from 1 in the order read, so a difference is a distance in
    lines). `known`: name keys DMbot has or must never suggest. `players`: the name keys of the
    player characters, whose own gear is never singled out. `is_item`: the one-particular-thing
    test (`unique_item.keeps`), given the phrase and the words it was said in. `live`: the name keys
    of the campaign's live entries, which alone may refuse a phrase by its head word (a rejected
    word, a said-as or a player's name word never does); None: `known` for both."""
    out = Decided()
    mine = [f for f in found if f.why == SINGLED_OUT]
    heads = [head_word(f.name) for f in mine]
    shared = {h for h in heads if heads.count(h) > 1}
    ready: list[Kept] = []
    for f, head in zip(mine, heads, strict=True):
        key = name_key(f.name)
        if not key or not head or key in known or head in (known if live is None else live):
            out.refused.append((f.name, KNOWN_NAME, f.times))
            continue
        said = " ".join(texts.get(n, "") for n in f.lines)
        if f.kind == "item" and (_possessor(f.name) in players or not is_item(f.name, said)):
            out.refused.append((f.name, EVERYDAY_ITEM, f.times))
            continue
        lines = [n for n in f.lines if n in texts]  # (only lines still readable: consent)
        whole = _phrase(f.name)
        if head not in shared and sum(bool(whole.search(texts[n])) for n in lines) >= WHOLE_LINES:
            # a shorter return: "the mine", when no other phrase has "mine"
            short = re.compile(rf"\b(?:the|that|this)\s+{re.escape(head)}\b", re.IGNORECASE)
            more = [n for n, t in texts.items() if n not in lines and short.search(t)]
            if more:
                out.shorter_returns.append((f.name, len(more)))
            lines += more
        if not _stretches(lines):
            reason = (
                ONE_STRETCH
                if len(lines) >= CAME_BACK_LINES
                else TWO_LINES_ONLY
                if len(lines) == 2
                else TOO_FEW
            )
            out.refused.append((f.name, reason, len(lines)))
            continue
        ordered = tuple(sorted(dict.fromkeys(lines), key=lambda n: int(n) if n.isdigit() else 0))
        cited = [texts[n] for n in ordered if n in texts]
        words = as_said(f.name, cited)
        ready.append(Kept(proposed(f.name, cited), f.name, f.kind, len(ordered), ordered, words))
    ready.sort(key=lambda k: (-k.times, k.name.casefold()))
    out.kept = ready[:SINGLED_MAX]
    out.refused += [(k.heard, OVER_SINGLED, k.times) for k in ready[SINGLED_MAX:]]
    return out
