"""A list of names in plain text: 📥 Add many and 📤 Download all (docs/PLAN.md, "Names
at scale", step 3; #126). Pure: no Discord, no database.

One name per line: `name | kind | other names | secret names`. Only the name is needed;
other names and secret names are separated by `,` or `;` (as in the Add a name form).
Lines starting with `#` are notes and are skipped, so the template's instructions and a
download's header can stay in the file when it's uploaded again.
"""

from __future__ import annotations

import re
import unicodedata
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


def header(*, secrets: bool) -> str:
    """The instructions at the top of the template and of a download. `secrets`: for the
    campaign's DMs, who may add secret names (the fourth part)."""
    parts = "name | kind | other names | secret names" if secrets else "name | kind | other names"
    lines = [
        "### DMbot names list",
        "###",
        "### One name per line. Put a | (a straight up-and-down line) between the parts:",
        f"###     {parts}",
        "###",
        "### - Only the name is needed. To skip a part, leave it empty: Ulfgar | | Ulf",
        "### - kind: NPC, place, group, creature, item, god, spell, event or other.",
        "###   These work too: person or character (NPC); town, city or location (place);",
        "###   faction or guild (group); monster or beast (creature); object or weapon",
        "###   (item); deity (god); magic (spell); thing (other).",
        "###   Another word (like wizard), or no kind? After you add the list, DMbot asks",
        '###   you once what every "wizard" is. If there are many words, the rest wait in',
        "###   Check new names.",
        "### - other names: nicknames, titles or short forms people say.",
        "###   Put a , or ; between them: Bell, the old knight",
    ]
    if secrets:
        lines += [
            "### - secret names: disguises or secret identities (who it really is), with a ,",
            "###   or ; between them. Only the campaign's DM can add them. Players never",
            "###   see them.",
        ]
    lines += [
        "### - Names only: no descriptions or notes. Each name is up to 100 characters.",
        "### - Player's characters: add them with Add a player's character instead.",
        "### - Lines starting with # are notes. DMbot skips them, so you can leave these.",
        "### - Names DMbot already knows are skipped. Up to 2,000 lines per file.",
        "### - If a line doesn't fit, DMbot tells you, and lets you add the rest or have its",
        "###   AI tidy the list.",
        "###",
        "### To use it: change the examples to your own names and save it as a .txt file",
        "### (Notepad on Windows; TextEdit on a Mac: Format > Make Plain Text).",
        '### Then in Discord type /dmbot names and add the file in the "file" box.',
        "### On a phone? Copy the lines instead, then press Add many > Paste a list.",
    ]
    return "\n".join(lines) + "\n"


def template(*, secrets: bool) -> str:
    """The file to fill in: the instructions, then examples to change."""
    belleros = "Belleros | NPC | Bell, the old knight"
    return (
        header(secrets=secrets)
        + "###\n### Examples (change or delete them):\n"
        + (belleros + " | the hooded stranger" if secrets else belleros)
        + "\nBryn Shander | place | Bryn\nFrostwolf tribe | group | the Frostwolves\n"
        "Heartseeker | item\nAuril | god | the Frostmaiden\nUlfgar\n"
    )


HEADER = header(secrets=True)
TEMPLATE = template(secrets=True)
_SEPARATORS = re.compile(r"[;,]")
# A web address is never a name (#264): a pasted link must be read, not added.
_LINK = re.compile(r"://|^www\.", re.IGNORECASE)


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
    for part in _SEPARATORS.split(raw):
        text = " ".join(part.split())
        if text and name_key(text) not in seen:
            seen.add(name_key(text))
            out.append(text)
    return out


def _without(names: list[str], taken: set[str]) -> list[str]:
    """`names` minus any already said for this line (the name itself, its other names)."""
    return [n for n in names if name_key(n) not in taken]


def _problem(text: str) -> str | None:
    if any(unicodedata.category(c) == "Cc" for c in text):
        return "it has characters DMbot can't read. Save the file as .txt and try again"
    if len(text) > NAME_MAX or len(name_key(text)) > NAME_MAX:
        return f"a name is longer than {NAME_MAX} characters"
    if len(text.split()) > MAX_WORDS:
        return f"more than {MAX_WORDS} words. Names only, no descriptions"
    if _LINK.search(text):
        return "that's a link, not a name. To read what it points to, press 🔗 Paste a link"
    return None


def kind_of(word: str) -> str | None:
    """A kind in plain words ("Places", "cities", "god") → the memory rules' kind."""
    word = " ".join(word.casefold().split())
    if not word:
        return None
    for guess in (word, word.removesuffix("s"), word.removesuffix("ies") + "y"):
        if guess in KIND_WORDS:
            return KIND_WORDS[guess]
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
            out.refused.append(
                (number, "more than 4 parts. Use | only between name, kind, other names, "
                 "secret names")
            )  # fmt: skip
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
            out.refused.append(
                (number, "only the campaign's DM can add secret names. Remove the last part "
                 "and add it again")
            )  # fmt: skip
            continue
        key = name_key(name)
        if key in seen:
            out.repeated += 1
            continue
        seen.add(key)
        word = " ".join(cells[1].split())
        others, hidden = _without(others, {key}), _without(hidden, {key, *map(name_key, others)})
        out.lines.append(ListLine(number, name, kind_of(word), word, tuple(others), tuple(hidden)))
    return out


@dataclass(frozen=True, slots=True)
class OutName:
    name: str
    kind: str
    others: tuple[str, ...]
    secrets: tuple[str, ...]


def render(names: Iterable[OutName], *, campaign: str, secrets: bool) -> str:
    """A download: the instructions, then one line per name, A to Z. Uploading it again
    adds nothing that's already known."""
    lines = [
        header(secrets=secrets).rstrip("\n"),
        "###",
        f"### Names DMbot knows for {' '.join(campaign.split())}",
    ]
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
