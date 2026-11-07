"""Who in the story is not linked to the party (#3740): the graph clean-up's one rule. Pure.

The owner (2026-11-07): "Anyone who interacts with one of the player characters must be preserved
and by extension, anyone they interact with." So an entry is **isolated** when no chain of links
reaches it from a player character or the party. Isolated entries are grouped by the links among
themselves: a pair or a chain linked only to each other is one group (the drunk and the loud
sailors, #3736), and an entry with no link at all is a group of one.

The rule, as Supervisor ruled on #3740:
- Only characters and groups are walked and listed. A place or an item is no interaction: everyone
  who lives in one town is not tied to the party by it, and a lore upload's many places would bury
  the people.
- A link joins whether it is kept or still waiting (DMbot heard them together; the DM answers the
  waiting one on its own list), ended or not, secret or not. A rejected link never counts, nor
  does the story plan's "led to".
- A rejected or merged entry never counts. A waiting named entry joins a chain but is not listed
  (it is the names-to-check list's); a person the story never named is listed whether it is
  waiting or kept.
- A campaign with no kept player character has no seeds: nothing is isolated, and the page is
  empty. The party is a seed beside them, never instead of them.

The website's page (`web/graph_cleanup.py`), the count for the Discord DM screen
(`MemoryStore.isolated_count`) and the scene sweep all run this function on the same rows, so they
never disagree. Ids only; nothing is logged.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

WALKED = frozenset({"character", "faction"})  # the kinds that interact (Supervisor, #3740)
NOT_A_TIE = frozenset({"led_to"})  # the story plan's order, not an interaction
LIVE = frozenset({"proposed", "confirmed"})


@dataclass(frozen=True, slots=True)
class Entry:
    """An entry as the rule needs it."""

    id: str
    type: str
    status: str
    unnamed: bool = False
    player_character: bool = False
    party: bool = False


@dataclass(frozen=True, slots=True)
class Link:
    """A link as the rule needs it: `a` and `b` are its subject and object, walked both ways."""

    id: str
    a: str
    b: str
    predicate: str
    status: str


def isolated_groups(entries: Iterable[Entry], links: Iterable[Link]) -> list[list[str]]:
    """The isolated groups, each its listed entries' ids (sorted), largest group first, then by
    the first id, so the same graph always gives the same order. Empty with no player character."""
    nodes: Mapping[str, Entry] = {e.id: e for e in entries if e.type in WALKED and e.status in LIVE}
    pcs = [e.id for e in nodes.values() if e.player_character and e.status == "confirmed"]
    if not pcs:
        return []  # (a party left behind after its characters went is no anchor on its own)
    seeds = pcs + [e.id for e in nodes.values() if e.party]
    near: dict[str, set[str]] = {i: set() for i in nodes}
    for link in links:
        if link.status == "rejected" or link.predicate in NOT_A_TIE:
            continue
        if link.a in nodes and link.b in nodes and link.a != link.b:
            near[link.a].add(link.b)
            near[link.b].add(link.a)
    reached = _walk(seeds, near)
    left = [i for i in nodes if i not in reached]
    groups: list[list[str]] = []
    seen: set[str] = set()
    for start in sorted(left):
        if start in seen:
            continue
        group = _walk([start], near)
        seen |= group
        listed = sorted(i for i in group if _listed(nodes[i]))
        if listed:
            groups.append(listed)
    groups.sort(key=lambda g: (-len(g), g[0]))
    return groups


def _listed(e: Entry) -> bool:
    """Shown on the clean-up page: a kept entry, or a person or group the story never named."""
    return e.status == "confirmed" or e.unnamed


def _walk(start: Iterable[str], near: Mapping[str, set[str]]) -> set[str]:
    """Every entry reachable from `start` (breadth-first, so a long chain never recurses)."""
    seen = set(start)
    queue = deque(seen)
    while queue:
        for other in near[queue.popleft()]:
            if other not in seen:
                seen.add(other)
                queue.append(other)
    return seen


def isolated_ids(entries: Iterable[Entry], links: Iterable[Link]) -> set[str]:
    """Every listed isolated entry's id, for the count and the scene sweep."""
    return {i for group in isolated_groups(entries, links) for i in group}
