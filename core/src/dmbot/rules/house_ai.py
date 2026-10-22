"""House rules found in any text by one AI call, kept only if the file says so (#2377).

When a file is not in DMbot's numbered format, the careful tier lists the house rules it finds,
each with a short **quote** from the file. Code, not the AI, decides what is kept: a rule stays
only if its quote really appears in the text (spacing and case ignored), so DMbot never invents
one. Then the list is held to 200 rules of 500 characters, and a rule the campaign already has (or
the answer repeats) is dropped. The DM still decides: the result is an offer, never a save.

The file's words go to the AI for this one read and are not stored. Logs hold counts only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.ai import Reply
from dmbot.rules.house import HOUSE_RULES_MAX, RULE_MAX

log = logging.getLogger(__name__)

Complete = Callable[..., Awaitable[Reply]]

TIMEOUT_S = 120.0
MIN_QUOTE = 20  # a quote shorter than this proves nothing: it would be found in almost any text
QUOTE_MAX = 300
ROOM_MIN, ROOM_MAX = 2_000, 32_000  # tokens of answer; the retry may double the room, up to this
RETRY_MAX = 64_000

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "published_book": {"type": "boolean"},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "instead_of": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["text", "instead_of", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["published_book", "rules"],
    "additionalProperties": False,
}

SYSTEM = (
    "You read a Dungeons & Dragons table's own house rules, from a file the Dungeon Master wrote "
    "or kept, and list them.\n"
    "A house rule is a rule this table made up or changed. List only those. Do not list the "
    "game's official rules, lore, story notes, character sheets or schedules.\n"
    "For each rule give: `text`, the rule in one or two plain sentences in the file's own words "
    "(do not add anything the file does not say); `instead_of`, the official rule it replaces, "
    "only if the file says so, else an empty string; and `quote`, a short passage copied "
    "exactly, word for word, from the file where the rule is stated (at least twenty characters, "
    "at most two sentences). A rule whose words you cannot quote exactly is not a rule to list.\n"
    "If the file is a published rulebook or adventure (long, with page numbers and chapters) and "
    "not one table's own house rules, set `published_book` to true and list nothing.\n"
    "If the file has no house rules, list nothing. Reply with JSON only."
)

NOT_READ = "I couldn't read that file just now. Try again in a minute."


def room_for(chars: int) -> int:
    """Tokens of answer room for this much text: the rules and their quotes are about as long as
    the text again, at roughly 4 characters a token; never less than a small answer."""
    return max(ROOM_MIN, min(ROOM_MAX, chars // 2 + 1_000))


def norm(text: str) -> str:
    return " ".join(text.casefold().split())


@dataclass(frozen=True, slots=True)
class Candidate:
    text: str
    instead_of: str | None


@dataclass(frozen=True, slots=True)
class Found:
    rules: tuple[Candidate, ...] = ()
    published_book: bool = False
    asked: int = 0  # how many the AI listed
    unquoted: int = 0  # dropped: the quote is not in the file
    too_long: int = 0  # dropped: over 500 characters
    repeats: int = 0  # dropped: already a rule here, or listed twice
    over_cap: int = 0  # dropped: past 200
    unread: bool = False  # no usable answer: nothing is offered, and the DM is told


def keep(items: Sequence[object], source: str, existing: Sequence[tuple[str, str | None]]) -> Found:
    """The rules to offer from what the AI listed. `existing`: (words, instead-of) of the
    campaign's rules."""
    haystack = norm(source)
    seen = {(norm(w), norm(i) if i else None) for w, i in existing}
    room = max(0, HOUSE_RULES_MAX - len(existing))  # a campaign holds at most 200 in all
    kept: list[Candidate] = []
    unquoted = too_long = repeats = over = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        instead = " ".join(str(item.get("instead_of") or "").split()) or None
        quote = norm(str(item.get("quote") or ""))
        if not text:
            continue
        if len(quote) < MIN_QUOTE or len(quote) > QUOTE_MAX or quote not in haystack:
            unquoted += 1
            continue
        if len(text) > RULE_MAX or (instead is not None and len(instead) > RULE_MAX):
            too_long += 1
            continue
        key = (norm(text), norm(instead) if instead else None)
        if key in seen:
            repeats += 1
            continue
        if len(kept) >= room:
            over += 1
            continue
        seen.add(key)
        kept.append(Candidate(text, instead))
    return Found(tuple(kept), False, len(items), unquoted, too_long, repeats, over)


def parse(raw: str) -> tuple[bool, list[object]] | None:
    """(published book?, the listed rules), or None for an answer that is not the asked shape."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        return None
    return bool(data.get("published_book")), list(data["rules"])


async def read(complete: Complete, text: str, existing: Sequence[tuple[str, str | None]]) -> Found:
    """One AI call over the text (a cut answer asked for again once with twice the room, as the
    recap steps do). Never raises: a failing or unreadable answer is `unread`."""
    room = room_for(len(text))
    for attempt in range(2):
        try:
            async with asyncio.timeout(TIMEOUT_S):
                reply = await complete(SYSTEM, text, max_tokens=room, schema=SCHEMA)
        except Exception as exc:  # the service, the network, a timeout: the type only
            log.warning("House rules from a file: the AI call failed: %s", type(exc).__name__)
            return Found(unread=True)
        if reply.refused:
            log.info("House rules from a file: the AI declined")
            return Found(unread=True)
        if reply.cut and attempt == 0:
            room = min(room * 2, RETRY_MAX)
            log.info("House rules from a file: the answer was cut off, asking again")
            continue
        if reply.cut:
            return Found(unread=True)
        answer = parse(reply.text)
        if answer is None:
            log.info("House rules from a file: the answer was not in the asked shape")
            return Found(unread=True)
        book, listed = answer
        if book:
            return Found(published_book=True, asked=len(listed))
        found = keep(listed, text, existing)
        log.info(
            "House rules from a file: %d listed, %d kept (%d unquoted, %d too long, %d repeats)",
            found.asked,
            len(found.rules),
            found.unquoted,
            found.too_long,
            found.repeats,
        )
        return found
    return Found(unread=True)
