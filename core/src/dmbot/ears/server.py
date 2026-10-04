"""WebSocket server that the ears voice service connects to.

Only one ears connection is active at a time; a new authenticated connection
replaces the old one (ears restarted). Connections must authenticate with the
shared secret in their first message or they are closed.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from dmbot.ears.protocol import (
    PROTOCOL_VERSION,
    AudioFrame,
    EarsMessage,
    Hello,
    decode_audio_frame,
    parse_ears_message,
)
from dmbot.sharding import ShardSettings

log = logging.getLogger(__name__)

HELLO_TIMEOUT_S = 5.0
# One frame is ~20 ms of 16 kHz mono (640 bytes); allow generous headroom.
MAX_MESSAGE_BYTES = 256 * 1024

OnMessage = Callable[[EarsMessage], Awaitable[None]]
OnAudio = Callable[[AudioFrame], None]
OnLinkChange = Callable[[bool], Awaitable[None]]


class EarsServer:
    def __init__(
        self,
        host: str,
        port: int,
        secret: str,
        on_message: OnMessage,
        on_audio: OnAudio,
        on_link_change: OnLinkChange | None = None,
        shards: ShardSettings | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._secret = secret.encode()
        self._on_message = on_message
        self._on_audio = on_audio
        self._on_link_change = on_link_change
        self._shards = shards or ShardSettings()
        self._server: Server | None = None
        self._active: ServerConnection | None = None
        self.rejected_frames = 0

    @property
    def connected(self) -> bool:
        return self._active is not None

    @property
    def port(self) -> int:
        """The bound port (useful when started with port 0 in tests)."""
        if self._server is None:
            return self._port
        sockets = self._server.sockets
        return int(sockets[0].getsockname()[1]) if sockets else self._port

    async def start(self) -> None:
        self._server = await serve(self._handle, self._host, self._port, max_size=MAX_MESSAGE_BYTES)
        log.info("Waiting for ears on ws://%s:%s", self._host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def send(self, message: str) -> bool:
        """Send a control message to ears. Returns False if ears is not connected."""
        conn = self._active
        if conn is None:
            return False
        try:
            await conn.send(message)
        except ConnectionClosed:
            return False
        return True

    def _rejection(self, hello: EarsMessage | None) -> str | None:
        """Why this hello can't be accepted (for the log), or None if it's fine."""
        if not isinstance(hello, Hello):
            return "no valid hello"
        if not hmac.compare_digest(hello.secret.encode(), self._secret):
            return "wrong EARS_SHARED_SECRET"
        if hello.version != PROTOCOL_VERSION:
            return (
                f"ears speaks protocol {hello.version}, core speaks {PROTOCOL_VERSION}: "
                "update both to the same DMbot version"
            )
        if (hello.shard_count, hello.shard_ids) != (self._shards.count, self._shards.ids):
            return (
                f"ears serves shards {list(hello.shard_ids)} of {hello.shard_count}, core "
                f"serves {list(self._shards.ids)} of {self._shards.count}: give both the "
                "same SHARD_COUNT and SHARD_IDS"
            )
        return None

    async def _handle(self, conn: ServerConnection) -> None:
        try:
            first = await asyncio.wait_for(conn.recv(), HELLO_TIMEOUT_S)
        except (TimeoutError, ConnectionClosed):
            await conn.close(code=1008, reason="hello required")
            return

        hello = parse_ears_message(first) if isinstance(first, str) else None
        problem = self._rejection(hello)
        if problem is not None:
            log.warning("Rejected an ears connection: %s", problem)
            await conn.close(code=1008, reason="rejected")
            return

        previous, self._active = self._active, conn
        if previous is not None:
            await previous.close(code=1000, reason="replaced by a new ears connection")
        log.info("ears connected")
        if self._on_link_change is not None:
            await self._on_link_change(True)

        try:
            async for message in conn:
                if isinstance(message, bytes):
                    frame = decode_audio_frame(message)
                    if frame is None:
                        self.rejected_frames += 1
                    else:
                        self._on_audio(frame)
                else:
                    parsed = parse_ears_message(message)
                    if parsed is not None and not isinstance(parsed, Hello):
                        await self._on_message(parsed)
        except ConnectionClosed:
            pass
        finally:
            if self._active is conn:
                self._active = None
                log.warning("ears disconnected")
                if self._on_link_change is not None:
                    await self._on_link_change(False)
