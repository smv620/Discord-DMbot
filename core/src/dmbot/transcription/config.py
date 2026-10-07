"""Transcription settings, read from environment variables.

TRANSCRIBER picks the engine:
- ``whisper-local`` (default): faster-whisper on this machine's CPU or GPU. Free, private.
- ``cloud``: any OpenAI-compatible speech-to-text API. Pay as you go; no GPU needed.
- ``deepgram``: Deepgram Nova-3, with campaign names as keyterms (#170). Pay as you go.
- ``none``: no transcription (no text; only capture-check counts in the log).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

Engine = Literal["whisper-local", "cloud", "deepgram", "none"]
ENGINES: tuple[Engine, ...] = ("whisper-local", "cloud", "deepgram", "none")
# Engines that send players' voices to another company (the consent message says so).
OUTSIDE_ENGINES: frozenset[Engine] = frozenset({"cloud", "deepgram"})
DEFAULT_CLOUD_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


class TranscriptionConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TranscriptionSettings:
    engine: Engine = "whisper-local"
    language: str = "en"  # "" = auto-detect
    # whisper-local
    whisper_model: str = "small"
    whisper_device: str = "auto"  # auto | cpu | cuda
    whisper_compute_type: str = "auto"  # auto picks int8 on CPU, float16 on GPU
    whisper_beam_size: int = 1  # 1 = greedy: fastest, fine for live speech
    # cloud
    cloud_url: str = DEFAULT_CLOUD_URL
    cloud_api_key: str = field(default="", repr=False)
    cloud_model: str = "whisper-1"
    # deepgram
    deepgram_api_key: str = field(default="", repr=False)
    deepgram_model: str = "nova-3"
    deepgram_url: str = DEFAULT_DEEPGRAM_URL
    # Clips written at once, for different servers (#173). Local Whisper fills the CPU
    # with one; an outside company answers several requests side by side.
    workers: int = 1

    @property
    def sends_audio_out(self) -> bool:
        """True if players' voices go to another company to be turned into text."""
        return self.engine in OUTSIDE_ENGINES

    @property
    def outside_engine(self) -> str | None:
        """The outside engine's name (deepgram, cloud), or None for local or none."""
        return self.engine if self.sends_audio_out else None

    @property
    def company(self) -> str | None:
        """The outside company's name when DMbot knows it (for the consent message)."""
        return "Deepgram" if self.engine == "deepgram" else None


def _language(value: str) -> str:
    """'auto' means let the engine detect the language (empty string)."""
    return "" if value.lower() == "auto" else value


def _positive_int(name: str, value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise TranscriptionConfigError(
            f'{name} must be a whole number of 1 or more, got "{value}".'
        )
    return int(value)


MAX_WORKERS = 16


def _workers(value: str) -> int:
    workers = _positive_int("TRANSCRIBE_WORKERS", value)
    if workers > MAX_WORKERS:
        raise TranscriptionConfigError(
            f"TRANSCRIBE_WORKERS can be at most {MAX_WORKERS}, got {workers}."
        )
    return workers


def load_transcription_settings(env: Mapping[str, str]) -> TranscriptionSettings:
    def get(name: str, default: str) -> str:
        return env.get(name, "").strip() or default

    engine = get("TRANSCRIBER", "whisper-local")
    if engine not in ENGINES:
        raise TranscriptionConfigError(
            f'TRANSCRIBER must be one of {", ".join(ENGINES)}; got "{engine}".'
        )
    settings = TranscriptionSettings(
        engine=engine,
        language=_language(get("TRANSCRIBE_LANGUAGE", "en")),
        whisper_model=get("WHISPER_MODEL", "small"),
        whisper_device=get("WHISPER_DEVICE", "auto"),
        whisper_compute_type=get("WHISPER_COMPUTE_TYPE", "auto"),
        whisper_beam_size=_positive_int("WHISPER_BEAM_SIZE", get("WHISPER_BEAM_SIZE", "1")),
        cloud_url=get("CLOUD_STT_URL", DEFAULT_CLOUD_URL),
        cloud_api_key=get("CLOUD_STT_API_KEY", ""),
        cloud_model=get("CLOUD_STT_MODEL", "whisper-1"),
        deepgram_api_key=get("DEEPGRAM_API_KEY", ""),
        deepgram_model=get("DEEPGRAM_MODEL", "nova-3"),
        deepgram_url=get("DEEPGRAM_LISTEN_URL", DEFAULT_DEEPGRAM_URL),
        workers=_workers(get("TRANSCRIBE_WORKERS", "3" if engine in OUTSIDE_ENGINES else "1")),
    )
    if settings.engine == "cloud" and not settings.cloud_api_key:
        raise TranscriptionConfigError(
            "TRANSCRIBER=cloud needs CLOUD_STT_API_KEY (your speech-to-text provider's key)."
        )
    if settings.engine == "cloud" and not settings.cloud_url.startswith("https://"):
        raise TranscriptionConfigError("CLOUD_STT_URL must start with https:// to protect audio.")
    if settings.engine == "deepgram" and not settings.deepgram_api_key:
        raise TranscriptionConfigError(
            "TRANSCRIBER=deepgram needs DEEPGRAM_API_KEY (from your Deepgram account)."
        )
    if settings.engine == "deepgram" and not settings.deepgram_url.startswith("https://"):
        raise TranscriptionConfigError(
            "DEEPGRAM_LISTEN_URL must start with https:// to protect audio."
        )
    return settings
