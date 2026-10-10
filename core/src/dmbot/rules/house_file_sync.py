"""Bringing a house-rules file and DMbot's copy together (#969). No Discord.

`items_of` turns what differs (`house_file.compare`) into things the DM can accept one by
one; `apply` does the accepted ones, each checked again by the store (only the campaign's
DMs; a rule another DM changed meanwhile is skipped, never overwritten). Nothing here
changes anything by itself: the caller does it when a DM presses a button.

A new rule from the file keeps its own number if that number was never used in this
campaign (numbers are for good, and alerts cite them); otherwise it takes the next free
number and the DM is told.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.rules.house_file import Diff, FileRule

ADD, CHANGE, REMOVE = "add", "change", "remove"
SCENARIO = "From the house-rules file"
SHOWN = 300  # characters of a rule shown in a review


@dataclass(frozen=True, slots=True)
class Item:
    kind: str  # ADD, CHANGE or REMOVE
    number: int  # the file's number (ADD) or DMbot's (CHANGE, REMOVE)
    rule: str = ""  # the file's words (ADD, CHANGE)
    instead: str | None = None
    was: str = ""  # DMbot's words when compared (CHANGE, REMOVE)
    was_instead: str | None = None
    version: int = 0  # the version of DMbot's rule when compared (CHANGE, REMOVE)


class Store(Protocol):
    async def add(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        rule: str,
        supersedes: str | None = None,
        *,
        scenario: str | None = None,
        session_id: str | None = None,
        wanted: int | None = None,
    ) -> HouseRule: ...

    async def edit(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        number: int,
        text: str,
        instead: str | None = None,
        *,
        unchanged_since: int | None = None,
    ) -> HouseRule: ...

    async def remove(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        number: int,
        *,
        unchanged_since: int | None = None,
    ) -> HouseRule: ...


@dataclass(slots=True)
class Applied:
    done: list[str] = field(default_factory=list)  # one plain line for each
    skipped: list[str] = field(default_factory=list)  # one plain line for each, with why


def fingerprint(rules: Sequence[FileRule]) -> str:
    """64 hex characters standing for the rules a file holds (not its title or comments),
    so "ignore until the file changes" means the rules."""
    text = "\n".join(f"{r.number}\t{r.rule}\t{r.supersedes or ''}" for r in rules)
    return hashlib.sha256(text.encode()).hexdigest()


def items_of(diff: Diff) -> list[Item]:
    """Everything the DM may accept, new rules first, then changes, then removals. A rule
    that only has another number in the file (`moved`) is not an item: DMbot keeps its
    own numbers."""
    items = [Item(ADD, r.number, r.rule, r.supersedes) for r in diff.added]
    items += [
        Item(CHANGE, mine.number, theirs.rule, theirs.supersedes, mine.rule, mine.supersedes,
             mine.version)
        for mine, theirs in diff.changed
    ]  # fmt: skip
    items += [
        Item(REMOVE, r.number, was=r.rule, was_instead=r.supersedes, version=r.version)
        for r in diff.removed
    ]
    return items


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def summary(diff: Diff, unreadable: int = 0) -> str:
    """One short line saying what differs."""
    parts = []
    if diff.added:
        parts.append(_plural(len(diff.added), "new rule", "new rules"))
    if diff.changed:
        parts.append(f"{len(diff.changed)} changed")
    if diff.removed:
        parts.append(f"{len(diff.removed)} removed")
    text = "Your house-rules file has " + ", ".join(parts) + "."
    if diff.moved:
        text += (
            f" {_plural(len(diff.moved), 'rule has', 'rules have')} another number in the "
            "file; DMbot keeps its own numbers."
        )
    if unreadable:
        text += f" {_plural(unreadable, 'line', 'lines')} in the file couldn't be read."
    return text


def _cut(text: str) -> str:
    return text if len(text) <= SHOWN else text[: SHOWN - 1].rstrip() + "…"


def _words(rule: str, instead: str | None) -> str:
    return _cut(rule) + (f" (instead of: {_cut(instead)})" if instead else "")


def describe(item: Item) -> str:
    """One item, for a review. Plain text (the caller escapes it)."""
    if item.kind == ADD:
        return f"➕ New in the file: {item.number}. {_words(item.rule, item.instead)}"
    if item.kind == CHANGE:
        return (
            f"✏️ House rule {item.number} is different in the file.\n"
            f"DMbot has: {_words(item.was, item.was_instead)}\n"
            f"The file says: {_words(item.rule, item.instead)}"
        )
    return (
        f"🗑 House rule {item.number} isn't in the file any more: "
        f"{_words(item.was, item.was_instead)}\nAccepting removes it from DMbot."
    )


async def apply(
    store: Store, guild_id: int, campaign_id: str, user_id: int, items: Sequence[Item]
) -> Applied:
    """Do the items, one by one. One that can't be done is skipped and said, never forced:
    the store refuses it if another DM changed or removed the rule since it was compared."""
    out = Applied()
    for item in items:
        try:
            if item.kind == ADD:
                saved = await store.add(
                    guild_id, campaign_id, user_id, item.rule, item.instead,
                    scenario=SCENARIO, wanted=item.number,
                )  # fmt: skip
                if saved.number == item.number:
                    out.done.append(f"Added house rule {saved.number}.")
                else:
                    out.done.append(
                        f"Added the file's rule {item.number} as house rule {saved.number} "
                        f"(number {item.number} was used before)."
                    )
            elif item.kind == CHANGE:
                await store.edit(
                    guild_id, campaign_id, user_id, item.number, item.rule, item.instead,
                    unchanged_since=item.version,
                )  # fmt: skip
                out.done.append(f"Changed house rule {item.number}.")
            else:
                await store.remove(
                    guild_id, campaign_id, user_id, item.number, unchanged_since=item.version
                )
                out.done.append(f"Removed house rule {item.number}.")
        except HouseRuleError as exc:
            out.skipped.append(f"Rule {item.number} was left as it is: {exc}")
    return out


def result_text(applied: Applied) -> str:
    """What happened, in a few lines."""
    lines = list(applied.done[:8])
    if len(applied.done) > 8:
        lines.append(f"…and {len(applied.done) - 8} more.")
    lines += applied.skipped[:5]
    if len(applied.skipped) > 5:
        lines.append(f"…and {len(applied.skipped) - 5} more were left as they are.")
    return "\n".join(lines) or "Nothing to change."
