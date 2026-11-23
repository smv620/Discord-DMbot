"""Scores a campaign backup against the story graph answer key (#3244), with counts and ids only.

    python -m dmbot.devtools.graph_key_check BACKUP.dmbot.json.gz
        [--key core/tests/fixtures/story_graph/master_key.json] [--docs lore|session|both]
        [--counts-only] [--json OUT.json] [--why-missed [--decisions decisions-*.jsonl ...]]

The key lists what the graph should hold after the public sample lore and session are read (see
`core/tests/fixtures/story_graph/README.md`). This tool takes the campaign's backup (the file the
bot's backup button gives) and says, for entries and for ties: found, missing, wrong type, found
only as a waiting suggestion, and extra. It reads the backup file and nothing else: no database, no
Discord, no AI. `--docs lore` scores only what the lore says (run it after the lore is read);
`--docs session` only what the session says; `both` is the master key.

Only ids and counts are printed with `--counts-only`; without it, the key's own words (public
sample names) are printed beside each id so a person can read the report. The report never prints
a name from the backup, but its counts describe the campaign's shape, so the tool refuses a backup
whose campaign is not the public sample (`--i-know-this-is-the-test-campaign` overrides it).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dmbot.campaigns.store import decode_backup
from dmbot.devtools import sample_name
from dmbot.memory import kin, place_words, reputation_words, route_length, unnamed
from dmbot.memory.models import name_key
from dmbot.memory.session_progress import THREAD_WORDS

DEFAULT_KEY = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "story_graph" / "master_key.json"
)

# The kinds of entry the memory keeps a key kind as (a creature or a god is a character there).
ENTRY_TYPES: dict[str, frozenset[str]] = {
    "person": frozenset({"character", "person", "npc", "player_character"}),
    "creature": frozenset({"character", "creature"}),
    "deity": frozenset({"character", "deity"}),
    "group": frozenset({"faction"}),
    "place": frozenset({"place"}),
    "event": frozenset({"event", "deed"}),
    "item": frozenset({"item"}),
}
# How each key tie is grouped in the report.
GROUPS: dict[str, str] = {
    "parent_of": "family",
    "grandparent_of": "family",
    "sibling": "family",
    "spouse": "family",
    "uncle_of": "family",
    "apprentice_of": "work",
    "mentor_of": "work",
    "works_for": "work",
    "leads": "work",
    "member": "work",
    "knows": "feelings",
    "enemy_of": "feelings",
    "rival_of": "feelings",
    "held_captive_by": "feelings",
    "owns": "things",
    "carries": "things",
    "found": "things",
    "associate": "associations",
    "in": "places",
    "route": "places",  # a road or way between two places (#4255)
    "lives_in": "places",
    "lived_in": "places",
    "works_in": "places",
    "comes_from": "places",
    "last_seen_in": "places",
    "kept_in": "places",
    "was_kept_in": "places",
}
# The family roles a stored detail must be exactly (a grandparent is not a parent).
PARENT = frozenset({"parent", "mother", "father"})
CHILD = frozenset({"child", "son", "daughter"})
SIBLING = frozenset({"sibling", "brother", "sister"})
SPOUSE = frozenset({"spouse", "husband", "wife"})
UNCLE = frozenset({"uncle", "aunt", "aunt or uncle"})
NEPHEW = frozenset({"nephew", "niece", "niece or nephew"})
GRAND_UP = frozenset({"grandparent", "grandmother", "grandfather"})
GRAND_DOWN = frozenset({"grandchild", "grandson", "granddaughter"})
SAMPLE_CAMPAIGN = sample_name.OLD_NAME  # (numbered names pass `sample_name.is_sample`, #4538)
# What slot (word, past) each key place tie means, as `place_words.slot` reads a stored detail.
PLACE_SLOTS = {
    "lives_in": (place_words.LIVES, False), "lived_in": (place_words.LIVES, True),
    "works_in": (place_words.WORKS, False), "comes_from": (place_words.FROM, False),
    "last_seen_in": (place_words.LAST_SEEN, False), "kept_in": (place_words.KEPT, False),
    "was_kept_in": (place_words.KEPT, True),
}  # fmt: skip

# The stand-in the "who plays" step makes for a player (`store._placeholder_name`, #1559), and for
# a player without Discord, "Player 3 (no Discord)" (#3690; dev1's unmatched list on #5055 showed
# the four counted as extra names in every run).
PLACEHOLDER = re.compile(r"Player \d{4}|Player \d+ \(no Discord\)")
FOUND, WRONG, WAITING, MISSING = "found", "wrong_type", "waiting_only", "missing"
# A kind of creature the rules already hold is left out of story memory on purpose
# (`in_the_rules`, 2026-11-06 ruling on #3545): marked `"by_design": true` in the key, it is not a
# miss when absent and is listed on its own line, so a change in that rule shows up. A particular
# creature the lore names or sets apart is an entry and stays must-find (no flag).
BY_DESIGN = "by_design"
# A thing the story describes but never names (#5055 D; Supervisor's ruling): marked
# `"described": true`, with the words it is described by in `said_as`. DMbot never makes a name out
# of a description, so such a row is not a name miss: it is scored on its own line, found as an
# entry (named by the DM, or a person the story never named) or on the "met but not named" list
# (`memory_met`, which a backup does not carry, so the caller gives its words).
DESCRIBED = "described"
AS_ENTRY, ON_MET_LIST = "as_entry", "on_met_list"
MET_KINDS = frozenset({"person", "group", "creature"})  # what the met list can hold
HEADS = ("total", "found", "waiting", "wrong", "missing", "must-miss")
WIDTHS = (6, 7, 9, 7, 9, 11)


@dataclass(slots=True)
class Row:
    """One thing in the key and what became of it."""

    id: str
    text: str
    status: str
    group: str
    must: bool
    tier: str = ""
    detail: str = ""  # why it is wrong, in fixed words


@dataclass(slots=True)
class Report:
    entries: list[Row] = field(default_factory=list)
    ties: list[Row] = field(default_factory=list)
    extra_entries: Counter[str] = field(default_factory=Counter)
    unnamed_extras: int = 0  # people the story never named that the key does not list (#3736)
    described: list[Row] = field(default_factory=list)  # the key's described rows (#5055 D)
    # Each extra entry as (kind, status, source, stored name), for `--list-extras` (#5055): which
    # "miss" is the key's wording and which a real reader miss. The sample's own words only.
    extra_list: list[tuple[str, str, str, str]] = field(default_factory=list)
    extra_ties: int = 0
    # Key entries missed while a part of their name is stored ("Valley" for Dwarven Valley), the
    # "found, wrong shape" count (docs/design/session_names.md, section 8). Still counted missed;
    # this says how many misses are a name cut short rather than one never heard. Counts only.
    wrong_shape: int = 0
    # Which reader stored the key entries that were found, by the stored entry's own `source`
    # (scan, extractor for the story reader, lore, dm...): "which reader found what is left"
    # (the Expert, on session_names slice 2c). Counts only.
    found_by: Counter[str] = field(default_factory=Counter)
    violations: list[str] = field(default_factory=list)
    inferred_confirmed: list[str] = field(default_factory=list)
    # `must_not_ties` rows kept as facts (#4636 part 3): a near-miss a list was over-applied to,
    # "Kelvin's Cairn in Ten-Towns". Offered (waiting) is fine; kept is a false tie.
    false_ties: list[str] = field(default_factory=list)
    events_in_backup: int = 0
    events_expected: int = 0
    players_found: int = 0  # the key's player characters that are entries with that role (#3553)
    players_expected: int = 0
    # `Player 1234` entries the "who plays" step made and nothing uses: not DMbot's extras (#3545)
    placeholders: int = 0
    # #3606: for each key tie that expects a feeling, (id, how the stored word sits against it)
    feelings: list[tuple[str, str]] = field(default_factory=list)
    # #4256: a place's land, climate and ruler, per layer: found, wrong (another value), missed,
    # and extra (a row the key does not list: a false note). Counts only.
    map_facts: dict[str, Counter[str]] = field(default_factory=dict)
    # A character's languages (#4229; owner, 2026-11-14: a property of the character, never a tie):
    # found, missed, and extra (a language row the key does not list). Counts only.
    languages: Counter[str] = field(default_factory=Counter)
    # A place's, group's or people's feeling toward a lineage or a named culture (#4229 slice 4):
    # found (the key's, at any step), missed, and extra (one the key does not list). Counts only.
    attitudes: Counter[str] = field(default_factory=Counter)
    # A road's stated length (#4255; a property of the road, never a tie): found, missed, and extra
    # (a length on a road the key does not list). Counts only.
    route_lengths: Counter[str] = field(default_factory=Counter)
    # Story threads (#4453, quests slice 1): found, missed, wrong (another kind), doubles (one key
    # thread suggested twice), extra (a thread the key doesn't list), hints found
    threads: Counter[str] = field(default_factory=Counter)
    # A thread's parts (#4500, quests slice 3), on the backup thread its key thread matched: found
    # and missed by kind ("found objective"), on the wrong thread, and extra. Branches: found and
    # missed (a hint's miss isn't held against the reader), each a `branch_of` to its quest.
    thread_parts: Counter[str] = field(default_factory=Counter)
    # A thread's clues (#4624, quests slice 6b): found on the backup thread its key thread matched,
    # on the wrong thread, missed (must-find only), good finds missed, extra (a clue tied to a
    # matched thread that no key row is), false (a `must_not_clues` row: any tie to that thread)
    clues: Counter[str] = field(default_factory=Counter)
    # Progress a session makes on a thread the campaign had (#4522 quests 5b/5c), read off the
    # matched backup thread: found, missed (must-find only), good finds missed, and false (a
    # `must_not_progress` row that holds)
    progress: Counter[str] = field(default_factory=Counter)
    # The table's guesses (#4655, quests slice 7a): found (in the key's point of view, kept as a
    # belief or waiting as a reading), missed, wrong holder (the statement in another point of
    # view), and false (a `must_not_readings` row that became a guess anywhere)
    readings: Counter[str] = field(default_factory=Counter)


def _live(row: Mapping[str, Any]) -> bool:
    return row.get("status") in ("confirmed", "proposed")


def _tables(backup: object) -> dict[str, list[Mapping[str, Any]]]:
    """The memory rows of a backup, by table."""
    sections = backup.get("sections") if isinstance(backup, dict) else None
    rows = sections.get("memory") if isinstance(sections, dict) else None
    if not isinstance(rows, list):
        raise ValueError("That file has no story memory: it is not a campaign backup.")
    out: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        if isinstance(r, dict):
            out.setdefault(str(r.get("table")), []).append(r)
    return out


_OLD_SUFFIX = re.compile(r"\s*\(name unknown\)")  # the label before #3736, in older backups
_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)


def _plain(text: str) -> str:
    """A description as the key and the backup are compared: `name_key`, no leading article."""
    return name_key(_ARTICLE.sub("", text.strip()))


class Graph:
    """The backup's entries and connections, found by name."""

    def __init__(self, tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        self.tables = tables  # (every table, for the checks that read one of their own)
        self.entities = {
            str(e["id"]): e
            for e in tables.get("entity", ())
            if _live(e) and not e.get("merged_into")
        }
        # (by the name as written and without a leading article, as the decide step's key reads
        # names: "The Black Hand" stored is the key's "Black Hand", #5055)
        # `as_written` keeps the article: the readings' holder keys are compared to plain words
        # ("party" is the party's own view), so "The party" must not also become "party" there
        self.by_key: dict[str, set[str]] = {}
        self.as_written: dict[str, set[str]] = {}
        for e in self.entities.values():
            self.as_written.setdefault(name_key(str(e["name"])), set()).add(str(e["id"]))
            for k in {name_key(str(e["name"])), _plain(str(e["name"]))} - {""}:
                self.by_key.setdefault(k, set()).add(str(e["id"]))
        for a in tables.get("alias", ()):
            if a.get("status") != "rejected" and str(a["entity_id"]) in self.entities:
                said = str(a["key"]) or name_key(str(a["text"]))
                self.as_written.setdefault(said, set()).add(str(a["entity_id"]))
                for k in {said, _plain(str(a["text"]))} - {""}:
                    self.by_key.setdefault(k, set()).add(str(a["entity_id"]))
        # a person the story never named is found by its description (#3736): "unnamed - Okthandom's
        # child" (or an older backup's "Okthandom's child (name unknown)") is the key's
        # "Okthandom's child". Kept apart from the names, so a description never matches a name.
        self.by_description: dict[str, set[str]] = {}
        for e in self.entities.values():
            if e.get("unnamed"):
                said = _OLD_SUFFIX.sub("", unnamed.description(str(e["name"])))
                self.by_description.setdefault(_plain(said), set()).add(str(e["id"]))
        self.relations = [r for r in tables.get("relation", ()) if _live(r)]

    def named(self, names: Sequence[str]) -> set[str]:
        """The entries one of `names` is the name or other name of, never a description: a
        must-not name is a name stored as a name (#3545: "maul barbarian" as a person's name is a
        miss; "unnamed - maul barbarian" is the person the story never named, which is right)."""
        found: set[str] = set()
        for n in names:
            found |= self.by_key.get(name_key(n), set()) | self.by_key.get(_plain(n), set())
        return found

    def described(self, names: Sequence[str]) -> set[str]:
        """The people the story never named whose description is one of `names`, never a named
        entry: a key entry marked `unnamed` is found only this way (#3545)."""
        found: set[str] = set()
        for n in names:
            found |= self.by_description.get(_plain(_OLD_SUFFIX.sub("", n)), set())
        return found

    def ids(self, names: Sequence[str]) -> set[str]:
        """Entries by name or other name, and people the story never named by description."""
        return self.named(names) | self.described(names)

    def between(self, a: set[str], b: set[str]) -> list[tuple[Mapping[str, Any], bool]]:
        """Connections between two sets of entries, each with whether it runs a to b."""
        out = []
        for r in self.relations:
            s, o = str(r["subject_id"]), str(r["object_id"])
            if s in a and o in b:
                out.append((r, True))
            elif s in b and o in a:
                out.append((r, False))
        return out


def _slot(detail: str, kind: str) -> tuple[str, bool]:
    """Which place tie a stored detail is (the word and whether it is past). An item is only ever
    kept in a place, so an item with any other word, or none, is a different tie."""
    way = place_words.way_of(detail)
    if kind == "item":
        return (place_words.KEPT if way and way.key == place_words.KEPT else ""), bool(
            way and way.past
        )
    return (way.key, way.past) if way is not None else (place_words.LAST_SEEN, False)


def _role(detail: str, roles: frozenset[str]) -> bool:
    return kin.role_of(detail) in roles


def _feeling_verdict(tie: Mapping[str, Any], graph: Graph, a: set[str], b: set[str]) -> str | None:
    """How the stored feeling of a key tie sits against the key's (see `reputation_words.compare`),
    or None when the key expects none or nothing is stored for it. An old `enemy_of` (either end)
    counts as "hostile"; a `knows` in the key's direction with no word is "neutral"; a waiting
    (proposed) row is scored like a stored one, since it is what DMbot read; the closest of
    several rows is the one scored."""
    rel = str(tie["relation"])
    wanted = str(tie.get("feeling") or "") if rel == "knows" else ""
    if rel == "enemy_of":
        wanted = reputation_words.HOSTILE
    if not wanted or tie.get("tier") == "by_rule":
        return None
    best: str | None = None
    order = (
        reputation_words.EXACT,
        reputation_words.ONE_STEP,
        reputation_words.OVER_READ,
        reputation_words.UNDER_READ,
        reputation_words.WRONG_SIDE,
    )  # best first: when several rows are stored, the closest one is the one scored
    for r, forward in graph.between(a, b):
        pred, detail = str(r["predicate"]), str(r.get("detail") or "")
        if pred == "enemy_of":  # (reads the same from either end)
            got = reputation_words.HOSTILE
        elif pred == "knows" and forward:
            got = reputation_words.word_of(detail) or ""
            if detail and not got:
                continue  # a detail that is no feeling word is not a feeling row
            got = got or reputation_words.NEUTRAL
        else:
            continue
        verdict = reputation_words.compare(wanted, got)
        if best is None or order.index(verdict) < order.index(best):
            best = verdict
    return best


def _tie_status(
    tie: Mapping[str, Any], graph: Graph, a: set[str], b: set[str], kinds: Mapping[str, str]
) -> tuple[str, str, set[str]]:
    """(status, why, the ids of the connections that satisfy it) for one key tie, against the
    backup's connections between its two ends."""
    rel = str(tie["relation"])
    rows = graph.between(a, b)
    if not rows:
        return MISSING, "", set()
    best = WRONG
    used: set[str] = set()
    whys: list[str] = []
    for r, forward in rows:
        ok, why = _row_ok(tie, rel, r, forward, a, kinds)
        if ok:
            used.add(str(r["id"]))
            if r.get("status") == "confirmed":
                best = FOUND
            elif best != FOUND:
                best = WAITING
        elif why:
            whys.append(why)
    return (
        best,
        (whys[0] if best == WRONG and whys else "other kind" if best == WRONG else ""),
        used,
    )


def _row_ok(
    tie: Mapping[str, Any],
    rel: str,
    r: Mapping[str, Any],
    forward: bool,
    subjects: set[str],
    kinds: Mapping[str, str],
) -> tuple[bool, str]:
    """Does one stored connection satisfy a key tie; if not, a fixed-words reason or ''."""
    pred, detail = str(r["predicate"]), str(r.get("detail") or "")
    ended = r.get("to_session_at") is not None
    current = tie.get("current", True)
    ok, why = False, ""
    if rel in ("sibling", "spouse"):
        ok = pred == "kin_of" and _role(detail, SIBLING if rel == "sibling" else SPOUSE)
        why = "family without the role" if pred == "kin_of" else ""
    elif rel in ("parent_of", "grandparent_of", "uncle_of"):
        up, down = {
            "parent_of": (PARENT, CHILD),
            "grandparent_of": (GRAND_UP, GRAND_DOWN),
            "uncle_of": (UNCLE, NEPHEW),
        }[rel]
        ok = pred == "kin_of" and _role(detail, up if forward else down)
        why = "family without the role" if pred == "kin_of" else ""
    elif rel in ("mentor_of", "apprentice_of"):
        ok = pred == "mentor_of" and (forward == (rel == "mentor_of"))
    elif rel in ("owns", "carries", "leads"):
        ok = pred == rel and forward
    elif rel == "member":
        ok = pred == "member_of" and forward
    elif rel == "works_for":
        ok = pred == "serves" and forward
    elif rel == "rival_of":
        ok = pred == "rival_of"
    elif rel == "held_captive_by":
        ok = pred == "captive_of" and forward
    elif rel == "enemy_of":
        ok = pred == "enemy_of" or (pred == "knows" and reputation_words.sign_of(detail) == -1)
    elif rel == "found":
        ok = pred == "associated_with" and forward and "found" in detail.casefold().split()
        why = "kept as a plain link"
    elif rel == "knows":
        if pred == "knows":
            feeling = str(tie.get("feeling") or "")
            got = reputation_words.word_of(detail)
            if tie["tier"] == "by_rule" or not feeling:
                ok = True
            elif not forward:
                ok = False
            elif feeling == reputation_words.WARY:
                ok = got == reputation_words.WARY
            else:
                ok = reputation_words.sign_of(detail) == reputation_words.sign_of(feeling)
            why = "" if ok else "other feeling"
    elif rel == "associate":
        ok = pred == "associated_with"
    elif rel == "route":  # (a way between two places reads both ways round, #4255)
        ok = pred == "route_to"
    elif rel == "in":
        ok = pred == "located_in" and forward and not place_words.way_of(detail)
        why = "wrong place word" if pred == "located_in" else ""
    elif rel in PLACE_SLOTS and pred == "located_in" and forward:
        word, past = PLACE_SLOTS[rel]
        want_past = past or not current
        # one subject at a time: a name that resolves to several entries is right if any is
        ok = any(
            (slot := _slot(detail, kinds.get(sid, "")))[0] == word
            and (slot[1] or ended) == want_past
            for sid in subjects
        )
        why = "wrong place word"
    if ok and not current and rel not in PLACE_SLOTS and not ended and not _past_word(detail):
        ok, why = False, "should be history"
    return ok, why


def _past_word(detail: str) -> bool:
    way = place_words.way_of(detail)
    return bool(way and way.past) or detail.casefold().split()[:1] in (["was"], ["used"], ["once"])


def _text(tie: Mapping[str, Any]) -> str:
    rel = str(tie["relation"]) + (f" ({tie['feeling']})" if tie.get("feeling") else "")
    return f"{tie['a']} {rel} {tie['b']}"


def _on_met_list(words: Sequence[str], met: Sequence[str]) -> bool:
    """Whether a "met but not named" row's words hold one of `words` whole (#5055 D): "the kennel
    where the dogs sleep" holds "kennel". Plain words both sides, no leading article."""
    rows = [f" {_plain(m)} " for m in met]
    return any(f" {w} " in row for w in map(_plain, words) if w for row in rows)


def check(
    backup: object, key: Mapping[str, Any], docs: str = "both", met: Sequence[str] = ()
) -> Report:
    """Score a backup against the key. `met`: the words of the campaign's "met but not named" rows
    (`memory_met`), which a backup does not carry, for the key's described rows (#5055 D)."""
    graph = Graph(_tables(backup))
    wanted = {"lore", "session"} if docs == "both" else {docs}
    report = Report()
    nodes = [n for n in key["nodes"] if wanted & set(n.get("docs", ["lore", "session"]))]
    # (a described row is found by its `said_as` words too, in its own line and in its ties alike)
    names: dict[str, list[str]] = {
        n["name"]: [
            n["name"], *n.get("aliases", []), *(n.get("said_as", []) if n.get(DESCRIBED) else [])
        ]
        for n in nodes
    }  # fmt: skip
    kinds = {i: str(e["type"]) for i, e in graph.entities.items()}
    kind_of_key = {n["name"]: n["kind"] for n in nodes}
    matched_ids: set[str] = set()
    used_relations: set[str] = set()
    strict = {n["name"] for n in nodes if n.get("unnamed")}

    def lookup(name: str) -> set[str]:
        # (a key entry the story never names, marked `unnamed`, is found by an unnamed entry's
        # description only, so a description stored as a name, "maul barbarian", is never it)
        return graph.described(names[name]) if name in strict else graph.ids(names[name])

    for n in nodes:
        ids = lookup(n["name"])
        matched_ids |= ids
        if n.get(DESCRIBED):  # (its own line, never a name miss: #5055 D)
            # Found as an entry of any kind: the DM named it, or kept the person unnamed (the kind
            # is the names table's business). The met list holds only beings the party was with
            # (`met_reading.MET_ADDENDUM`), so only a being's row is looked for there: a place or
            # item matched by "the dogs in the kennel" would be a false find.
            being = n["kind"] in MET_KINDS
            on_list = being and _on_met_list(names[n["name"]], met)
            where = AS_ENTRY if ids else ON_MET_LIST if on_list else MISSING
            report.described.append(Row(n["name"], n["name"], where, n["kind"], False))
            continue
        if not ids:
            status = BY_DESIGN if n.get("by_design") else MISSING
        elif any(kinds[i] in ENTRY_TYPES.get(n["kind"], frozenset()) for i in ids):
            status = FOUND
            sources = {
                str(graph.entities[i].get("source") or "unknown")
                for i in ids
                if kinds[i] in ENTRY_TYPES.get(n["kind"], frozenset())
            }
            report.found_by.update(sorted(sources)[:1])  # (one per key entry: the first source)
        else:
            status = WRONG
        report.entries.append(
            # (an entry is must-find unless the key marks it a good find only, as ties can be)
            Row(
                n["name"],
                n["name"],
                status,
                n["kind"],
                not n.get("by_design", False) and n.get("must_find", True) is not False,
            )
        )
    for t in key["pairs"]:
        if not wanted & set(t.get("in", ["lore", "session"])):
            continue
        if t["a"] not in names or t["b"] not in names:
            continue
        a, b = lookup(t["a"]), lookup(t["b"])
        if not a or not b:
            status, why, used_here = MISSING, "no entry", set[str]()
        else:
            status, why, used_here = _tie_status(t, graph, a, b, kinds)
        used_relations |= used_here
        if t["tier"] == "inferred" and status == FOUND:
            report.inferred_confirmed.append(t["id"])
        if (
            a
            and b
            and status in (FOUND, WAITING, WRONG)
            and (verdict := _feeling_verdict(t, graph, a, b))
        ):
            report.feelings.append((str(t["id"]), verdict))
        group = GROUPS.get(t["relation"], "other")
        report.ties.append(
            Row(t["id"], _text(t), status, group, bool(t["must_find"]), t["tier"], why)
        )
    # extras: entries and connections the key does not name
    # a placeholder's own tie is `plays` (the player, to the character they play): that is what
    # makes it a placeholder, so only another connection makes it a real entry
    others = [r for r in graph.relations if str(r["predicate"]) != "plays"]
    linked = {str(r["subject_id"]) for r in others} | {str(r["object_id"]) for r in others}
    tied_clues = {str(r["subject_id"]) for r in graph.relations if r["predicate"] == "clue_to"}
    named_extras: list[str] = []  # (stored names only, never a description: the wrong-shape count)
    for i, e in graph.entities.items():
        if i in matched_ids:
            continue
        if PLACEHOLDER.fullmatch(str(e["name"])) and i not in linked:
            report.placeholders += 1  # (the who-plays stand-in, #1559: not an extra entry)
            continue
        listed = (
            _kind_for(str(e["type"])), str(e.get("status", "")), str(e.get("source", "")),
            str(e["name"]),
        )  # fmt: skip
        if e.get("unnamed"):
            report.unnamed_extras += 1  # (their own line: a description, not an extra name)
            report.extra_list.append(listed)
            continue
        if str(e["type"]) in THREAD_TYPES:
            continue  # (a story thread is scored on its own line, #4453, never as an extra name)
        if i in tied_clues:
            continue  # (a clue tied to a thread is scored on the Clues line, #4624 6b)
        report.extra_entries[_kind_for(str(e["type"]))] += 1
        report.extra_list.append(listed)
        named_extras.append(str(e["name"]))
    report.wrong_shape = _wrong_shape(report.entries, names, named_extras)
    for name in key.get("must_not_names", ()):
        # (a must-not name is still found in a description too, as before; only one that is a
        # key `unnamed` entry's own description is right as a description, #3545)
        right_as_description = any(_plain(name) in {_plain(x) for x in names[s]} for s in strict)
        if graph.named([name]) or (not right_as_description and graph.described([name])):
            report.violations.append(name)
    # A must-not-make row (#4636 part 3): every row, whichever document (a near-miss is false in
    # either); a kept one is counted here only, never again as an extra tie
    for t in key.get("must_not_ties", []):
        if t["a"] not in names or t["b"] not in names:
            continue
        a, b = lookup(t["a"]), lookup(t["b"])
        if not a or not b:
            continue
        status, _why, used_here = _tie_status(t, graph, a, b, kinds)
        if status == FOUND:
            report.false_ties.append(str(t["id"]))
            used_relations |= used_here
    for r in graph.relations:
        if (
            str(r["subject_id"]) in matched_ids
            and str(r["object_id"]) in matched_ids
            and str(r["id"]) not in used_relations
        ):
            report.extra_ties += 1
    players = key.get("players", {})
    report.players_expected = len(players)
    report.players_found = sum(
        1
        for name in players
        if any(graph.entities[i].get("role") == "player_character" for i in graph.ids([name]))
    )
    report.events_expected = sum(1 for n in nodes if kind_of_key[n["name"]] == "event")
    report.events_in_backup = sum(
        1 for e in graph.entities.values() if e["type"] in ("event", "deed")
    )
    _score_map_facts(report, graph, key, names)
    _score_readings(report, graph, key, names)
    _score_threads(report, graph, key, names)
    _score_languages(report, graph, key, names, wanted)
    _score_attitudes(report, graph, key, names, wanted)
    _score_route_lengths(report, graph, key, names, wanted)
    return report


def _score_languages(
    report: Report,
    graph: Graph,
    key: Mapping[str, Any],
    names: Mapping[str, list[str]],
    wanted: set[str],
) -> None:
    """The key's `languages` (#4229): {character, language}. A backup language row on that
    character with that language (compared as `name_key`, so "elvish" is "Elvish") is found; none
    is missed. Every other row is extra: a language the document never gives that character, such
    as runes on an item read as a language. Whoever gave it (DMbot's read or the DM) counts alike:
    the check scores what the graph holds, as for ties. A key with no `languages` list (the
    session key, a key from before #4229) is not scored, so its report is unchanged; nor is a run
    that scores none of the documents its languages come from (`doc`, lore when not given), so a
    session-only run never counts the lore's languages as missed. Two things this does not judge:
    a row on an entry turned down since is still extra, and a character with a sheet gets no story
    languages at all (its languages are the sheet's), so a run that sets one before the lore is
    read scores its languages as missed."""
    langs = [x for x in key.get("languages", []) if x.get("doc", "lore") in wanted]
    if not langs:
        return
    rows = list(graph.tables.get("story_language", []))
    used: set[int] = set()
    for lang in langs:
        who = graph.ids(names.get(lang["character"], [lang["character"]]))
        want = name_key(str(lang["language"]))
        hit = [
            i
            for i, r in enumerate(rows)
            if str(r["entity_id"]) in who and str(r.get("name_key") or name_key(r["name"])) == want
        ]
        used.update(hit)
        report.languages["found" if hit else "missed"] += 1
    report.languages["extra"] += sum(1 for i in range(len(rows)) if i not in used)


def _score_attitudes(
    report: Report,
    graph: Graph,
    key: Mapping[str, Any],
    names: Mapping[str, list[str]],
    wanted: set[str],
) -> None:
    """The key's `attitudes` (#4229 slice 4): {holder, toward}, `toward` a lineage key ("elf") or a
    named culture's name. An open backup row from that holder toward it is found, at any step (the
    step is the DM's to set); none is missed. Every other open row toward a lineage or a culture is
    extra: a feeling the lore never states. A key with no `attitudes` list is not scored, and an
    empty one scores every such row as extra (the sample lore states none). Lore only."""
    if "attitudes" not in key or "lore" not in wanted:
        return
    cultures = {str(r["id"]): name_key(str(r["name"])) for r in graph.tables.get("culture", [])}
    rows = [
        r
        for r in graph.tables.get("attitude", [])
        if r.get("closed_at") is None and (r.get("toward_lineage") or r.get("toward_culture_id"))
    ]
    used: set[int] = set()
    for want in key["attitudes"]:
        who = graph.ids(names.get(want["holder"], [want["holder"]]))
        toward = name_key(str(want["toward"]))
        hit = [
            i
            for i, r in enumerate(rows)
            if str(r["holder_id"]) in who
            and toward
            in (str(r.get("toward_lineage") or ""), cultures.get(str(r.get("toward_culture_id"))))
        ]
        used.update(hit)
        report.attitudes["found" if hit else "missed"] += 1
    report.attitudes["extra"] += sum(1 for i in range(len(rows)) if i not in used)


def _same_length(stored: Mapping[str, Any], words: str) -> bool:
    """A stored length says what the key's words say: the same amount and unit when the words give
    one (so "1 hour" is "an hour"), else the same words."""
    want = route_length.parse(words)
    if want is None:
        return False
    if want.amount is not None:
        return (stored.get("length_amount"), stored.get("length_unit")) == (want.amount, want.unit)
    return name_key(str(stored.get("length_words") or "")) == name_key(want.words)


def _joins(s: str, o: str, a: set[str], b: set[str]) -> bool:
    """The road runs between the two places, either way round (never a road with one end only)."""
    return (s in a and o in b) or (s in b and o in a)


def _score_route_lengths(
    report: Report,
    graph: Graph,
    key: Mapping[str, Any],
    names: Mapping[str, list[str]],
    wanted: set[str],
) -> None:
    """The key's `route_lengths` (#4255): {from, to, words}. A road (`route_to`, either way round)
    between those two places whose stored length says the same is found; none is missed. Every
    other road with a length is extra. Whoever gave it (DMbot's read or the DM) counts alike, as
    for languages. A key with no list, or a run that scores none of its documents (`doc`, the
    session when not given), scores none, so its report is unchanged."""
    lengths = [x for x in key.get("route_lengths", []) if x.get("doc", "session") in wanted]
    if not lengths:
        return
    roads = [r for r in graph.relations if r["predicate"] == "route_to" and r.get("length_words")]
    used: set[int] = set()
    for want in lengths:
        a = graph.ids(names.get(want["from"], [want["from"]]))
        b = graph.ids(names.get(want["to"], [want["to"]]))
        hit = [
            i
            for i, r in enumerate(roads)
            if _joins(str(r["subject_id"]), str(r["object_id"]), a, b)
            and _same_length(r, str(want["words"]))
        ]
        used.update(hit)
        report.route_lengths["found" if hit else "missed"] += 1
    report.route_lengths["extra"] += sum(1 for i in range(len(roads)) if i not in used)


def _score_map_facts(
    report: Report, graph: Graph, key: Mapping[str, Any], names: Mapping[str, list[str]]
) -> None:
    """The key's `map_facts` (#4256): {place, layer, value}, the value a word of the layer's list
    or, for control, the ruling group's key name. A backup row on that place and layer with that
    value is found; one with another value is wrong; none is missed. A row the key doesn't list is
    extra (a false note, design §13.15). Only the rows still open count."""
    rows = [
        r
        for r in graph.tables.get("map_fact", [])
        if r.get("status") != "rejected" and r.get("to_session_at") is None
    ]
    used: set[str] = set()
    # a road's condition (#4258): the sample states none, so the key expects none and every row is
    # a false note; the line is always printed, so a zero shows too
    report.map_facts.setdefault("condition", Counter())
    for fact in key.get("map_facts", []):
        layer = str(fact["layer"])
        tally = report.map_facts.setdefault(layer, Counter())
        places = graph.ids(names.get(fact["place"], [fact["place"]]))
        wanted = (
            graph.ids(names.get(fact["value"], [fact["value"]]))
            if layer == "control"
            else {str(fact["value"])}
        )
        here = [r for r in rows if str(r["entity_id"]) in places and r["layer"] == layer]
        hit = [
            r for r in here if str(r["faction_id"] if layer == "control" else r["value"]) in wanted
        ]
        used.update(str(r["id"]) for r in here)
        tally["found" if hit else "wrong" if here else "missed"] += 1
    for r in rows:
        if str(r["id"]) not in used:
            report.map_facts.setdefault(str(r["layer"]), Counter())["extra"] += 1


THREAD_TYPES = frozenset({"thread", "quest", "mystery", "hook", "goal", "promise"})
# The key's people of a thread, by the part word `in_thread` carries (`thread_read`)
THREAD_ROLES = {"giver": "quest giver", "target": "target", "taker": "takes it on"}


GUESS_STATUSES = frozenset({"theory", "accusation"})


def _guesses(graph: Graph) -> list[tuple[str, frozenset[str], frozenset[str]]]:
    """Every guess in the backup as (holder, subject's name keys, object's name keys): kept as a
    belief (a theory or an accusation in the party's or a character's point of view), or waiting
    as a claim's reading. The holder is "party" or a name key of the character."""
    keys: dict[str, set[str]] = {}
    for key, ids in graph.as_written.items():
        for i in ids:
            keys.setdefault(i, set()).add(key)
    views = {str(p["id"]): p for p in graph.tables.get("perspective", ())}
    # (live connections only: a guess the DM turned down is no guess)
    relations = {str(r["id"]): r for r in graph.relations}
    out: list[tuple[str, frozenset[str], frozenset[str]]] = []
    for b in graph.tables.get("belief", ()):
        view, rel = (
            views.get(str(b.get("perspective_id"))),
            relations.get(str(b.get("relation_id"))),
        )
        if view is None or rel is None or str(b.get("status")) not in GUESS_STATUSES:
            continue
        if view.get("kind") == "party":
            holder = {"party"}
        elif view.get("entity_id"):
            holder = keys.get(str(view["entity_id"]), set())
        else:
            continue  # (The truth, or everyone: never a guess)
        ends = (keys.get(str(rel["subject_id"]), set()), keys.get(str(rel["object_id"]), set()))
        out += [(h, frozenset(ends[0]), frozenset(ends[1])) for h in holder]
    for c in graph.tables.get("claim", ()):
        reading = c.get("reading")
        if c.get("status") != "proposed" or not isinstance(reading, Mapping):
            continue
        if str(reading.get("status")) not in GUESS_STATUSES:
            continue
        said = str(reading.get("view") or "")
        holders = {"party"} if said == "party" else _said_keys(said.removeprefix("pc:"))
        subject = _said_keys(str(c.get("subject") or ""))
        obj = _said_keys(str(c.get("object") or ""))
        out += [(h, subject, obj) for h in holders]
    return out


_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.I)


def _said_keys(words: str) -> frozenset[str]:
    """A waiting reading's name as the model wrote it, with and without a leading "the" ("the Old
    Well"), as name keys: the key lists each entry's short forms as aliases."""
    return frozenset({name_key(words), name_key(_ARTICLE.sub("", words.strip()))})


def _score_readings(
    report: Report, graph: Graph, key: Mapping[str, Any], names: Mapping[str, list[str]]
) -> None:
    """The table's guesses against the key (#4655): each `readings` row is found (the statement,
    either way round, in the key's point of view), wrong holder (only in another one) or missed;
    each `must_not_readings` row that is a guess anywhere is false. The relationship's word is not
    scored: the model says it in its own words. Counts only."""
    rows, nots = key.get("readings", []), key.get("must_not_readings", [])
    if not rows and not nots:
        return
    guesses = _guesses(graph)

    def ends(name: str) -> frozenset[str]:
        return frozenset(name_key(n) for n in names.get(name, [name]))

    def holds(row: Mapping[str, Any]) -> list[str]:
        a, b = ends(str(row["subject"])), ends(str(row["object"]))
        return [h for h, s, o in guesses if (s & a and o & b) or (s & b and o & a)]

    for row in rows:
        held = holds(row)
        who = "party" if row["who"] == "party" else None
        wanted = {who} if who else ends(str(row["who"]))
        if set(held) & wanted:
            report.readings["found"] += 1
        elif held:
            report.readings["wrong holder"] += 1
        else:
            report.readings["missed"] += 1
    report.readings["false"] += sum(1 for row in nots if holds(row))


def _score_threads(
    report: Report, graph: Graph, key: Mapping[str, Any], names: Mapping[str, list[str]]
) -> None:
    """The key's `threads` (#4453; quests design §9.7): a backup thread is the key's when it has
    every person the key names in the same part (who gave it, who it's aimed at, who takes it on;
    with none of those, who it is about). It is found when its kind matches too, wrong when not;
    a second backup thread for one key thread is a double; a backup thread no key thread is is an
    extra. A hint is counted when found and never missed. Only the key's rows count as missed
    when they are must-find."""
    threads = {i for i, e in graph.entities.items() if str(e["type"]) in THREAD_TYPES}
    parts: dict[str, set[tuple[str, str]]] = {}
    for r in graph.relations:
        if r.get("predicate") == "in_thread" and str(r["object_id"]) in threads:
            role = str(r.get("detail") or "").strip().lower()
            parts.setdefault(str(r["object_id"]), set()).add((role, str(r["subject_id"])))
    used: set[str] = set()
    matched: dict[str, str] = {}  # (the key thread's id: the backup thread it is, for its parts)
    every: dict[str, list[str]] = {}  # (and every one it is, for progress: a double holds it too)
    for t in key.get("threads", []):
        wanted_kinds = {str(t["kind"])}
        need: list[tuple[str, set[str]]] = [
            (role, graph.ids(names.get(t[field], [t[field]])))
            for field, role in THREAD_ROLES.items()
            if t.get(field)
        ]
        # (a person to find is the target or "the one missing": either word is right, #4453)
        alike = {"target": ("target", "the one missing")}
        if not need:  # (a thread about someone, with nobody giving or taking it)
            need = [
                (role, graph.ids(names.get(n, [n])))
                for role, people in (("the one missing", t.get("missing", [])),
                                     ("in danger", t.get("in_danger", [])))
                for n in people
            ]  # fmt: skip
        hits = [
            th
            for th in sorted(threads)
            if need
            and all(
                any(
                    (word, i) in parts.get(th, set())
                    for i in ids
                    for word in alike.get(role, (role,))
                )
                for role, ids in need
            )
        ]
        used.update(hits)
        right = [th for th in hits if str(graph.entities[th]["type"]) in wanted_kinds]
        if hits and t.get("id"):
            matched[str(t["id"])] = (right or hits)[0]
            every[str(t["id"])] = right or hits
        if t.get("hint"):
            report.threads["hints found"] += 1 if hits else 0
            continue
        if right:
            report.threads["found"] += 1
        elif hits:
            report.threads["wrong"] += 1
        elif t.get("must_find", True):
            report.threads["missed"] += 1
        report.threads["doubles"] += max(0, len(hits) - 1)
    report.threads["extra"] += len(threads - used)
    # talk that must make no thread (§9.7: a plan, an errand, a threat, a wish), by its words
    for rule in key.get("must_not_threads", []):
        words = [str(w).casefold() for w in rule.get("words", [])]
        for th in threads:
            e = graph.entities[th]
            said = f"{e.get('name', '')} {e.get('description', '')}".casefold()
            if any(w in said for w in words):
                report.threads["false"] += 1
                break
    _score_thread_parts(report, graph, key, matched)
    _score_clues(report, graph, key, matched)
    _score_progress(report, graph, key, every)


def _score_thread_parts(
    report: Report, graph: Graph, key: Mapping[str, Any], matched: Mapping[str, str]
) -> None:
    """A thread's parts and branches (#4500; quests design §9.7). A key part is found when the
    backup thread its key thread matched has a part of that kind whose words hold one of the key's
    `words` (casefold); on another thread only, it is "wrong thread". A key thread's part counts as
    missed whether or not the thread was found, so a missed quest's parts are missed too. Parts on a
    matched thread that no key part is are extra. A key thread with `branch_of` is found when the
    backup thread it matched has a live `branch_of` to the one its quest matched."""
    rows = [
        r
        for r in graph.tables.get("thread_part", ())
        if r.get("status") != "rejected" and r.get("to_session_at") is None
    ]
    on: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        on.setdefault(str(r["thread_id"]), []).append(r)
    used: set[str] = set()

    def fits(r: Mapping[str, Any], kind: str, words: Sequence[str]) -> bool:
        said = str(r.get("words", "")).casefold()
        return str(r["kind"]) == kind and any(str(w).casefold() in said for w in words)

    for t in key.get("threads", []):
        thread = matched.get(str(t.get("id", "")))
        for p in t.get("parts", []):
            kind, words = str(p["kind"]), [str(w) for w in p.get("words", [])]
            here = [r for r in on.get(thread or "", []) if fits(r, kind, words)]
            if here:
                report.thread_parts[f"found {kind}"] += 1
                used.update(str(r["id"]) for r in here)
            elif any(fits(r, kind, words) for r in rows):
                report.thread_parts["wrong thread"] += 1
            else:
                report.thread_parts[f"missed {kind}"] += 1
        bigger = str(t.get("branch_of", ""))
        if bigger:
            links = {
                (str(r["subject_id"]), str(r["object_id"]))
                for r in graph.relations
                if r.get("predicate") == "branch_of"
            }
            if thread and (thread, matched.get(bigger, "")) in links:
                report.thread_parts["branches found"] += 1
            elif not t.get("hint"):
                report.thread_parts["branches missed"] += 1
    for thread in set(matched.values()):
        report.thread_parts["extra"] += sum(
            1 for r in on.get(thread, []) if str(r["id"]) not in used
        )


def _score_clues(
    report: Report, graph: Graph, key: Mapping[str, Any], matched: Mapping[str, str]
) -> None:
    """The key's `clues` (#4624, quests 6b; design §7, §9.3): a clue tied with `clue_to` to the
    backup thread its key thread matched, whose name holds one of the row's `words` (casefold), is
    found; tied only to another thread, it is "wrong thread". Clues tied to a matched thread that no
    row is are extra. A `must_not_clues` row names a thread that must have no clue tied at all."""
    if not key.get("clues") and not key.get("must_not_clues"):
        return
    ties = [
        (str(r["subject_id"]), str(r["object_id"]))
        for r in graph.relations
        if r.get("predicate") == "clue_to"
    ]

    def named(clue: str, words: Sequence[str]) -> bool:
        # (from a word's start, so "Wit" is never "with" nor "whisper" past "whispers")
        said = str(graph.entities.get(clue, {}).get("name", ""))
        return any(re.search(rf"\b{re.escape(str(w))}", said, re.IGNORECASE) for w in words)

    used: set[tuple[str, str]] = set()
    # (must-find rows first, and each tie answers one row only: one clue is never two finds)
    rows = sorted(key.get("clues", []), key=lambda c: not c.get("must_find", True))
    for c in rows:
        thread = matched.get(str(c.get("thread", "")), "")
        words = [str(w) for w in c.get("words", [])]
        here = [
            t for t in ties if thread and t[1] == thread and t not in used and named(t[0], words)
        ]
        if here:
            report.clues["found"] += 1
            used.add(here[0])
        elif any(named(clue, words) for clue, th in ties if th != thread):
            report.clues["wrong thread"] += 1
        elif c.get("must_find", True):
            report.clues["missed"] += 1
        else:
            report.clues["good finds missed"] += 1
    named_threads = {str(c.get("thread", "")) for c in key.get("clues", [])}
    keyed = {matched[t] for t in named_threads if t in matched}
    report.clues["extra"] += sum(1 for t in ties if t[1] in keyed and t not in used)
    for rule in key.get("must_not_clues", []):
        thread = matched.get(str(rule.get("thread", "")), "")
        if thread and any(t[1] == thread for t in ties):
            report.clues["false"] += 1


def _progress_holds(
    graph: Graph, thread: str, row: Mapping[str, Any], parts: Sequence[Mapping[str, Any]]
) -> bool:
    """Whether one key progress row (or one of its `or` rows) stands on a backup thread now:
    "thread_moves", its open state is the row's; "part_moves", an open part of the row's kind
    whose words hold one of the row's `words` is in the row's state; "new_part", such a part was
    read from a session (casefold words, as `_score_thread_parts`)."""
    change = str(row.get("change", ""))
    if change == "thread_moves":
        want = THREAD_WORDS.get(str(row.get("state", "")), str(row.get("state", "")))
        return any(
            str(r.get("entity_id")) == thread
            and r.get("closed_at") is None
            and str(r.get("condition")) == want
            for r in graph.tables.get("state", ())
        )
    words = [str(w).casefold() for w in row.get("words", [])]
    kind = str(row.get("part_kind", ""))
    for r in parts:
        if str(r["thread_id"]) != thread or str(r["kind"]) != kind:
            continue
        if words and not any(w in str(r.get("words", "")).casefold() for w in words):
            continue
        if change == "part_moves" and str(r.get("state")) == str(row.get("state")):
            return True
        same_state = not row.get("state") or str(r.get("state")) == str(row.get("state"))
        if change == "new_part" and str(r.get("source")) == "session" and same_state:
            return True
    return False


def _score_progress(
    report: Report, graph: Graph, key: Mapping[str, Any], matched: Mapping[str, Sequence[str]]
) -> None:
    """The key's `progress` (#4522, quests 5c): what the session changes on a thread the lore
    made, read off the backup thread that key thread matched once the sample keeps every progress
    line (`accept_all.keep_progress`). A row is found when it, or one of its `or` rows, holds on
    any backup thread its key thread matched (the session reader may suggest the thread again, and
    which double sorts first is chance); on an unmatched thread it can't be. `must_not_progress`
    rows count as false when they hold on any of them."""
    if not key.get("progress") and not key.get("must_not_progress"):
        return
    parts = [
        r
        for r in graph.tables.get("thread_part", ())
        if r.get("status") != "rejected" and r.get("to_session_at") is None
    ]
    for p in key.get("progress", []):
        threads = matched.get(str(p.get("thread", "")), ())
        rows = [p, *p.get("or", [])]
        if any(_progress_holds(graph, th, r, parts) for th in threads for r in rows):
            report.progress["found"] += 1
        elif p.get("must_find", True):
            report.progress["missed"] += 1
        else:
            report.progress["good finds missed"] += 1
    for p in key.get("must_not_progress", []):
        threads = matched.get(str(p.get("thread", "")), ())
        if any(_progress_holds(graph, th, p, parts) for th in threads):
            report.progress["false"] += 1


def _kind_for(entry_type: str) -> str:
    for kind, types in ENTRY_TYPES.items():
        if entry_type in types:
            return kind
    return entry_type


_ARTICLES = frozenset({"a", "an", "the"})


def _wrong_shape(
    rows: Sequence[Row], names: Mapping[str, Sequence[str]], extras: Sequence[str]
) -> int:
    """How many missed key entries have an extra stored name whose words are some, never all, of
    one of the entry's names: "Valley" or "Dwarven" for "Dwarven Valley". An article never counts,
    so "The" alone is no part of "Valley of the Dead". Each key entry counts once, however many
    parts of it are stored."""
    parts = [frozenset(_plain(extra).split()) - _ARTICLES for extra in extras]
    parts = [p for p in parts if p]
    out = 0
    for r in rows:
        if r.status != MISSING:
            continue
        wholes = [frozenset(_plain(n).split()) for n in names.get(r.id, ())]
        if any(p < whole for whole in wholes for p in parts):
            out += 1
    return out


def summarize(report: Report) -> dict[str, Any]:
    """Counts only: by kind of entry and group of tie, found, missing, wrong, waiting."""

    def tally(rows: Sequence[Row]) -> dict[str, Counter[str]]:
        out: dict[str, Counter[str]] = {}
        for r in rows:
            g = out.setdefault(r.group, Counter())
            g[r.status] += 1
            g["total"] += 1
            if r.must:
                g["must"] += 1
                if r.status in (MISSING, WRONG):
                    g["must_missed"] += 1
        return out

    return {
        "entries": tally(report.entries),
        "ties": tally(report.ties),
        "extra_entries": dict(report.extra_entries),
        "unnamed_extras": report.unnamed_extras,
        "described": dict(Counter(r.status for r in report.described), total=len(report.described)),
        "extra_ties": report.extra_ties,
        "wrong_shape": report.wrong_shape,
        "found_by": dict(report.found_by),
        "violations": len(report.violations),
        "inferred_confirmed": len(report.inferred_confirmed),
        "false_ties": len(report.false_ties),
        "event_entries": {"expected": report.events_expected, "in_backup": report.events_in_backup},
        "player_characters": {"found": report.players_found, "expected": report.players_expected},
        "map_facts": {layer: dict(c) for layer, c in sorted(report.map_facts.items())},
        "threads": dict(report.threads),
        **({"thread_parts": dict(report.thread_parts)} if report.thread_parts else {}),
        **({"clues": dict(report.clues)} if report.clues else {}),
        **({"progress": dict(report.progress)} if report.progress else {}),
        **({"readings": dict(report.readings)} if report.readings else {}),
        **({"languages": dict(report.languages)} if report.languages else {}),
        **({"attitudes": dict(report.attitudes)} if report.attitudes else {}),
        **({"route_lengths": dict(report.route_lengths)} if report.route_lengths else {}),
    }


def _row(label: str, c: Mapping[str, int]) -> str:
    cells = (c["total"], c[FOUND], c[WAITING], c[WRONG], c[MISSING], c["must_missed"])
    return f"  {label:<13}" + "".join(f"{n:>{w}}" for n, w in zip(cells, WIDTHS, strict=True))


def render(report: Report, counts_only: bool = False, list_extras: bool = False) -> str:
    """The report as text. `counts_only`: ids and counts, no names. `list_extras` (#5055): each
    entry the key does not match, by kind, status, source and stored name, for the public sample
    only (its words may go on #3545); it lists names even with `counts_only`."""
    s = summarize(report)
    lines = ["Story graph check", ""]
    for title, key in (("Entries", "entries"), ("Ties", "ties")):
        lines.append(title)
        lines.append(
            f"  {'group':<13}" + "".join(f"{h:>{w}}" for h, w in zip(HEADS, WIDTHS, strict=True))
        )
        total: Counter[str] = Counter()
        for group, c in sorted(s[key].items()):
            lines.append(_row(group, c))
            total.update(c)
        lines.append(_row("ALL", total))
        lines.append("")
    lines.append(
        f"Extra entries (not in the key): {sum(report.extra_entries.values())} "
        f"{dict(report.extra_entries)}"
    )
    lines.append(f"Unnamed people not in the key: {report.unnamed_extras}")
    lines.append(f"Missed, but part of the name stored (wrong shape): {report.wrong_shape}")
    found_by = ", ".join(f"{s} {n}" for s, n in sorted(report.found_by.items()))
    lines.append(f"Found, by the stored entry's source: {found_by or 'none'}")
    if report.described:  # (#5055 D: not name misses; the series broke here)
        d = Counter(r.status for r in report.described)
        lines.append(
            f"Described, never named: found {d[AS_ENTRY] + d[ON_MET_LIST]} of "
            f"{len(report.described)} ({d[AS_ENTRY]} as an entry, {d[ON_MET_LIST]} on the met list)"
        )
        for r in report.described:
            if r.status == MISSING:
                lines.append("  missing    " + ("" if counts_only else r.text))
    if list_extras:
        lines.append("Extra entries, listed (kind | status | source | stored name):")
        for kind, status, source, name in sorted(report.extra_list):
            lines.append(f"  {kind} | {status} | {source} | {name}")
    lines.append(f"Player placeholders left out (not extras): {report.placeholders}")
    lines.append(f"Extra ties between key entries: {report.extra_ties}")
    lines.append(
        f"Event entries: {report.events_in_backup} in the backup, {report.events_expected} expected"
    )
    lines.append(f"Player characters found: {report.players_found} of {report.players_expected}")
    for layer, c in sorted(report.map_facts.items()):  # (#4256: land, climate and ruler)
        lines.append(
            f"Place {layer}: found {c['found']}, wrong {c['wrong']}, missed {c['missed']}, "
            f"extra {c['extra']}"
        )
    if report.languages:  # (#4229: a character's languages, a property, not a tie)
        c = report.languages
        lines.append(f"Languages: found {c['found']}, missed {c['missed']}, extra {c['extra']}")
    if report.attitudes:  # (#4229 slice 4: feelings toward a lineage or a culture)
        c = report.attitudes
        lines.append(f"Feelings: found {c['found']}, missed {c['missed']}, extra {c['extra']}")
    if report.threads:  # (#4453: story threads, quests slice 1)
        c = report.threads
        lines.append(
            f"Story threads: found {c['found']}, missed {c['missed']}, wrong {c['wrong']}, "
            f"doubles {c['doubles']}, extra {c['extra']}, false {c['false']} "
            f"(hints found {c['hints found']})"
        )
    if report.thread_parts:  # (#4500: a thread's parts and branches, quests slice 3)
        c = report.thread_parts
        kinds = ("objective", "complication", "reward", "reward_given", "help", "time_limit")
        said = ", ".join(
            f"{k} {c['found ' + k]}/{c['found ' + k] + c['missed ' + k]}"
            for k in kinds
            if c["found " + k] or c["missed " + k]
        )
        lines.append(
            f"Thread parts found: {said or 'none in the key'}; wrong thread {c['wrong thread']}, "
            f"extra {c['extra']}; branches found {c['branches found']}, "
            f"missed {c['branches missed']}"
        )
    if report.clues:  # (#4624: a thread's clues, quests 6b)
        c = report.clues
        lines.append(
            f"Clues: found {c['found']}, missed {c['missed']}, wrong thread {c['wrong thread']}, "
            f"extra {c['extra']}, false {c['false']} (good finds missed {c['good finds missed']})"
        )
    if report.progress:  # (#4522: what the session moved on the lore's threads, quests 5c)
        c = report.progress
        lines.append(
            f"Thread progress: found {c['found']}, missed {c['missed']}, false {c['false']} "
            f"(good finds missed {c['good finds missed']})"
        )
    if report.readings:  # (#4655: the table's guesses, quests slice 7a)
        c = report.readings
        lines.append(
            f"Readings: found {c['found']}, missed {c['missed']}, "
            f"wrong holder {c['wrong holder']}, false {c['false']}"
        )
    if report.route_lengths:  # (#4255: a road's length, a property of the road, not a tie)
        c = report.route_lengths
        lines.append(f"Road lengths: found {c['found']}, missed {c['missed']}, extra {c['extra']}")
    lines.append(f"Must-not-find names present: {len(report.violations)}")
    if report.violations and not counts_only:
        lines.append("  " + ", ".join(report.violations))
    lines.append(
        "Inferred ties already confirmed as facts (they should be offered; a yes from the DM "
        f"is fine): {len(report.inferred_confirmed)}"
    )
    lines.append(f"Must-not-make ties kept as facts: {len(report.false_ties)}")
    if report.false_ties and not counts_only:
        lines.append("  " + ", ".join(report.false_ties))
    lines.append("")
    for label, status in (
        ("Must-find ties missing", MISSING),
        ("Must-find ties of the wrong type", WRONG),
    ):
        rows = [t for t in report.ties if t.must and t.status == status]
        lines.append(f"{label}: {len(rows)}")
        for t in rows:
            more = "" if counts_only else f"  {t.text}" + (f"  [{t.detail}]" if t.detail else "")
            lines.append(f"  {t.id}{more}")
    waiting = [t for t in report.ties if t.must and t.status == WAITING]
    lines.append(f"Must-find ties found only as a waiting suggestion: {len(waiting)}")
    for t in waiting:
        lines.append(f"  {t.id}" + ("" if counts_only else f"  {t.text}"))
    scored = Counter(v for _, v in report.feelings)
    lines.append(
        f"Feelings scored: {len(report.feelings)} (exact {scored[reputation_words.EXACT]}, "
        f"one step off {scored[reputation_words.ONE_STEP]}, "
        f"over-read {scored[reputation_words.OVER_READ]}, "
        f"under-read {scored[reputation_words.UNDER_READ]}, "
        f"wrong side {scored[reputation_words.WRONG_SIDE]})"
    )
    for tie_id, verdict in report.feelings:
        if verdict not in (reputation_words.EXACT, reputation_words.ONE_STEP):
            lines.append(f"  {tie_id}  {verdict}")
    design = [e for e in report.entries if e.status == BY_DESIGN]
    lines.append(f"Entries left out by design (kinds the rules hold, not misses): {len(design)}")
    for e in design:
        lines.append("  " + ("" if counts_only else e.text))
    left = [e for e in report.entries if e.status not in (FOUND, BY_DESIGN)]
    lines.append(f"Entries missing or of the wrong kind: {len(left)}")
    for e in left:
        lines.append(f"  {e.status:<11}" + ("" if counts_only else e.text))
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, *, met: Sequence[str] = ()) -> int:
    """The command. `met`: the campaign's "met but not named" words, from the caller (sample_run
    reads them from the database), or `--met FILE` (a JSON list, kept on the server)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("backup", type=Path, help="the campaign backup (.dmbot.json.gz or .json)")
    parser.add_argument(
        "--key", type=Path, default=DEFAULT_KEY, help="the answer key (default: the master key)"
    )
    parser.add_argument("--docs", choices=("lore", "session", "both"), default="both")
    parser.add_argument("--counts-only", action="store_true", help="print ids and counts, no names")
    parser.add_argument("--json", type=Path, help="also write the counts and rows as json")
    parser.add_argument("--i-know-this-is-the-test-campaign", action="store_true", dest="force")
    parser.add_argument(
        "--met", type=Path, help='the "met but not named" words, a JSON list (#5055 D)'
    )
    parser.add_argument(
        "--list-extras",
        action="store_true",
        help="list each entry the key does not match by name (the public sample only, #5055)",
    )
    parser.add_argument(
        "--why-missed",
        action="store_true",
        help="also say, for each key name not found and kept, where it stopped and why (#5055)",
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="with --why-missed: the sample run's decision record (decisions-*.jsonl), for what "
        "the careful AI decided; give it once for each file",
    )
    parser.add_argument(
        "--trace",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="with --why-missed: the run's name trace (trace-<campaign>.jsonl, #5055 B), so a name "
        "never stored says which reader ended it and why",
    )
    parser.add_argument(
        "--reasons",
        action="store_true",
        help="with --why-missed: add the careful AI's own words from the decision record; for "
        "reading on the server only (the record never goes on GitHub)",
    )
    args = parser.parse_args(argv)
    try:
        backup = decode_backup(args.backup.read_bytes())
        key = json.loads(args.key.read_text())
        campaign = backup.get("campaign") if isinstance(backup, dict) else None
        name = campaign.get("name") if isinstance(campaign, dict) else None
        sample = isinstance(name, str) and sample_name.is_sample(name)
        if args.list_extras and not sample:  # (names go on a public issue: the sample's only)
            raise ValueError("--list-extras is only for the public sample campaign.")
        if not sample and not args.force:
            raise ValueError(
                'This campaign is not "test of the frostmaiden (sample N)"; the check is only '
                "for the sample."
            )
        if args.met is not None:
            listed = json.loads(args.met.read_text())
            if not isinstance(listed, list) or not all(isinstance(w, str) for w in listed):
                raise ValueError("--met is a JSON list of words")
            met = listed
        report = check(backup, key, args.docs, met)
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    sys.stdout.write(
        f"Documents scored: {args.docs}\n"
        + render(report, args.counts_only, list_extras=args.list_extras)
    )
    if args.why_missed:
        # (here, not at the top: `why_missed` reads this module's lookups)
        from dmbot.devtools import why_missed

        try:
            decided = why_missed.read_decisions(args.decisions)
        except (OSError, ValueError) as exc:  # (ValueError: a file that is not text)
            sys.stderr.write(f"{exc}\n")
            return 2
        traced = None
        if args.trace:
            from dmbot.memory import name_trace

            try:
                traced = [r for path in args.trace for r in name_trace.read(path)]
            except (OSError, ValueError) as exc:  # (a missing file: one plain line, exit 2)
                sys.stderr.write(f"{exc}\n")
                return 2
        whys = why_missed.explain(
            backup, key, args.docs, decided, reasons=args.reasons, trace=traced
        )
        if args.counts_only:  # (the stages alone: no key names)
            counts = why_missed.tally(whys)
            sys.stdout.write(
                "Why missed: "
                + ", ".join(f"{s} {counts[s]}" for s in why_missed.STAGES if counts[s])
                + "\n"
            )
        else:
            sys.stdout.write(why_missed.render(whys))
    if args.json:
        rows = [asdict(t) for t in report.ties]
        entries = [asdict(e) for e in report.entries]
        if args.counts_only:
            rows = [{k: v for k, v in r.items() if k not in ("text", "detail")} for r in rows]
            entries = [{k: v for k, v in e.items() if k not in ("id", "text")} for e in entries]
        args.json.write_text(
            json.dumps(
                {"docs": args.docs, "summary": summarize(report), "ties": rows, "entries": entries},
                indent=1,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
