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

It runs on the public sample alone (`graph_key_check` refuses any other campaign). By default
each line is a key entry's name, its stage and a fixed phrase: it names only key entries, never a
name the run made that the key does not list, so it may be posted on #3545. The careful AI's own
reasons come from the decision record, which stays on the server (CLAUDE.md), so they are added
only with `reasons=True` (`--reasons`), for reading there, cut to `REASON_MAX`: a reason is the
AI's free words and may name anything.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
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
    """The newest decision for each item (an entry id) across the run's record files (by its
    time; a later line wins a tie). A line that is not a decision is passed over."""
    out: dict[str, Mapping[str, Any]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not (isinstance(row, dict) and row.get("item")):
                continue
            had = out.get(str(row["item"]))
            if had is None or int(row.get("at") or 0) >= int(had.get("at") or 0):
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
    *,
    reasons: bool = False,
) -> list[Why]:
    """One `Why` for each key entry (of `docs`) that the run did not find and keep: see the
    module's note for the stages. An entry the key marks `by_design` (never expected) is left
    out, as the score leaves it out, and so is one the story never names (`unnamed`: the unnamed
    checks score those by description). `reasons`: add the careful AI's own words (server only)."""
    tables = _tables(backup)
    decided = decisions or {}
    wanted = {"lore", "session"} if docs == "both" else {docs}
    every_node = list(key.get("nodes", ()))
    nodes = [
        n
        for n in every_node
        if wanted & set(n.get("docs", ["lore", "session"]))
        and not n.get("by_design")
        and not n.get("unnamed")
    ]
    entities = {str(e["id"]): e for e in tables.get("entity", ())}
    # every entry, any status, by its name and its other names that stand; a turned-down other
    # name apart (it is no way to find an entry, as `graph_key_check` reads it, but it says why)
    by_key: dict[str, set[str]] = {}
    turned_down: dict[str, set[str]] = {}
    for e in entities.values():
        for k in _keys(str(e.get("name", ""))):
            by_key.setdefault(k, set()).add(str(e["id"]))
    for a in tables.get("alias", ()):
        if str(a.get("entity_id")) not in entities:
            continue
        index = turned_down if a.get("status") == "rejected" else by_key
        for k in _keys(str(a.get("text", ""))) | ({str(a["key"])} if a.get("key") else set()):
            index.setdefault(k, set()).add(str(a["entity_id"]))

    def spellings(node: Mapping[str, Any]) -> set[str]:
        return {k for s in (node["name"], *node.get("aliases", ())) for k in _keys(str(s))}

    key_name_of: dict[str, str] = {}  # entry id -> the key entry it is (any document)
    for n in every_node:
        if not n.get("unnamed"):
            for k in spellings(n):
                for i in by_key.get(k, set()):
                    key_name_of.setdefault(i, str(n["name"]))

    def end_of(entity_id: str) -> str:
        """Where a chain of merges ends."""
        seen: set[str] = set()
        while (nxt := entities.get(entity_id, {}).get("merged_into")) and entity_id not in seen:
            seen.add(entity_id)
            entity_id = str(nxt)
        return entity_id

    def said(row: Mapping[str, Any] | None) -> str:
        return f": {_short(row.get('reason'))}" if reasons and row is not None else ""

    out: list[Why] = []
    for n in nodes:
        name, kind = str(n["name"]), str(n["kind"])
        right = ENTRY_TYPES.get(kind, frozenset())
        mine = spellings(n)
        rows = [entities[i] for i in sorted({i for k in mine for i in by_key.get(k, set())})]
        alive = [
            e
            for e in rows
            if e.get("status") in ("confirmed", "proposed") and not e.get("merged_into")
        ]
        if [e for e in alive if e.get("status") == "confirmed" and str(e["type"]) in right]:
            continue  # (found and kept: nothing to explain)
        waiting = [e for e in alive if str(e["type"]) in right]
        if waiting:
            out.append(Why(name, kind, WAITING, _waiting_why(waiting, decided, said)))
            continue
        if alive:
            types = sorted({str(e["type"]) for e in alive})
            out.append(Why(name, kind, WRONG_KIND, f"kept as {', '.join(types)}"))
            continue
        merged = [e for e in rows if e.get("merged_into")]
        if merged:
            into = end_of(str(merged[0]["id"]))
            other = key_name_of.get(into)
            if other == name:
                where = "another entry for this same name"
            elif other:
                where = f'"{other}"'
            else:
                where = "an entry the key doesn't list"
            out.append(Why(name, kind, MERGED, f"joined into {where}"))
            continue
        rejected = [e for e in rows if e.get("status") == "rejected"]
        if rejected:
            record = [decided[str(e["id"])] for e in rejected if str(e["id"]) in decided]
            row = _latest(record)
            if row is not None and row.get("decision") == "no":
                why = "the careful AI said no" + said(row)
            elif row is not None and row.get("decision") == "yes":
                why = "the careful AI said yes; turned down later (the DM or another step)"
            else:
                why = "turned down (no decision on record: the DM or an earlier step)"
            out.append(Why(name, kind, DECIDED_NO, why))
            continue
        if any(k in turned_down for k in mine):
            out.append(Why(name, kind, DECIDED_NO, "turned down as another name of an entry"))
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


def _latest(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The newest decision of several (by its time), or None."""
    return max(rows, key=lambda r: int(r.get("at") or 0), default=None)


def _waiting_why(
    waiting: Sequence[Mapping[str, Any]],
    decided: Mapping[str, Mapping[str, Any]],
    said: Callable[[Mapping[str, Any] | None], str],
) -> str:
    row = _latest([decided[str(e["id"])] for e in waiting if str(e["id"]) in decided])
    if row is None:
        if any(e.get("role") == "player_character" for e in waiting):
            return "never asked: a player character waits for who plays it"
        if any(str(e.get("type")) == "person" for e in waiting):
            return "never asked: a person waits for the unnamed and look-alike questions"
        return "never asked: past the run's decision budget, or no decide step ran"
    answer, refused = row.get("decision"), row.get("done") is False
    if answer == "yes" and refused:
        return "the careful AI said yes, but a rule refused it" + said(row)
    if answer == "no" and refused:
        return "the careful AI said no, but a rule refused turning it down" + said(row)
    if answer == "yes":
        return "the careful AI said yes, but it stayed waiting" + said(row)
    if answer == "no":
        return "the careful AI said no, but it stayed waiting" + said(row)
    return "the careful AI was not sure" + said(row)


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
