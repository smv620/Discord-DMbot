"""The part of "read my sheet document" that needs no Discord interaction (#1125, #2849): open the
public link, take the text out, ask the AI once, and keep the server's one-at-a-time rule and
daily count of reads. Discord's panel (`ui.sheet_import`) and the website's queue
(`memory.web_sheets`) both call this, so a read costs and is limited the same way from either.

Nothing of the document is kept or logged here: the bytes and the text live only in the calls.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from dmbot import fetch
from dmbot.memory import sheet_import
from dmbot.memory.document_reader import READING, read_document

READS_PER_DAY = 20  # a server's AI reads of documents a day: each one costs money
TIME_LIMIT_S = 240  # a read that takes longer than this is given up

BUSY, DAILY = "busy", "daily"  # why a read may not start

# One read at a time for a server, and the day's count. In memory only: a restart forgets them,
# which is the safe way round for a cap (and a day's reads are few).
_busy: set[int] = set()
_reads: dict[tuple[int, str], int] = {}


class TooBig(Exception):
    """The document is over `sheet_import.FILE_MAX_BYTES`."""


def today() -> str:
    return datetime.fromtimestamp(time.time(), UTC).strftime("%Y-%m-%d")


def refusal(guild_id: int, day: str) -> str | None:
    """`BUSY` or `DAILY` when a read for this server may not start now, else None."""
    if guild_id in _busy:
        return BUSY
    if _reads.get((guild_id, day), 0) >= READS_PER_DAY:
        return DAILY
    return None


def claim(guild_id: int, day: str) -> None:
    """Start a read: the server is busy and the day's count goes up. Called with no await since
    `refusal`, so two reads for one server can't both pass. Only today's counts are kept."""
    _busy.add(guild_id)
    for old in [k for k in _reads if k[1] != day]:
        del _reads[old]
    _reads[(guild_id, day)] = _reads.get((guild_id, day), 0) + 1


def release(guild_id: int) -> None:
    _busy.discard(guild_id)


def give_back(guild_id: int, day: str) -> None:
    """A read that never reached the AI doesn't count against the day. (Another server's read
    may already have cleared yesterday's key just after midnight.)"""
    key = (guild_id, day)
    _reads[key] = max(0, _reads.get(key, 0) - 1)


async def link_text(link: str) -> str:
    """The text of a public document link. `fetch.fetch` does every network check (https only,
    public addresses re-checked after each redirect, size and time caps). Raises `fetch.LinkError`,
    `TooBig` or `DocumentError`."""
    got = await fetch.fetch(link)
    if len(got.data) > sheet_import.FILE_MAX_BYTES:
        raise TooBig
    async with READING:  # the same limit as the names screen
        return await read_document(got.filename, got.data)


async def read_snapshot(ai: Any, text: str) -> tuple[dict[str, Any] | None, bool]:
    """(the checked snapshot or None when nothing was found, whether the text was cut short).
    Raises `AIError` or `TimeoutError`."""
    shortened = len(text) > sheet_import.TEXT_MAX
    async with asyncio.timeout(TIME_LIMIT_S):
        return await sheet_import.read_character(ai, text), shortened
