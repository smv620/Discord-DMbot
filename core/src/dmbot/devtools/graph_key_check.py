"""Scores a campaign backup against the story graph answer key (#3244), with counts and ids only.

    python -m dmbot.devtools.graph_key_check BACKUP.dmbot.json.gz
        [--key core/tests/fixtures/story_graph/master_key.json] [--docs lore|session|both]
        [--counts-only] [--json OUT.json]

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
from dmbot.memory import kin, place_words, reputation_words
from dmbot.memory.models import name_key

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
SAMPLE_CAMPAIGN = "test of the frostmaiden (sample)"
# What slot (word, past) each key place tie means, as `place_words.slot` reads a stored detail.
PLACE_SLOTS = {
    "lives_in": (place_words.LIVES, False), "lived_in": (place_words.LIVES, True),
    "works_in": (place_words.WORKS, False), "comes_from": (place_words.FROM, False),
    "last_seen_in": (place_words.LAST_SEEN, False), "kept_in": (place_words.KEPT, False),
    "was_kept_in": (place_words.KEPT, True),
}  # fmt: skip

# The stand-in the "who plays" step makes for a player (`store._placeholder_name`, #1559).
PLACEHOLDER = re.compile(r"Player \d{4}")
FOUND, WRONG, WAITING, MISSING = "found", "wrong_type", "waiting_only", "missing"
# A kind of creature the rules already hold is left out of story memory on purpose
# (`in_the_rules`, 2026-11-06 ruling on #3545): marked `"by_design": true` in the key, it is not a
# miss when absent and is listed on its own line, so a change in that rule shows up. A particular
# creature the lore names or sets apart is an entry and stays must-find (no flag).
BY_DESIGN = "by_design"
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
    extra_ties: int = 0
    violations: list[str] = field(default_factory=list)
    inferred_confirmed: list[str] = field(default_factory=list)
    events_in_backup: int = 0
    events_expected: int = 0
    players_found: int = 0  # the key's player characters that are entries with that role (#3553)
    players_expected: int = 0
    # `Player 1234` entries the "who plays" step made and nothing uses: not DMbot's extras (#3545)
    placeholders: int = 0


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


class Graph:
    """The backup's entries and connections, found by name."""

    def __init__(self, tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        self.entities = {
            str(e["id"]): e
            for e in tables.get("entity", ())
            if _live(e) and not e.get("merged_into")
        }
        self.by_key: dict[str, set[str]] = {}
        for e in self.entities.values():
            self.by_key.setdefault(name_key(str(e["name"])), set()).add(str(e["id"]))
        for a in tables.get("alias", ()):
            if a.get("status") != "rejected" and str(a["entity_id"]) in self.entities:
                self.by_key.setdefault(str(a["key"]) or name_key(str(a["text"])), set()).add(
                    str(a["entity_id"])
                )
        self.relations = [r for r in tables.get("relation", ()) if _live(r)]

    def ids(self, names: Sequence[str]) -> set[str]:
        found: set[str] = set()
        for n in names:
            found |= self.by_key.get(name_key(n), set())
        return found

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


def check(backup: object, key: Mapping[str, Any], docs: str = "both") -> Report:
    graph = Graph(_tables(backup))
    wanted = {"lore", "session"} if docs == "both" else {docs}
    report = Report()
    nodes = [n for n in key["nodes"] if wanted & set(n.get("docs", ["lore", "session"]))]
    names: dict[str, list[str]] = {n["name"]: [n["name"], *n.get("aliases", [])] for n in nodes}
    kinds = {i: str(e["type"]) for i, e in graph.entities.items()}
    kind_of_key = {n["name"]: n["kind"] for n in nodes}
    matched_ids: set[str] = set()
    used_relations: set[str] = set()
    for n in nodes:
        ids = graph.ids(names[n["name"]])
        matched_ids |= ids
        if not ids:
            status = BY_DESIGN if n.get("by_design") else MISSING
        elif any(kinds[i] in ENTRY_TYPES.get(n["kind"], frozenset()) for i in ids):
            status = FOUND
        else:
            status = WRONG
        report.entries.append(
            Row(n["name"], n["name"], status, n["kind"], not n.get("by_design", False))
        )
    for t in key["pairs"]:
        if not wanted & set(t.get("in", ["lore", "session"])):
            continue
        if t["a"] not in names or t["b"] not in names:
            continue
        a, b = graph.ids(names[t["a"]]), graph.ids(names[t["b"]])
        if not a or not b:
            status, why, used_here = MISSING, "no entry", set[str]()
        else:
            status, why, used_here = _tie_status(t, graph, a, b, kinds)
        used_relations |= used_here
        if t["tier"] == "inferred" and status == FOUND:
            report.inferred_confirmed.append(t["id"])
        group = GROUPS.get(t["relation"], "other")
        report.ties.append(
            Row(t["id"], _text(t), status, group, bool(t["must_find"]), t["tier"], why)
        )
    # extras: entries and connections the key does not name
    # a placeholder's own tie is `plays` (the player, to the character they play): that is what
    # makes it a placeholder, so only another connection makes it a real entry
    others = [r for r in graph.relations if str(r["predicate"]) != "plays"]
    linked = {str(r["subject_id"]) for r in others} | {str(r["object_id"]) for r in others}
    for i, e in graph.entities.items():
        if i in matched_ids:
            continue
        if PLACEHOLDER.fullmatch(str(e["name"])) and i not in linked:
            report.placeholders += 1  # (the who-plays stand-in, #1559: not an extra entry)
            continue
        report.extra_entries[_kind_for(str(e["type"]))] += 1
    for name in key.get("must_not_names", ()):
        if graph.ids([name]):
            report.violations.append(name)
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
    return report


def _kind_for(entry_type: str) -> str:
    for kind, types in ENTRY_TYPES.items():
        if entry_type in types:
            return kind
    return entry_type


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
        "extra_ties": report.extra_ties,
        "violations": len(report.violations),
        "inferred_confirmed": len(report.inferred_confirmed),
        "event_entries": {"expected": report.events_expected, "in_backup": report.events_in_backup},
        "player_characters": {"found": report.players_found, "expected": report.players_expected},
    }


def _row(label: str, c: Mapping[str, int]) -> str:
    cells = (c["total"], c[FOUND], c[WAITING], c[WRONG], c[MISSING], c["must_missed"])
    return f"  {label:<13}" + "".join(f"{n:>{w}}" for n, w in zip(cells, WIDTHS, strict=True))


def render(report: Report, counts_only: bool = False) -> str:
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
    lines.append(f"Player placeholders left out (not extras): {report.placeholders}")
    lines.append(f"Extra ties between key entries: {report.extra_ties}")
    lines.append(
        f"Event entries: {report.events_in_backup} in the backup, {report.events_expected} expected"
    )
    lines.append(f"Player characters found: {report.players_found} of {report.players_expected}")
    lines.append(f"Must-not-find names present: {len(report.violations)}")
    if report.violations and not counts_only:
        lines.append("  " + ", ".join(report.violations))
    lines.append(
        "Inferred ties already confirmed as facts (they should be offered; a yes from the DM "
        f"is fine): {len(report.inferred_confirmed)}"
    )
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
    design = [e for e in report.entries if e.status == BY_DESIGN]
    lines.append(f"Entries left out by design (kinds the rules hold, not misses): {len(design)}")
    for e in design:
        lines.append("  " + ("" if counts_only else e.text))
    left = [e for e in report.entries if e.status not in (FOUND, BY_DESIGN)]
    lines.append(f"Entries missing or of the wrong kind: {len(left)}")
    for e in left:
        lines.append(f"  {e.status:<11}" + ("" if counts_only else e.text))
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
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
    args = parser.parse_args(argv)
    try:
        backup = decode_backup(args.backup.read_bytes())
        key = json.loads(args.key.read_text())
        campaign = backup.get("campaign") if isinstance(backup, dict) else None
        name = campaign.get("name") if isinstance(campaign, dict) else None
        if name != SAMPLE_CAMPAIGN and not args.force:
            raise ValueError(
                f'This campaign is not "{SAMPLE_CAMPAIGN}"; the check is only for the sample.'
            )
        report = check(backup, key, args.docs)
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    sys.stdout.write(f"Documents scored: {args.docs}\n" + render(report, args.counts_only))
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
