"""ears <-> core wire protocol (version 2).

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
from dataclasses import dataclass, field
from typing import Literal

# 2: hello carries the shard settings, so core can refuse an ears serving other shards.
PROTOCOL_VERSION = 2
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
    secret: str = field(repr=False)
    shard_count: int = 1
    shard_ids: tuple[int, ...] = (0,)


@dataclass(frozen=True, slots=True)
class Status:
    """ears' state. "warning": one speaker's audio kept failing (ears gave up re-listening
    until they next speak, or they keep sending but none of it can be heard; #631, #645);
    `user_id` says whose. The session goes on."""

    state: Literal["ready", "joined", "left", "error", "warning"]
    guild_id: int | None = None
    channel_id: int | None = None
    detail: str | None = None
    user_id: int | None = None


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
    # Why frames are missing, for the log (#43, #45); already left out of frames_received.
    decrypt_failures: int = 0  # packets the voice library couldn't decrypt (DAVE)
    decode_errors: int = 0  # packets the Opus decoder refused
    link_dropped: int = 0  # frames dropped on the link to core because it was busy


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
        if version is None or not isinstance(secret, str):
            return None
        if version != PROTOCOL_VERSION:
            # Another version: don't guess its fields. The version check refuses it with
            # a clear reason instead of "no valid hello".
            return Hello(version=version, secret=secret)
        count = _non_negative_int(data.get("shardCount"))
        raw_ids = data.get("shardIds")
        if count is None or count < 1 or not isinstance(raw_ids, list) or not raw_ids:
            return None
        ids = [_non_negative_int(i) for i in raw_ids]
        if any(i is None or i >= count for i in ids) or len(set(ids)) != len(ids):
            return None
        return Hello(
            version=version,
            secret=secret,
            shard_count=count,
            shard_ids=tuple(sorted(i for i in ids if i is not None)),
        )

    if kind == "status":
        state = data.get("state")
        if state not in ("ready", "joined", "left", "error", "warning"):
            return None
        detail = data.get("detail")
        user_id = _snowflake(data.get("userId"))
        if state == "warning" and user_id is None:
            return None  # a warning is always about someone
        return Status(
            state=state,
            guild_id=_snowflake(data.get("guildId")),
            channel_id=_snowflake(data.get("channelId")),
            detail=detail if isinstance(detail, str) else None,
            user_id=user_id if state == "warning" else None,
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
        # Omitted when zero; a wrong type is a broken message, not a zero.
        extras = [
            0 if data.get(key) is None else _non_negative_int(data.get(key))
            for key in ("decryptFailures", "decodeErrors", "linkDropped")
        ]
        if any(count is None for count in extras):
            return None
        decrypt, decode, dropped = (count or 0 for count in extras)
        return Health(guild_id, user_id, received, expected, decrypt, decode, dropped)

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
