"""Whether a waiting list of names is a group DMbot knows, for the names-to-check page (#3406).

Discord's "Yes, that group" button (#3404) is offered from `MemoryStore.group_hints`. The website's
database role cannot call the store, and a second copy of the rule in SQL could drift, so the page
asks `dmbot_name_group_info` for the few facts that rule reads (for the listed parts' spellings: the
entries they belong to, their memberships, the confirmed groups) and this module applies the same
rule with the same `split_list` the store uses. A test keeps the two answers equal.
"""

from __future__ import annotations

from typing import Any

from dmbot.memory.list_names import split_list
from dmbot.memory.models import GroupHint, lookup_key

MAX_HINTS = 3  # as the store: up to three, the best fit first


def parts_of(name: str) -> list[str]:
    """The parts of a waiting name when it is a list the DM could split (two or more), else [].
    The DM has decided on pressing the button, so "and", "or" and "&" split like a comma. (The
    store also passes the campaign's held spellings, but only to keep a name that is held whole
    from splitting, and it removes the name's own key from them first, so with the DM's decision
    they change nothing here.)"""
    parts = split_list(name, dm=True)
    return parts if len(parts) >= 2 else []


def hints_for(parts: list[str], info: dict[str, Any]) -> list[GroupHint]:
    """The known groups `parts` may be: every listed name, or all but one, already belongs to the
    group. `info` is what `dmbot_name_group_info` answered for `lookup_key` of each part."""
    if len(parts) < 2:
        return []
    by_key: dict[str, str] = {}
    for alias in info.get("aliases", []):
        by_key.setdefault(str(alias["key"]), str(alias["entity"]))  # (the first, as the store)
    ids = {p: by_key.get(lookup_key(p)) for p in parts}
    members = info.get("members", [])
    hints: list[GroupHint] = []
    for group in info.get("groups", []):
        belongs = {str(m["subject"]) for m in members if str(m["group"]) == str(group["id"])}
        inside = tuple(p for p in parts if ids[p] in belongs)
        if inside and len(inside) >= len(parts) - 1:
            outside = tuple(p for p in parts if p not in inside)
            hints.append(GroupHint(str(group["id"]), str(group["name"]), inside, outside))
    hints.sort(key=lambda h: (-len(h.members), h.group_name.casefold()))
    return hints[:MAX_HINTS]
