"""What the sample's answer key says about one decision the careful model made (#3900). Pure.

Supervisor's ruling on #3900: every yes or no the model gives on the sample is scored against the
key, so the agreement rate per kind is measured before anyone proposes letting the AI decide for a
real DM. The key only knows the public sample, so this is only ever used by `sample_run`.

The key says "yes" (it holds this name, or a tie between these two), "no" (a name it lists as one
that must never be found, or two people it keeps apart) or nothing ("not in the key"). Against the
model's yes or no that gives right yes, right no, wrong yes, wrong no, or not in the key. A yes on
two people the key keeps apart is a wrong merge, reported in its own row. Names are matched as
`graph_key_check` matches them: `name_key` with no leading article, and a key entry marked
`unnamed` only by an unnamed person's description (a description confirmed as a name is no find).

The key scores a tie by its two ends, not its kind, and holds no "no" for two of its entries it
simply doesn't tie, so the rates for connections and things said are upper bounds, and say so.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from dmbot.devtools.graph_key_check import _OLD_SUFFIX, _plain
from dmbot.memory.models import name_key

YES, NO, NOT_SURE = "yes", "no", "not sure"
RIGHT_YES, RIGHT_NO, WRONG_YES, WRONG_NO, NOT_IN_KEY = (
    "right yes", "right no", "wrong yes", "wrong no", "not in the key",
)  # fmt: skip
VERDICTS = (RIGHT_YES, RIGHT_NO, WRONG_YES, WRONG_NO, NOT_IN_KEY)
UNNAMED_PREFIX = "unnamed - "


def verdict(decision: str, key_says: str | None) -> str | None:
    """How the model's yes or no stands against the key; None for "not sure" (nothing decided)."""
    if decision not in (YES, NO):
        return None
    if key_says is None:
        return NOT_IN_KEY
    if decision == YES:
        return RIGHT_YES if key_says == YES else WRONG_YES
    return RIGHT_NO if key_says == NO else WRONG_NO


class Key:
    """The answer key, read once: which key entry each spelling means, which pairs it holds, and
    the names it says must never be found."""

    def __init__(self, key: Mapping[str, Any]) -> None:
        self._node: dict[str, str] = {}  # a named entry's spellings -> its own name
        self._described: dict[str, str] = {}  # an unnamed entry's descriptions -> its own name
        for node in key.get("nodes", ()):
            own = str(node["name"])
            into = self._described if node.get("unnamed") else self._node
            for spelling in (own, *node.get("aliases", ())):
                into.setdefault(_plain(str(spelling)), own)
        self._pairs = {
            frozenset((str(p["a"]), str(p["b"])))
            for p in key.get("pairs", ())
            if p.get("a") and p.get("b")
        }
        self._never = {name_key(str(n)) for n in key.get("must_not_names", ())}

    def entry(self, name: str) -> str | None:
        """The key entry a name means, or None. An unnamed person's stored label ("unnamed - the
        guard at the gate") is read as its description, and only it can mean an unnamed entry."""
        if name.startswith(UNNAMED_PREFIX):
            return self._described.get(_plain(_OLD_SUFFIX.sub("", name[len(UNNAMED_PREFIX) :])))
        return self._node.get(_plain(name))

    def name_says(self, name: str) -> str | None:
        if self.entry(name) is not None:
            return YES
        return NO if name_key(name) in self._never else None

    def tie_says(self, a: str, b: str) -> str | None:
        """Yes when the key holds a tie between these two (of any kind: the kind is the reader's
        to word, and is scored by `graph_key_check`); "no" when either end is a must-not name."""
        if name_key(a) in self._never or name_key(b) in self._never:  # (as the key lists them)
            return NO
        ea, eb = self.entry(a), self.entry(b)
        if ea is not None and eb is not None and ea != eb and frozenset((ea, eb)) in self._pairs:
            return YES
        return None

    def same_says(self, unnamed: str, named: str) -> str | None:
        """Yes when both mean the same key entry; "no" when they mean two the key keeps apart (a
        yes on these is a wrong merge); None when either is not in the key."""
        ea, eb = self.entry(unnamed), self.entry(named)
        if ea is None or eb is None:
            return None
        return YES if ea == eb else NO


@dataclass
class Agreement:
    """The verdicts by kind of thing, and the wrong merges counted apart."""

    by_kind: dict[str, Counter[str]] = field(default_factory=dict)
    wrong_merges: int = 0

    def add(self, kind: str, decision: str, key_says: str | None, *, merge: bool = False) -> None:
        got = verdict(decision, key_says)
        if got is None:
            return
        self.by_kind.setdefault(kind, Counter())[got] += 1
        if merge and got == WRONG_YES:
            self.wrong_merges += 1

    def lines(self, kinds: Iterable[str], upper: Iterable[str] = ()) -> list[str]:
        """One line per kind: the verdicts, and the share the key agreed with among those it
        knows ("not in the key" is left out of the rate). `upper`: kinds whose rate is an upper
        bound (the key can't say no to a tie it simply lacks), marked so."""
        out = []
        bounded = set(upper)
        for kind in kinds:
            c = self.by_kind.get(kind, Counter())
            known = c[RIGHT_YES] + c[RIGHT_NO] + c[WRONG_YES] + c[WRONG_NO]
            rate = f"{round(100 * (c[RIGHT_YES] + c[RIGHT_NO]) / known)}%" if known else "n/a"
            rate += " (at most)" if kind in bounded and known else ""
            parts = ", ".join(f"{v} {c[v]}" for v in VERDICTS)
            out.append(f"agreement with the key, {kind}: {rate} ({parts})")
        out.append(f"merged two the key keeps apart: {self.wrong_merges}")
        return out
