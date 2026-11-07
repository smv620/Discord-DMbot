"""What follows from the family links a campaign has stated (#2959, item 2).

Pure: no database, no AI.

Two rules, each built from exactly two stated links, so a derived link is never a source for
another:

1. Parent of a sibling: "X parent A" and "A sibling B" give "X parent B".
2. Grandparent: "G parent P" and "P parent A" give "G grandparent A".

Nothing else is derived. No aunt, uncle, niece or nephew: kin.py has no neutral word for a parent's
sibling, and DMbot never guesses a gender. No cousin: it needs three stated links. No sibling from a
shared parent: half-siblings share one parent, so "sibling" is only ever stated, never derived.

Rule 1 can be wrong for half-siblings: A and B may share only the other parent, so "X parent B" is
not true. The owner's example (a mother, her son, his sister: the sister is the mother's daughter)
is the rule, and a DM who deletes a derived link keeps it deleted. The derived role is always the
neutral word from kin.py ("parent", "grandparent"), never a gendered one, so what is stored keeps
only what the stored role says.

A stated "child" is read as the reverse "parent" before any rule runs, so "A child-of X" is "X
parent A". Roles outside parent, child, sibling and grandparent/grandchild are not sources and do
not block a derived link either: only a stated parent, sibling or grandparent fact counts as
already stated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_PARENT = "parent"
_GRANDPARENT = "grandparent"
_SIBLING = "sibling"


@dataclass(frozen=True, slots=True)
class Stated:
    """A stated family link: "a_id is the <role> of b_id" (kin.py's words)."""

    link_id: str
    a_id: str
    role: str
    b_id: str


@dataclass(frozen=True, slots=True)
class Derived:
    """A link that follows from two stated links; source_ids are those two link ids."""

    a_id: str
    role: str
    b_id: str
    source_ids: tuple[str, str]


def _parent_of(link: Stated) -> tuple[str, str] | None:
    """(parent, child) for a parent or child link, read in the parent's direction."""
    if link.role == _PARENT:
        return link.a_id, link.b_id
    if link.role == "child":
        return link.b_id, link.a_id
    return None


def derive(stated: Sequence[Stated]) -> list[Derived]:
    """The links that follow from the stated ones, each with the two stated links it came from,
    sorted by (a_id, role, b_id). The same input in any order gives the same output."""
    links = sorted(stated, key=lambda s: s.link_id)  # a fixed order: the first source wins
    known: set[tuple[str, str, str]] = set()
    parents: dict[str, list[tuple[str, str]]] = {}  # child -> [(parent, link_id)]
    children: dict[str, list[tuple[str, str]]] = {}  # parent -> [(child, link_id)]
    siblings: dict[str, list[tuple[str, str]]] = {}  # name -> [(other, link_id)]

    for link in links:
        if (pc := _parent_of(link)) is not None:
            parent, child = pc
            known.add((parent, _PARENT, child))
            parents.setdefault(child, []).append((parent, link.link_id))
            children.setdefault(parent, []).append((child, link.link_id))
        elif link.role == _SIBLING:
            a, b = sorted((link.a_id, link.b_id))
            known.add((a, _SIBLING, b))
            siblings.setdefault(link.a_id, []).append((link.b_id, link.link_id))
            siblings.setdefault(link.b_id, []).append((link.a_id, link.link_id))
        elif link.role == _GRANDPARENT:
            known.add((link.a_id, _GRANDPARENT, link.b_id))
        elif link.role == "grandchild":
            known.add((link.b_id, _GRANDPARENT, link.a_id))

    found: dict[tuple[str, str, str], Derived] = {}

    def offer(a_id: str, role: str, b_id: str, sources: tuple[str, str]) -> None:
        # Never a self-link, never a link already stated, never the same link twice
        if a_id == b_id or (a_id, role, b_id) in known or (a_id, role, b_id) in found:
            return
        found[(a_id, role, b_id)] = Derived(a_id, role, b_id, sources)

    # Rule 1: X parent A and A sibling B give X parent B
    for x, a_links in sorted(children.items()):
        for a, parent_link in a_links:
            for b, sibling_link in sorted(siblings.get(a, [])):
                offer(x, _PARENT, b, (parent_link, sibling_link))

    # Rule 2: G parent P and P parent A give G grandparent A
    for p, p_children in sorted(children.items()):
        for g, g_link in sorted(parents.get(p, [])):
            for a, a_link in sorted(p_children):
                offer(g, _GRANDPARENT, a, (g_link, a_link))

    return [found[key] for key in sorted(found, key=lambda k: (k[0], k[1], k[2]))]
