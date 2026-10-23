"""The words around a name a DM's own text suggested, for the name card (#2561).

The reading keeps only a short quote beside each name (`Entity.description`): the DM's text is
read once and never stored (docs/PLAN.md). That quote is often too short to tell what a name is,
so while the text is still in memory right after the reading, the sentence around each name (up to
`CONTEXT_MAX` characters) is cut out and held in memory only, for the review that follows. It is
never written to the database, a log or a backup, it is lost at a restart (the card then shows the
short quote as before), it goes after `HOLD_S`, and it is shown only to the DM who reviews the
names. Pure apart from the clock.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

CONTEXT_MAX = 300  # the whole sentence, up to about this many characters
HOLD_S = 6 * 3600  # the review of a long list can take an evening
HOLD_MAX = 600  # sentences held at once, across servers
_ENDS = ".!?"


def around(text: str, quote: str, name: str, limit: int = CONTEXT_MAX) -> str:
    """The sentence of `text` that holds the quote (else the name), up to `limit` characters, cut
    around the match when the sentence is longer. A heading or list item has no sentence end, so
    its own line is the sentence. "" when neither is found: the card keeps the short quote."""
    at = -1
    hit = ""
    for needle in (quote, name):
        needle = needle.strip()
        if not needle:
            continue
        found = text.find(needle)
        if found < 0:
            match = re.search(re.escape(needle), text, re.IGNORECASE)
            found = match.start() if match else -1
        if found >= 0:
            at, hit = found, needle
            break
    if at < 0:
        return ""
    # The line is the outer bound (a list item or heading); a sentence end narrows it.
    line_start = text.rfind("\n", 0, at) + 1
    line_end = text.find("\n", at + len(hit))
    line_end = len(text) if line_end < 0 else line_end
    start = line_start
    for i in range(at - 1, line_start - 1, -1):
        if text[i] in _ENDS and i + 1 < len(text) and text[i + 1].isspace():
            start = i + 1
            break
    end = line_end
    for i in range(at + len(hit), line_end):
        if text[i] in _ENDS and (i + 1 >= len(text) or text[i + 1].isspace()):
            end = i + 1
            break
    sentence = " ".join(text[start:end].split())
    if len(sentence) <= limit:
        return sentence
    # Longer than the card allows: keep the match in the middle of what is shown.
    shown = " ".join(hit.split())
    centre = sentence.lower().find(shown.lower())
    centre = 0 if centre < 0 else centre + len(shown) // 2
    lo = max(0, min(centre - limit // 2, len(sentence) - limit))
    piece = sentence[lo : lo + limit].strip()
    return ("…" if lo > 0 else "") + piece + ("…" if lo + limit < len(sentence) else "")


class Held:
    """Sentences held in memory for the names of one read, keyed by server and name ID."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._now = clock
        self._items: dict[tuple[int, str], tuple[float, str]] = {}

    def put(self, guild_id: int, entity_id: str, sentence: str) -> None:
        if not sentence:
            return
        self._drop_old()
        while len(self._items) >= HOLD_MAX:  # the oldest goes first
            self._items.pop(next(iter(self._items)))
        self._items[(guild_id, entity_id)] = (self._now() + HOLD_S, sentence)

    def get(self, guild_id: int, entity_id: str) -> str:
        found = self._items.get((guild_id, entity_id))
        if found is None or found[0] <= self._now():
            self._items.pop((guild_id, entity_id), None)
            return ""
        return found[1]

    def forget(self, guild_id: int, entity_id: str) -> None:
        """The name was decided: its sentence is not needed any more."""
        self._items.pop((guild_id, entity_id), None)

    def _drop_old(self) -> None:
        now = self._now()
        for key in [k for k, (until, _) in self._items.items() if until <= now]:
            del self._items[key]
