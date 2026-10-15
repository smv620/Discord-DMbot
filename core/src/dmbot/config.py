"""Configuration from environment variables (a .env file in the repo root works too)."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dmbot.ai import DEFAULT_MODELS, AIModels
from dmbot.entitlements import parse_free_users
from dmbot.logs import LOG_FORMATS, LOG_LEVELS
from dmbot.mail import MailConfigError, MailSettings, load_mail_settings
from dmbot.memory.continuity import Levels
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
    # The model each AI tier uses (#1006): FAST, CAREFUL and DEEP. `ai_model_notice` is a
    # line to log at start-up when the old setting name was used.
    ai_models: AIModels = DEFAULT_MODELS
    ai_model_notice: str = ""
    # How long Undo works on campaign memory; older change-log entries are deleted (#164).
    memory_keep_days: int = 30
    # The owner's own Discord accounts: free access, no caps (#771). Never logged.
    free_users: frozenset[int] = field(default=frozenset(), repr=False)
    # Plan checks at /dmbot start and the hours warnings (#437 part 2). Off until the
    # website goes live (#498): the meter records either way, and nobody is refused
    # before they can pay. `site_url` is where the refusals send people.
    enforce_plans: bool = False
    site_url: str = ""
    # The DM sidebar's ways in (#935) use the answer engine (#934) only when this is on.
    # Off until the sidebar's 17 test answers have been read (#954).
    sidebar_on: bool = False
    # The story extractor (phase 5a, #1297) reads each session with one AI call per batch of
    # lines. Off, and not shown to customers, until it passes its measurement (#1282); with it
    # off nothing calls the AI and the after-session step has nothing to ask.
    story_extractor_on: bool = False
    # Which model tier reads the transcript, and how many calls one session may make (#1363).
    story_extractor_tier: str = "fast"
    story_extractor_max_calls: int = 480
    # Continuity warnings (#1320): where each level starts and how much confirmed story a
    # campaign needs first. Backend settings, tuned on the golden set; no switch shown to anyone.
    continuity: Levels = field(default_factory=Levels)
    # Test recordings (#1019): servers where people who agreed to it have their voices saved
    # for DMbot's own tests, and where the files go. Empty: nothing is ever saved.
    test_recording_guilds: frozenset[int] = frozenset()
    test_recordings_dir: Path = Path("/var/lib/dmbot/test-recordings")
    # Who is messaged when DMbot's AI account is out of funds (#972): the owner and a backup
    # admin, by Discord user ID. None: not set (the problem is only logged). Never logged.
    admin_primary_id: int | None = field(default=None, repr=False)
    admin_secondary_id: int | None = field(default=None, repr=False)
    # Sending email (#1229). Nothing is sent unless SMTP is set up; offer emails also need a
    # postal address. The link secret signs unsubscribe links.
    mail: MailSettings = field(default_factory=MailSettings)
    link_token_secret: bytes = field(default=b"", repr=False)


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

    try:
        free_users = parse_free_users(get("DMBOT_FREE_USERS"))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    enforce_raw = (get("DMBOT_ENFORCE_PLANS") or "0").lower()
    if enforce_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(
            'DMBOT_ENFORCE_PLANS must be 1 (on) or 0 (off), got "' + enforce_raw + '".'
        )

    continuity = _continuity_levels(get)

    extractor_raw = (get("DMBOT_STORY_EXTRACTOR") or "0").lower()
    if extractor_raw not in ("0", "1", "true", "on", "false", "off"):
        raise ConfigError(
            'DMBOT_STORY_EXTRACTOR must be 1 (on) or 0 (off), got "' + extractor_raw + '".'
        )
    extractor_tier = (get("DMBOT_STORY_EXTRACTOR_TIER") or "fast").lower()
    if extractor_tier not in ("fast", "careful", "deep"):
        raise ConfigError(
            'DMBOT_STORY_EXTRACTOR_TIER must be fast, careful or deep, got "'
            + extractor_tier
            + '".'
        )
    extractor_calls_raw = get("DMBOT_STORY_EXTRACTOR_MAX_CALLS") or "480"
    if not extractor_calls_raw.isdigit() or not 1 <= int(extractor_calls_raw) <= 5000:
        raise ConfigError(
            'DMBOT_STORY_EXTRACTOR_MAX_CALLS must be a whole number from 1 to 5000, got "'
            + extractor_calls_raw
            + '".'
        )
    sidebar_raw = (get("DMBOT_SIDEBAR") or "0").lower()
    if sidebar_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError('DMBOT_SIDEBAR must be 1 (on) or 0 (off), got "' + sidebar_raw + '".')

    ai_models, ai_notice = parse_ai_models(get)

    admin_primary = _admin_id(get, "DMBOT_ADMIN_PRIMARY_ID")
    admin_secondary = _admin_id(get, "DMBOT_ADMIN_SECONDARY_ID")

    try:
        test_guilds = parse_server_ids(get("DMBOT_TEST_RECORDING_GUILDS"))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    try:
        mail = load_mail_settings(env)
    except MailConfigError as exc:
        raise ConfigError(str(exc)) from exc
    link_secret = get("LINK_TOKEN_SECRET")
    if link_secret and len(link_secret) < 32:
        raise ConfigError("LINK_TOKEN_SECRET must be at least 32 characters (or empty).")

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
        ai_models=ai_models,
        ai_model_notice=ai_notice,
        memory_keep_days=keep_days,
        free_users=free_users,
        enforce_plans=enforce_raw in ("1", "true", "on"),
        site_url=get("WEB_SITE_URL").rstrip("/"),
        mail=mail,
        link_token_secret=link_secret.encode("utf-8"),
        sidebar_on=sidebar_raw in ("1", "true", "on"),
        story_extractor_on=extractor_raw in ("1", "true", "on"),
        story_extractor_tier=extractor_tier,
        story_extractor_max_calls=int(extractor_calls_raw),
        continuity=continuity,
        admin_primary_id=admin_primary,
        admin_secondary_id=admin_secondary,
        test_recording_guilds=test_guilds,
        test_recordings_dir=Path(
            get("DMBOT_TEST_RECORDINGS_DIR") or "/var/lib/dmbot/test-recordings"
        ),
    )


def parse_server_ids(raw: str) -> frozenset[int]:
    """`DMBOT_TEST_RECORDING_GUILDS`: Discord server ids, comma-separated. Raises ValueError."""
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not all(p.isascii() and p.isdigit() and 0 < int(p) < 2**63 for p in ids):
        raise ValueError(
            "DMBOT_TEST_RECORDING_GUILDS must be Discord server numbers (digits only), "
            "separated by commas. Fix it in .env and start again."
        )
    return frozenset(int(p) for p in ids)


MODEL_NAME = re.compile(r"^claude-[a-z0-9][a-z0-9._-]{1,80}$")
OLD_MODEL_NOTICE = (
    "Your .env still has AI_MODEL. It works for now, as AI_MODEL_FAST (the model for all the "
    "quick jobs, not only Find names as before), but please rename it to AI_MODEL_FAST, "
    "keeping the same value. A future update will stop reading AI_MODEL."
)
BOTH_MODEL_NOTICE = (
    "Your .env has both AI_MODEL and AI_MODEL_FAST. Only AI_MODEL_FAST is used; delete the "
    "AI_MODEL line."
)


def _unquote(raw: str) -> str:
    """A value without the spaces and quotes a phone adds around it."""
    return raw.strip().strip("\"'").strip()


def parse_ai_models(get: Callable[[str], str]) -> tuple[AIModels, str]:
    """The model for each AI tier, from `AI_MODEL_FAST`, `AI_MODEL_CAREFUL` and
    `AI_MODEL_DEEP` (empty: that tier's default). The old `AI_MODEL`, if set, stands for
    FAST for now; the second value is the line to log about that (or ""). A value that
    isn't a Claude model name stops start-up, naming the setting (never the value)."""
    old = get("AI_MODEL")
    notice = ""
    chosen: dict[str, str] = {}
    for tier, name in (
        ("fast", "AI_MODEL_FAST"),
        ("careful", "AI_MODEL_CAREFUL"),
        ("deep", "AI_MODEL_DEEP"),
    ):
        raw, shown = _unquote(get(name)), name  # (a phone adds quotes)
        if tier == "fast":
            if old and raw:
                notice = BOTH_MODEL_NOTICE
            elif old:
                raw, shown, notice = _unquote(old), "AI_MODEL", OLD_MODEL_NOTICE
        if raw and not MODEL_NAME.match(raw):
            raise ConfigError(
                f"{shown} in .env isn't a Claude model name. Use something like "
                f"{getattr(DEFAULT_MODELS, tier)} (no quotes or spaces), or leave it empty to "
                "use that one."
            )
        chosen[tier] = raw or getattr(DEFAULT_MODELS, tier)
    return AIModels(**chosen), notice


def _admin_id(get: Callable[[str], str], name: str) -> int | None:
    """An admin's Discord user ID: empty, or 17 to 20 digits. Anything else stops start-up
    naming the setting, never its value (it is a person's ID)."""
    raw = get(name)
    if not raw:
        return None
    if not (raw.isascii() and raw.isdigit() and 17 <= len(raw) <= 20):
        raise ConfigError(f"{name} must be empty or a Discord user ID (17 to 20 digits).")
    return int(raw)


def _continuity_levels(get: Callable[[str], str]) -> Levels:
    """The continuity-warning settings (#1320): each level's starting point (0 to 1, in order)
    and how much confirmed story a campaign needs before any warning. Unset: the defaults."""
    defaults = Levels()

    def number(name: str, default: float) -> float:
        raw = get(name)
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            value = -1.0
        if not 0 < value <= 1:
            raise ConfigError(f'{name} must be a number above 0 and up to 1, got "{raw}".')
        return value

    def whole(name: str, default: int) -> int:
        raw = get(name)
        if not raw:
            return default
        if not (raw.isascii() and raw.isdigit() and int(raw) >= 1):
            raise ConfigError(f'{name} must be a whole number from 1 up, got "{raw}".')
        return int(raw)

    levels = Levels(
        possible=number("DMBOT_CONTINUITY_POSSIBLE", defaults.possible),
        probable=number("DMBOT_CONTINUITY_PROBABLE", defaults.probable),
        likely=number("DMBOT_CONTINUITY_LIKELY", defaults.likely),
        min_facts=whole("DMBOT_CONTINUITY_MIN_FACTS", defaults.min_facts),
        min_sessions=whole("DMBOT_CONTINUITY_MIN_SESSIONS", defaults.min_sessions),
    )
    if not levels.possible <= levels.probable <= levels.likely:
        raise ConfigError(
            "DMBOT_CONTINUITY_POSSIBLE, _PROBABLE and _LIKELY must be in that order, "
            "smallest first."
        )
    return levels
