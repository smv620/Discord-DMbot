"""Stops made on the website, followed by the bot (#1151).

The website deletes the consent and, in the same transaction, sends a notification
(consent_notify). The notification is only a nudge, never trusted: any database role can send
one. This process, if it serves that server, reads the server's consent from the database and
stops whoever it still holds in memory but the database no longer lists, through the bot's
usual withdraw path (capture and ears at once, the DM told, saved). Run `follow` for as long
as the bot runs.

A notification sent while the listener was down is lost, so every time it (re)connects the
same check runs for every server held in memory. A server that is unavailable when the nudge
arrives is skipped here and caught by that reconnect check.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Protocol

from dmbot import consent_notify
from dmbot.dm_screen.site_offers import RECONNECT_DELAY_S, STEADY_S
from dmbot.logs import log_context

log = logging.getLogger(__name__)

Listen = Callable[[str, Callable[[], None]], AsyncGenerator[str, None]]


class ConsentView(Protocol):
    """What the follower needs from the consent store (consent.ConsentStore)."""

    def cached_guilds(self) -> list[int]: ...

    def agreed_set(self, guild_id: int) -> frozenset[int]: ...  # paused or not

    async def stored(self, guild_id: int) -> frozenset[int]: ...


class SiteStops:
    def __init__(
        self,
        *,
        is_ours: Callable[[int], bool],
        consent: ConsentView,
        stop: Callable[[int, int], Awaitable[object]],
        spawn: Callable[[Awaitable[None], str], object],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`is_ours`: this process serves that server. `stop`: the bot's withdraw path for one
        person (capture, ears, the DM told, save); it runs while memory still holds them."""
        self._is_ours = is_ours
        self._consent = consent
        self._stop = stop
        self._spawn = spawn
        self._sleep = sleep
        self._clock = clock

    async def follow(self, listen: Listen) -> None:
        failures = 0
        connected_at: float | None = None

        def on_listening() -> None:
            nonlocal connected_at
            connected_at = self._clock()
            self._spawn(self.recheck_all(), "consent-recheck")

        while True:
            connected_at = None
            try:
                async with contextlib.aclosing(listen(consent_notify.CHANNEL, on_listening)) as s:
                    async for raw in s:
                        ids = consent_notify.parse(raw)
                        # Every process hears every announcement: act only on ours. Only the
                        # server is used: who stopped is read from the database, not the nudge.
                        if ids is not None and self._is_ours(ids[0]):
                            self._spawn(self.recheck(ids[0]), "site-consent-recheck")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the connection dropped; carry on without crashing
                log.warning("Lost consent stops (%s); will listen again", exc)
            if connected_at is not None and self._clock() - connected_at >= STEADY_S:
                failures = 0
            delay = RECONNECT_DELAY_S[min(failures, len(RECONNECT_DELAY_S) - 1)]
            failures += 1
            await self._sleep(delay)

    async def recheck_all(self) -> None:
        for guild_id in self._consent.cached_guilds():
            await self.recheck(guild_id)

    async def recheck(self, guild_id: int) -> None:
        """Stop whoever this process holds as consenting in this server but the database no
        longer lists. Never raises; each failure is logged by kind only (a database error's
        text can quote a row). A person whose save failed stays stopped in this process."""
        with log_context(guild_id=guild_id):
            try:
                held = self._consent.agreed_set(guild_id)  # off the record is still agreed
                gone = held - await self._consent.stored(guild_id)
            except Exception as exc:
                log.error("Couldn't read consent again for a server (%s)", type(exc).__name__)
                return
            for user_id in sorted(gone):
                try:
                    await self._stop(guild_id, user_id)
                except Exception as exc:
                    log.error("Couldn't save a stop made on the website (%s)", type(exc).__name__)
