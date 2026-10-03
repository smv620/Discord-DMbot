"""ears <-> core wire protocol (version 1).

Keep in sync with ears/src/protocol.ts. Shared test vectors live in
protocol/fixtures.json and are checked by both test suites.

Text frames carry JSON control messages. Binary frames carry audio (ears -> core):
    byte 0       kind (1 = PCM audio)
    bytes 1-8    Discord guild (server) ID, uint64 big-endian
    bytes 9-16   Discord user ID, uint64 big-endian
    bytes 17-24  capture timestamp, Unix epoch milliseconds, uint64 big-endian
    bytes 25-    PCM, signed 16-bit little-endian, mono, 16 kHz
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from typing import Literal

PROTOCOL_VERSION = 1
AUDIO_FRAME_KIND = 1
AUDIO_HEADER_BYTES = 25
SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2

_HEADER = struct.Struct(">BQQQ")
_SNOWFLAKE = re.compile(r"^\d{1,20}$")


# ---- ears -> core ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hello:
    version: int
    secret: str


@dataclass(frozen=True, slots=True)
class Status:
    state: Literal["ready", "joined", "left", "error"]
    guild_id: int | None = None
    channel_id: int | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Speaking:
    guild_id: int
    user_id: int
    event: Literal["start", "end"]
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class Health:
    guild_id: int
    user_id: int
    frames_received: int
    frames_expected: int


EarsMessage = Hello | Status | Speaking | Health


@dataclass(frozen=True, slots=True)
class AudioFrame:
    guild_id: int
    user_id: int
    timestamp_ms: int
    pcm: bytes

    @property
    def duration_ms(self) -> float:
        return len(self.pcm) / BYTES_PER_SAMPLE / SAMPLE_RATE * 1000


def _snowflake(value: object) -> int | None:
    if isinstance(value, str) and _SNOWFLAKE.match(value):
        return int(value)
    return None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def parse_ears_message(raw: str) -> EarsMessage | None:
    """Parse and validate a JSON control message from ears. Returns None if invalid."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None

    kind = data.get("type")
    if kind == "hello":
        version, secret = _non_negative_int(data.get("version")), data.get("secret")
        if version is not None and isinstance(secret, str):
            return Hello(version=version, secret=secret)
        return None

    if kind == "status":
        state = data.get("state")
        if state not in ("ready", "joined", "left", "error"):
            return None
        detail = data.get("detail")
        return Status(
            state=state,
            guild_id=_snowflake(data.get("guildId")),
            channel_id=_snowflake(data.get("channelId")),
            detail=detail if isinstance(detail, str) else None,
        )

    if kind == "speaking":
        guild_id, user_id = _snowflake(data.get("guildId")), _snowflake(data.get("userId"))
        event, ts = data.get("event"), _non_negative_int(data.get("timestampMs"))
        if guild_id is None or user_id is None or ts is None or event not in ("start", "end"):
            return None
        return Speaking(guild_id=guild_id, user_id=user_id, event=event, timestamp_ms=ts)

    if kind == "health":
        guild_id, user_id = _snowflake(data.get("guildId")), _snowflake(data.get("userId"))
        received = _non_negative_int(data.get("framesReceived"))
        expected = _non_negative_int(data.get("framesExpected"))
        if guild_id is None or user_id is None or received is None or expected is None:
            return None
        return Health(guild_id, user_id, received, expected)

    return None


def decode_audio_frame(frame: bytes) -> AudioFrame | None:
    """Decode a binary audio frame from ears. Returns None if malformed."""
    if len(frame) < AUDIO_HEADER_BYTES:
        return None
    kind, guild_id, user_id, timestamp_ms = _HEADER.unpack_from(frame)
    pcm = frame[AUDIO_HEADER_BYTES:]
    if kind != AUDIO_FRAME_KIND or len(pcm) % BYTES_PER_SAMPLE != 0:
        return None
    return AudioFrame(guild_id=guild_id, user_id=user_id, timestamp_ms=timestamp_ms, pcm=bytes(pcm))


def encode_audio_frame(guild_id: int, user_id: int, timestamp_ms: int, pcm: bytes) -> bytes:
    """Build a binary audio frame (used by tests; ears is the real producer)."""
    return _HEADER.pack(AUDIO_FRAME_KIND, guild_id, user_id, timestamp_ms) + pcm


# ---- core -> ears ---------------------------------------------------------


def join_command(guild_id: int, channel_id: int) -> str:
    return json.dumps({"type": "join", "guildId": str(guild_id), "channelId": str(channel_id)})


def leave_command(guild_id: int) -> str:
    return json.dumps({"type": "leave", "guildId": str(guild_id)})


def allowlist_command(guild_id: int, user_ids: set[int] | frozenset[int]) -> str:
    return json.dumps(
        {
            "type": "allowlist",
            "guildId": str(guild_id),
            "userIds": [str(u) for u in sorted(user_ids)],
        }
    )
