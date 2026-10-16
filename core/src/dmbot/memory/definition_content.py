"""What a shared rule set may carry of a relationship definition (#1456): the shape, the limits,
and the checks on content from outside. Pure (no database, no store) so the rules layer can use it.
The database parts are in `dmbot.memory.set_definitions`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dmbot.memory.ontology import ACTIVE, CORE_PREDICATES, CORE_TYPES, PredicateTerm

# What one definition holds, in a set's content. Lists and limits mirror the memory's own.
MAX_DEFINITIONS = 20  # in one set
LIST_MAX = 12
ITEM_MAX = 200
TEXT_FIELDS = ("key", "label", "inverse_label", "description")
# Only words the DM sees in the tick list travel. A relationship's examples, the cases it covers,
# its "not this" cases and its synonyms are free text the list doesn't show, and they hold the
# publisher's own names ("Mira mentors Tobin"), so none of them ever leaves (the isolation rule:
# only rules and their definitions cross, never names).
LIST_FIELDS = ("subject_types", "object_types")
CORE_TYPE_KEYS = frozenset(t.key for t in CORE_TYPES)
CORE_PREDICATE_KEYS = frozenset(p.key for p in CORE_PREDICATES)
FROM_SET_REASON = "From the shared set {name}."


def valid_definitions(raw: Any) -> list[dict[str, Any]]:
    """Definitions as a set may hold them: only the fields below, each within its limit, at
    most `MAX_DEFINITIONS`. An entry that doesn't fit is dropped (the content is untrusted: it
    may come from a backup or a share code)."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:MAX_DEFINITIONS]:
        if not isinstance(item, dict):
            continue
        clean: dict[str, Any] = {}
        try:
            for field in TEXT_FIELDS:
                value = item[field] if field != "inverse_label" else item.get(field, "")
                if not isinstance(value, str) or len(value) > 300:
                    raise ValueError(field)
                clean[field] = value.strip()
            for field in LIST_FIELDS:
                value = item.get(field, [])
                if (
                    not isinstance(value, list)
                    or len(value) > LIST_MAX
                    or not all(isinstance(v, str) and 0 < len(v) <= ITEM_MAX for v in value)
                ):
                    raise ValueError(field)
                clean[field] = [v.strip() for v in value]
            symmetric, version = item.get("symmetric", False), item.get("version", 1)
            if not isinstance(symmetric, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("symmetric")
        except (KeyError, ValueError):
            continue
        clean["symmetric"], clean["version"] = symmetric, min(version, 10_000)
        if (
            clean["key"]
            and clean["label"]
            and clean["subject_types"]
            and clean["object_types"]
            and all(k in CORE_TYPE_KEYS for k in (*clean["subject_types"], *clean["object_types"]))
            and not any(d["key"] == clean["key"] for d in out)
        ):
            out.append(clean)
    return out


def definition_of(term: PredicateTerm) -> dict[str, Any]:
    """A campaign's own relationship as a set carries it: its name, what it reads as from each
    side, what it means and the kinds of thing it connects, all shown to the DM before sharing.
    Built field by field: its examples, covers, "not this" cases and synonyms (free text with the
    publisher's names), what a fact used it for, who made it and when never travel; neither do
    its parent or conflicts, which point at other terms of the publisher's."""
    return {
        "key": term.key,
        "label": term.label,
        "inverse_label": term.inverse_label,
        "description": term.description,
        "subject_types": list(term.subject_types),
        "object_types": list(term.object_types),
        "symmetric": term.symmetric,
        "version": term.version,
    }


def shareable(term: PredicateTerm) -> bool:
    """Whether a relationship may travel: the campaign's own, active, and connecting only the
    official kinds of thing (a custom kind is the publisher's alone)."""
    return (
        not term.core
        and term.status == ACTIVE
        and term.key not in CORE_PREDICATE_KEYS
        and all(k in CORE_TYPE_KEYS for k in (*term.subject_types, *term.object_types))
    )


def tick_line(definition: Mapping[str, Any]) -> str:
    """One definition as the DM sees it in the tick list: its label, how it reads from the other
    side if it says, and what it means. This is all of what travels."""
    text = " ".join(str(definition.get("description", "")).split())
    back = definition.get("inverse_label")
    reads = f" (back: {back})" if back else ""
    return f"{definition['label']}{reads}: {text}"[:140]
