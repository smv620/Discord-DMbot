"""The campaign page's text, pasted in, read for the campaign's name and its characters (#3955;
docs/WIZARD_DESIGN.md, 4.2). A plain reader: no AI, nothing stored, nothing fetched.

A D&D Beyond campaign page is behind the DM's own sign-in and DMbot never holds a D&D Beyond
password or cookie, so it cannot read the page itself. The DM may paste what they see instead. This
is only a helper for step 2, where the DM confirms every name: when the text's shape is not
recognised it finds nothing, and the page says so. The shape handled is what a signed-in DM sees:
the campaign's name on the first line, then a "Characters" heading followed by one character a line
(the name, and after a dash, a bar or a comma, the player and the build), or a block for each
character (the name, then a "Level 3 Human Fighter" line and buttons); and an inline sentence such
as "The player characters in this adventure are Cerric, Belleros and Claude." Nothing here is
checked against a live page, so the parser is deliberately cautious (#4214).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TEXT = 20_000
MAX_CHARACTERS = 12
MAX_NAME_WORDS = 4  # a long clause is not a name
_HEADING = re.compile(r"^\s*((player|active)\s+)?characters\s*:?\s*$", re.IGNORECASE)
_STOP = re.compile(
    r"^\s*(notes?|description|players?|dungeon\s+master|invite|settings|members|"
    r"campaign\s+notes?|private\s+notes?|join\s+link)\b.*$",
    re.IGNORECASE,
)
_SPLIT = re.compile(r"\s+[-–—|]\s+|\s*\|\s*|,\s+")
_NAME = re.compile(r"^[^\W\d_][\w'’.\- ]{0,98}$", re.UNICODE)
# What a page shows beside a character that is not a name: the build line ("Level 3 Human Fighter")
# and the buttons.
_BUILD = re.compile(r"^level\s+\d+\b", re.IGNORECASE)
_BUTTONS = frozenset({"level", "view", "manage", "leave", "remove"})
# "The player characters in this adventure are Cerric, Belleros, and Claude." (to the full stop)
_SENTENCE = re.compile(
    r"(?:the\s+)?(?:player\s+)?characters(?:\s+in\s+this\s+(?:adventure|campaign|game))?"
    r"\s*(?:\bare\b|\bis\b|:)\s*(?P<list>[^\n]+?)(?=\.(?:\s|$)|$)",
    re.IGNORECASE | re.MULTILINE,
)
_LIST_SPLIT = re.compile(r"\s*(?:,|&|\band\b|\bor\b)\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PageRead:
    name: str
    characters: tuple[str, ...]


def _is_button(line: str) -> bool:
    first = line.split(maxsplit=1)[0].casefold() if line.split() else ""
    return line.casefold() in _BUTTONS or (first in _BUTTONS and len(line.split()) == 1)


def _heading_list(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The names under a "Characters" (or "Player/Active characters") heading, one block a line: the
    name line, then maybe a build line ("Level 3 Human Fighter") and button words, which are
    skipped. Stops at the next section."""
    found: list[tuple[int, str]] = []
    in_list = False
    for at, line in lines:
        if _HEADING.match(line):
            in_list = True
            continue
        if not in_list:
            continue
        if _STOP.match(line):
            break
        if _BUILD.match(line) or _is_button(line):
            continue
        who = _SPLIT.split(line, maxsplit=1)[0].strip()
        if _NAME.match(who):
            found.append((at, who))
    return found


def _sentences(text: str) -> list[tuple[int, str]]:
    """The names an inline sentence lists ("The characters are Tav and Shadowheart."): split on
    commas, "and", "&" and "or"; each is a capitalised name of at most MAX_NAME_WORDS words, or the
    sentence is not read (so "characters are what make a story" gives nothing)."""
    found: list[tuple[int, str]] = []
    for m in _SENTENCE.finditer(text):
        names = [n.strip() for n in _LIST_SPLIT.split(m.group("list")) if n.strip()]
        if not names or not all(
            _NAME.match(n) and n[0].isupper() and len(n.split()) <= MAX_NAME_WORDS for n in names
        ):
            continue
        found.extend((m.start("list"), n) for n in names)
    return found


def read(text: str) -> PageRead:
    """The campaign's name and the characters' names found in the pasted page, or empty ones. Every
    shape that matches counts: the names are taken in the order they first appear, without a name
    twice (any case), at most MAX_CHARACTERS."""
    if not isinstance(text, str):
        return PageRead("", ())
    clean = text.replace("\x00", "")[:MAX_TEXT]
    lines: list[tuple[int, str]] = []
    offset = 0
    for raw in clean.splitlines(keepends=True):
        stripped = raw.strip()
        if stripped:
            lines.append((offset + raw.index(stripped[0]), stripped))
        offset += len(raw)
    if not lines:
        return PageRead("", ())
    first = lines[0][1]
    name = first if len(first) <= 100 and not _HEADING.match(first) else ""
    found: list[str] = []
    seen: set[str] = set()
    for _, who in sorted(_heading_list(lines[1:]) + _sentences(clean), key=lambda p: p[0]):
        if who.casefold() not in seen:
            seen.add(who.casefold())
            found.append(who)
        if len(found) >= MAX_CHARACTERS:
            break
    return PageRead(name, tuple(found))
