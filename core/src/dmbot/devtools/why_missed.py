"""Why each answer-key name a sample run did not end up with is missing (#5055 slice A, the owner's
question of 2026-11-22: "which names are missed and why, and why a name under consideration was
not added"). Pure: it reads a campaign backup, the key and the sample run's decision record, never
the database, Discord or an AI.

For every key entry that is not found and kept, one line: the stage it stopped at and why.
- **waiting**: stored as a suggestion, never kept. The careful AI was not sure (its reason), or it
  was never asked (past the run's budget, or a kind the decide step leaves alone).
- **decided no**: stored, then turned down, with the careful AI's reason when the record has it.
- **merged**: joined into another entry (named only when that entry is in the key).
- **wrong kind**: kept under another kind of thing.
- **not stored**: the reader never made it. Which step dropped it, and why, is the reader trace's
  (#5055 slice B); until then this says only that.

It runs on the public sample alone (`graph_key_check` refuses any other campaign), and it names
only key entries: a name the run made that the key does not list is never printed. The careful
AI's reasons are about the invented sample, cut to `REASON_MAX`.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dmbot.devtools.graph_key_check import ENTRY_TYPES, _plain, _tables
from dmbot.memory.models import name_key

REASON_MAX = 160  # one table row; the record keeps the whole reason on the server

WAITING, DECIDED_NO, MERGED, WRONG_KIND, NOT_STORED = (
    "waiting",
    "decided no",
    "merged",
    "wrong kind",
    "not stored",
)
STAGES = (NOT_STORED, DECIDED_NO, MERGED, WRONG_KIND, WAITING)  # the report's order


@dataclass(frozen=True, slots=True)
class Why:
    name: str  # the key's own name (public sample words)
    kind: str  # the key's kind
    stage: str  # one of STAGES
    reason: str  # a fixed phrase, or the careful AI's reason after it


def read_decisions(paths: Iterable[Path]) -> dict[str, Mapping[str, Any]]:
    """The last decision for each item (an entry id) across the run's record files, in order."""
    out: dict[str, Mapping[str, Any]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("item"):
                out[str(row["item"])] = row
    return out


def _keys(text: str) -> set[str]:
    """The two ways a name is looked up: as written, and without a leading article."""
    return {k for k in (name_key(text), _plain(text)) if k}


def _short(reason: object) -> str:
    text = " ".join(str(reason or "").split())
    return text if len(text) <= REASON_MAX else text[: REASON_MAX - 1].rstrip() + "…"


def explain(
    backup: object,
    key: Mapping[str, Any],
    docs: str = "both",
    decisions: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[Why]:
    """One `Why` for each key entry (of `docs`) that the run did not find and keep: see the
    module's note for the stages. An entry the key marks `by_design` (never expected) is left
    out, as the score leaves it out; a described ("unnamed") key entry is matched by its
    description only, as `graph_key_check` does."""
    tables = _tables(backup)
    decided = decisions or {}
    wanted = {"lore", "session"} if docs == "both" else {docs}
    nodes = [
        n
        for n in key.get("nodes", ())
        if wanted & set(n.get("docs", ["lore", "session"])) and not n.get("by_design")
    ]
    entities = {str(e["id"]): e for e in tables.get("entity", ())}
    by_key: dict[str, set[str]] = {}  # every entry, any status, by its name and other names
    for e in entities.values():
        for k in _keys(str(e.get("name", ""))):
            by_key.setdefault(k, set()).add(str(e["id"]))
    for a in tables.get("alias", ()):
        if str(a.get("entity_id")) in entities:
            for k in _keys(str(a.get("text", ""))) | ({str(a["key"])} if a.get("key") else set()):
                by_key.setdefault(k, set()).add(str(a["entity_id"]))
    key_name_of: dict[str, str] = {}  # entry id -> the key entry it is, for "merged into"
    for n in nodes:
        for spelling in (n["name"], *n.get("aliases", ())):
            for i in by_key.get(name_key(spelling), set()) | by_key.get(_plain(spelling), set()):
                key_name_of.setdefault(i, n["name"])

    def ids_of(node: Mapping[str, Any]) -> set[str]:
        if node.get("unnamed"):
            return set()  # (a description is matched by `graph_key_check`, not here)
        out: set[str] = set()
        for spelling in (node["name"], *node.get("aliases", ())):
            for k in _keys(str(spelling)):
                out |= by_key.get(k, set())
        return out

    out: list[Why] = []
    for n in nodes:
        if n.get("unnamed"):
            continue  # (a person the story never names: the unnamed checks score these)
        name, kind = str(n["name"]), str(n["kind"])
        right = ENTRY_TYPES.get(kind, frozenset())
        rows = [entities[i] for i in sorted(ids_of(n))]
        live = [e for e in rows if e.get("status") in ("confirmed", "proposed")]
        alive = [e for e in live if not e.get("merged_into")]
        kept = [e for e in alive if e.get("status") == "confirmed" and str(e["type"]) in right]
        if kept:
            continue  # (found and kept: nothing to explain)
        waiting = [e for e in alive if str(e["type"]) in right]
        if waiting:
            out.append(Why(name, kind, WAITING, _waiting_why(waiting, decided)))
            continue
        if alive:
            types = sorted({str(e["type"]) for e in alive})
            out.append(Why(name, kind, WRONG_KIND, f"kept as {', '.join(types)}"))
            continue
        merged = [e for e in rows if e.get("merged_into")]
        if merged:
            into = str(merged[0]["merged_into"])
            other = key_name_of.get(into)
            where = f'"{other}"' if other and other != name else "an entry the key doesn't list"
            out.append(Why(name, kind, MERGED, f"joined into {where}"))
            continue
        turned_down = [e for e in rows if e.get("status") == "rejected"]
        if turned_down:
            row = next(
                (decided[str(e["id"])] for e in turned_down if str(e["id"]) in decided), None
            )
            if row is not None and row.get("decision") == "no":
                why = f"the careful AI said no: {_short(row.get('reason'))}"
            else:
                why = "turned down (no decision on record: the DM or an earlier step)"
            out.append(Why(name, kind, DECIDED_NO, why))
            continue
        out.append(
            Why(
                name,
                kind,
                NOT_STORED,
                "never stored (which reader step dropped it, and why, is #5055 slice B)",
            )
        )
    return out


def _waiting_why(
    waiting: Sequence[Mapping[str, Any]], decided: Mapping[str, Mapping[str, Any]]
) -> str:
    rows = [decided[str(e["id"])] for e in waiting if str(e["id"]) in decided]
    if not rows:
        if any(e.get("role") == "player_character" for e in waiting):
            return "never asked: a player character waits for who plays it"
        if any(str(e.get("type")) == "person" for e in waiting):
            return "never asked: a person waits for the unnamed and look-alike questions"
        return "never asked: past the run's decision budget, or no decide step ran"
    row = rows[-1]
    if row.get("decision") == "yes" and row.get("done") is False:
        return f"the careful AI said yes, but a rule refused it: {_short(row.get('reason'))}"
    if row.get("decision") == "yes":
        return f"the careful AI said yes, but it stayed waiting: {_short(row.get('reason'))}"
    return f"the careful AI was not sure: {_short(row.get('reason'))}"


def tally(whys: Iterable[Why]) -> Counter[str]:
    """How many missed names each stage holds."""
    return Counter(w.stage for w in whys)


def render(whys: Sequence[Why]) -> str:
    """The report: the tally by stage, then one line per name, by stage then name."""
    if not whys:
        return "Why missed: every key name was found and kept.\n"
    counts = tally(whys)
    lines = ["Why missed: " + ", ".join(f"{s} {counts[s]}" for s in STAGES if counts[s])]
    order = {s: i for i, s in enumerate(STAGES)}
    for w in sorted(whys, key=lambda w: (order[w.stage], w.kind, w.name)):
        lines.append(f"- {w.name} ({w.kind}): {w.stage}: {w.reason}")
    return "\n".join(lines) + "\n"


__all__ = [
    "DECIDED_NO",
    "MERGED",
    "NOT_STORED",
    "REASON_MAX",
    "STAGES",
    "WAITING",
    "WRONG_KIND",
    "Why",
    "explain",
    "read_decisions",
    "render",
    "tally",
]
