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
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from dmbot.rules.conflicts import Checked
from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.rules.house_file import Diff, FileRule

log = logging.getLogger(__name__)

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
    async def list(self, guild_id: int, campaign_id: str) -> list[HouseRule]: ...

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
        source: str = "typed",
        source_name: str | None = None,
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
    """One short line saying what differs, and what pressing would do."""
    parts = []
    if diff.added:
        parts.append(f"{len(diff.added)} to add")
    if diff.changed:
        parts.append(f"{len(diff.changed)} to change")
    if diff.removed:
        parts.append(f"{len(diff.removed)} to remove")
    text = "**Your rules file and DMbot's house rules don't match:** " + ", ".join(parts) + "."
    if diff.moved:
        text += (
            f" {_plural(len(diff.moved), 'rule has', 'rules have')} another number in the "
            "file; DMbot keeps its own numbers."
        )
    if unreadable:
        one = unreadable == 1
        text += (
            f" {_plural(unreadable, 'line', 'lines')} in the file didn't look like "
            f"{'a rule' if one else 'rules'} and {'was' if one else 'were'} skipped."
        )
    return text


def _cut(text: str) -> str:
    return text if len(text) <= SHOWN else text[: SHOWN - 1].rstrip() + "…"


def _words(rule: str, instead: str | None) -> str:
    return _cut(rule) + (f" (instead of: {_cut(instead)})" if instead else "")


def describe(item: Item) -> str:
    """One item, for a review. Plain text (the caller escapes it)."""
    if item.kind == ADD:
        return f"➕ Add as a new rule: {item.number}. {_words(item.rule, item.instead)}"
    if item.kind == CHANGE:
        return (
            f"✏️ House rule {item.number} is different in the file.\n"
            f"DMbot has: {_words(item.was, item.was_instead)}\n"
            f"The file says: {_words(item.rule, item.instead)}"
        )
    return (
        f"🗑 House rule {item.number} isn't in the file any more: "
        f"{_words(item.was, item.was_instead)}\nAccepting removes it from DMbot. Skip keeps it."
    )


UNCHECKED = (
    "Couldn't check the file's rules for clashes with your other house rules. "
    "Added anyway; look them over in `/dmbot houserules`."
)


def _checked_words(item: Item) -> str:
    return item.rule + (f" (instead of: {item.instead})" if item.instead else "")


def _named(number: int, from_set: str | None) -> str:
    return f"{from_set} rule {number}" if from_set else f"rule {number}"


async def apply(
    store: Store,
    guild_id: int,
    campaign_id: str,
    user_id: int,
    items: Sequence[Item],
    check: Callable[[Sequence[str], frozenset[int]], Awaitable[Checked]] | None = None,
    *,
    source: str = "linked",
    source_name: str | None = None,
) -> Applied:
    """Do the items, one by one. One that can't be done is skipped and said, never forced:
    the store refuses it if another DM changed or removed the rule since it was compared.
    A new rule that is already there (the same words) is not added a second time, so doing
    the same offer twice, or again after a failure part way, never makes copies.
    `check` (#2284) compares the new rules, in one call, with the rules already in force: one that
    disagrees is not added (the DM adds it by hand and picks which rule wins), and the rest are."""
    out = Applied()
    present: set[tuple[str, str | None]] = set()
    if any(item.kind == ADD for item in items):
        try:
            present = {(r.rule, r.supersedes) for r in await store.list(guild_id, campaign_id)}
        except Exception:
            log.exception("Couldn't read the house rules before adding from the file")
    held: dict[tuple[str, int], str] = {}  # (kind, file rule number) -> what it disagrees with
    if check is not None:
        # New rules and changed ones, in one call. A changed rule's old words are not compared
        # with (they are being replaced), like Edit's `skip`.
        fresh = [
            i
            for i in items
            if (i.kind == ADD and (i.rule, i.instead) not in present) or i.kind == CHANGE
        ]
        changing = frozenset(i.number for i in fresh if i.kind == CHANGE)
        found = await check([_checked_words(i) for i in fresh], changing)
        for d in found.disagreements:
            at = fresh[d.new - 1]
            held.setdefault((at.kind, at.number), _named(d.other.number, d.other.from_set))
        if found.unchecked and fresh:
            out.skipped.append(UNCHECKED)
    for item in items:
        try:
            if item.kind == ADD:
                if (item.rule, item.instead) in present:
                    out.done.append(f"Rule {item.number} was already there.")
                    continue
                if (ADD, item.number) in held:
                    out.skipped.append(
                        f"Rule {item.number} in the file may clash with {held[ADD, item.number]}"
                        ", so it wasn't added. To add it and pick a winner, use **Add a house "
                        "rule** in `/dmbot houserules`."
                    )
                    continue
                saved = await store.add(
                    guild_id, campaign_id, user_id, item.rule, item.instead,
                    scenario=SCENARIO, wanted=item.number,
                    source=source, source_name=source_name,
                )  # fmt: skip
                present.add((item.rule, item.instead))
                if saved.number == item.number:
                    out.done.append(f"Added house rule {saved.number}.")
                else:
                    out.done.append(
                        f"Added the file's rule {item.number} as house rule {saved.number} "
                        "(a number is never used twice, so old alerts stay right)."
                    )
            elif item.kind == CHANGE:
                if (CHANGE, item.number) in held:
                    out.skipped.append(
                        f"Rule {item.number} in the file now says something that may clash with "
                        f"{held[CHANGE, item.number]}, so it wasn't changed. To change it and pick "
                        "a winner, use **Edit** in `/dmbot houserules`."
                    )
                    continue
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
        except Exception:
            log.exception("Couldn't apply a house rule from the file")
            out.skipped.append(
                f"Rule {item.number} couldn't be done just now; it was left as it is."
            )
    return out


def result_text(applied: Applied) -> str:
    """What happened: a count first, then the lines."""
    if not applied.done and not applied.skipped:
        return "Nothing to change."
    head = f"Done: {len(applied.done)} changed." if applied.done else "Nothing was changed."
    if applied.skipped:
        head += f" {len(applied.skipped)} left alone."
    lines = [head, *applied.done[:8]]
    if len(applied.done) > 8:
        lines.append(f"…and {len(applied.done) - 8} more.")
    lines += applied.skipped[:5]
    if len(applied.skipped) > 5:
        lines.append(f"…and {len(applied.skipped) - 5} more were left as they are.")
    return "\n".join(lines)
