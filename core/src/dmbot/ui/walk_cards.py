"""One-by-one cards for the kinds of check that "✅ Check everything" walks through after the first
four (#4192, slice 2): a card for the next waiting item with its own buttons, a progress line, Later
and Stop for now, and on to the next part when none is left.

A kind brings only what is its own: how to list what waits, how to word one card, and what each
button does (the same store call its own persistent button makes, so an answer here is the same
answer as there and nothing is asked twice). Nothing is kept about where the DM got to: each card
re-reads what waits. Counts and the kind's own plain words only; logs carry counts, never words.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import discord

from dmbot.memory.models import MemoryRuleError
from dmbot.ui.check_everything import add_stop, next_part, progress
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _send, _tell
from dmbot.walk import Walk

log = logging.getLogger(__name__)

GONE = "That one was answered meanwhile. On to the next."
LATER_DONE = "⏳ Left for later. DMbot will ask again next time."
FAILED = "Couldn't do that. Try again in a minute."


@dataclass(frozen=True, slots=True)
class Answer:
    """What a button did: the words to show, and an Undo button for it if the kind has one."""

    text: str
    undo: discord.ui.Item[Any] | None = None


@dataclass(frozen=True, slots=True)
class Choice:
    """One button of a card: its label and style, what it does with the shown item (it may raise
    `MemoryRuleError` when the item is gone), and whether it only leaves the item for later."""

    label: str
    style: discord.ButtonStyle
    do: Callable[[Any], Awaitable[Answer]]
    later: bool = False


@dataclass(frozen=True, slots=True)
class Kind:
    """A kind's side of the walk: what waits (in the order to show), how an item's id is told
    apart, the card's words, its buttons, and the line when the part is finished."""

    waiting: Callable[[], Awaitable[list[Any]]]
    key: Callable[[Any], str]
    text: Callable[[Any, int], str]  # (the item, how many wait including it)
    choices: Callable[[], list[Choice]]
    finished: str


class WalkCards(_Menu):
    """The card of one item at a time, for one kind inside a walk."""

    def __init__(self, kind: Kind, walk: Walk) -> None:
        super().__init__()
        self.kind, self.walk = kind, walk
        self.total = 0  # how many waited when this part began, for "4 of 11"
        self.later: set[str] = set()  # left for another time this round
        self.shown: Any = None

    async def current_text(self) -> str | None:
        """The next waiting item with its buttons set, or None when none is left."""
        waiting = [i for i in await self.kind.waiting() if self.kind.key(i) not in self.later]
        self.clear_items()
        if not waiting:
            self.shown = None
            return None
        self.shown = waiting[0]
        self.total = max(self.total, len(waiting))
        for choice in self.kind.choices():
            self.add_item(
                _Button(self._press(choice), label=choice.label, style=choice.style, row=0)
            )
        add_stop(self, self.walk, row=1)
        done = self.total - len(waiting) + 1
        return progress(self.walk, done, self.total) + self.kind.text(self.shown, len(waiting))

    def _press(self, choice: Choice) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def handler(interaction: discord.Interaction) -> None:
            await self._answer(interaction, choice)

        return handler

    async def _answer(self, interaction: discord.Interaction, choice: Choice) -> None:
        item = self.shown
        if item is None:
            return  # (a second press while the first is still being answered)
        # An answer can be slow (moving many connections, reading what waits): answer Discord first
        # (it gives up after 3 seconds, #537), and let a second press find nothing to answer.
        await _answer_first(interaction, in_place=True)
        self.shown = None
        if choice.later:
            self.later.add(self.kind.key(item))
            await self._next(interaction, LATER_DONE, None)
            return
        try:
            answer = await choice.do(item)
        except MemoryRuleError as exc:
            # (usually "answered meanwhile"; any other rule says why, in the store's own words)
            await self._next(interaction, str(exc) or GONE, None)
            return
        except Exception as exc:  # (the database was unreachable, say): the card must not hang
            log.warning("Check everything: an answer could not be saved (%s)", type(exc).__name__)
            self.shown = item  # (still waiting: the DM may press again)
            await _tell(interaction, FAILED)
            return
        await self._next(interaction, answer.text, answer.undo)

    async def _next(
        self, interaction: discord.Interaction, done: str, undo: discord.ui.Item[Any] | None
    ) -> None:
        text = await self.current_text()
        # An Undo needs a message of its own: the card moves on to the next item at once.
        undo_view: discord.ui.View | None = None
        if undo is not None:
            undo_view = discord.ui.View(timeout=None)
            undo_view.add_item(undo)
        head = "" if undo_view is not None else done
        if text is None:
            self.stop()
            await _replace(interaction, f"{head}\n\n{self.kind.finished}".strip(), None)
        else:
            await _replace(interaction, f"{head}\n\n{text}".strip(), self)
        if undo_view is not None:
            await _tell(interaction, done, view=undo_view)
        if text is None:
            await next_part(interaction, self.walk)


async def start(interaction: discord.Interaction, kind: Kind, walk: Walk) -> None:
    """Open the first card of a part, or go straight on when nothing waits any more."""
    view = WalkCards(kind, walk)
    text = await view.current_text()
    if text is None:
        await next_part(interaction, walk)
        return
    await _send(interaction, text, view)
