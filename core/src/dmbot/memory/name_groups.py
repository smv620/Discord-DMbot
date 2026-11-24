"""One name heard several ways becomes one suggestion (docs/design/session_names.md, section 5).

The after-session name step hands this every spelling the scan and the name reader found, each with
its hearings, before anything is counted or cut. In the design's order (with the Expert's check and
Supervisor's ruling on #5133):

1. every spelling is grouped by sound (`scan.same_thing`) before any count, so spellings heard once
   each are joined, not lost (rule 3); the reader's kind only breaks a borderline match;
2. a group with any spelling close to a secret (or DM-only) name is dropped whole, never asked
   about and never written (counted, without its names);
3. a group with a spelling close to a name DMbot has is that name heard wrong. Until slice 4 hands
   these to the name pass, the group is offered as one question, as today, and the card asks
   "Sounds like X. The same, or new?": a real new name close to a known one is still asked about;
4. hearings are counted for the group, and the group needs what its strongest reason asks for;
5. the spelling shown: a part never leads its whole, nor a split spelling the joined one; then a
   typed one, else the most heard, else the first found (rule 4); with capitals when nothing typed
   or capitalised was heard;
6. the groups are ordered by how sure DMbot is: introduced or typed first, then times heard.

Every spelling ends exactly once: offered (the shown one), or with one fixed reason. Pure: no store,
no Discord, no AI. Names, never lines, so nothing here is logged.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from dmbot.memory.models import name_key
from dmbot.memory.scan import (
    JOINING,
    MIN_TIMES,
    NEAR_ONE_WORD,
    Match,
    Suggestion,
    alike,
    same_thing,
    sound_keys,
)

_JOINERS = (JOINING, "the")  # never given a capital: "Heart of the Mountain"
SCAN = "scan"  # found by the scan alone (capitals, or the loosened rule beside the reader)
# The name reader's reasons (`name_reader.WHYS`), strongest first, then the scan's
INTRODUCED = "introduced"
WHY_ORDER = (INTRODUCED, "addressed", "place", "spoken_of", SCAN)

# The fixed reasons a spelling ends with here (the dev-tools trace's words)
TOO_FEW = "too_few_hearings"  # the group's hearings are fewer than its reason needs
ORDINARY = "ordinary_word"  # the scan alone, and every word of it is said in lower case too
GROUPED = "grouped_into_another"  # one spelling of a group whose shown spelling is another
# (a count row only, never a name: spellings dropped as close to a secret)
NEAR_SECRET = "close_to_a_secret_name"


@dataclass(frozen=True, slots=True)
class Spelling:
    """One spelling as one reader found it: how many lines hold it, why it is a name (a reader's
    reason, or `SCAN`), how many of the lines it was found in were typed, and the reader's kind
    ('' unknown)."""

    text: str
    times: int
    why: str = SCAN
    typed: int = 0
    kind: str = ""
    ordinary: bool = False  # (the loosened scan's mark: every word also said in lower case)


@dataclass(frozen=True, slots=True)
class _Key:
    """One spelling key with both readers' findings kept apart: both read the same lines, so their
    hearings are never added for one key (a line would count twice), while across a group's keys
    each reader's own hearings add up."""

    text: str
    scan: int  # the scan's hearings of this key
    read: int  # the reader's
    why: str
    typed: int
    kind: str
    ordinary: bool  # the scan alone found it, and every word is said in lower case too

    @property
    def times(self) -> int:
        return max(self.scan, self.read)


@dataclass(frozen=True, slots=True)
class Group:
    """One name: the spelling to show, the others heard, the group's hearings and reasons."""

    name: str
    also: tuple[str, ...]
    times: int
    why: str  # the strongest reason any spelling has
    typed: bool  # a person typed one of its spellings
    kind: str  # the reader's kind given most, '' when none gave one
    heard_wrong: Match | None = None  # the known name it is close to (the card asks, until slice 4)
    typed_times: int = 0  # of its hearings, how many a person typed (the card says so, 3c)

    @property
    def sure(self) -> bool:
        """Introduced by name or typed: offered first (section 6)."""
        return self.why == INTRODUCED or self.typed

    def suggestion(self) -> Suggestion:
        return Suggestion(self.name, self.times, self.also)


@dataclass(slots=True)
class Settled:
    offered: list[Group] = field(default_factory=list)  # most sure first, none dropped for number
    refused: list[tuple[str, str, int]] = field(default_factory=list)  # (name, reason, times)
    near_secret: int = 0  # spellings dropped as close to a secret: counted, never named


def _why(whys: Iterable[str]) -> str:
    return min(whys, key=lambda w: WHY_ORDER.index(w) if w in WHY_ORDER else len(WHY_ORDER))


def needed(why: str, typed: bool) -> int:
    """The hearings a group needs (section 5's table): introduced or typed, once; anything else,
    twice in the group."""
    return 1 if why == INTRODUCED or typed else MIN_TIMES


def _joins(a: _Key, b: _Key) -> bool:
    """Two spellings of one thing (`scan.same_thing`). The reader's kind is a guess the DM corrects,
    so it only breaks a borderline match: spellings as alike as a one-word match needs group
    whatever the kinds say (the Expert, on #5133), so one wrong guess never splits one name into
    two questions. (A spelling with no kind may join two that disagree: then they are one name.)"""
    if not same_thing(a.text, b.text):
        return False
    if not a.kind or not b.kind or a.kind == b.kind:
        return True
    return alike(a.text, b.text, NEAR_ONE_WORD) >= NEAR_ONE_WORD


def _neighbours(keys: Sequence[_Key]) -> list[set[int]]:
    """For each spelling, the others it could fold with: `same_thing` needs a shared sound code or
    a part that begins or ends the other, so only spellings sharing a sound code, a first word or a
    last word are compared (a session's hundreds of spellings, never every pair)."""
    buckets: dict[str, set[int]] = defaultdict(set)
    for i, k in enumerate(keys):
        words = name_key(k.text).split()
        for code in sound_keys(k.text):
            buckets["s:" + code].add(i)
        if words:
            buckets["f:" + words[0]].add(i)
            buckets["l:" + words[-1]].add(i)
    near: list[set[int]] = [set() for _ in keys]
    for members in buckets.values():
        for i in members:
            near[i] |= members
    for i, n in enumerate(near):
        n.discard(i)
    return near


def _group(keys: Sequence[_Key]) -> list[list[int]]:
    """Spellings of one thing, as groups of indexes, longest spellings placed first. A spelling
    joins a group only when it fits exactly one: "Lord" between "Lord Neverember" and "Lord Dagult"
    stays its own question. (Unlike `scan.group_alike`, two longer spellings it fits in one group
    are fine: "Hrothgar" joins "hroth gar" and "Rothgar", heard as one name.)"""
    near = _neighbours(keys)
    order = sorted(range(len(keys)), key=lambda i: (-len(keys[i].text.split()), -keys[i].times))
    home: dict[int, int] = {}
    groups: list[list[int]] = []
    for i in order:
        homes = {home[j] for j in near[i] if j in home and _joins(keys[i], keys[j])}
        if len(homes) == 1:
            g = homes.pop()
            groups[g].append(i)
        else:
            g = len(groups)
            groups.append([i])
        home[i] = g
    return groups


def _part_of(short: str, long_: str) -> bool:
    """`short` is a part of `long_` ("Vane" of "Oskar Vane", "Valley" of "Dwarven Valley"): its
    words begin or end the longer one."""
    part, whole = name_key(short).split(), name_key(long_).split()
    n = len(part)
    return n < len(whole) and (whole[:n] == part or whole[-n:] == part)


def _split_of(split: str, whole: str) -> bool:
    """`split` is `whole` heard in more pieces ("hroth gar" for "Hrothgar"): alike by sound and
    letters, never a whole and its part ("Dwarven Valley" is not "Valley" in pieces)."""
    return (
        len(split.split()) > len(whole.split())
        and not _part_of(whole, split)
        and same_thing(split, whole)
    )


def _shown(group: Sequence[_Key]) -> str:
    """The spelling to show, by two rules for two kinds of fold (the Expert, on #5133):
    - a part never leads its whole ("Dwarven Valley" over "Valley", "Oskar Vane" over "Vane"),
      and a spelling heard in more pieces never leads the joined one ("Hrothgar" over "hroth gar");
    - among the spellings of one name left, a typed one, else the most heard, else the first found
      (`group` keeps the order the readers listed them in: the scan's, most heard first, then the
      reader's), rule 4."""
    texts = [k.text for k in group]
    able = [
        k
        for k in group
        if not any(_part_of(k.text, o) or _split_of(k.text, o) for o in texts if o != k.text)
    ] or list(group)
    typed = [k for k in able if k.typed]
    if typed:
        return max(typed, key=lambda k: k.typed).text
    most = max(k.times for k in able)
    return next(k.text for k in able if k.times == most)


def proposed(name: str, group: Sequence[_Key]) -> str:
    """The spelling DMbot proposes. When nothing in the group was typed or heard with a capital
    (speech has none), each name word gets one, never "of" or "of the": "hrothgar" is proposed as
    "Hrothgar" (the Expert, on #5133). It is only a proposal the DM confirms with "Yes, spelled …";
    the quoted line stays exactly as heard. This shapes the proposal and decides nothing: whether a
    word is a name never depends on its case (design rule 2)."""
    if any(k.typed for k in group) or any(c.isupper() for k in group for c in k.text):
        return name
    words = name.split()
    return " ".join(
        w if i and w.casefold() in _JOINERS else w[:1].upper() + w[1:] for i, w in enumerate(words)
    )


def _merged(spellings: Iterable[Spelling]) -> list[_Key]:
    """One entry per spelling key, in the order first found, both readers' findings kept apart."""
    by_key: dict[str, list[Spelling]] = {}
    for s in spellings:
        if name_key(s.text):
            by_key.setdefault(name_key(s.text), []).append(s)
    out: list[_Key] = []
    for found in by_key.values():
        scan = [s for s in found if s.why == SCAN]
        read = [s for s in found if s.why != SCAN]
        out.append(
            _Key(
                text=max(found, key=lambda s: s.times).text,
                scan=max((s.times for s in scan), default=0),
                read=max((s.times for s in read), default=0),
                why=_why(s.why for s in found),
                typed=max(s.typed for s in found),
                kind=next((s.kind for s in read if s.kind), ""),
                ordinary=bool(scan) and not read and all(s.ordinary for s in scan),
            )
        )
    return out


def settle(
    spellings: Iterable[Spelling],
    *,
    heard_wrong: Callable[[str], Match | None] = lambda _s: None,
    near_secret: Callable[[str], bool] = lambda _s: False,
) -> Settled:
    """Every spelling found this session, in the order found, to the suggestions (section 5).

    `heard_wrong`: the known name a spelling is close to, when exactly one (`scan.near_match_in`).
    `near_secret`: whether a spelling is close to a secret or DM-only name (the name pass's test).
    Hearings for a group: each reader's hearings added across the group's keys, and the larger of
    the two taken (they read the same lines, so adding the two would count one line twice). A
    part's sightings inside its whole are in its own count too ("Valley" inside "Dwarven Valley"),
    as `scan.group_alike` always counted: a group's count can run high, never low."""
    out = Settled()
    keys = _merged(spellings)
    for members in _group(keys):
        members.sort()  # (the order found: "first found" for the shown spelling)
        group = [keys[i] for i in members]
        # 2. close to a secret: the whole group goes, never asked, never written, not even named
        # in the dev trace (a count only)
        if any(near_secret(k.text) for k in group):
            out.near_secret += len(group)
            continue
        times = max(sum(k.scan for k in group), sum(k.read for k in group))
        why = _why(k.why for k in group)
        typed = any(k.typed for k in group)
        heard_as = _shown(group)
        shown = proposed(heard_as, group)
        # 4. the group's hearings against what its strongest reason needs. An ordinary word the
        # scan alone found is never offered, typed or not ("i hope so" typed in chat is no name).
        if all(k.ordinary for k in group):
            refusal = ORDINARY
        elif times < needed(why, typed):
            refusal = TOO_FEW
        else:
            refusal = ""
        if refusal:
            out.refused += [(k.text, refusal, k.times) for k in group]
            continue
        # 3. a name DMbot has, heard wrong: one question for the group, the card asks which
        matches = Counter(m for k in group if (m := heard_wrong(k.text)) is not None)
        kinds = Counter(k.kind for k in group if k.kind)
        # (the heard spelling given capitals is the same name, not another spelling: its key is
        # the shown one's, so the name itself matches it and it is no "also heard as")
        others = tuple(k.text for k in group if name_key(k.text) != name_key(shown))
        out.refused += [(t, GROUPED, 0) for t in others]  # (each spelling ends with one reason)
        out.offered.append(
            Group(
                shown,
                others,
                times,
                why,
                typed,
                kinds.most_common(1)[0][0] if kinds else "",
                matches.most_common(1)[0][0] if matches else None,
                # (each key's typed lines, never more than the group's hearings: a key the scan
                # alone found and one the reader alone found are each counted in `times` once)
                min(sum(k.typed for k in group), times),
            )
        )
    out.offered.sort(key=lambda g: (not g.sure, -g.times, g.name.casefold()))
    return out
