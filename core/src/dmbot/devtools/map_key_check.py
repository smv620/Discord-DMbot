"""Scores a campaign backup against the sample map's answer key (#4423), with counts only.

    python -m dmbot.devtools.map_key_check BACKUP.dmbot.json.gz
        [--key core/tests/fixtures/story_graph/map_key.json] [--json OUT.json]

The key (`map_key.json`, readable as `map_key.md`) says what MapBot should read from the owner's
sample Icewind Dale map. The map itself is published art and stays on the server; the key holds
names and facts only, and this tool prints counts only, so its report can go on #3545. It reads the
backup file and nothing else: no database, no Discord, no AI. Like `graph_key_check`, it refuses a
backup whose campaign is not the public sample.

What MapBot keeps today (#4349, its close read) is each map's note: the places the map names, with
their size and form, and how the places sit (`in`, `next_to`, `route_to`, `apart`). So:
- **scored now:** the pins by name, the roads (`route_to`), "in" and "on the shore of" (`in` and
  `next_to`), and places on the map the key says it doesn't name;
- **not stored yet,** said as such and never counted as missed: the pins' positions, the compass
  directions, the straight-line distances, the scale and north, and, while no MapBot slice writes
  them, layer rows read from the map (`source = 'map'`) and entries made from a map. A later slice
  that keeps them turns these lines into scores with no change to the key.

Names are compared as `name_key` does, with spaces and a leading "the" dropped too, so "Caer
Konig", "Caer-Konig" and "The Redrun" / "Redrun" meet.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dmbot.campaigns.store import decode_backup
from dmbot.devtools import sample_name
from dmbot.memory.models import name_key

DEFAULT_KEY = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "story_graph" / "map_key.json"
)
SAMPLE_CAMPAIGN = sample_name.OLD_NAME  # (numbered names pass `sample_name.is_sample`, #4538)
FOUND, MISSING, NOT_STORED = "found", "missing", "not stored yet"
# How a key's "where it sits" word is kept by the close read (`images.LINK_KINDS`); a compass word
# needs two positions on the map, which nothing keeps yet
REL_KINDS = {"in": "in", "on the shore of": "next_to"}
# Layer rows a map must never give (the key's must-not list, mapbot_layers 7.2)
NEVER_FROM_A_MAP = ("culture", "population", "control", "climate")


@dataclass(slots=True)
class Report:
    """Counts only, by part of the key."""

    parts: dict[str, Counter[str]] = field(default_factory=dict)
    must_not: Counter[str] = field(default_factory=Counter)
    extra_places: int = 0
    notes: int = 0  # map notes in the backup
    read_closely: int = 0  # of them, with a close read

    def add(self, part: str, status: str, must: bool) -> None:
        tally = self.parts.setdefault(part, Counter())
        tally[status] += 1
        if must and status == MISSING:
            tally["must missed"] += 1


def _k(name: str) -> str:
    """How the key and the map's names are compared (see the module's note)."""
    key = name_key(name)
    if key.startswith("the "):
        key = key[4:]
    return key.replace(" ", "")


def _section(backup: Mapping[str, Any], name: str) -> list[Mapping[str, Any]]:
    sections = backup.get("sections")
    rows = sections.get(name) if isinstance(sections, Mapping) else None
    return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else []


def read_maps(
    backup: Mapping[str, Any],
) -> tuple[set[str], list[tuple[str, str, str]], int, int]:
    """(the place names the maps name, as keys; their links as (a, kind, b) keys; notes; notes with
    a close read) from the backup's map notes."""
    names: set[str] = set()
    links: list[tuple[str, str, str]] = []
    notes = _section(backup, "map_notes")
    closely = 0
    for note in notes:
        for p in note.get("places") or []:
            if isinstance(p, Mapping) and p.get("name"):
                names.add(_k(str(p["name"])))
        close = note.get("close")
        if not isinstance(close, Mapping):
            continue
        closely += 1
        for p in close.get("places") or []:
            if isinstance(p, Mapping) and p.get("name"):
                names.add(_k(str(p["name"])))
        for k in close.get("links") or []:
            if isinstance(k, Mapping) and k.get("a") and k.get("b"):
                links.append((_k(str(k["a"])), str(k.get("kind", "")), _k(str(k["b"]))))
    return names, links, len(notes), closely


def _memory(backup: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = {}
    for r in _section(backup, "memory"):
        out.setdefault(str(r.get("table")), []).append(r)
    return out


def _linked(links: Iterable[tuple[str, str, str]], a: str, kind: str, b: str) -> bool:
    """A link of `kind` between a and b; `in` keeps its way round, the others go either way."""
    for x, k, y in links:
        if k != kind:
            continue
        if (x, y) == (a, b) or (kind != "in" and (x, y) == (b, a)):
            return True
    return False


def check(backup: Mapping[str, Any], key: Mapping[str, Any]) -> Report:
    report = Report()
    names, links, report.notes, report.read_closely = read_maps(backup)
    pins = key.get("pins", [])
    for pin in pins:
        must = bool(pin.get("must_find"))
        found = _k(str(pin["name"])) in names
        report.add("pins (by name)", FOUND if found else MISSING, must)
        report.add("pins (position)", NOT_STORED, must)
    report.extra_places = len(names - {_k(str(p["name"])) for p in pins})
    for road in key.get("routes", []):
        a, b = _k(str(road["from"])), _k(str(road["to"]))
        report.add(
            "roads", FOUND if _linked(links, a, "route_to", b) else MISSING, bool(road["must_find"])
        )
    for s in key.get("spatial", []):
        kind = REL_KINDS.get(str(s["rel"]))
        must = bool(s.get("must_find"))
        if kind is None:
            report.add("which way (compass)", NOT_STORED, must)
            continue
        a, b = _k(str(s["a"])), _k(str(s["b"]))
        report.add("in, on the shore of", FOUND if _linked(links, a, kind, b) else MISSING, must)
    for d in key.get("distances", []):
        report.add("distances", NOT_STORED, bool(d.get("must_find")))
    for part in ("scale", "north"):
        if part in key.get("map", {}):
            report.add(part, NOT_STORED, bool(key["map"][part].get("must_find")))
    memory = _memory(backup)
    places = {str(e["id"]): _k(str(e.get("name", ""))) for e in memory.get("entity", [])}
    from_map = [
        f for f in memory.get("map_fact", [])
        if f.get("source") == "map" and f.get("status") != "rejected"
    ]  # fmt: skip
    for row in key.get("layers", []) if not from_map else ():  # (nothing writes them yet)
        report.add("layers from the map", NOT_STORED, bool(row.get("must_find")))
    for row in key.get("layers", []) if from_map else ():
        want = (_k(str(row["place"])), str(row["layer"]), str(row["value"]))
        hit = any(
            (places.get(str(f.get("entity_id"))), f.get("layer"), f.get("value")) == want
            for f in from_map
        )
        report.add("layers from the map", FOUND if hit else MISSING, bool(row.get("must_find")))
    report.must_not.update(
        f"a {f['layer']} row from the map" for f in from_map if f.get("layer") in NEVER_FROM_A_MAP
    )
    for rule in key.get("must_not_find", []):
        named = {_k(str(n)) for n in rule.get("names", [])}
        if "doesn't name" in str(rule.get("what", "")):
            report.must_not["a place the map doesn't name"] += len(named & names)
        elif named:  # (a second entry: no MapBot slice makes entries from a map yet)
            report.add("a second entry from the map", NOT_STORED, False)
    return report


def render(report: Report) -> list[str]:
    """Counts only: never a name from the backup."""
    out = [
        f"Map notes: {report.notes} ({report.read_closely} read closely)",
    ]
    for part, c in report.parts.items():
        total = sum(v for k, v in c.items() if k != "must missed")
        if c[NOT_STORED] == total:
            out.append(f"{part}: {total} in the key, not stored yet")
            continue
        out.append(
            f"{part}: found {c[FOUND]} of {total}, missing {c[MISSING]}"
            f" (must-find missed {c['must missed']})"
        )
    out.append(f"places on the maps the key doesn't list: {report.extra_places}")
    hits = ", ".join(f"{k} {v}" for k, v in sorted(report.must_not.items()) if v)
    out.append(f"must-not-find hits: {hits or 'none'}")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("backup", type=Path, help="the campaign backup (.dmbot.json.gz or .json)")
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY, help="the map key")
    parser.add_argument("--json", type=Path, help="also write the counts as json")
    args = parser.parse_args(argv)
    try:
        backup = decode_backup(args.backup.read_bytes())
        key = json.loads(args.key.read_text(encoding="utf-8"))
        if not isinstance(backup, dict):
            raise ValueError("That file is not a campaign backup.")
        campaign = backup.get("campaign")
        found = campaign.get("name") if isinstance(campaign, dict) else None
        if not isinstance(found, str) or not sample_name.is_sample(found):
            raise ValueError(
                'This campaign is not "test of the frostmaiden (sample N)"; the check is only '
                "for it."
            )
        report = check(backup, key)
    except OSError as exc:  # (the kind only: its message holds the file's path)
        sys.stderr.write(f"The file could not be read ({type(exc).__name__}).\n")
        return 2
    except (KeyError, TypeError):
        sys.stderr.write("The map key is not readable.\n")
        return 2
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    lines = render(report)
    sys.stdout.write("\n".join(lines) + "\n")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "parts": {k: dict(v) for k, v in report.parts.items()},
                    "must_not": dict(report.must_not),
                    "extra_places": report.extra_places,
                },
                indent=1,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
