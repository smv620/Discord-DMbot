"""A waiting message that keeps itself current while a slow job runs (#2559, #2564).

A long read used to show one line and then nothing, so a DM could not tell "working" from
"stuck". `WaitingMessage` edits that one message in place: the step it is on, "part n of m"
where that is known, and, after about 30 seconds, how long it has been. It edits at most once
every 10 seconds (Discord rate-limits message edits, and a faster counter is only noise), and
only when the text changed. The caller ends it by editing the same message with the result or a
plain failure, after leaving the `async with`, so the last word is never overwritten.

It carries words and counts only. Nothing from the document being read can reach the message
because the caller passes fixed strings and numbers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from types import TracebackType

log = logging.getLogger(__name__)

EDIT_EVERY_S = 10.0  # the most often the message is edited
SHOW_ELAPSED_AFTER_S = 30.0  # "so far" appears once the wait is long enough to wonder


def elapsed_text(seconds: float) -> str:
    """'45 s' under a minute, then whole minutes with the seconds left over: '2 min 5 s'."""
    whole = int(seconds)
    if whole < 60:
        return f"{whole} s"
    minutes, rest = divmod(whole, 60)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


class WaitingMessage:
    def __init__(
        self,
        edit: Callable[[str], Awaitable[None]],
        title: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        every: float = EDIT_EVERY_S,
        show_after: float = SHOW_ELAPSED_AFTER_S,
    ) -> None:
        self._edit = edit
        self._title = title
        self._clock = clock
        self._every = every
        self._show_after = show_after
        self._started = clock()
        self._step = ""
        self._done = 0
        self._total = 0
        self._sent = ""
        self._task: asyncio.Task[None] | None = None

    def step(self, words: str, done: int = 0, total: int = 0) -> None:
        """Where the job is now. `done` of `total` is shown only when `total` is known."""
        self._step, self._done, self._total = words, done, total

    def text(self) -> str:
        line = self._step
        if self._total > 0:
            line = f"{line} ({min(self._done, self._total)} of {self._total})"
        passed = self._clock() - self._started
        if passed >= self._show_after:
            line = f"{line}, {elapsed_text(passed)} so far"
        return f"{self._title}\n{line}" if line else self._title

    async def _refresh(self) -> None:
        text = self.text()
        if text == self._sent:
            return
        await self._edit(text)
        self._sent = text  # after the edit lands, so a failed one is tried again

    async def _keep_current(self) -> None:
        while True:
            await asyncio.sleep(self._every)
            try:
                await self._refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # an edit that fails (Discord hiccup) must not end the read
                log.warning("Could not update the waiting message", exc_info=True)

    async def __aenter__(self) -> WaitingMessage:
        self._sent = self.text()  # the caller has already shown the opening message
        self._task = asyncio.ensure_future(self._keep_current())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
