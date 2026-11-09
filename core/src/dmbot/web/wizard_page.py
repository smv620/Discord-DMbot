"""The campaign page's text, pasted in, read for the campaign's name and its characters (#3955;
docs/WIZARD_DESIGN.md, 4.2). A plain reader: no AI, nothing stored, nothing fetched.

A D&D Beyond campaign page is behind the DM's own sign-in and DMbot never holds a D&D Beyond
password or cookie, so it cannot read the page itself. The DM may paste what they see instead. This
is only a helper for step 2, where the DM confirms every name: when the text's shape is not
recognised it finds nothing, and the page says so. The shape handled is what a signed-in DM sees:
the campaign's name on the first line, then a "Characters" heading followed by one character a line
(the name, and after a dash, a bar or a comma, the player and the build). Nothing here is checked
against a live page, so the parser is deliberately cautious.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TEXT = 20_000
MAX_CHARACTERS = 12
_HEADING = re.compile(r"^\s*(player\s+)?characters\s*:?\s*$", re.IGNORECASE)
_STOP = re.compile(
    r"^\s*(notes?|description|players?|dungeon\s+master|invite|settings|members|"
    r"campaign\s+notes?|private\s+notes?|join\s+link)\b.*$",
    re.IGNORECASE,
)
_SPLIT = re.compile(r"\s+[-–—|]\s+|\s*\|\s*|,\s+")
_NAME = re.compile(r"^[^\W\d_][\w'’.\- ]{0,98}$", re.UNICODE)


@dataclass(frozen=True, slots=True)
class PageRead:
    name: str
    characters: tuple[str, ...]


def read(text: str) -> PageRead:
    """The campaign's name and the characters' names found in the pasted page, or empty ones."""
    if not isinstance(text, str):
        return PageRead("", ())
    lines = [ln.strip() for ln in text.replace("\x00", "")[:MAX_TEXT].splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return PageRead("", ())
    name = lines[0] if len(lines[0]) <= 100 and not _HEADING.match(lines[0]) else ""
    found: list[str] = []
    in_list = False
    for line in lines[1:]:
        if _HEADING.match(line):
            in_list = True
            continue
        if not in_list:
            continue
        if _STOP.match(line):
            break
        who = _SPLIT.split(line, maxsplit=1)[0].strip()
        if _NAME.match(who) and who.casefold() not in {c.casefold() for c in found}:
            found.append(who)
        if len(found) >= MAX_CHARACTERS:
            break
    return PageRead(name, tuple(found))
