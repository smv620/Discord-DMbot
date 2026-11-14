"""A story language's row (#4229): the DM's no on one (#4362), shared by the character's ✖ and
World notes' 🚫, so both mark it the same way and no later read gives it again."""

from __future__ import annotations

from dmbot.memory._changes import STORY_LANGUAGES, Changes
from dmbot.memory.models import LANGUAGE_NOT, MemoryRuleError

GONE = "That language is gone already."


async def say_not(w: Changes, language_id: str) -> None:
    """The DM's no on a story language, in the caller's write (one Undo): the row stays, marked
    `given_by = 'not'`, and every reader skips it. MemoryRuleError when it is gone or already a
    no."""
    row = await w.get(STORY_LANGUAGES, language_id)
    if row is None or row["given_by"] == LANGUAGE_NOT:
        raise MemoryRuleError(GONE)
    await w.update(STORY_LANGUAGES, language_id, {"given_by": LANGUAGE_NOT})
