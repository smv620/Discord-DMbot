"""What follows from the family links a campaign has stated (#2959, item 2): a pure function, with
no database and no AI. The lead stores the result and wires it in later.

Only two rules, each from exactly two stated links. A derived link is never a source for another:

- **Parent of a sibling.** "X is the parent of A" and "A is the sibling of B" give "X is the parent
  of B". This is the owner's example: a mother has a son, the son has a sister, so the sister is the
  mother's child. The derived word is always the neutral "parent", never a gendered one.
- **Grandparent.** "G is the parent of P" and "P is the parent of A" give
  "G is the grandparent of A".

Half-siblings: the parent-of-a-sibling rule can be wrong when two siblings share only one parent. It
is kept on purpose: a derived "parent" only comes from a stated parent and a stated sibling, never
from a shared parent alone, and the DM can delete a derived link, which then stays deleted.

Gendered words are read as the neutral word they stand for: "mother" and "father" are parents, "son"
and "daughter" are children, "sister" and "brother" are siblings. No gender is ever guessed. Roles
the rules do not use (spouse, aunt, uncle, niece, nephew, cousin, grandparent and the rest) are not
sources.

Three more rules (owner, 2026-11-01, #3238), each from exactly two stated links and still only a
suggestion the DM says yes to:

- **Sibling of a sibling.** "A is the sibling of B" and "A is the sibling of C" give "B is the
  sibling of C". Half-siblings are possible: the DM decides.
- **A parent's sibling.** "P is the parent of A" and "P is the sibling of U" give "U is the aunt
  or uncle of A" (the neutral word; "niece or nephew" is the same link read from A's side).
- **What siblings share.** "U is the aunt or uncle of A" or "G is the grandparent of A", with
  "A is the sibling of B", give the same link to B. A stated aunt, uncle, niece or nephew is read
  as the neutral "aunt or uncle" link.
Nothing is guessed that the text does not say: whose sibling an uncle is, or whose parent a
grandparent was, stays unsaid.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# Stated words that mean one of the neutral sources. A child is the reverse of a parent, so "child"
# is read as a parent link running the other way.
_PARENT_WORDS = frozenset({"parent", "mother", "father"})
_CHILD_WORDS = frozenset({"child", "son", "daughter"})
_SIBLING_WORDS = frozenset({"sibling", "sister", "brother"})
_GRANDPARENT_WORDS = frozenset({"grandparent", "grandmother", "grandfather"})
_GRANDCHILD_WORDS = frozenset({"grandchild", "granddaughter", "grandson"})
_AUNT_UNCLE_WORDS = frozenset({"aunt", "uncle", "aunt or uncle"})
_NIECE_NEPHEW_WORDS = frozenset({"niece", "nephew", "niece or nephew"})


# The only roles ever worked out. Typed so mypy rejects a gendered one.
DerivedRole = Literal["parent", "grandparent", "sibling", "aunt or uncle"]


@dataclass(frozen=True)
class Stated:
    """One stated family link: "a_id is the role of b_id", with the id that identifies it."""

    link_id: str
    a_id: str
    role: str
    b_id: str


@dataclass(frozen=True)
class Derived:
    """A link that follows from two stated links, with the ids of those two links."""

    a_id: str
    role: DerivedRole
    b_id: str
    source_ids: tuple[str, str]


def derive(stated: Sequence[Stated]) -> list[Derived]:
    """The links that follow from the stated ones, sorted by (a_id, role, b_id) so the result does
    not depend on the order of the input. A link that follows two different ways keeps the pair of
    sources whose ids sort first as strings. Roles outside the lists above are dropped, and so is a
    stated link from a name to itself, which says nothing about family."""
    parents: list[tuple[str, str, str]] = []  # (parent_id, child_id, link_id)
    siblings: list[tuple[str, str, str]] = []  # (a_id, b_id, link_id), read both ways below
    known: set[tuple[str, str, str]] = set()  # (a_id, neutral role, b_id) as stated, both readings
    aunts: list[tuple[str, str, str]] = []  # (aunt_or_uncle_id, nibling_id, link_id)
    grandparents: list[tuple[str, str, str]] = []  # (grandparent_id, grandchild_id, link_id)
    for link in stated:
        if link.a_id == link.b_id:
            continue
        if link.role in _PARENT_WORDS:
            parents.append((link.a_id, link.b_id, link.link_id))
            known.add((link.a_id, "parent", link.b_id))
        elif link.role in _CHILD_WORDS:
            parents.append((link.b_id, link.a_id, link.link_id))
            known.add((link.b_id, "parent", link.a_id))
        elif link.role in _SIBLING_WORDS:
            siblings.append((link.a_id, link.b_id, link.link_id))
            known.add((link.a_id, "sibling", link.b_id))
            known.add((link.b_id, "sibling", link.a_id))
        elif link.role in _GRANDPARENT_WORDS:
            known.add((link.a_id, "grandparent", link.b_id))
            grandparents.append((link.a_id, link.b_id, link.link_id))
        elif link.role in _GRANDCHILD_WORDS:
            known.add((link.b_id, "grandparent", link.a_id))
            grandparents.append((link.b_id, link.a_id, link.link_id))
        elif link.role in _AUNT_UNCLE_WORDS:
            known.add((link.a_id, "aunt or uncle", link.b_id))
            aunts.append((link.a_id, link.b_id, link.link_id))
        elif link.role in _NIECE_NEPHEW_WORDS:
            known.add((link.b_id, "aunt or uncle", link.a_id))
            aunts.append((link.b_id, link.a_id, link.link_id))

    found: dict[tuple[str, DerivedRole, str], tuple[str, str]] = {}

    def offer(a_id: str, role: DerivedRole, b_id: str, sources: tuple[str, str]) -> None:
        # A self-link is never a family link. A link already stated in either reading is not worked
        # out again. The reversed reading only matters for contradictory input (A parent of B and B
        # parent of A): the derived link is dropped rather than guessing which statement is wrong.
        if a_id == b_id or (a_id, role, b_id) in known or (b_id, role, a_id) in known:
            return
        if role == "sibling" and b_id < a_id:
            a_id, b_id = b_id, a_id  # (a two-way link is stored once, the smaller id first)
        key = (a_id, role, b_id)
        if key not in found or sources < found[key]:
            found[key] = sources

    for parent_id, child_id, parent_link in parents:
        for a_id, b_id, sibling_link in siblings:
            # Sibling links read both ways, so each one is tried from either end.
            for x_id, y_id in ((a_id, b_id), (b_id, a_id)):
                if x_id == child_id:
                    offer(parent_id, "parent", y_id, (parent_link, sibling_link))
    for grandparent_id, middle_id, first_link in parents:
        for middle_parent_id, grandchild_id, second_link in parents:
            if middle_parent_id == middle_id:
                offer(grandparent_id, "grandparent", grandchild_id, (first_link, second_link))

    def sibling_ends(person_id: str) -> list[tuple[str, str]]:
        """(sibling_id, link_id) for each stated sibling of this person, read from either end."""
        return [
            (y_id, link_id)
            for a_id, b_id, link_id in siblings
            for x_id, y_id in ((a_id, b_id), (b_id, a_id))
            if x_id == person_id
        ]

    people = {x for a_id, b_id, _ in siblings for x in (a_id, b_id)}
    for shared_id in sorted(people):
        ends = sibling_ends(shared_id)
        for i, (first_id, first_link) in enumerate(ends):
            for second_id, second_link in ends[i + 1 :]:
                offer(first_id, "sibling", second_id, (first_link, second_link))
    for parent_id, child_id, parent_link in parents:
        for uncle_id, sibling_link in sibling_ends(parent_id):
            if (uncle_id, "parent", child_id) in known:
                continue  # (contradictory input: the same person is the parent and the uncle)
            offer(uncle_id, "aunt or uncle", child_id, (parent_link, sibling_link))
    for uncle_id, niece_id, uncle_link in aunts:
        for other_id, sibling_link in sibling_ends(niece_id):
            offer(uncle_id, "aunt or uncle", other_id, (uncle_link, sibling_link))
    for grandparent_id, grandchild_id, grand_link in grandparents:
        for other_id, sibling_link in sibling_ends(grandchild_id):
            offer(grandparent_id, "grandparent", other_id, (grand_link, sibling_link))

    return [
        Derived(a_id=a, role=role, b_id=b, source_ids=sources)
        for (a, role, b), sources in sorted(found.items())
    ]
