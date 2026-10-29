"""What changed between two versions of a shared house-rules set (#1191), and what a
campaign gets when it takes some of the changes. Pure: no database, no Discord.

Rules are matched by number: a set's numbers are its publisher's own and never reused (the
house-rules module's note), so a number always means the same rule. A change is one of
three: a rule added, changed (its text or its "instead of") or removed.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from dmbot.memory.definition_content import valid_definitions

Kind = Literal["added", "changed", "removed"]
Rule = dict[str, Any]  # {"number": int, "rule": str, "instead": str | None}


@dataclass(frozen=True, slots=True)
class Change:
    kind: Kind
    number: int
    before: Rule | None
    after: Rule | None

    @property
    def rule(self) -> Rule:
        """The rule as it matters to a reader: the new one, or the removed one."""
        rule = self.after if self.after is not None else self.before
        assert rule is not None  # a change always has one side
        return rule


@dataclass(frozen=True, slots=True)
class DefChange:
    """A relationship definition a set carries (#1456), added, changed or taken away. Matched by
    key; the follower picks them one at a time, like rules."""

    kind: Kind
    key: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None

    @property
    def label(self) -> str:
        item = self.after if self.after is not None else self.before
        assert item is not None
        return str(item["label"])


def definitions_of(content: Any) -> dict[str, dict[str, Any]]:
    """The relationship definitions in a set's content by key (none for a set made before they
    could travel)."""
    items = content.get("definitions") if isinstance(content, dict) else None
    return {d["key"]: d for d in valid_definitions(items)}


def diff_definitions(old: Any, new: Any) -> list[DefChange]:
    """What changed in the definitions a set carries, by key."""
    before, after = definitions_of(old), definitions_of(new)
    out: list[DefChange] = []
    for key in sorted(before.keys() | after.keys()):
        if key not in before:
            out.append(DefChange("added", key, None, after[key]))
        elif key not in after:
            out.append(DefChange("removed", key, before[key], None))
        elif before[key] != after[key]:
            out.append(DefChange("changed", key, before[key], after[key]))
    return out


def describe_definition(change: DefChange) -> str:
    """One plain line for a relationship the set added, changed or took away."""
    if change.kind == "added":
        return f"New relationship: {change.label}"
    if change.kind == "changed":
        return f"Changed relationship: {change.label} (meaning changed)"
    return f"Removed from the set: {change.label}"


def rules_of(content: Any) -> dict[int, Rule]:
    """The rules in a set's content by number; a row that doesn't fit is skipped."""
    out: dict[int, Rule] = {}
    items = content.get("rules") if isinstance(content, dict) else None
    if not isinstance(items, list):
        return out
    for item in items:
        try:
            if not isinstance(item["rule"], str):
                continue
            number = int(item["number"])
            out[number] = {
                "number": number,
                "rule": str(item["rule"]),
                "instead": str(item["instead"]) if item.get("instead") else None,
            }
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def diff(old: Any, new: Any) -> list[Change]:
    """The changes that turn `old` into `new`, lowest rule number first."""
    before, after = rules_of(old), rules_of(new)
    changes: list[Change] = []
    for number in sorted(before.keys() | after.keys()):
        if number not in before:
            changes.append(Change("added", number, None, after[number]))
        elif number not in after:
            changes.append(Change("removed", number, before[number], None))
        elif before[number] != after[number]:
            changes.append(Change("changed", number, before[number], after[number]))
    return changes


def apply(
    old: Any,
    changes: Sequence[Change],
    accepted: Collection[int] | None = None,
    newer: Any = None,
    def_changes: Sequence[DefChange] = (),
    accepted_defs: Collection[str] | None = None,
) -> Any:
    """`old`'s content with the changes whose numbers are in `accepted` applied (all of
    them when `accepted` is None). Returns new content; `old` is not changed. `newer`: the
    content the changes came from; when given, its rules document link is carried over (the
    link is not a rule, so `changes` can't hold it). The relationship definitions follow the same
    way: `def_changes` whose keys are in `accepted_defs` (all of them when None) are applied."""
    rules = rules_of(old)
    defs = definitions_of(old)
    for dchange in def_changes:
        if accepted_defs is not None and dchange.key not in accepted_defs:
            continue
        if dchange.kind == "removed":
            defs.pop(dchange.key, None)
        elif dchange.after is not None:
            defs[dchange.key] = dict(dchange.after)
    for change in changes:
        if accepted is not None and change.number not in accepted:
            continue
        if change.kind == "removed":
            rules.pop(change.number, None)
        else:
            rules[change.number] = dict(change.rule)
    source = newer if isinstance(newer, dict) else old
    link = source.get("link") if isinstance(source, dict) else None
    return {
        "rules": [rules[n] for n in sorted(rules)],
        "link": link,
        "definitions": [defs[k] for k in sorted(defs)],
    }


def describe(change: Change, width: int = 140) -> str:
    """One plain line for the update note or a Cherry-pick screen."""
    text = " ".join(str(change.rule["rule"]).split())
    if len(text) > width:
        text = text[: width - 1] + "…"
    verb = {"added": "New", "changed": "Changed", "removed": "Removed"}[change.kind]
    return f"{verb}: rule {change.number}, {text}"


def count_words(changes: Sequence[Change], def_changes: Sequence[DefChange] = ()) -> str:
    """ "1 rule" / "3 rules": how many rules the update touches, and "and 2 relationships" when the
    set's relationship definitions changed too."""
    n = len(changes)
    text = f"{n} rule" + ("" if n == 1 else "s")
    if def_changes:
        m = len(def_changes)
        text = (
            f"{m} relationship" + ("" if m == 1 else "s")
            if n == 0
            else f"{text} and {m} relationship" + ("" if m == 1 else "s")
        )
    return text
