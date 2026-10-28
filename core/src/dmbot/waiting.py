"""A waiting message that keeps itself current while a slow job runs (#2559, #2564).

A long read used to show one line and then nothing, so a DM could not tell "working" from
"stuck". `WaitingMessage` edits that one message in place and answers both questions:

- what step it is on, "n of m" where that is known, and, after about 30 seconds, how long it has
  been since the DM clicked;
- when it last moved forward ("last change 2 min ago", once that is over a minute), and, past the
  stage's limit, a plain line that says nothing has changed and what DMbot will do next.

It edits at most once every 10 seconds (Discord rate-limits message edits, and a faster counter is
only noise), and only when the text changed. A step, a count or `moved()` (a pass that went round
again) all count as movement. The caller ends it by editing the same message with the result or a
plain failure, after leaving the `async with`, so the last word is never overwritten. When Discord
says the message is gone (the DM dismissed it, or its token ran out) the edits stop.

It carries words and counts only. Nothing from the document being read can reach the message
because the caller passes fixed strings and numbers. Every wait that edits a message uses this
class, so they all follow the same rules ("Every wait shows that DMbot is still working").
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any

import discord

log = logging.getLogger(__name__)

EDIT_EVERY_S = 10.0  # the most often the message is edited
SHOW_ELAPSED_AFTER_S = 30.0  # "so far" appears once the wait is long enough to wonder
SHOW_LAST_CHANGE_AFTER_S = 60.0  # "last change N ago" appears once nothing moved for this long


def message_is_gone(exc: BaseException) -> bool:
    """The DM dismissed the message, or its 15 minutes ran out: it cannot be edited any more."""
    return isinstance(exc, discord.NotFound) or (
        isinstance(exc, discord.HTTPException) and exc.code == 50027  # invalid webhook token
    )


def elapsed_text(seconds: float) -> str:
    """'45 s' under a minute, then whole minutes with the seconds left over: '2 min 5 s'."""
    whole = int(seconds)
    if whole < 60:
        return f"{whole} s"
    minutes, rest = divmod(whole, 60)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


def ago_text(seconds: float) -> str:
    """'2 min' (whole minutes, at least one): the message is only edited every 10 seconds."""
    return f"{max(1, int(seconds) // 60)} min"


class WaitingMessage:
    def __init__(
        self,
        edit: Callable[[str], Awaitable[None]],
        title: str,
        *,
        stall_after_s: float | None = None,
        stops_at_s: float | None = None,
        gone: Callable[[BaseException], bool] = lambda _exc: False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        started: float | None = None,
        every: float = EDIT_EVERY_S,
        show_after: float = SHOW_ELAPSED_AFTER_S,
    ) -> None:
        """`stall_after_s`: no movement for this long is named as a stall. `stops_at_s`: when the
        job stops by itself (seconds since `started`), said in the stall line. `gone(exc)`: True
        for an error that means the message cannot be edited any more. `started`: the clock
        reading when the DM clicked, if that was earlier than this object."""
        self._edit = edit
        self._title = title
        self._stall_after = stall_after_s
        self._stops_at = stops_at_s
        self._gone = gone
        self._clock = clock
        self._sleep = sleep
        self._every = every
        self._show_after = show_after
        self._started = clock() if started is None else started
        self._moved = self._started
        self._step = ""
        self._done = 0
        self._total = 0
        self._sent = ""
        self._over = False
        self._keeps = True
        self._task: asyncio.Task[None] | None = None

    def step(self, words: str, done: int = 0, total: int = 0, *, keeps: bool = True) -> None:
        """Where the job is now. `done` of `total` is shown only when `total` is known. Anything
        that differs from the last call counts as movement. `keeps`: whether stopping now would
        keep what the job has (a stage that has produced nothing yet says that nothing is added)."""
        if (words, done, total) != (self._step, self._done, self._total):
            self._moved = self._clock()
        self._step, self._done, self._total = words, done, total
        self._keeps = keeps

    def moved(self) -> None:
        """The job did something that is not a new step (a call went round again)."""
        self._moved = self._clock()

    def text(self) -> str:
        now = self._clock()
        line = self._step
        if self._total > 0:
            line = f"{line} ({min(self._done, self._total)} of {self._total})"
        if now - self._started >= self._show_after:
            line = f"{line}, {elapsed_text(now - self._started)} so far"
        still = now - self._moved
        notes = []
        if self._stall_after is not None and still >= self._stall_after:
            stall = f"No change for {ago_text(still)}."
            if self._stops_at is not None:
                stop = elapsed_text(self._stops_at)
                kept = "keep what it has" if self._keeps else "add nothing"
                stall += f" DMbot will stop by itself at {stop} and {kept}."
            notes.append(stall)
        elif still >= SHOW_LAST_CHANGE_AFTER_S:
            notes.append(f"Last change {ago_text(still)} ago.")
        body = "\n".join(part for part in (line, *notes) if part)
        return f"{self._title}\n{body}" if body else self._title

    async def _refresh(self) -> None:
        text = self.text()
        if text == self._sent:
            return
        await self._edit(text)
        self._sent = text  # after the edit lands, so a failed one is tried again

    async def _keep_current(self) -> None:
        while not self._over:
            await self._sleep(self._every)
            try:
                await self._refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # an edit that fails (Discord hiccup) must not end the read
                if self._gone(exc):
                    self._over = True  # nobody is looking at it: no more edits, no traceback
                    log.info("The waiting message is gone; no more edits")
                else:
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
            await asyncio.gather(self._task, return_exceptions=True)


EDIT_FOR_S = (
    14 * 60.0
)  # a card edits itself this long: Discord's interaction token lives 15 minutes
STOPPED_UPDATING = "This card has stopped updating by itself; press 🔄 Refresh to keep watching."


async def keep_card_current(
    render: Callable[[], Awaitable[tuple[str, Any]]],
    edit: Callable[[str, Any], Awaitable[None]],
    still_going: Callable[[], bool],
    *,
    current: Callable[[], bool] = lambda: True,
    every: float = EDIT_EVERY_S,
    limit_s: float = EDIT_FOR_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    gone: Callable[[BaseException], bool] = message_is_gone,
) -> None:
    """Keep a card that shows a long job current while the job runs (#2564): redraw it every
    `every` seconds (only editing when the text changed), until the job ends (one last draw), until
    `limit_s` (the card then ends on `STOPPED_UPDATING`, because its token is about to run out and
    a stopped card must say so, not look frozen), or until a newer card took over (`current()` is
    False: say nothing) or the message is gone. A draw or an edit that fails for another reason is
    logged and tried again at the next tick. The caller draws the card once before starting this."""
    started = clock()
    last = ""
    while True:
        await sleep(every)
        if not current():
            return
        ended = False
        try:
            text, view = await render()
            ended = not still_going()
            late = clock() - started >= limit_s
            if late and not ended:
                text = f"{text}\n\n{STOPPED_UPDATING}"
            if text != last:
                await edit(text, view)
                last = text
            if ended or late:
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if gone(exc):
                log.info("The card is gone; no more edits")
                return
            log.warning("Could not update the card", exc_info=True)
            if clock() - started >= limit_s:
                return
