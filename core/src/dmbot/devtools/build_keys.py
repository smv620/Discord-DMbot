"""Builds the two-stage sample run's answer keys (#4523 slice 1b) from the keys written by hand, so
nothing is merged by hand twice and a test can check the built files match.

    python -m dmbot.devtools.build_keys [--check]

- `total_key.json`, stage 1: the four secret backstories (`backstory_*.json`), then the public lore
  (`lore_key.json`). What the lore states of a backstory (its `reveals`) is one row with both
  sources, known as the lore says (the party's, or everyone's); every other backstory row stays its
  own knowers' secret.
- `full_key.json`, stage 2: stage 1, then the session. It starts from `master_key.json` (the lore
  and session already merged, by hand, as today's single-stage run scores them) and adds the
  backstories the same way. A backstory row the session states (the same two entries and tie) is
  the party's too: said at the table.

`master_key.json` is left as it is: today's run (`--stages 1`) keeps scoring against it, so its
series goes on. Every row of a built key says who knows it (`knowers`, #4523 ruling A); a row the
lore or session states and the hand-written keys give no knowers is the party's, said at the table.
Reads and writes only the key files: names, kinds, ties and knowers, never a document's text."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

KEYS = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "story_graph"
BACKSTORIES = ("cerric", "okthandom", "claude", "belleros")  # (the upload order, stage 1a)
PARTY = "party"


def _load(name: str) -> dict[str, Any]:
    return dict(json.loads((KEYS / name).read_text(encoding="utf-8")))


def _names(node: Mapping[str, Any]) -> list[str]:
    return [str(node["name"]), *(str(a) for a in node.get("aliases", ()))]


class _Key:
    """A key being built: its nodes by every name they answer to, and its rows."""

    def __init__(self, base: dict[str, Any]) -> None:
        self.key = base
        self.canon: dict[str, str] = {}
        for node in base["nodes"]:
            for name in _names(node):
                self.canon.setdefault(name.casefold(), str(node["name"]))
        self.nodes = {str(n["name"]): n for n in base["nodes"]}

    def name(self, name: str) -> str:
        return self.canon.get(name.casefold(), name)

    def triple(self, row: Mapping[str, Any]) -> tuple[str, str, str]:
        return (self.name(str(row["a"])), str(row["relation"]), self.name(str(row["b"])))

    def add_node(self, node: Mapping[str, Any], doc: str) -> None:
        """A backstory's entry: one already in the key (by any name) gains the backstory as a
        source and keeps the key's knowers, since the public document names it; a new one is
        added with the backstory's own knowers."""
        known = next((self.name(n) for n in _names(node) if n.casefold() in self.canon), None)
        if known is not None:
            have = self.nodes[known]
            have["docs"] = [*have.get("docs", []), doc]
            extra = [a for a in _names(node) if a.casefold() not in self.canon]
            if extra:
                have["aliases"] = [*have.get("aliases", []), *extra]
            for alias in extra:
                self.canon[alias.casefold()] = known
            return
        new = {k: v for k, v in node.items() if k != "check"}
        new["docs"] = [doc]
        if node.get("check"):
            new["check"] = True
        self.key["nodes"].append(new)
        self.nodes[str(node["name"])] = new
        for name in _names(node):
            self.canon.setdefault(name.casefold(), str(node["name"]))


def _source(doc: str, row: Mapping[str, Any]) -> dict[str, Any]:
    return {"doc": doc, "id": row["id"], "line": row.get("line")}


def _merge_pair(have: dict[str, Any], doc: str, row: Mapping[str, Any]) -> None:
    have["in"] = [*have.get("in", []), doc]
    have["sources"] = [*have.get("sources", []), _source(doc, row)]


def _add_pair(key: _Key, doc: str, row: Mapping[str, Any]) -> None:
    new = {
        "id": row["id"], "a": key.name(str(row["a"])), "relation": row["relation"],
        "b": key.name(str(row["b"])), "tier": row["tier"], "must_find": row["must_find"],
        "in": [doc], "sources": [_source(doc, row)], "knowers": row["knowers"],
    }  # fmt: skip
    for field in ("detail", "current", "check", "note"):
        if field in row:
            new[field] = row[field]
    key.key["pairs"].append(new)


def _add_backstory(
    key: _Key, slug: str, story: Mapping[str, Any], lore_ids: Mapping[str, list[str]], by: str
) -> None:
    """One backstory into the key: `lore_ids` maps a backstory row to the key rows that reveal it
    (the lore's `reveals`, turned round); `by` is the prefix the key's lore rows carry."""
    doc = f"backstory_{slug}"
    for node in story["nodes"]:
        key.add_node(node, doc)
    pairs_by_lore = {
        str(src["id"]): p
        for p in key.key["pairs"]
        for src in p.get("sources", ())
        if src.get("doc") == "lore"
    }
    pairs_by_triple = {key.triple(p): p for p in key.key["pairs"]}
    for row in story["pairs"]:
        revealed = [pairs_by_lore[i] for i in lore_ids.get(row["id"], ()) if i in pairs_by_lore]
        same = revealed[0] if revealed else pairs_by_triple.get(key.triple(row))
        if same is not None:
            _merge_pair(same, doc, row)  # (stated by a public document: known as it says)
        else:
            _add_pair(key, doc, row)
    events = {str(e["id"]): e for e in key.key.get("events", [])}
    for event in story["events"]:
        shown = [events[f"{by}{i}"] for i in lore_ids.get(event["id"], ()) if f"{by}{i}" in events]
        if shown:
            shown[0]["also"] = [*shown[0].get("also", []), _source(doc, event)]
        else:
            key.key.setdefault("events", []).append({**event, "doc": doc})
    threads = {str(t["id"]): t for t in key.key.get("threads", [])}
    for thread in story["threads"]:
        lore_id = thread.get("same_as") or next(iter(lore_ids.get(thread["id"], ())), None)
        same_thread = threads.get(f"{by}{lore_id}") if lore_id else None
        if same_thread is not None:
            same_thread["also"] = [*same_thread.get("also", []), _source(doc, thread)]
        else:
            key.key.setdefault("threads", []).append({**thread, "doc": doc})
    have = {(lang["character"], lang["language"]) for lang in key.key.get("languages", [])}
    for lang in story.get("languages", ()):
        if (lang["character"], lang["language"]) not in have:
            key.key.setdefault("languages", []).append({**lang, "doc": doc})


def _revealed_by(lore: Mapping[str, Any]) -> dict[str, list[str]]:
    """The lore's `reveals` turned round: a backstory row id -> the lore ids that state it."""
    out: dict[str, list[str]] = {}
    for by, ids in lore.get("reveals", {}).items():
        for i in ids:
            out.setdefault(str(i), []).append(str(by))
    return out


def _with_knowers(rows: Iterable[dict[str, Any]], lore_knowers: Mapping[str, str]) -> None:
    """A row the lore states takes the lore's knowers; one only the session states is the party's
    (said at the table)."""
    for row in rows:
        if "knowers" in row:
            continue
        lore_ids = [str(s["id"]) for s in row.get("sources", ()) if s.get("doc") == "lore"]
        row["knowers"] = next((lore_knowers[i] for i in lore_ids if i in lore_knowers), PARTY)


def total_key() -> dict[str, Any]:
    """Stage 1: the four backstories, then the lore."""
    lore = _load("lore_key.json")
    base: dict[str, Any] = {
        "campaign": lore["campaign"],
        "version": 1,
        "stage": "1: the four secret backstories (backstory_*.json), then the public lore",
        "order": [f"backstory_{s}" for s in BACKSTORIES] + ["lore"],
        "how_to_read": {
            **lore["how_to_read"],
            "in": "the documents a row is stated in; sources gives each one's row and line",
            "also": "an event or thread a backstory tells too, with its row",
            "built": "built by `python -m dmbot.devtools.build_keys`: never edit by hand",
        },
        "players": lore["players"],
        "nodes": [{**n, "docs": ["lore"]} for n in lore["nodes"]],
        "pairs": [
            {
                **{k: v for k, v in p.items() if k != "line"},
                "in": ["lore"],
                "sources": [{"doc": "lore", "id": p["id"], "line": p["line"]}],
            }
            for p in lore["pairs"]
        ],
        "events": [{**e, "doc": "lore"} for e in lore["events"]],
        "threads": [
            {**t, "doc": "lore", "knowers": lore.get("thread_knowers", PARTY)}
            for t in lore["threads"]
        ],
        "languages": [{**lang, "doc": "lore"} for lang in lore.get("languages", [])],
        "must_not_find": list(lore.get("must_not_find", [])),
    }
    key = _Key(base)
    revealed = _revealed_by(lore)
    for slug in BACKSTORIES:
        _add_backstory(key, slug, _load(f"backstory_{slug}.json"), revealed, by="")
    return key.key


def full_key() -> dict[str, Any]:
    """Stage 2: stage 1, then the session (master_key.json is the lore and session together)."""
    lore = _load("lore_key.json")
    master = copy.deepcopy(_load("master_key.json"))
    lore_knowers = {str(p["id"]): str(p["knowers"]) for p in lore["pairs"]}
    _with_knowers(master["pairs"], lore_knowers)
    node_knowers = {str(n["name"]): str(n["knowers"]) for n in lore["nodes"]}
    for node in master["nodes"]:
        node.setdefault("knowers", node_knowers.get(str(node["name"]), PARTY))
    event_knowers = {f"L-{e['id']}": str(e["knowers"]) for e in lore["events"]}
    for event in master.get("events", []):
        event.setdefault("knowers", event_knowers.get(str(event["id"]), PARTY))
    for thread in master.get("threads", []):
        thread.setdefault("knowers", lore.get("thread_knowers", PARTY))
    master["version"] = 1
    master["stage"] = "2: stage 1, then the sample session (master_key.json, the lore and session)"
    master["order"] = [f"backstory_{s}" for s in BACKSTORIES] + ["lore", "session"]
    master["how_to_read"] = {
        **master["how_to_read"],
        "knowers": lore["how_to_read"]["knowers"],
        "also": "an event or thread a backstory tells too, with its row",
        "built": "built by `python -m dmbot.devtools.build_keys`: never edit by hand",
    }
    key = _Key(master)
    revealed = _revealed_by(lore)
    for slug in BACKSTORIES:
        _add_backstory(key, slug, _load(f"backstory_{slug}.json"), revealed, by="L-")
    return key.key


def _text(key: Mapping[str, Any]) -> str:
    return json.dumps(key, ensure_ascii=False, indent=1) + "\n"


BUILT = {"total_key.json": total_key, "full_key.json": full_key}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if a built file is stale")
    args = parser.parse_args(argv)
    stale = []
    for name, build in BUILT.items():
        text = _text(build())
        path = KEYS / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            path.write_text(text, encoding="utf-8")
    if stale:
        print(f"stale: {', '.join(stale)} (run python -m dmbot.devtools.build_keys)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
