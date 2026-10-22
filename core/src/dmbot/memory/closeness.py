"""How near one name is to another in the campaign's web of connections (#2321).

Used to order the name dropdown on a name card ("Connect to…", "Same as…") so the names most
likely wanted come first: a name one connection away before one two away, and names with no path
last. Pure: it reads the lookup's adjacency (who is connected to whom) and nothing else, so it
needs no database and no Discord.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Set

UNREACHABLE = 1_000_000  # a name with no path sorts after every reachable one


def distances(neighbours: Mapping[str, Set[str]], start: str, *, limit: int = 6) -> dict[str, int]:
    """Steps from `start` to every entry within `limit` connections (`start` itself is 0).
    A breadth-first walk, so each entry gets its shortest distance; `limit` keeps a very large
    web cheap, since anything farther sorts as far as an unconnected name anyway."""
    seen = {start: 0}
    queue: deque[str] = deque([start])
    while queue:
        here = queue.popleft()
        if seen[here] >= limit:
            continue
        for there in neighbours.get(here, ()):
            if there not in seen:
                seen[there] = seen[here] + 1
                queue.append(there)
    return seen


def by_closeness(entries: list[tuple[str, str]], steps: Mapping[str, int]) -> list[tuple[str, str]]:
    """`(id, name)` pairs, nearest first, ties and the unconnected in alphabetical order."""
    return sorted(entries, key=lambda e: (steps.get(e[0], UNREACHABLE), e[1].casefold(), e[0]))


def alphabetical(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """`(id, name)` pairs in alphabetical order, ignoring case."""
    return sorted(entries, key=lambda e: (e[1].casefold(), e[0]))
