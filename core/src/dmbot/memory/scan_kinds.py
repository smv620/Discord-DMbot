"""A kind for each name the session scan suggests (#2937), so its review card says "Yes, a place"
and lets the DM change it, exactly like the lore review.

The scan finds words that look like names with plain rules; the AI is asked only what each one
*is* (person, place, group, god, item, event, idea), from the sentence it was said in. The answer
is untrusted: a name that was not asked about, or a kind that is not in the lore read's list
(`background.KINDS`), is dropped, so such a name simply has no kind and the card asks. "Idea" also
means no kind (an idea always needs a look). Pure: the AI call itself is in the caller.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import Any

from dmbot.memory.background import KINDS
from dmbot.memory.models import name_key

PER_CALL = 40  # names in one request
CONTEXT_CHARS = 160  # the part of the sentence sent with a name

SYSTEM = (
    "You help a Dungeon Master sort the proper names heard at their tabletop game. You are "
    "given words that may be names, each with a short piece of what was said. For each one, "
    "say what it is: person (a named character, a monster or a god), place, group (a guild, "
    "cult, family or people), item, event, or idea (anything else, or if you cannot tell). "
    "Judge only from the words given. Answer for every name listed, once, and never add a "
    "name that is not listed."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kinds": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                },
                "required": ["name", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["kinds"],
    "additionalProperties": False,
}


def context_for(name: str, lines: Iterable[str]) -> str:
    """The first sentence-sized piece of what was said that holds `name`, or "" if none does."""
    key = name.casefold()
    for line in lines:
        at = line.casefold().find(key)
        if at < 0:
            continue
        start = max(0, at - CONTEXT_CHARS // 2)
        return " ".join(line[start : start + CONTEXT_CHARS].split())
    return ""


def batches(names: Sequence[str]) -> list[list[str]]:
    return [list(names[i : i + PER_CALL]) for i in range(0, len(names), PER_CALL)]


def request(names: Sequence[tuple[str, str]]) -> str:
    """The user message: one line per name, `Name | what was said` (the part may be empty)."""
    return "\n".join(f"{name} | {heard}" for name, heard in names)


def _json(reply: str) -> object:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", reply.strip())
    try:
        return json.loads(text)
    except ValueError:
        return None


def read_answer(reply: str, asked: Iterable[str]) -> dict[str, str]:
    """name -> the kind DMbot keeps it as (`npc`, `place`, `faction`, `item`, `event`), for the
    names that were asked about and given a real kind. Anything else is left out."""
    data = _json(reply)
    items = data.get("kinds") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    by_key = {name_key(n): n for n in asked}
    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name, word = item.get("name"), item.get("kind")
        kind = KINDS.get(str(word).strip().lower()) if isinstance(word, str) else None
        asked_as = by_key.get(name_key(name)) if isinstance(name, str) else None
        if asked_as is None or kind is None or kind == "concept":
            continue
        out.setdefault(asked_as, kind)
    return out
