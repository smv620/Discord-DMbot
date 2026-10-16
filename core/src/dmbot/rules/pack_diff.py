"""What changed between two versions of a homebrew pack (#1527 part C), and what a campaign gets
when it takes some of the changes. Pure: no database, no Discord.

Entries are matched by name (case and spacing ignored, like the campaign's own homebrew): a name
means the same entry. A change is one of three: an entry added, changed (its kind, summary or
text) or removed.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from dmbot.rules.homebrew import name_key

Kind = Literal["added", "changed", "removed"]
Entry = dict[str, Any]  # {"kind", "name", "summary", "text", "known"}


@dataclass(frozen=True, slots=True)
class Change:
    kind: Kind
    key: str  # the entry's name_key
    before: Entry | None
    after: Entry | None

    @property
    def entry(self) -> Entry:
        """The entry as it matters to a reader: the new one, or the removed one."""
        entry = self.after if self.after is not None else self.before
        assert entry is not None  # a change always has one side
        return entry

    @property
    def name(self) -> str:
        return str(self.entry["name"])


def entries_of(content: Any) -> dict[str, Entry]:
    """The entries in a pack's content by name key; a row that doesn't fit is skipped."""
    out: dict[str, Entry] = {}
    items = content.get("entries") if isinstance(content, dict) else None
    if not isinstance(items, list):
        return out
    for item in items:
        try:
            name, text = item["name"], item["text"]
            if not isinstance(name, str) or not isinstance(text, str):
                continue
            if item.get("kind") == "rule":
                continue  # a pack made before rules left homebrew (#1628)
            out[name_key(name)] = {
                "kind": str(item["kind"]),
                "name": name,
                "summary": str(item["summary"]) if item.get("summary") else None,
                "text": text,
                "known": True,  # (always: nothing is hidden any more; older packs may say false)
            }
        except (KeyError, TypeError, AttributeError):
            continue
    return out


def diff(old: Any, new: Any) -> list[Change]:
    """The changes that turn `old` into `new`, by name."""
    before, after = entries_of(old), entries_of(new)
    changes: list[Change] = []
    for key in sorted(before.keys() | after.keys()):
        if key not in before:
            changes.append(Change("added", key, None, after[key]))
        elif key not in after:
            changes.append(Change("removed", key, before[key], None))
        elif before[key] != after[key]:
            changes.append(Change("changed", key, before[key], after[key]))
    return changes


def apply(old: Any, changes: Sequence[Change], accepted: Collection[str] | None = None) -> Any:
    """`old`'s content with the changes whose keys are in `accepted` applied (all of them when
    `accepted` is None). Returns new content; `old` is not changed."""
    entries = entries_of(old)
    for change in changes:
        if accepted is not None and change.key not in accepted:
            continue
        if change.kind == "removed":
            entries.pop(change.key, None)
        else:
            entries[change.key] = dict(change.entry)
    return {"entries": [entries[k] for k in sorted(entries)]}


def describe(change: Change, width: int = 140) -> str:
    """One plain line for the update note or the Pick screen."""
    verb = {"added": "New", "changed": "Changed", "removed": "Removed"}[change.kind]
    shown = change.entry.get("summary") or change.entry["text"]
    text = " ".join(str(shown).split())
    if len(text) > width:
        text = text[: width - 1] + "…"
    return f"{verb}: {change.name}, {text}"


def count_words(changes: Sequence[Change]) -> str:
    n = len(changes)
    return f"{n} entr" + ("y" if n == 1 else "ies")
