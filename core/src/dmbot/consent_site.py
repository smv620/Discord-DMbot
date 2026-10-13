"""Stops made on the website, followed by the bot (#1151).

The website saves the stop and sends a notification (consent_notify). This process, if it
serves that server, stops the person exactly as its own Stop button does: out of the consent
cache and ears' allowlist at once, then saved. Run `follow` for as long as the bot runs.

A notification sent while the listener was down is lost, so every time it (re)connects the
cached servers' consent is read again from the database: a stop saved meanwhile still lands.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable

from dmbot import consent_notify
from dmbot.dm_screen.site_offers import RECONNECT_DELAY_S, STEADY_S
from dmbot.logs import log_context

log = logging.getLogger(__name__)

Listen = Callable[[str, Callable[[], None]], AsyncGenerator[str, None]]


class SiteStops:
    def __init__(
        self,
        *,
        is_ours: Callable[[int], bool],
        stop: Callable[[int, int], Awaitable[object]],
        resync: Callable[[], Awaitable[None]],
        spawn: Callable[[Awaitable[None], str], object],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`is_ours`: this process serves that server. `stop`: stop recording the person
        (cache, ears, save). `resync`: read the cached servers' consent again."""
        self._is_ours = is_ours
        self._stop = stop
        self._resync = resync
        self._spawn = spawn
        self._sleep = sleep
        self._clock = clock

    async def follow(self, listen: Listen) -> None:
        failures = 0
        connected_at: float | None = None

        def on_listening() -> None:
            nonlocal connected_at
            connected_at = self._clock()
            self._spawn(self._resync_quietly(), "consent-resync")

        while True:
            connected_at = None
            try:
                async with contextlib.aclosing(listen(consent_notify.CHANNEL, on_listening)) as s:
                    async for raw in s:
                        ids = consent_notify.parse(raw)
                        # Every process hears every announcement: act only on ours.
                        if ids is not None and self._is_ours(ids[0]):
                            self._spawn(self.stop_one(*ids), "site-consent-stop")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the connection dropped; carry on without crashing
                log.warning("Lost consent stops (%s); will listen again", exc)
            if connected_at is not None and self._clock() - connected_at >= STEADY_S:
                failures = 0
            delay = RECONNECT_DELAY_S[min(failures, len(RECONNECT_DELAY_S) - 1)]
            failures += 1
            await self._sleep(delay)

    async def stop_one(self, guild_id: int, user_id: int) -> None:
        """Never raises. A failed save is logged by kind only: the person stays stopped in
        this process either way."""
        with log_context(guild_id=guild_id):
            try:
                await self._stop(guild_id, user_id)
            except Exception as exc:
                log.error("Couldn't save a stop made on the website (%s)", type(exc).__name__)

    async def _resync_quietly(self) -> None:
        try:
            await self._resync()
        except Exception as exc:
            log.error("Couldn't read consent again after reconnecting (%s)", type(exc).__name__)
