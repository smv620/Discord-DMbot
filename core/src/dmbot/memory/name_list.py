"""A list of names in plain text: 📥 Add many and 📤 Download all (docs/PLAN.md, "Names
at scale", step 3; #126). Pure: no Discord, no database.

One name per line: `name | kind | other names | secret names`. Only the name is needed;
other names and secret names are separated by `;`. Lines starting with `#` are notes and
are skipped, so the template's instructions and a download's header can stay in the
file when it's uploaded again.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from dmbot.memory.models import NAME_MAX, name_key

MAX_FILE_BYTES = 256 * 1024
MAX_LINES = 2000
MAX_NAMES = 10_000  # a campaign holds up to about this many
MAX_WORDS = 8  # more than this is a description, not a name
PC = "player_character"

# Kinds in plain words → the memory rules' kinds. Anything else "needs a look".
KIND_WORDS: dict[str, str] = {
    "npc": "npc",
    "person": "npc",
    "character": "npc",
    "place": "place",
    "location": "place",
    "town": "place",
    "city": "place",
    "group": "faction",
    "faction": "faction",
    "guild": "faction",
    "creature": "creature",
    "monster": "creature",
    "beast": "creature",
    "item": "item",
    "object": "item",
    "weapon": "item",
    "god": "deity",
    "deity": "deity",
    "spell": "spell",
    "magic": "spell",
    "event": "event",
    "other": "concept",
    "thing": "concept",
}
# How each kind is written in a download (read back by KIND_WORDS).
KIND_OUT: dict[str, str] = {
    "npc": "NPC",
    PC: "player's character",
    "character": "character",
    "place": "place",
    "faction": "group",
    "creature": "creature",
    "item": "item",
    "deity": "god",
    "spell": "spell",
    "event": "event",
    "concept": "other",
}

HEADER = """\
### DMbot names list
###
### One name per line, like this:
###     name | kind | other names | secret names
###
### - Only the name is needed. Leave the rest out, or empty: Ulfgar || Ulf
### - kind: NPC, place, group, creature, item, god, spell, event or other.
###   Leave it out and DMbot asks you later.
### - other names: nicknames, titles or short forms people say, separated by ;
### - secret names: disguises or secret identities, separated by ; (only the
###   campaign's DM can add them; DMbot never puts them in the transcript)
### - Names only: no descriptions or notes. Each name is up to 100 characters.
### - Lines starting with # are notes. DMbot skips them, so you can leave these.
### - Names DMbot already knows are skipped. Up to 2,000 lines per file.
###
### To use it: fill it in, save it as plain text (UTF-8), then in Discord type
### /dmbot names and add this file in the "file" box. Or press 📥 Add many and
### paste the lines.
"""

TEMPLATE = (
    HEADER
    + """\
###
### Examples (change or delete them):
Belleros | NPC | Bell; the old knight | the hooded stranger
Bryn Shander | place | Bryn
Frostwolf tribe | group | the Frostwolves
Heartseeker | item
Auril | god | the Frostmaiden
Ulfgar
"""
)


@dataclass(frozen=True, slots=True)
class ListLine:
    number: int  # line number in the file, for messages
    name: str
    kind: str | None  # a memory kind, or None when missing or unclear
    kind_word: str  # as written ("" if none)
    others: tuple[str, ...]
    secrets: tuple[str, ...]


@dataclass(slots=True)
class Parsed:
    lines: list[ListLine] = field(default_factory=list)
    refused: list[tuple[int, str]] = field(default_factory=list)  # (line, why), plain words
    repeated: int = 0  # the same name twice in the list


def _names(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(";"):
        text = " ".join(part.split())
        if text and name_key(text) not in seen:
            seen.add(name_key(text))
            out.append(text)
    return out


def _without(names: list[str], taken: set[str]) -> list[str]:
    """`names` minus any already said for this line (the name itself, its other names)."""
    return [n for n in names if name_key(n) not in taken]


def _problem(text: str) -> str | None:
    if len(text) > NAME_MAX:
        return f"a name is longer than {NAME_MAX} characters"
    if len(text.split()) > MAX_WORDS:
        return "it looks like a description, not a name"
    return None


def parse(text: str, *, secrets: bool) -> Parsed:
    """The names in a list. `secrets`: whether this person may add secret names (the
    campaign's DMs); otherwise a line with secret names is refused, never half-saved."""
    out = Parsed()
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 4:
            out.refused.append((number, "too many | parts (names only, no notes)"))
            continue
        cells += [""] * (4 - len(cells))
        name = " ".join(cells[0].split())
        if not name:
            out.refused.append((number, "no name before the first |"))
            continue
        others, hidden = _names(cells[2]), _names(cells[3])
        why = next((p for t in (name, *others, *hidden) if (p := _problem(t))), None)
        if why:
            out.refused.append((number, why))
            continue
        if hidden and not secrets:
            out.refused.append((number, "only the campaign's DM can add secret names"))
            continue
        key = name_key(name)
        if key in seen:
            out.repeated += 1
            continue
        seen.add(key)
        word = " ".join(cells[1].split())
        kind = KIND_WORDS.get(word.casefold().rstrip("s")) if word else None
        if kind is None and word:
            kind = KIND_WORDS.get(word.casefold())
        others, hidden = _without(others, {key}), _without(hidden, {key, *map(name_key, others)})
        out.lines.append(ListLine(number, name, kind, word, tuple(others), tuple(hidden)))
    return out


@dataclass(frozen=True, slots=True)
class OutName:
    name: str
    kind: str
    others: tuple[str, ...]
    secrets: tuple[str, ...]


def render(names: Iterable[OutName], *, campaign: str, secrets: bool) -> str:
    """A download: the header, then one line per name, A to Z. Uploading it again adds
    nothing that's already known."""
    lines = [HEADER.rstrip("\n"), "###", f"### Names DMbot knows for {campaign}"]
    if secrets:
        lines.append("### This file includes secret names. Don't share it with players.")
    for n in sorted(names, key=lambda n: name_key(n.name)):
        cells = [n.name, KIND_OUT.get(n.kind, "other"), "; ".join(n.others)]
        if secrets and n.secrets:
            cells.append("; ".join(n.secrets))
        while cells and not cells[-1]:
            cells.pop()
        lines.append(" | ".join(cells))
    return "\n".join(lines) + "\n"
