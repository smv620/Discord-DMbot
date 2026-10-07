"""Configuration from environment variables (a .env file in the repo root works too)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dmbot.ai import DEFAULT_MODEL
from dmbot.logs import LOG_FORMATS, LOG_LEVELS
from dmbot.sharding import ShardConfigError, ShardSettings, parse_shards
from dmbot.transcription.config import (
    TranscriptionConfigError,
    TranscriptionSettings,
    load_transcription_settings,
)


class ConfigError(ValueError):
    pass


MAX_KEEP_DAYS = 3650  # ten years: plenty, and far from overflowing a timestamp


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str = field(repr=False)
    ears_secret: str = field(repr=False)
    ears_host: str = "127.0.0.1"
    ears_port: int = 8765
    dev_guild_id: int | None = None
    database_url: str = field(default="", repr=False)  # contains the password
    data_dir: Path = Path("data")
    transcription: TranscriptionSettings = field(default_factory=TranscriptionSettings)
    shards: ShardSettings = field(default_factory=ShardSettings)
    log_format: str = "text"
    log_level: str = "INFO"
    # AI text calls (reading a document into a names list). Empty: switched off.
    ai_key: str = field(default="", repr=False)
    ai_model: str = DEFAULT_MODEL
    # How long Undo works on campaign memory; older change-log entries are deleted (#164).
    memory_keep_days: int = 30


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    def get(name: str) -> str:
        return env.get(name, "").strip()

    missing = [n for n in ("DISCORD_TOKEN", "EARS_SHARED_SECRET", "DATABASE_URL") if not get(n)]
    if missing:
        raise ConfigError(
            f"Missing required settings: {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in."
        )

    port_raw = get("EARS_WS_PORT") or "8765"
    if not port_raw.isdigit():
        raise ConfigError(f'EARS_WS_PORT must be a number, got "{port_raw}".')

    guild_raw = get("DISCORD_DEV_GUILD_ID")
    if guild_raw and not guild_raw.isdigit():
        raise ConfigError("DISCORD_DEV_GUILD_ID must be a server ID (digits only).")

    try:
        transcription = load_transcription_settings(env)
    except TranscriptionConfigError as exc:
        raise ConfigError(str(exc)) from exc

    try:
        shards = parse_shards(get("SHARD_COUNT"), get("SHARD_IDS"))
    except ShardConfigError as exc:
        raise ConfigError(str(exc)) from exc

    log_format = (get("LOG_FORMAT") or "text").lower()
    if log_format not in LOG_FORMATS:
        raise ConfigError(f'LOG_FORMAT must be "text" or "json", got "{log_format}".')
    log_level = (get("LOG_LEVEL") or "INFO").upper()
    if log_level not in LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}.")

    keep_raw = get("MEMORY_CHANGELOG_KEEP_DAYS") or "30"
    keep_days = int(keep_raw) if keep_raw.isascii() and keep_raw.isdigit() else 0
    if not 1 <= keep_days <= MAX_KEEP_DAYS:
        raise ConfigError(
            f"MEMORY_CHANGELOG_KEEP_DAYS must be a whole number of days from 1 to "
            f'{MAX_KEEP_DAYS}, got "{keep_raw}".'
        )

    return Settings(
        discord_token=get("DISCORD_TOKEN"),
        ears_secret=get("EARS_SHARED_SECRET"),
        ears_host=get("EARS_WS_HOST") or "127.0.0.1",
        ears_port=int(port_raw),
        dev_guild_id=int(guild_raw) if guild_raw else None,
        database_url=get("DATABASE_URL"),
        data_dir=Path(get("DMBOT_DATA_DIR") or "data"),
        transcription=transcription,
        shards=shards,
        log_format=log_format,
        log_level=log_level,
        ai_key=get("ANTHROPIC_API_KEY"),
        ai_model=get("AI_MODEL") or DEFAULT_MODEL,
        memory_keep_days=keep_days,
    )
