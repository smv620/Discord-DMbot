"""What a homebrew pack carries (#1527, #1635; docs/PLAN.md, "Shared rulesets"): the campaign's
homebrew entries and nothing else, built field by field so no story, name, note or sheet can ride
along. An entry loaded from a confirmed book (`from_book`, #1524) is **never** in a pack: its text
is licensed to one campaign only (CLAUDE.md, IP rule), so `content_of` leaves it out and says which
ones, by name, so the Share screen can tell the DM why they stay.

**Imported entries travel by reference** (#1635): an entry imported from a published page is a
pointer to the page kept once in `homebrew_pages` ("page": its address, "domain": its site), with
no words of its own in the pack. An upload credited to a website carries its words and its
"domain". Either way the follower must have approved that site before the entry is used
(`HomebrewReader`).

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
from dmbot.rules.site_urls import normalise_domain, normalise_url

SITES_MAX = 5  # distinct sites one pack may name: a pack cannot make a follower a long list of asks
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
    # Left out, and said so: an entry from a book, and an imported one whose pointer is unusable
    # (it must never fall back to carrying the page's words).
    stays = [e for e in own if e.from_book or (e.source_kind == "link" and not _pointable(e))]
    kept = [e for e in own if e not in stays]
    left_out = [e.name for e in stays]
    return {"entries": [_entry_of(e) for e in kept]}, left_out


def _pointable(e: Homebrew) -> bool:
    """An imported entry that can travel as a pointer: its address and site are in hand."""
    return bool(e.page_url and e.credit_domain and _pointer_ok(e.page_url, e.credit_domain))


def _entry_of(e: Homebrew) -> dict[str, Any]:
    """One entry as a pack carries it. An imported one is a pointer to its page (#1635): no words,
    only the page's address and site. An upload credited to a website keeps its words and names
    the site, so a follower needs that site's approval too."""
    entry: dict[str, Any] = {
        "kind": e.kind,
        "name": e.name,
        "summary": e.summary,
        "text": e.text,
        "known": True,
    }
    if e.source_kind == "link":
        entry.update(summary=None, text=None, page=e.page_url, domain=e.credit_domain)
    elif e.credit_domain:
        entry["domain"] = e.credit_domain
    return entry


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
        page, domain = entry.get("page"), entry.get("domain")
        if page is not None:  # a pointer to a published page: no words of its own
            ok_words = text is None and summary is None and _pointer_ok(page, domain)
        else:
            ok_words = (
                isinstance(text, str)
                and 0 < len(text) <= TEXT_MAX
                and (summary is None or (isinstance(summary, str) and len(summary) <= SUMMARY_MAX))
                and (
                    domain is None
                    or (isinstance(domain, str) and normalise_domain(domain) == domain)
                )
            )
        if (
            kind not in KINDS
            or not isinstance(name, str)
            or not 0 < len(name) <= NAME_MAX
            or not ok_words
            or name_key(name) in seen
        ):
            raise BadPack("entry")
        seen.add(name_key(name))
        clean: dict[str, Any] = {
            "kind": kind, "name": name, "summary": summary, "text": text, "known": True
        }  # fmt: skip
        if domain is not None:
            clean["domain"] = domain
        if page is not None:
            clean["page"] = page
        out.append(clean)
    if len({e["domain"] for e in out if "domain" in e}) > SITES_MAX:
        raise BadPack("too many sites")
    return {"entries": out}


def _pointer_ok(page: object, domain: object) -> bool:
    """A pointer's address is in its one normal form and its site is the address's own."""
    if not isinstance(page, str) or not isinstance(domain, str):
        return False
    found = normalise_url(page)
    return found is not None and found == (page, domain)


def is_pack(content: Any) -> bool:
    return isinstance(content, dict) and "entries" in content
