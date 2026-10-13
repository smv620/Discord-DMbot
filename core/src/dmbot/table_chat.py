"""Typed chat at the table (#1118; docs/PLAN.md, "Typed chat at the table"): which typed
messages DMbot reads. Pure rules, so they are tested without Discord.

DMbot reads the table voice channel's own text chat and at most one more text channel the DM
picked, only while it is listening, only from people who agreed to be recorded, never a bot,
a webhook or a slash command. Anything else is ignored before any work is done.
"""

from __future__ import annotations

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
