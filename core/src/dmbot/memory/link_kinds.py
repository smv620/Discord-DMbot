"""The kind of a connection for the story graph's colours and filters (#2942): family, friends and
allies, groups, work (work, service and places) or other. A fixed table from the connection's
relationship type, read in code; no AI, and no new data (the type is on a connection the viewer
may already see). A type not listed here, including a campaign's own, is `other`.
"""

from __future__ import annotations

FAMILY = "family"
ALLY = "ally"
GROUP = "group"
WORK = "work"
OTHER = "other"
KINDS = (FAMILY, ALLY, GROUP, WORK, OTHER)

BY_PREDICATE: dict[str, str] = {
    "kin_of": FAMILY,
    "ally_of": ALLY,
    "member_of": GROUP,
    "of_people": GROUP,
    "serves": WORK,
    "sworn_to": WORK,
    "owns": WORK,
    "carries": WORK,
    "located_in": WORK,
    "mentor_of": WORK,
    "leads": GROUP,
    "founded": GROUP,
    "captive_of": OTHER,
    "contains": WORK,
}


def link_kind(predicate: str) -> str:
    """The kind of a connection with this relationship type; `other` when it has no entry."""
    return BY_PREDICATE.get(predicate, OTHER)
