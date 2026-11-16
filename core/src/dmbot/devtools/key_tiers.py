"""Each key row's tier, from the backup and the sample run's decision record (#4523 slice 2).

Supervisor's ruling on #4523 (the tier table, and E and F): how a row the key lists got into the
graph, or why it didn't.

| Tier | When |
|---|---|
| total success | in the graph, and never asked about: DMbot added it by itself |
| partial success | asked, and the AI DM took DMbot's first choice (a yes to a yes-or-no item) |
| provisional success | asked, and the AI DM took another choice the card offered |
| partial failure | not in the graph, and the AI DM said "not a name" or "not a connection" |
| failure | not in the graph otherwise, or found but leaked to a player character |

A row left waiting because the stage reached its cap is **not decided**: its own line, never a
success or a failure. A row in the graph that the AI DM said no to (it came in by another way) is
**found despite a no**, its own line too, since it was asked about. A look-alike answer ("is this
unnamed person that one?") joins or keeps apart two entries; it never adds or refuses a name or a
tie, so no row's tier comes from it. Extras (a name or tie the key doesn't list) are counted by
how they came: added by DMbot itself is a failure, one the AI DM kept is counted, and one flagged
and turned down is counted apart and never a failure.

A decision is matched to a key row by the names it is about (`Decision.names`), compared as the
check compares names (`name_key`), or through the backup's own entries for them. The record stays
on the server: this module reads it and prints counts only.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dmbot.devtools import key_knowers
from dmbot.memory.models import name_key

if TYPE_CHECKING:
    from dmbot.devtools.graph_key_check import Report

TOTAL, PARTIAL, PROVISIONAL = "total success", "partial success", "provisional success"
PARTIAL_FAILURE, FAILURE = "partial failure", "failure"
TIERS = (TOTAL, PARTIAL, PROVISIONAL, PARTIAL_FAILURE, FAILURE)
NOT_DECIDED = "not decided"  # (decide.NOT_DECIDED: the record's word for an item left at the cap)
FOUND_DESPITE_NO = "found despite a no"  # (asked, said no, and in the graph all the same)
EXTRA_ITSELF, EXTRA_KEPT, EXTRA_TURNED_DOWN = "added by itself", "kept by the AI DM", "turned down"

# The decision kinds (decide.KINDS) by what they are about: a name, or a tie between two names.
# (Look-alikes are neither: see the note at the top.) A no to one of the flagged kinds is "not a
# name" or "not a connection"; a "Which one?"'s "neither" is not about whether a tie is real.
NAME_KINDS = frozenset({"names"})
TIE_KINDS = frozenset({"which one", "things said", "connections"})
FLAGGED_KINDS = frozenset({"names", "things said", "connections"})
YES, NO = "yes", "no"


def read_record(path: Path) -> list[dict[str, Any]]:
    """The decision record's lines (one JSON object each); a line that isn't one is skipped."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("kind"), str):
            out.append(row)
    return out


class _Names:
    """Does a word a decision was about name a key entry? By the key's own names, or by the backup
    entries the word and the key entry both find."""

    def __init__(
        self,
        key: Mapping[str, Any],
        ids_of_word: Callable[[str], set[str]],
        ids_of_key: Callable[[str], set[str]],
    ) -> None:
        self.keys = {
            n["name"]: {name_key(x) for x in (n["name"], *n.get("aliases", ()))}
            for n in key["nodes"]
        }
        self.ids_of_word = ids_of_word
        self.ids_of_key = ids_of_key

    def same(self, word: str, entry: str) -> bool:
        if not word:
            return False
        if name_key(word) in self.keys.get(entry, ()):
            return True
        return bool(self.ids_of_word(word) & self.ids_of_key(entry))


def _ends(d: Mapping[str, Any]) -> tuple[str, list[str]]:
    """A tie decision's two ends: the first name, and what the other may be (a "Which one?"'s
    pick, or every candidate it offered when none was picked)."""
    names = list(d.get("names") or ())
    first = names[0] if names else ""
    if d.get("kind") == "which one":
        return first, [d["picked"]] if d.get("picked") else list(d.get("offered") or ())
    return first, names[1:2]


def _touches_node(d: Mapping[str, Any], entry: str, names: _Names) -> bool:
    return d.get("kind") in NAME_KINDS and any(names.same(w, entry) for w in d.get("names") or ())


def _touches_tie(d: Mapping[str, Any], a: str, b: str, names: _Names) -> bool:
    if d.get("kind") not in TIE_KINDS:
        return False
    one, others = _ends(d)
    return (names.same(one, a) and any(names.same(o, b) for o in others)) or (
        names.same(one, b) and any(names.same(o, a) for o in others)
    )


def _took_first(d: Mapping[str, Any]) -> bool:
    """A yes took DMbot's first choice: a yes-or-no item (nothing offered), or the first offered,
    by place when the record has it (two candidates can share a name)."""
    if not d.get("offered"):
        return True
    if d.get("picked_at"):
        return int(d["picked_at"]) == 1
    return bool(d.get("picked") == d.get("first"))


def tier(found: bool, leaked: bool, decisions: Sequence[Mapping[str, Any]]) -> str:
    """One key row's tier, from whether it is in the graph (an entry confirmed with the right
    kind; a tie confirmed with the right words), whether a player character knows it who
    shouldn't, and the decisions about it in order."""
    done = [d for d in decisions if d.get("done", True)]
    yes = [d for d in done if d.get("decision") == YES]
    if found:
        if leaked:
            return FAILURE
        if yes:
            return PARTIAL if _took_first(yes[-1]) else PROVISIONAL
        if any(d.get("decision") == NO for d in done):
            return FOUND_DESPITE_NO
        return TOTAL  # (never asked, or asked and left "not sure": DMbot's own)
    if any(d.get("decision") == NO for d in done):
        return PARTIAL_FAILURE
    if any(d.get("decision") == NOT_DECIDED for d in decisions) and not yes:
        return NOT_DECIDED
    return FAILURE


def score(
    report: Report,
    key: Mapping[str, Any],
    record: Sequence[Mapping[str, Any]],
    ids_of_word: Callable[[str], set[str]],
    ids_of_key: Callable[[str], set[str]],
    confirmed_entries: Collection[str],
    extra_entities: Sequence[Mapping[str, Any]],
    extra_relations: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    found_status: str,
    by_design: str,
) -> None:
    """Fill `report.tiers`, `tiers_must`, `extras` and `unordered` (a `graph_key_check.Report`,
    whose entry and tie rows are already scored). `confirmed_entries`: the key entries found as a
    confirmed entry (a waiting one is not in the graph yet); `extra_entities`: the confirmed
    entries the key doesn't list; `extra_relations`: the two ends of each confirmed tie between key
    entries it doesn't list."""
    names = _Names(key, ids_of_word, ids_of_key)
    leaked = {(k.section, k.id) for k in report.knowers if k.verdict == key_knowers.LEAKED}
    pairs = {str(t["id"]): t for t in key["pairs"]}
    # (a decision about any key row, scored here or not, is never an extra turned down)
    touched = {
        i
        for i, d in enumerate(record)
        if any(_touches_node(d, str(n["name"]), names) for n in key["nodes"])
        or any(_touches_tie(d, str(t["a"]), str(t["b"]), names) for t in key["pairs"])
    }

    def count(row_tier: str, must: bool) -> None:
        report.tiers[row_tier] += 1
        if must:
            report.tiers_must[row_tier] += 1

    for row in report.entries:
        if row.status == by_design:
            continue
        mine = [d for d in record if _touches_node(d, row.id, names)]
        found = row.status == found_status and row.id in confirmed_entries
        count(tier(found, ("entry", row.id) in leaked, mine), row.must)
    for row in report.ties:
        t = pairs[row.id]
        mine = [d for d in record if _touches_tie(d, t["a"], t["b"], names)]
        count(tier(row.status == found_status, ("tie", row.id) in leaked, mine), row.must)

    def kept(d: Mapping[str, Any]) -> bool:
        return d.get("decision") == YES and bool(d.get("done", True))

    def is_(word: str, entity: Mapping[str, Any]) -> bool:
        """A decision's word names this backup entry: its name, or an entry the word finds."""
        return bool(word) and (
            name_key(word) == name_key(str(entity["name"]))
            or str(entity["id"]) in ids_of_word(word)
        )

    kept_names = [d for d in record if d.get("kind") in NAME_KINDS and kept(d)]
    for e in extra_entities:
        by_ai = any(is_(w, e) for d in kept_names for w in d.get("names") or ())
        report.extras[EXTRA_KEPT if by_ai else EXTRA_ITSELF] += 1
    kept_ties = [d for d in record if d.get("kind") in TIE_KINDS and kept(d)]
    for a, b in extra_relations:
        by_ai = any(
            (is_(one, a) and any(is_(o, b) for o in others))
            or (is_(one, b) and any(is_(o, a) for o in others))
            for one, others in map(_ends, kept_ties)
        )
        report.extras[EXTRA_KEPT if by_ai else EXTRA_ITSELF] += 1
    report.extras[EXTRA_TURNED_DOWN] += sum(
        1
        for i, d in enumerate(record)
        if i not in touched
        and d.get("kind") in FLAGGED_KINDS
        and d.get("decision") == NO
        and d.get("done", True)
    )
    report.unordered = sum(1 for d in record if d.get("no_order"))
