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


def rules_of(content: Any) -> dict[int, Rule]:
    """The rules in a set's content by number; a row that doesn't fit is skipped."""
    out: dict[int, Rule] = {}
    items = content.get("rules") if isinstance(content, dict) else None
    if not isinstance(items, list):
        return out
    for item in items:
        try:
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


def apply(old: Any, changes: Sequence[Change], accepted: Collection[int] | None = None) -> Any:
    """`old`'s content with the changes whose numbers are in `accepted` applied (all of
    them when `accepted` is None). Returns new content; `old` is not changed."""
    rules = rules_of(old)
    for change in changes:
        if accepted is not None and change.number not in accepted:
            continue
        if change.kind == "removed":
            rules.pop(change.number, None)
        else:
            rules[change.number] = dict(change.rule)
    link = old.get("link") if isinstance(old, dict) else None
    return {"rules": [rules[n] for n in sorted(rules)], "link": link}


def describe(change: Change, width: int = 140) -> str:
    """One plain line for the update note or a Cherry-pick screen."""
    text = " ".join(str(change.rule["rule"]).split())
    if len(text) > width:
        text = text[: width - 1] + "…"
    verb = {"added": "New", "changed": "Changed", "removed": "Removed"}[change.kind]
    return f"{verb}: rule {change.number}, {text}"


def count_words(changes: Sequence[Change]) -> str:
    """ "1 rule" / "3 rules": how many rules the update touches."""
    n = len(changes)
    return f"{n} rule" + ("" if n == 1 else "s")
