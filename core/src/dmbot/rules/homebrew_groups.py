"""Where a campaign's homebrew came from, grouped by source (#2285; docs/PLAN.md, "Seeing where
house rules come from", the Homebrew menu's half). Pure: no database and no Discord.

The groups: entries typed in Discord, each kind of upload (by who or what it is credited to),
each site entries were linked from, and each followed pack. Every entry already records its
source (`source_kind`, the credit, the page address; #1633), so nothing new is stored here. A
download is that group's entries in the existing homebrew file format (`homebrew_file.write`).
Linked entries keep their pages' addresses: those are public pages, so they are safe to list.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.rules import homebrew_file
from dmbot.rules.homebrew import Homebrew, credit_label

TYPED_TITLE = "Typed in Discord"
UPLOAD_TITLE = "Uploaded files"
MAX_SOURCES = 25  # what one Discord menu can hold


@dataclass(frozen=True, slots=True)
class Group:
    key: str  # stable inside one listing
    kind: str  # "typed", "upload", "link" or "pack"
    title: str
    entries: tuple[Homebrew, ...]

    @property
    def pages(self) -> tuple[str, ...]:
        """The published pages behind a linked group's entries, once each."""
        return tuple(dict.fromkeys(e.page_url for e in self.entries if e.page_url))

    @property
    def line(self) -> str:
        count = f"{len(self.entries)} entr{'ies' if len(self.entries) != 1 else 'y'}"
        return f"{count} · {self.title}"


def group(entries: Sequence[Homebrew]) -> list[Group]:
    """The entries by source: typed, uploads, linked sites, then followed packs. A group with
    no entries is not listed."""
    typed: list[Homebrew] = []
    uploads: dict[str, list[Homebrew]] = {}
    links: dict[str, list[Homebrew]] = {}
    packs: dict[str, list[Homebrew]] = {}
    for e in entries:
        if e.source:  # a followed pack's entry: read-only here, named by its pack
            packs.setdefault(e.source, []).append(e)
        elif e.source_kind == "link":
            links.setdefault(e.credit_domain or "a website", []).append(e)
        elif e.source_kind == "upload":
            uploads.setdefault(credit_label(e) or UPLOAD_TITLE, []).append(e)
        else:
            typed.append(e)
    out: list[Group] = []
    if typed:
        out.append(Group("typed", "typed", TYPED_TITLE, tuple(typed)))
    for label, rows in uploads.items():
        out.append(Group(f"upload:{label}", "upload", f"Upload, {label}", tuple(rows)))
    for site, rows in links.items():
        out.append(Group(f"link:{site}", "link", f"Linked from {site}", tuple(rows)))
    for name, rows in packs.items():
        out.append(Group(f"pack:{name}", "pack", f"Pack: {name}", tuple(rows)))
    return out[:MAX_SOURCES]


def download(campaign_name: str, target_ruleset: str, source: Group) -> tuple[str, str]:
    """(file name, text) for one group's entries in the homebrew file format."""
    title = f"{campaign_name} ({source.title})"
    return (
        homebrew_file.filename(title),
        homebrew_file.write(title, source.entries, target_ruleset),
    )
