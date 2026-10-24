"""Configuration from environment variables (a .env file in the repo root works too)."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from dmbot import imports_switch
from dmbot.ai import DEFAULT_MODELS, AIModels
from dmbot.entitlements import parse_free_users
from dmbot.logs import LOG_FORMATS, LOG_LEVELS
from dmbot.mail import MailConfigError, MailSettings, load_mail_settings
from dmbot.memory.continuity import Levels
from dmbot.memory.recall import DEFAULT_BUDGET
from dmbot.sharding import ShardConfigError, ShardSettings, parse_shards
from dmbot.transcription.config import (
    TranscriptionConfigError,
    TranscriptionSettings,
    load_transcription_settings,
)

# Characters of confirmed story the DM sidebar's prompt may hold (#1370): about one more
# "campaign names" section (five lines of up to 200), so the prompt stays near today's size.
SIDEBAR_RECALL_BUDGET = 1000


DEFAULT_POOL_MAX = 10  # today's value: nothing changes for a deployment that sets nothing
MAX_POOL_MAX = 100  # a typo like 1000 would eat Postgres' connection limit


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
    # Servers whose old per-server copy of the slash commands is removed once at start (#1767).
    clear_guild_commands: tuple[int, ...] = ()
    database_url: str = field(default="", repr=False)  # contains the password
    # The most connections this process holds open to Postgres (#77). Ten is what DMbot always
    # used; with many shard pods, about 4 each keeps them under Postgres' own limit.
    database_pool_max: int = 10
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
    # Who writes the recap (the creative tier, #2575): "anthropic" (the default) or "gemini", which
    # needs `gemini_key` (a billing-enabled Google key) and a Gemini name in AI_MODEL_CREATIVE.
    # Off unless set; with no key the writer stays on Anthropic and `ai_model_notice` says so.
    ai_provider_creative: str = "anthropic"
    gemini_key: str = field(default="", repr=False)
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
    # Images posted in the table chat are described once by the AI (#2450). Off until dev1 has run
    # a live check; with it off no image is fetched or sent anywhere. `table_images_per_day` is the
    # most images one campaign has described in a day (each counts toward its AI use); past it an
    # image is noted as not described.
    table_images_on: bool = False
    table_images_per_day: int = 40
    recaps_on: bool = False  # cinematic recaps (#1382): off until dev1's quality check
    # PlotBot's check of each recap before it is accepted (#2122); needs recaps on. Off by default.
    plot_check_on: bool = False
    # Importing older sessions (#2105): off for everyone until dev1 has checked a real import,
    # and on for the listed servers alone (#2259). The web API reads the same two settings.
    imports_on: bool = False
    imports_servers: frozenset[int] = field(default=frozenset(), repr=False)
    # A time jump suggested from vague narration (#1758): the DM screen only, never automatic.
    # Off until dev1 has run a live check.
    time_suggestions: bool = False
    # The recap's narration and music (#1383): made only when recaps are on, a key and a voice
    # are set and the campaign's plan includes spoken recaps. No key: text-only, nothing fails.
    elevenlabs_key: str = field(default="", repr=False)
    elevenlabs_voice_id: str = ""
    elevenlabs_speech_model: str = "eleven_v3"  # the voice model; dev1 compares models by ear
    recap_audio_max_credits: int = 15000  # provider credits one recap may use (about $3)
    # The recap's mood cues reach the voice as delivery tags (#1983); 0 sends only whisper, sigh and
    # pauses, so dev1 can compare the two narrations by ear.
    recap_voice_tags: bool = True
    # The most recaps, and narrated recaps, one campaign may make in a calendar month (#1428).
    recaps_per_month: int = 10
    recap_audio_per_month: int = 8

    def imports_enabled_for(self, guild_id: int) -> bool:
        return imports_switch.enabled(self.imports_on, self.imports_servers, guild_id)

    @property
    def recap_audio_on(self) -> bool:
        return self.recaps_on and bool(self.elevenlabs_key) and bool(self.elevenlabs_voice_id)

    # Which model tier reads the transcript, and how many calls one session may make (#1363).
    story_extractor_tier: str = "fast"
    story_extractor_max_calls: int = 480
    cleaner_rank_max: int = 40  # "Did they mean…?" ranking calls a session; 0 turns it off (#2048)
    story_extractor_max_calls_day: int = 960  # per campaign, a day (two sessions' worth)
    # Continuity warnings (#1320): where each level starts and how much confirmed story a
    # campaign needs first. Backend settings, tuned on the golden set; no switch shown to anyone.
    continuity: Levels = field(default_factory=Levels)
    # A story thread (#1662) is stalled when this many sessions have started since it last moved.
    thread_stalled_sessions: int = 3
    # The most characters of story the recall service hands one helper (#1350).
    recall_budget: int = DEFAULT_BUDGET
    # The DM sidebar answers from recall (#1370): on by default; off is exactly the old prompt.
    sidebar_recall: bool = True
    # The first live heads-up, a confirmed-dead character voiced at the table (#2068): off until
    # dev1's live check. The gap between two of them, in seconds.
    live_heads_up: bool = False
    live_heads_up_cooldown_s: float = 300.0
    secret_reveal_ask: bool = True  # ask the DM at once when they say a secret name (#2088)
    presession_note: bool = True  # the note on the DM screen when a session starts (#1403)
    sidebar_recall_budget: int = SIDEBAR_RECALL_BUDGET
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

    pool_raw = get("DATABASE_POOL_MAX") or str(DEFAULT_POOL_MAX)
    if not (pool_raw.isascii() and pool_raw.isdigit()) or not 1 <= int(pool_raw) <= MAX_POOL_MAX:
        raise ConfigError(f"DATABASE_POOL_MAX must be a number from 1 to {MAX_POOL_MAX}.")

    port_raw = get("EARS_WS_PORT") or "8765"
    if not port_raw.isdigit():
        raise ConfigError(f'EARS_WS_PORT must be a number, got "{port_raw}".')

    guild_raw = get("DISCORD_DEV_GUILD_ID")
    if guild_raw and not guild_raw.isdigit():
        raise ConfigError("DISCORD_DEV_GUILD_ID must be a server ID (digits only).")

    clear_raw = get("DISCORD_CLEAR_GUILD_COMMANDS")
    clear_ids = [part.strip() for part in clear_raw.split(",") if part.strip()]
    if not all(part.isdigit() for part in clear_ids):
        raise ConfigError(
            "DISCORD_CLEAR_GUILD_COMMANDS must be server IDs (digits only), separated by commas."
        )

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
    stalled_raw = get("DMBOT_THREAD_STALLED_SESSIONS") or "3"
    if not (stalled_raw.isascii() and stalled_raw.isdigit() and int(stalled_raw) >= 1):
        raise ConfigError(
            f'DMBOT_THREAD_STALLED_SESSIONS must be a whole number from 1 up, got "{stalled_raw}".'
        )
    recall_budget = _recall_budget(get)
    sidebar_recall_raw = (get("DMBOT_SIDEBAR_RECALL") or "1").lower()
    if sidebar_recall_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(
            'DMBOT_SIDEBAR_RECALL must be 1 (on) or 0 (off), got "' + sidebar_recall_raw + '".'
        )
    live_raw = (get("DMBOT_LIVE_HEADS_UP") or "0").lower()
    if live_raw not in ("0", "1", "true", "on", "false", "off"):
        raise ConfigError('DMBOT_LIVE_HEADS_UP must be 1 (on) or 0 (off), got "' + live_raw + '".')
    live_minutes_raw = get("DMBOT_LIVE_HEADS_UP_MINUTES") or "5"
    try:
        live_minutes = float(live_minutes_raw)
    except ValueError:
        live_minutes = -1.0
    if not 0 <= live_minutes <= 120:
        raise ConfigError(
            'DMBOT_LIVE_HEADS_UP_MINUTES must be a number from 0 to 120, got "'
            + live_minutes_raw
            + '".'
        )
    reveal_raw = (get("DMBOT_SECRET_REVEAL_ASK") or "1").lower()
    if reveal_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(
            'DMBOT_SECRET_REVEAL_ASK must be 1 (on) or 0 (off), got "' + reveal_raw + '".'
        )
    presession_raw = (get("DMBOT_PRESESSION_NOTE") or "1").lower()
    if presession_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(
            'DMBOT_PRESESSION_NOTE must be 1 (on) or 0 (off), got "' + presession_raw + '".'
        )
    sidebar_budget_raw = get("DMBOT_SIDEBAR_RECALL_BUDGET") or str(SIDEBAR_RECALL_BUDGET)
    if not (
        sidebar_budget_raw.isascii()
        and sidebar_budget_raw.isdigit()
        and 200 <= int(sidebar_budget_raw) <= 4000
    ):
        raise ConfigError(
            'DMBOT_SIDEBAR_RECALL_BUDGET must be a number from 200 to 4000, got "'
            + sidebar_budget_raw
            + '".'
        )

    suggestions_raw = (get("DMBOT_TIME_SUGGESTIONS") or "0").lower()
    if suggestions_raw not in ("0", "1", "true", "on", "false", "off"):
        raise ConfigError(
            'DMBOT_TIME_SUGGESTIONS must be 1 (on) or 0 (off), got "' + suggestions_raw + '".'
        )
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
    if (
        not (extractor_calls_raw.isascii() and extractor_calls_raw.isdigit())
        or not 1 <= int(extractor_calls_raw) <= 5000
    ):
        raise ConfigError(
            'DMBOT_STORY_EXTRACTOR_MAX_CALLS must be a whole number from 1 to 5000, got "'
            + extractor_calls_raw
            + '".'
        )
    extractor_day_raw = get("DMBOT_STORY_EXTRACTOR_MAX_CALLS_DAY") or "960"
    if not (extractor_day_raw.isascii() and extractor_day_raw.isdigit()) or not (
        1 <= int(extractor_day_raw) <= 50000
    ):
        raise ConfigError(
            'DMBOT_STORY_EXTRACTOR_MAX_CALLS_DAY must be a whole number from 1 to 50000, got "'
            + extractor_day_raw
            + '".'
        )
    rank_raw = get("DMBOT_CLEANER_RANK_MAX") or "40"
    if not (rank_raw.isascii() and rank_raw.isdigit()) or int(rank_raw) > 1000:
        raise ConfigError(
            'DMBOT_CLEANER_RANK_MAX must be a whole number from 0 to 1000, got "' + rank_raw + '".'
        )
    recaps_raw = (get("DMBOT_RECAPS") or "0").lower()
    if recaps_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError('DMBOT_RECAPS must be 1 (on) or 0 (off), got "' + recaps_raw + '".')
    imports_raw = (get("DMBOT_IMPORTS") or "0").lower()
    if imports_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError('DMBOT_IMPORTS must be 1 (on) or 0 (off), got "' + imports_raw + '".')
    try:
        imports_servers = imports_switch.parse_servers(get("DMBOT_IMPORTS_SERVERS"))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    voice_tags_raw = (get("DMBOT_RECAP_VOICE_TAGS") or "1").lower()
    if voice_tags_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError(
            'DMBOT_RECAP_VOICE_TAGS must be 1 (on) or 0 (off), got "' + voice_tags_raw + '".'
        )
    audio_credits_raw = get("DMBOT_RECAP_AUDIO_MAX_CREDITS") or "15000"
    if not (audio_credits_raw.isascii() and audio_credits_raw.isdigit()) or not (
        1000 <= int(audio_credits_raw) <= 200000
    ):
        raise ConfigError(
            'DMBOT_RECAP_AUDIO_MAX_CREDITS must be a whole number from 1000 to 200000, got "'
            + audio_credits_raw
            + '".'
        )
    monthly = {}
    for name, default in (("DMBOT_RECAPS_PER_MONTH", "10"), ("DMBOT_RECAP_AUDIO_PER_MONTH", "8")):
        raw = get(name) or default
        if not (raw.isascii() and raw.isdigit()) or not 1 <= int(raw) <= 1000:
            raise ConfigError(f'{name} must be a whole number from 1 to 1000, got "{raw}".')
        monthly[name] = int(raw)
    voice_id = get("ELEVENLABS_VOICE_ID")
    if voice_id and not (voice_id.isascii() and voice_id.isalnum() and len(voice_id) <= 64):
        raise ConfigError("ELEVENLABS_VOICE_ID must be the voice's id: letters and digits only.")
    speech_model = get("ELEVENLABS_SPEECH_MODEL") or "eleven_v3"
    if not (speech_model.isascii() and speech_model.replace("_", "").replace(".", "").isalnum()):
        raise ConfigError("ELEVENLABS_SPEECH_MODEL must be a model id such as eleven_v3.")
    images_raw = (get("DMBOT_TABLE_IMAGES") or "0").lower()
    if images_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError('DMBOT_TABLE_IMAGES must be 1 (on) or 0 (off), got "' + images_raw + '".')
    images_day_raw = get("DMBOT_TABLE_IMAGES_PER_DAY") or "40"
    if not (images_day_raw.isascii() and images_day_raw.isdigit()) or not (
        1 <= int(images_day_raw) <= 1000
    ):
        raise ConfigError(
            'DMBOT_TABLE_IMAGES_PER_DAY must be a whole number from 1 to 1000, got "'
            + images_day_raw
            + '".'
        )
    sidebar_raw = (get("DMBOT_SIDEBAR") or "0").lower()
    if sidebar_raw not in ("0", "1", "true", "false", "on", "off"):
        raise ConfigError('DMBOT_SIDEBAR must be 1 (on) or 0 (off), got "' + sidebar_raw + '".')

    ai_models, ai_notice = parse_ai_models(get)
    ai_provider_creative, ai_models, gemini_notice = parse_creative_provider(get, ai_models)
    ai_notice = " ".join(n for n in (ai_notice, gemini_notice) if n)

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
        clear_guild_commands=tuple(int(part) for part in clear_ids),
        database_url=get("DATABASE_URL"),
        database_pool_max=int(pool_raw),
        data_dir=Path(get("DMBOT_DATA_DIR") or "data"),
        transcription=transcription,
        shards=shards,
        log_format=log_format,
        log_level=log_level,
        ai_key=get("ANTHROPIC_API_KEY"),
        ai_models=ai_models,
        ai_model_notice=ai_notice,
        ai_provider_creative=ai_provider_creative,
        gemini_key=get("GEMINI_API_KEY"),
        memory_keep_days=keep_days,
        free_users=free_users,
        enforce_plans=enforce_raw in ("1", "true", "on"),
        site_url=get("WEB_SITE_URL").rstrip("/"),
        mail=mail,
        link_token_secret=link_secret.encode("utf-8"),
        sidebar_on=sidebar_raw in ("1", "true", "on"),
        story_extractor_on=extractor_raw in ("1", "true", "on"),
        table_images_on=images_raw in ("1", "true", "on"),
        table_images_per_day=int(images_day_raw),
        recaps_on=recaps_raw in ("1", "true", "on"),
        plot_check_on=recaps_raw in ("1", "true", "on")
        and (get("DMBOT_PLOT_CHECK") or "0").lower() in ("1", "true", "on"),
        imports_on=imports_raw in ("1", "true", "on"),
        imports_servers=imports_servers,
        time_suggestions=suggestions_raw in ("1", "true", "on"),
        elevenlabs_key=get("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=voice_id,
        elevenlabs_speech_model=speech_model,
        recap_audio_max_credits=int(audio_credits_raw),
        recap_voice_tags=voice_tags_raw in ("1", "true", "on"),
        recaps_per_month=monthly["DMBOT_RECAPS_PER_MONTH"],
        recap_audio_per_month=monthly["DMBOT_RECAP_AUDIO_PER_MONTH"],
        story_extractor_tier=extractor_tier,
        story_extractor_max_calls=int(extractor_calls_raw),
        story_extractor_max_calls_day=int(extractor_day_raw),
        cleaner_rank_max=int(rank_raw),
        continuity=continuity,
        thread_stalled_sessions=int(stalled_raw),
        recall_budget=recall_budget,
        sidebar_recall=sidebar_recall_raw in ("1", "true", "on"),
        live_heads_up=live_raw in ("1", "true", "on"),
        live_heads_up_cooldown_s=live_minutes * 60,
        secret_reveal_ask=reveal_raw in ("1", "true", "on"),
        presession_note=presession_raw in ("1", "true", "on"),
        sidebar_recall_budget=int(sidebar_budget_raw),
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
GEMINI_MODEL_NAME = re.compile(r"^gemini-[a-z0-9][a-z0-9._-]{1,80}$")
NO_GEMINI_KEY_NOTICE = (
    "AI_PROVIDER_CREATIVE is gemini but GEMINI_API_KEY is empty, so the recap is written by the "
    "Anthropic model as before. Set the key (scripts/set-key GEMINI_API_KEY) and restart to use "
    "Gemini."
)
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
    """The model for each AI tier, from `AI_MODEL_FAST`, `AI_MODEL_CAREFUL`, `AI_MODEL_DEEP`,
    `AI_MODEL_CREATIVE` and `AI_MODEL_FORMATTING` (empty: that tier's default). The old
    `AI_MODEL`, if set, stands for FAST for now; the second value is the line to log about
    that (or ""). A value that isn't a Claude model name stops start-up, naming the setting
    (never the value)."""
    old = get("AI_MODEL")
    notice = ""
    chosen: dict[str, str] = {}
    for tier, name in (
        ("fast", "AI_MODEL_FAST"),
        ("careful", "AI_MODEL_CAREFUL"),
        ("deep", "AI_MODEL_DEEP"),
        ("creative", "AI_MODEL_CREATIVE"),
        ("formatting", "AI_MODEL_FORMATTING"),
    ):
        raw, shown = _unquote(get(name)), name  # (a phone adds quotes)
        if tier == "fast":
            if old and raw:
                notice = BOTH_MODEL_NOTICE
            elif old:
                raw, shown, notice = _unquote(old), "AI_MODEL", OLD_MODEL_NOTICE
        gemini_writes = (
            tier == "creative" and _unquote(get("AI_PROVIDER_CREATIVE")).lower() == "gemini"
        )
        if (
            raw and not gemini_writes and not MODEL_NAME.match(raw)
        ):  # (a Gemini name: checked later)
            raise ConfigError(
                f"{shown} in .env isn't a Claude model name. Use something like "
                f"{getattr(DEFAULT_MODELS, tier)} (no quotes or spaces), or leave it empty to "
                "use that one."
            )
        chosen[tier] = raw or getattr(DEFAULT_MODELS, tier)
    return AIModels(**chosen), notice


def parse_creative_provider(
    get: Callable[[str], str], models: AIModels
) -> tuple[str, AIModels, str]:
    """(who writes the recap, the models to use, a line to log). `AI_PROVIDER_CREATIVE` is
    `anthropic` (the default, also when empty) or `gemini`. With `gemini`, AI_MODEL_CREATIVE must
    be a Gemini model name (nothing here guesses one) and GEMINI_API_KEY must be set; with no key
    the writer is the Anthropic default instead and the line says so. A bad value stops start-up
    naming the setting (never the value of a key)."""
    provider = _unquote(get("AI_PROVIDER_CREATIVE")).lower() or "anthropic"
    if provider not in ("anthropic", "gemini"):
        raise ConfigError("AI_PROVIDER_CREATIVE in .env must be anthropic or gemini.")
    if provider == "anthropic":
        return provider, models, ""
    if not get("GEMINI_API_KEY"):  # first: with no key nothing here may stop start-up
        return "anthropic", replace(models, creative=DEFAULT_MODELS.creative), NO_GEMINI_KEY_NOTICE
    name = _unquote(get("AI_MODEL_CREATIVE"))
    if not GEMINI_MODEL_NAME.match(name):
        raise ConfigError(
            "AI_PROVIDER_CREATIVE is gemini, so AI_MODEL_CREATIVE in .env must be a Gemini model "
            "name (it starts with gemini-, no quotes or spaces)."
        )
    return provider, replace(models, creative=name), ""


def _admin_id(get: Callable[[str], str], name: str) -> int | None:
    """An admin's Discord user ID: empty, or 17 to 20 digits. Anything else stops start-up
    naming the setting, never its value (it is a person's ID)."""
    raw = get(name)
    if not raw:
        return None
    if not (raw.isascii() and raw.isdigit() and 17 <= len(raw) <= 20):
        raise ConfigError(f"{name} must be empty or a Discord user ID (17 to 20 digits).")
    return int(raw)


def _recall_budget(get: Callable[[str], str]) -> int:
    """How many characters of the story one recall may hold (#1350). Unset: the default."""
    raw = get("DMBOT_RECALL_BUDGET")
    if not raw:
        return DEFAULT_BUDGET
    if not (raw.isascii() and raw.isdigit() and 200 <= int(raw) <= 50_000):
        raise ConfigError(f'DMBOT_RECALL_BUDGET must be a number from 200 to 50000, got "{raw}".')
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
