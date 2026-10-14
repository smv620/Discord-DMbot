"""Typed chat at the table (#1118; docs/PLAN.md, "Typed chat at the table"): which typed
messages DMbot reads. Pure rules, so they are tested without Discord.

DMbot reads the table voice channel's own text chat and at most one more text channel the DM
picked, only while it is listening, only from people who agreed to be recorded, never a bot,
a webhook or a slash command. Anything else is ignored before any work is done.
"""

from __future__ import annotations

from collections import deque

import discord

MAX_CHARS = 1000  # a pasted wall of text isn't something said at the table
# Only plain messages and replies are typed talk; joins, pins, slash commands and the rest
# are not.
READ_TYPES = frozenset({discord.MessageType.default, discord.MessageType.reply})


def is_table_chat(channel_id: int, voice_channel_id: int, extra_channel_id: int | None) -> bool:
    """The table's voice channel (its own text chat) or the DM's one extra channel."""
    return channel_id == voice_channel_id or (
        extra_channel_id is not None and channel_id == extra_channel_id
    )


def clean(content: str) -> str | None:
    """The message as one line of talk, or None if there is nothing to read (empty, only a
    file, or too long)."""
    text = " ".join(content.split())
    if not text or len(text) > MAX_CHARS:
        return None
    return text


TYPED_PER_MINUTE = 20  # lines one person can send to the listeners in a minute (#1154)
CLOCK_WAITING_MAX = 8  # clock-phrase checks waiting at once for one campaign (#1154)
WINDOW_S = 60.0


class TypedRate:
    """How fast each person types into the listeners (#1154). Discord's own limit keeps chat
    sane, but nothing capped it on DMbot's side, and every typed line costs a rules look-up,
    a clock check and a "Hey DMbot" check. One per table (one campaign), so nothing is
    shared between campaigns. A line over the limit still goes to the raw transcript; it just
    skips the listeners. Pure, so it is tested without Discord."""

    def __init__(self) -> None:
        self._sent: dict[int, deque[float]] = {}
        self._last_logged = -WINDOW_S

    def allow(self, user_id: int, now: float) -> bool:
        """Count a line and say whether the listeners may see it."""
        sent = self._sent.setdefault(user_id, deque())
        while sent and sent[0] <= now - WINDOW_S:
            sent.popleft()
        if len(sent) >= TYPED_PER_MINUTE:
            return False
        sent.append(now)
        return True

    def should_log(self, now: float) -> bool:
        """True at most once a minute, so a flood is one log line, never one per message."""
        if now - self._last_logged < WINDOW_S:
            return False
        self._last_logged = now
        return True
