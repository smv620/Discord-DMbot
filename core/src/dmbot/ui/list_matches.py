"""A names list against the names DMbot already knows (📥 Add many, #369). Pure: no
Discord, no database.

Exact matches fold in quietly; near matches ask; DMbot never joins names by sound on its
own. For anyone but the campaign's DMs, a match against a secret name behaves exactly as
no match: they never learn one exists.

- **Same name** (the line's name or any of its other names is already known): the line
  folds into that entry. Its names the entry doesn't have yet are added as other names,
  in the same change as the new names, so one Undo takes everything back. A kind that
  differs is never changed here: it's counted and asked about after saving.
- **Near name** (spelled almost the same as a confirmed name, or as a line above it):
  saved as a suggestion, as before, and asked about after saving: same name, or
  different?
- Matching uses the copy's sound index: one look-up per sound code, never a scan.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from dmbot.memory.lookup import CampaignLookup, NameEntry
from dmbot.memory.models import CONFIRMED, PROPOSED, MoreNames, NewName, name_key
from dmbot.memory.name_list import MAX_ADDED, MAX_PER_NAME, PC, ListLine
from dmbot.memory.sounds import sound_codes
from dmbot.transcript.cleaner import likeness

NEAR = 0.9  # spelled this alike: asked about
NEAR_SOUND = 0.8  # this alike, with a shared sound, for names of more than one word
# One-word names use NEAR only: real words sound like names too (the Cleaner's lesson).
UNKNOWN_KIND = "concept"  # "other": counts as no kind when comparing kinds
# Spelling comparisons are slow (difflib), so they're bounded: a sound shared by more
# names than this says little, and one list gets at most BUDGET of them. A line that
# runs out is saved as a suggestion instead, so nothing is confirmed unchecked.
CROWDED = 64
BUDGET = 50_000


@dataclass(frozen=True, slots=True)
class Near:
    """A new name spelled almost like a known one, asked about after saving."""

    position: int  # in `Plan.new`
    name: str
    like: str  # the known name, as written
    entity_id: str | None  # the known entry; None when it's a name earlier in the list
    like_position: int | None = None  # that earlier name's place in `Plan.new`


@dataclass(frozen=True, slots=True)
class KindDiffers:
    """A known name whose kind the list gives differently. Never changed unasked."""

    entity_id: str
    name: str
    have: str  # its kind in DMbot
    want: str  # the list's kind


@dataclass(slots=True)
class Plan:
    new: list[NewName] = field(default_factory=list)
    more: list[MoreNames] = field(default_factory=list)
    groups: dict[str, list[int]] = field(default_factory=dict)  # kind word → `new` places
    near: list[Near] = field(default_factory=list)
    kinds: list[KindDiffers] = field(default_factory=list)
    swapped: list[tuple[str, str]] = field(default_factory=list)  # (other name, main name)
    known: int = 0  # lines that were names DMbot already knows
    enriched: int = 0  # of those, how many got new other names
    look: int = 0  # new names waiting in 📝 Check new names that aren't asked about
    dropped: int = 0  # other names already used by another name
    repeated: int = 0  # lines that turned out to be a name earlier in the list


@dataclass(frozen=True, slots=True)
class _Listed:
    position: int
    name: str
    kind: str | None


def plan(lines: list[ListLine], names: CampaignLookup, *, secrets: bool) -> Plan:
    """What to save and what to ask. `secrets`: whether the uploader may see secret names
    (the campaign's DMs)."""
    out = Plan()
    # Who owns each name key: a known entry's ID, or a new name's place in `out.new`.
    owner: dict[str, str | int] = {}
    for entry in names.names:
        if secrets or not entry.secret:
            owner.setdefault(entry.key, entry.entity_id)
    more: dict[str, tuple[list[str], list[str]]] = {}
    folded: set[str] = set()  # keys this list adds to known names
    listed: dict[str, list[_Listed]] = defaultdict(list)  # sound code → new names
    budget = [BUDGET]
    for line in lines:
        key = name_key(line.name)
        owners = {
            owner[k] for k in map(name_key, (line.name, *line.others, *line.secrets)) if k in owner
        }
        # The name itself decides; otherwise its other names, if they agree. Other names of
        # two different names are no reason to guess: it's saved as a suggestion.
        target = owner[key] if key in owner else (next(iter(owners)) if len(owners) == 1 else None)
        if isinstance(target, int):  # a name earlier in the list, by another of its names
            _fold_new(out, target, line, owner)
            continue
        if target is not None:
            _fold_known(out, names, target, line, owner, more, folded)
            continue
        others = [o for o in line.others if name_key(o) not in owner]
        secret = [x for x in line.secrets if name_key(x) not in owner]
        out.dropped += len(line.others) - len(others) + len(line.secrets) - len(secret)
        position = len(out.new)
        near, unchecked = _near(position, line, names, listed, budget)
        sounds_known = _sounds_known(names, line.name)
        unclear = line.kind is None or sounds_known or near is not None or bool(owners) or unchecked
        if near is not None:
            out.near.append(near)
        elif unclear:
            out.look += 1
        if line.kind is None and not sounds_known and near is None:
            # Asked once per word after saving: "every wizard is an NPC".
            out.groups.setdefault(" ".join(line.kind_word.casefold().split()), []).append(position)
        out.new.append(
            NewName(
                line.name, line.kind or UNKNOWN_KIND, PROPOSED if unclear else CONFIRMED,
                tuple(others), tuple(secret),
            )
        )  # fmt: skip
        for k in (key, *map(name_key, others), *map(name_key, secret)):
            owner[k] = position
        if line.kind is not None and not unclear:  # a confirmed name: later lines compare
            for code in sound_codes(line.name):
                listed[code].append(_Listed(position, line.name, line.kind))
    out.more = [MoreNames(e, tuple(o), tuple(s)) for e, (o, s) in more.items() if o or s]
    out.enriched = len(out.more)
    return out


def too_many(p: Plan, names: CampaignLookup, quote: Callable[[str], str] = str) -> str | None:
    """Why a list adds too many other or secret names to save, in plain words; None if
    it's fine (#598). Counts only what the list adds: a name's lines are already joined
    here, and names a known name has already are left out."""
    total = 0
    for name, others, secrets in (
        *((n.name, n.others, n.secrets) for n in p.new),
        *((names.entities[m.entity_id].name, m.others, m.secrets) for m in p.more),
    ):
        for word, given in (("other", others), ("secret", secrets)):
            if len(given) > MAX_PER_NAME:
                return (
                    f"{quote(name)} has more than {MAX_PER_NAME} {word} names in this list. "
                    "Keep the ones people say most."
                )
        total += len(others) + len(secrets)
    if total > MAX_ADDED:
        return f"This list has more than {MAX_ADDED:,} other names. Split it into two uploads."
    return None


def _fold_known(
    out: Plan,
    names: CampaignLookup,
    entity_id: str,
    line: ListLine,
    owner: dict[str, str | int],
    more: dict[str, tuple[list[str], list[str]]],
    folded: set[str],
) -> None:
    entity = names.entities[entity_id]
    # Named by a name this list just gave it: the same name listed twice, not a known one.
    again = name_key(line.name) in folded
    if again:
        out.repeated += 1
    else:
        out.known += 1
    others, secret = more.setdefault(entity_id, ([], []))
    added = 0
    for text, into in (
        *((t, others) for t in (line.name, *line.others)),
        *((t, secret) for t in line.secrets),
    ):
        k = name_key(text)
        if k not in owner:
            into.append(text)
            owner[k] = entity_id
            folded.add(k)
            added += 1
        elif owner[k] != entity_id:
            out.dropped += 1
    if again:
        return
    main = name_key(entity.name)
    if not added and name_key(line.name) != main and owner.get(name_key(line.name)) == entity_id:
        out.swapped.append((line.name, entity.name))
    if (
        line.kind is not None
        and line.kind not in (UNKNOWN_KIND, entity.type)
        and entity.type != PC
        and entity_id not in {k.entity_id for k in out.kinds}
    ):
        out.kinds.append(KindDiffers(entity_id, entity.name, entity.type, line.kind))


def _fold_new(out: Plan, position: int, line: ListLine, owner: dict[str, str | int]) -> None:
    """A line that names a new name from earlier in the list: one name, all its names."""
    out.repeated += 1
    first = out.new[position]
    others, secret = list(first.others), list(first.secrets)
    for text, into in (
        *((t, others) for t in (line.name, *line.others)),
        *((t, secret) for t in line.secrets),
    ):
        k = name_key(text)
        if k not in owner:
            into.append(text)
            owner[k] = position
        elif owner[k] != position:
            out.dropped += 1
    out.new[position] = NewName(first.name, first.type, first.status, tuple(others), tuple(secret))


def _alike(name: str, other: str, one_word: bool) -> float:
    floor = NEAR if one_word else NEAR_SOUND
    a, b = len(name), len(other)
    if 2 * min(a, b) / (a + b) < floor:  # the most two spellings this long can share
        return 0.0
    score = likeness(name, other)
    return score if score >= floor else 0.0


def _near(
    position: int,
    line: ListLine,
    names: CampaignLookup,
    listed: dict[str, list[_Listed]],
    budget: list[int],
) -> tuple[Near | None, bool]:
    """The known name this one is spelled almost like (same kind, or either unknown), if
    any, and whether some names couldn't be compared (`budget` ran out, or a sound was
    too crowded). Only names that share a sound with it are compared, each once. Never a
    secret name: joining to it would make the new spelling a name players see."""
    key = name_key(line.name)
    one_word = len(key.split()) == 1
    best: tuple[float, Near] | None = None
    unchecked = False
    seen: set[str] = set()

    def spend(bucket: int) -> bool:
        nonlocal unchecked
        if bucket > CROWDED or budget[0] < bucket:
            unchecked = True
            return False
        budget[0] -= bucket
        return True

    def kinds_fit(kind: str | None) -> bool:
        return line.kind is None or kind in (None, UNKNOWN_KIND) or kind == line.kind

    for code in sound_codes(line.name):
        entries = names.by_sound.get(code, ())
        for entry in entries if spend(len(entries)) else ():
            if entry.alias_id in seen:
                continue
            seen.add(entry.alias_id)
            entity = names.entities.get(entry.entity_id)
            if entity is None or not _usable(entry, key) or not kinds_fit(entity.type):
                continue
            score = _alike(line.name, entry.text, one_word)
            if score and (best is None or score > best[0]):
                best = (score, Near(position, line.name, entry.text, entry.entity_id))
        earlier_lines = listed.get(code, [])
        for earlier in earlier_lines if spend(len(earlier_lines)) else ():
            if f"#{earlier.position}" in seen:
                continue
            seen.add(f"#{earlier.position}")
            if name_key(earlier.name) == key or not kinds_fit(earlier.kind):
                continue
            score = _alike(line.name, earlier.name, one_word)
            if score and (best is None or score > best[0]):
                best = (score, Near(position, line.name, earlier.name, None, earlier.position))
    return (None if best is None else best[1]), unchecked


def _usable(entry: NameEntry, key: str) -> bool:
    return entry.confirmed and entry.key != key and not entry.secret


def _sounds_known(names: CampaignLookup, name: str) -> bool:
    """It sounds like a different name everyone may know (Bell Eros and Belleros): saved
    as a suggestion for the DM to check."""
    key = name_key(name)
    return any(
        entry.confirmed and not entry.secret and entry.key != key
        for code in sound_codes(name)
        for entry in names.by_sound.get(code, ())
    )
