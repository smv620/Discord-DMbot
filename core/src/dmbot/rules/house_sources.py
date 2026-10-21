"""Where a campaign's house rules came from (#2285; docs/PLAN.md, "Seeing where house rules come
from"): the rules grouped by source, one group for each place, so a DM can see and download what
came from each. Pure: no database and no Discord.

The groups: the linked file, each uploaded file (by its name), rules added in Discord (typed, by
voice, from a rules card, and every rule from before the source was recorded), and each followed
set. A download is that group's rules in the plain numbered format (`house_file.write`), from
DMbot's own copy: an uploaded file itself is never kept.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dmbot.rules import house_file
from dmbot.rules.house import HouseRule

LINKED_TITLE = "The linked house-rules file"
DISCORD_TITLE = "Added in Discord"
UPLOAD_TITLE = "An uploaded file"
MAX_SOURCES = 25  # what one Discord menu can hold


@dataclass(frozen=True, slots=True)
class Source:
    key: str  # stable inside one listing; a menu's value
    kind: str  # "linked", "upload", "discord" or "followed"
    title: str
    rules: tuple[HouseRule, ...]
    publisher: str | None = None  # a followed set's publisher

    @property
    def line(self) -> str:
        """The list's line for this source: its title, what it is, and how many rules."""
        count = f"{len(self.rules)} rule{'s' if len(self.rules) != 1 else ''}"
        by = f", by {self.publisher}" if self.publisher else ""
        return f"{self.title}{by}: {count}"


def group(rules: Sequence[HouseRule], publishers: Mapping[str, str] | None = None) -> list[Source]:
    """The rules by source, in the order a DM reads them: the linked file, uploads (newest
    first), added in Discord, then followed sets. A source with no rules is not listed.
    `publishers`: a followed set's name to its publisher."""
    publishers = publishers or {}
    linked: list[HouseRule] = []
    uploads: dict[str, list[HouseRule]] = {}
    discord_rules: list[HouseRule] = []
    sets: dict[str, list[HouseRule]] = {}
    for rule in rules:
        followed = getattr(rule, "set_name", None) or rule.from_set
        if followed:
            sets.setdefault(followed, []).append(rule)
        elif rule.source == "linked":
            linked.append(rule)
        elif rule.source == "upload":
            uploads.setdefault(rule.source_name or UPLOAD_TITLE, []).append(rule)
        else:  # typed, voice, alert, and everything from before it was recorded
            discord_rules.append(rule)
    out: list[Source] = []
    if linked:
        out.append(Source("linked", "linked", LINKED_TITLE, tuple(linked)))
    for name, group_rules in sorted(
        uploads.items(), key=lambda kv: max(r.created_at for r in kv[1]), reverse=True
    ):
        out.append(Source(f"upload:{name}", "upload", name, tuple(group_rules)))
    if discord_rules:
        out.append(Source("discord", "discord", DISCORD_TITLE, tuple(discord_rules)))
    for name, group_rules in sets.items():
        out.append(
            Source(f"set:{name}", "followed", name, tuple(group_rules), publishers.get(name))
        )
    return out[:MAX_SOURCES]


def download(campaign_name: str, source: Source) -> tuple[str, str]:
    """(file name, text) for one source's rules, in the plain numbered format."""
    title = f"{campaign_name} ({source.title})"
    return house_file.filename(title), house_file.write(title, source.rules)
