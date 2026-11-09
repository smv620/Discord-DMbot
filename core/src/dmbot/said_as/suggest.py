"""DMbot's suggested spelling for how the narrator should say a name (#3806, #2316).

One fast-tier call with the name and its kind only, shared by the website's 💡 Suggest and the
Discord name card's, so both ask the same question and hold the answer to the same check. The
suggestion is never saved here: the DM takes it, changes it or ignores it.

A cut or failed call raises `SuggestFailed` (never read as "no suggestion", the AI-limits rule);
a refused answer or one that isn't a plain spelling is "". Logs hold the error type only: a
provider's message may echo the request, which holds the name.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Protocol

from dmbot.ai import Reply
from dmbot.memory.link_words import quoted

log = logging.getLogger(__name__)

SAID_AS_MAX = 80  # the same limit as MemoryStore.set_said_as and the database's check
# Letters, then more letters joined by a hyphen, a space or an apostrophe ("Kah-zeth", "Ka'zeth").
# The answer is only ever shown to the DM, but a sentence, a number or a refusal is not a spelling.
SPELLING = re.compile(r"[^\W\d_]+(?:[-' ’][^\W\d_]+)*")
SYSTEM = (
    "You help a Dungeon Master tell a text-to-speech narrator how to say a name from a fantasy "
    "story. Reply with only the name respelled the way it sounds, in plain letters, with hyphens "
    "between the parts to say (for example Ka'zeth -> Kah-zeth). No quotes, no explanation. If the "
    "name is already easy to say, reply with the name unchanged."
)


class SuggestFailed(Exception):
    """The AI did not answer or was cut off: say so, never "no suggestion"."""


class Asker(Protocol):
    async def complete(self, system: str, text: str, *, max_tokens: int = ...) -> Reply: ...


async def suggest(ai: Asker, name: str, kind: str) -> str:
    """The suggested spelling, or "" when there is none to give."""
    one_line = " ".join(name.split())  # so a name can't forge the next line of the question
    text = f"Name: {quoted(one_line)}\nKind: {kind}"
    try:
        reply = await ai.complete(SYSTEM, text, max_tokens=200)
    except Exception as err:
        log.warning("said-as suggestion failed: %s", type(err).__name__)
        raise SuggestFailed from None
    if reply.cut:
        log.warning("said-as suggestion was cut off")
        raise SuggestFailed
    answer = unicodedata.normalize("NFC", " ".join(reply.text.split()))
    if reply.refused or len(answer) > SAID_AS_MAX or not SPELLING.fullmatch(answer):
        return ""
    return answer
