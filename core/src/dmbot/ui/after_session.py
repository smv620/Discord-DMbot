"""The one message that shows a live session's after-session reads while they run (#5124, CLAUDE.md
"Every wait shows that DMbot is still working"): the story read, the names step and the story
threads take minutes of AI calls and used to show nothing. It is a single message in the DM screen,
edited in place by `WaitingMessage` (stage, part n of m, how long it has run, when it last moved,
and a plain line once a stage stops moving), and ended with what was not read.

It carries stages, counts and fixed words only: never a line of the transcript, a name or a
question. It is opened by the first slow read that has something to say, so a session with every
read switched off posts nothing, and it never changes what a read does: a card that cannot be
posted or edited is only logged.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from dmbot.waiting import WaitingMessage, message_is_gone

log = logging.getLogger(__name__)

TITLE = "📖 DMbot is reading the session…"
STALL_AFTER_S = 10 * 60  # one batch is one AI call (minutes at most): longer is said out loud
STALL_NOTE = "DMbot goes on to the next step by itself; nothing is lost."
DONE = "Finished reading the session."

STORY = "Reading the story"
NAMES = "Reading names"
THREADS = "Reading story threads"


def not_read(stretches: int) -> str:
    """A stretch a read could not do is counted, never shown as "nothing found"."""
    return f"{stretches} stretch{'' if stretches == 1 else 'es'} of the names could not be read."


class AfterSessionCard:
    """`post(text)` sends the opening message and returns an object with `edit(content=...)`, or
    None when it could not be posted (then nothing here does anything)."""

    def __init__(self, post: Callable[[str], Awaitable[Any | None]]) -> None:
        self._post = post
        self._message: Any | None = None
        self._waiting: WaitingMessage | None = None
        self._tried = False
        self._notes: list[str] = []

    async def _edit(self, text: str) -> None:
        if self._message is not None:
            await self._message.edit(content=text)

    async def stage(self, words: str, done: int = 0, total: int = 0) -> None:
        """Where the reading is now; opens the card the first time. Never raises."""
        try:
            if not self._tried:
                self._tried = True
                self._message = await self._post(TITLE)
                if self._message is not None:
                    self._waiting = WaitingMessage(
                        self._edit,
                        TITLE,
                        stall_after_s=STALL_AFTER_S,
                        stall_note=STALL_NOTE,
                        gone=message_is_gone,
                    )
                    await self._waiting.__aenter__()
            if self._waiting is not None:
                self._waiting.step(words, done, total)
        except Exception as exc:  # the type only: the read goes on without its card
            log.warning("After-session card: could not show a stage (%s)", type(exc).__name__)

    def note(self, text: str) -> None:
        """A fixed sentence for the end of the card (a stretch that was not read)."""
        if text not in self._notes:
            self._notes.append(text)

    async def finish(self) -> None:
        """End the card with the last word. Does nothing if it was never opened."""
        if self._waiting is None:
            return
        try:
            await self._waiting.__aexit__(None, None, None)
            await self._edit("\n".join([f"📖 {DONE}", *self._notes]))
        except Exception as exc:
            log.warning("After-session card: could not end it (%s)", type(exc).__name__)
        finally:
            self._waiting = None
