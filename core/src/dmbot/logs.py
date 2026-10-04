"""Logging setup: plain text for local runs, one JSON object per line for servers.

Every record can carry `shard_id`, `guild_id` and `campaign_id`, so logs from many pods
can be filtered by server or campaign. Set them for a block of work with
`log_context(guild_id=..., campaign_id=...)`; the shard is worked out from the server.

Privacy: logs hold IDs only. Never log player names, display names, message or
transcript text, tokens, or keys (CLAUDE.md).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from dmbot.sharding import ShardSettings

LOG_FORMATS = ("text", "json")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

_guild: ContextVar[int | None] = ContextVar("dmbot_guild_id", default=None)
_campaign: ContextVar[str | None] = ContextVar("dmbot_campaign_id", default=None)

# Fields a caller may also pass with `extra={...}`.
CONTEXT_FIELDS = ("shard_id", "guild_id", "campaign_id")


@contextmanager
def log_context(*, guild_id: int | None = None, campaign_id: str | None = None) -> Iterator[None]:
    """Tag every log record in this block (and tasks started in it) with these IDs."""
    guild_token = _guild.set(guild_id) if guild_id is not None else None
    campaign_token = _campaign.set(campaign_id) if campaign_id is not None else None
    try:
        yield
    finally:
        if campaign_token is not None:
            _campaign.reset(campaign_token)
        if guild_token is not None:
            _guild.reset(guild_token)


def set_log_context(*, guild_id: int | None = None, campaign_id: str | None = None) -> None:
    """Tag the rest of the current task with these IDs.

    Only for tasks that handle exactly one thing, such as one Discord interaction. In a
    long-lived task (a connection, a loop) use `log_context` instead, or the IDs would
    stick to everything that task logs afterwards.
    """
    if guild_id is not None:
        _guild.set(guild_id)
    if campaign_id is not None:
        _campaign.set(campaign_id)


class ContextFilter(logging.Filter):
    """Adds shard_id / guild_id / campaign_id to every record (None if unknown)."""

    def __init__(self, shards: ShardSettings) -> None:
        super().__init__()
        self._shards = shards

    def filter(self, record: logging.LogRecord) -> bool:
        guild = getattr(record, "guild_id", None)
        if guild is None:
            guild = _guild.get()
            record.guild_id = guild
        if getattr(record, "campaign_id", None) is None:
            record.campaign_id = _campaign.get()
        if getattr(record, "shard_id", None) is None:
            if isinstance(guild, int):
                record.shard_id = self._shards.shard_of(guild)
            elif len(self._shards.ids) == 1:
                record.shard_id = self._shards.ids[0]
            else:
                record.shard_id = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in CONTEXT_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                # Discord IDs exceed JavaScript's safe integer range; keep them as text.
                entry[name] = str(value) if name == "guild_id" else value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s%(ctx)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            f"{label}={value}"
            for label, value in (
                ("shard", getattr(record, "shard_id", None)),
                ("guild", getattr(record, "guild_id", None)),
                ("campaign", getattr(record, "campaign_id", None)),
            )
            if value is not None
        ]
        record.ctx = f" [{' '.join(parts)}]" if parts else ""
        return super().format(record)


# discord.py logs raw gateway events at DEBUG, which include usernames and what people
# type into commands and forms. Never let LOG_LEVEL turn that on.
LIBRARY_MIN_LEVEL = {"discord": logging.INFO}


def configure_logging(fmt: str, level: str, shards: ShardSettings) -> None:
    """Replace the root handlers with one stderr handler in the chosen format.

    Safe to call again: the previous handlers are replaced, not added to.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(ContextFilter(shards))
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)
    for name, minimum in LIBRARY_MIN_LEVEL.items():
        logging.getLogger(name).setLevel(max(minimum, logging.getLevelName(level)))
