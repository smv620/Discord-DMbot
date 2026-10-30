"""What the player characters know of an uploaded lore text (#2959, owner 2026-10-29).

The DM picks one of three when campaign lore is uploaded:

- **World general knowledge** (`WORLD`): all of it is known to every player character, at 1.0.
- **Spoilers** (`SPOILERS`): none of it is known to them; they can discover it in play. This is what
  an upload did before the choice, so it changes nothing.
- **Smart guess** (`SMART`): the AI decides, statement by statement, whether the world would know it
  or the player characters would have to discover it. Anything it does not call common knowledge, or
  does not answer for, is a spoiler: a wrong guess then hides something instead of telling it.

A statement is a name (with its other names) or a connection. The AI sees one short line each (the
kind, the name and the quote the read already keeps, never the document) and answers with numbers.
Nothing here decides anything about NPCs or groups, and nothing is invented: it only sorts what the
read found. The DM can change any of it afterwards (Who knows this?).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any, Protocol

from dmbot.ai import Reply

log = logging.getLogger(__name__)

WORLD, SPOILERS, SMART = "world", "spoilers", "smart"
CHOICES = (WORLD, SPOILERS, SMART)
DEFAULT = SPOILERS  # what an upload did before there was a choice

MAX_STATEMENTS = 300  # one call; a longer list is sorted in parts of this size
LINE_MAX = 240  # characters of one statement's line

SYSTEM = (
    "You sort statements from a Dungeons & Dragons campaign's lore into two kinds. "
    "WORLD: something any ordinary inhabitant of the setting would know (who rules a place, a "
    "famous landmark, well-known history, a public office, a widely known organisation, "
    "common customs). SPOILER: something the player characters would have to discover in play "
    "(a secret, a hidden identity, a plot or plan, a private motive, a hidden place, what only "
    "insiders know, a betrayal). When you are unsure, it is a SPOILER. You only sort the "
    "statements you are given: you never add, change or explain them."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"world": {"type": "array", "items": {"type": "integer"}}},
    "required": ["world"],
    "additionalProperties": False,
}


class Client(Protocol):
    async def complete(
        self, system: str, user: str, *, max_tokens: int, schema: dict[str, Any] | None = None
    ) -> Reply: ...


def normalize(choice: str | None) -> str:
    """A known choice, else the default (an upload from before the choice, or a wrong word)."""
    return choice if choice in CHOICES else DEFAULT


def line(kind: str, text: str, quote: str = "") -> str:
    """One statement as the AI sees it: what it is, and the short quote the read keeps."""
    said = f' — "{quote}"' if quote else ""
    return f"{kind}: {text}{said}"[:LINE_MAX]


def _prompt(lines: Sequence[str]) -> str:
    numbered = "\n".join(f"{i}. {x}" for i, x in enumerate(lines))
    return (
        "Statements:\n"
        f"{numbered}\n\n"
        'Answer with JSON: {"world": [numbers of the WORLD statements]}. '
        "Leave every SPOILER and every statement you are unsure of out of the list."
    )


def parse(text: str, count: int) -> set[int] | None:
    """The numbers the answer calls WORLD, or None when it cannot be read. Numbers outside the
    list are ignored; a plain number list without the key is read as well."""
    try:
        data = json.loads(text)
    except ValueError:
        found = re.findall(r"\d+", text)
        return {int(n) for n in found if int(n) < count} if found else None
    items = data.get("world") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    return {n for n in items if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < count}


async def decide(
    choice: str, lines: Sequence[str], ai: Client | None = None
) -> tuple[set[int], bool]:
    """(the indexes of `lines` that every player character knows, whether the AI was asked and
    answered). WORLD is all of them, SPOILERS none. SMART asks the AI in parts; a part that fails
    (no AI, a refusal, a cut or unreadable answer) is all spoilers, never all world."""
    if choice == WORLD:
        return set(range(len(lines))), False
    if choice != SMART or ai is None or not lines:
        return set(), False
    known: set[int] = set()
    answered = True
    for start in range(0, len(lines), MAX_STATEMENTS):
        part = lines[start : start + MAX_STATEMENTS]
        try:
            reply = await ai.complete(SYSTEM, _prompt(part), max_tokens=4000, schema=SCHEMA)
        except Exception as exc:  # the type only; the lines are the DM's words
            log.warning("Lore knowledge: the AI call failed (%s)", type(exc).__name__)
            answered = False
            continue
        picked = None if reply.refused or reply.cut else parse(reply.text, len(part))
        if picked is None:
            log.warning("Lore knowledge: the answer could not be used")
            answered = False
            continue
        known |= {start + n for n in picked}
    return known, answered
