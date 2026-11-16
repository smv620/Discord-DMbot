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
        """A backstory's entry. One a public document names (by any name) gains the backstory as
        a source and keeps the public knowers; a name only the backstory gives is kept beside it
        as a secret name with the backstory's knowers (`secret_aliases`), never as a public one.
        One only earlier backstories name gains this backstory's knowers too. A new one is added
        with the backstory's own knowers."""
        known = next((self.name(n) for n in _names(node) if n.casefold() in self.canon), None)
        if known is None:
            new = {k: v for k, v in node.items()}
            new["docs"] = [doc]
            self.key["nodes"].append(new)
            self.nodes[str(node["name"])] = new
            for name in _names(node):
                self.canon.setdefault(name.casefold(), str(node["name"]))
            return
        have = self.nodes[known]
        public = _is_public(have.get("docs", ()))
        have["docs"] = [*have.get("docs", []), doc]
        if not public:
            have["knowers"] = _union(have.get("knowers"), node.get("knowers"))
        for alias in (a for a in _names(node) if a.casefold() not in self.canon):
            if public:
                have.setdefault("secret_aliases", {})[alias] = node.get("knowers")
            else:
                have["aliases"] = [*have.get("aliases", []), alias]
            self.canon[alias.casefold()] = known


PUBLIC_DOCS = ("lore", "session")


def _is_public(docs: Iterable[str]) -> bool:
    return any(d in PUBLIC_DOCS for d in docs)


def _union(a: Any, b: Any) -> Any:
    """Two backstories' knowers of one thing: a group word wins (it is wider); else both lists."""
    for word in ("everyone", PARTY):
        if word in (a, b):
            return word
    if isinstance(a, list) and isinstance(b, list):
        return [*a, *(x for x in b if x not in a)]
    return a if a is not None else b


def _source(doc: str, row: Mapping[str, Any]) -> dict[str, Any]:
    return {"doc": doc, "id": row["id"], "line": row.get("line")}


def _merge(have: dict[str, Any], doc: str, row: Mapping[str, Any]) -> None:
    """A backstory row a public row states: one row, both sources, the public row's knowers."""
    if "relation" in have:
        have["in"] = [*have.get("in", []), doc]
        have["sources"] = [*have.get("sources", []), _source(doc, row)]
    else:
        have["also"] = [*have.get("also", []), _source(doc, row)]


def _add_pair(key: _Key, doc: str, row: Mapping[str, Any], knowers: Any) -> None:
    new = {
        "id": row["id"], "a": key.name(str(row["a"])), "relation": row["relation"],
        "b": key.name(str(row["b"])), "tier": row["tier"], "must_find": row["must_find"],
        "in": [doc], "sources": [_source(doc, row)], "knowers": knowers,
    }  # fmt: skip
    for field in ("detail", "current", "check", "note"):
        if field in row:
            new[field] = row[field]
    key.key["pairs"].append(new)


def _event(event: Mapping[str, Any], doc: str, knowers: Any) -> dict[str, Any]:
    """A backstory event in the master key's shape (`who` a string, `lines` a string)."""
    out = {k: v for k, v in event.items() if k not in ("who", "line", "knowers")}
    who = event.get("who", "")
    out.update({"doc": doc, "who": ", ".join(who) if isinstance(who, list) else str(who),
                "lines": str(event.get("line", "")), "knowers": knowers})  # fmt: skip
    return out


def _public_rows(key: _Key, by: str) -> dict[str, dict[str, Any]]:
    """Every row a public document states, by the lore id `reveals` uses: a connection by its
    lore source, an event or a thread by its id without the key's prefix (`by`)."""
    rows: dict[str, dict[str, Any]] = {}
    for p in key.key["pairs"]:
        for src in p.get("sources", ()):
            if src.get("doc") == "lore":
                rows[str(src["id"])] = p
    for section in ("events", "threads"):
        for row in key.key.get(section, []):
            if row.get("doc") == "lore" or (not row.get("doc") and str(row["id"]).startswith(by)):
                rows[str(row["id"])[len(by) :]] = row
    return rows


def _add_backstory(
    key: _Key, slug: str, story: Mapping[str, Any], lore_ids: Mapping[str, list[str]], by: str
) -> None:
    """One backstory into the key. `lore_ids` maps a backstory row to the lore rows that reveal
    it (the lore's `reveals`, turned round), of any kind: a connection the lore tells as an event
    is the party's all the same. A revealed row of the same kind is merged into the lore's; one of
    another kind keeps its own row, known as the lore row says. A connection a public document
    states (the same two entries and tie) is merged too. `by` is the prefix the key's lore events
    and threads carry. A reveal that points at nothing stops the build."""
    doc = f"backstory_{slug}"
    for node in story["nodes"]:
        key.add_node(node, doc)
    public = _public_rows(key, by)
    by_triple = {key.triple(p): p for p in key.key["pairs"] if _is_public(p.get("in", ()))}

    def reveal(row: Mapping[str, Any]) -> dict[str, Any] | None:
        targets = lore_ids.get(str(row["id"]), [])
        missing = [t for t in targets if t not in public]
        if missing:
            raise ValueError(f"{row['id']}: the lore's reveals point at no row {missing}")
        return public[targets[0]] if targets else None

    for row in story["pairs"]:
        shown = reveal(row)
        same = (
            shown if shown is not None and "relation" in shown else by_triple.get(key.triple(row))
        )
        if same is not None:
            _merge(same, doc, row)
        else:
            _add_pair(key, doc, row, shown["knowers"] if shown is not None else row["knowers"])
    for section in ("events", "threads"):
        for row in story[section]:
            shown = reveal(row)
            if shown is None and section == "threads" and row.get("same_as"):
                shown = public.get(str(row["same_as"]))
            if shown is not None and "relation" not in shown and section in _kind_of(shown):
                _merge(shown, doc, row)
                continue
            knowers = shown["knowers"] if shown is not None else row["knowers"]
            if section == "events":
                key.key.setdefault("events", []).append(_event(row, doc, knowers))
            else:
                kept = {k: v for k, v in row.items() if k != "same_as"}
                key.key.setdefault("threads", []).append({**kept, "doc": doc, "knowers": knowers})
    have = {
        (key.name(lang["character"]), lang["language"]) for lang in key.key.get("languages", [])
    }
    for lang in story.get("languages", ()):
        if (key.name(lang["character"]), lang["language"]) not in have:
            key.key.setdefault("languages", []).append(
                {
                    **lang,
                    "character": key.name(lang["character"]),
                    "doc": doc,
                    "knowers": story["speaker"],
                }
            )
    key.key.setdefault("must_not_find", []).extend(
        f"{m} ({doc})" for m in story.get("must_not_find", ())
    )


def _kind_of(row: Mapping[str, Any]) -> str:
    """Which section a public row is in: a thread has a kind of story, an event says what."""
    return "threads" if "kind" in row and "name" in row else "events"


def _revealed_by(lore: Mapping[str, Any]) -> dict[str, list[str]]:
    """The lore's `reveals` turned round: a backstory row id -> the lore ids that state it."""
    out: dict[str, list[str]] = {}
    for by, ids in lore.get("reveals", {}).items():
        for i in ids:
            out.setdefault(str(i), []).append(str(by))
    return out


def _with_knowers(rows: Iterable[dict[str, Any]], lore_knowers: Mapping[str, Any]) -> None:
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
        "languages": [
            {**lang, "doc": "lore", "knowers": PARTY} for lang in lore.get("languages", [])
        ],
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
    lore_knowers = {str(p["id"]): p["knowers"] for p in lore["pairs"]}
    _with_knowers(master["pairs"], lore_knowers)
    node_knowers = {str(n["name"]): n["knowers"] for n in lore["nodes"]}
    for node in master["nodes"]:
        node.setdefault("knowers", node_knowers.get(str(node["name"]), PARTY))
    event_knowers = {f"L-{e['id']}": e["knowers"] for e in lore["events"]}
    for event in master.get("events", []):
        event.setdefault("knowers", event_knowers.get(str(event["id"]), PARTY))
    for thread in master.get("threads", []):
        thread.setdefault("knowers", lore.get("thread_knowers", PARTY))
    for lang in master.get("languages", []):
        lang.setdefault("knowers", PARTY)  # (the lore's backgrounds and the session: the party's)
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
