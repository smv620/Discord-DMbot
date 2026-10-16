"""What a homebrew pack carries (#1527; docs/PLAN.md, "Shared rulesets"): the campaign's own
homebrew entries and nothing else, built field by field so no story, name, note or sheet can ride
along. An entry loaded from a confirmed book (`from_book`, #1524) is **never** in a pack: its text
is licensed to one campaign only (CLAUDE.md, IP rule), so `content_of` leaves it out and says which
ones, by name, so the Share screen can tell the DM why they stay.

A leaf module (it needs only the homebrew limits) so the shared-set store can check a pack's shape
the same way for an offer, a follow and a backup file.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from dmbot.rules.homebrew import (
    HOMEBREW_MAX,
    KINDS,
    NAME_MAX,
    RULE_KIND,
    SUMMARY_MAX,
    TEXT_MAX,
    Homebrew,
    name_key,
)

PACKS_MAX = 5  # packs one campaign follows, so a prompt and a backup stay a sensible size


class BadPack(ValueError):
    """The content isn't a homebrew pack's. Callers turn it into their own error."""


def content_of(entries: Sequence[Homebrew]) -> tuple[dict[str, Any], list[str]]:
    """The pack's content for these entries, oldest first, and the names of the entries left out
    because they came from a confirmed book. `known` is always true now: nothing is hidden from
    players any more ("Homebrew (part 2)"). The key stays so older readers (the website's pack
    door) and packs published before still fit."""
    # Only the campaign's own entries: one read from a followed pack (`source`) is not its to share.
    # An entry the DM turned off (#1629) is not offered either: DMbot uses it nowhere.
    own = sorted(
        (e for e in entries if e.source is None and e.enabled), key=lambda e: (e.created_at, e.id)
    )
    # An entry from a link is a published page's, kept by reference (#1633): it is never copied
    # into a pack as words. Packs carry such entries by reference later (#1635).
    kept = [e for e in own if not e.from_book and e.source_kind != "link"]
    left_out = [e.name for e in own if e.from_book]
    return (
        {
            "entries": [
                {
                    "kind": e.kind,
                    "name": e.name,
                    "summary": e.summary,
                    "text": e.text,
                    "known": True,
                }
                for e in kept
            ]
        },
        left_out,
    )


def valid_content(content: Any) -> dict[str, Any]:
    """A pack's content as a copy that holds only what a pack may: entries within the homebrew
    limits, each name once. Anything else is dropped; a wrong shape raises BadPack."""
    if not isinstance(content, dict) or not isinstance(content.get("entries"), list):
        raise BadPack("not a pack")
    if len(content["entries"]) > HOMEBREW_MAX:
        raise BadPack("too many")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in content["entries"]:
        if not isinstance(entry, dict):
            raise BadPack("entry")
        if entry.get("kind") == RULE_KIND:
            continue  # a pack made before rules left homebrew (#1628): the rule is left out
        kind, name, text = entry.get("kind"), entry.get("name"), entry.get("text")
        summary = entry.get("summary")
        if (
            kind not in KINDS
            or not isinstance(name, str)
            or not 0 < len(name) <= NAME_MAX
            or not isinstance(text, str)
            or not 0 < len(text) <= TEXT_MAX
            or not (summary is None or (isinstance(summary, str) and len(summary) <= SUMMARY_MAX))
            or name_key(name) in seen
        ):
            raise BadPack("entry")
        seen.add(name_key(name))
        out.append({"kind": kind, "name": name, "summary": summary, "text": text, "known": True})
    return {"entries": out}


def is_pack(content: Any) -> bool:
    return isinstance(content, dict) and "entries" in content
