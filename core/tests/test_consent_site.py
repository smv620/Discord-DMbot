"""Stops made on the website reach the bot (#1151): the notification, the follower that acts on
it (only for its own servers), and the consent cache the audio path trusts."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

from dmbot import consent_notify
from dmbot.consent import ConsentStore
from dmbot.consent_site import SiteStops
from tests.pg import DatabaseTest

GUILD = 111
ALICE = 1001


class Notify(unittest.TestCase):
    def test_the_payload_round_trips(self) -> None:
        self.assertEqual(consent_notify.parse(consent_notify.payload(GUILD, ALICE)), (GUILD, ALICE))

    def test_anything_else_is_ignored(self) -> None:
        for raw in ("", "1", "1:2:3", "a:b", "0:5", "5:0", "-1:5", "1:" + "9" * 30, "1: 2"):
            self.assertIsNone(consent_notify.parse(raw), raw)


def listener(*batches: list[str]) -> Callable[[str, Callable[[], None]], AsyncGenerator[str, None]]:
    """A listen function: each call is one connection that says its batch, then drops."""
    remaining = list(batches)

    async def listen(channel: str, on_listening: Callable[[], None]) -> AsyncGenerator[str, None]:
        assert channel == consent_notify.CHANNEL
        on_listening()
        for raw in remaining.pop(0) if remaining else []:
            yield raw

    return listen


class Follower(unittest.IsolatedAsyncioTestCase):
    def build(self, *, ours: set[int], stop: Any, resync: Any) -> SiteStops:
        def spawn(work: Awaitable[None], name: str) -> object:
            return asyncio.ensure_future(work)

        # Cancels the follow loop on the second wait, after one connection has been served.
        sleeps = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        return SiteStops(
            is_ours=lambda guild_id: guild_id in ours,
            stop=stop,
            resync=resync,
            spawn=spawn,
            sleep=sleeps,
        )

    async def run_follow(self, follower: SiteStops, listen: Any) -> None:
        with self.assertRaises(asyncio.CancelledError):
            await follower.follow(listen)
        await asyncio.sleep(0.05)  # let spawned work finish

    async def test_it_stops_the_person_for_its_own_servers_only(self) -> None:
        stop = AsyncMock()
        follower = self.build(ours={GUILD}, stop=stop, resync=AsyncMock())
        listen = listener(
            [consent_notify.payload(GUILD, ALICE), consent_notify.payload(222, 5), "junk"]
        )
        await self.run_follow(follower, listen)
        stop.assert_awaited_once_with(GUILD, ALICE)

    async def test_it_reads_consent_again_every_time_it_listens(self) -> None:
        resync = AsyncMock()
        follower = self.build(ours=set(), stop=AsyncMock(), resync=resync)
        await self.run_follow(follower, listener([]))
        self.assertGreaterEqual(resync.await_count, 1)  # once for each connection

    async def test_a_failed_save_is_logged_by_kind_and_never_raised(self) -> None:
        stop = AsyncMock(side_effect=RuntimeError("secret row text"))
        follower = self.build(ours={GUILD}, stop=stop, resync=AsyncMock())
        with self.assertLogs("dmbot.consent_site", "ERROR") as logged:
            await self.run_follow(follower, listener([consent_notify.payload(GUILD, ALICE)]))
        text = " ".join(logged.output)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("secret row text", text)

    async def test_a_failed_resync_is_logged_and_listening_goes_on(self) -> None:
        resync = AsyncMock(side_effect=RuntimeError("boom"))
        stop = AsyncMock()
        follower = self.build(ours={GUILD}, stop=stop, resync=resync)
        with self.assertLogs("dmbot.consent_site", "ERROR"):
            await self.run_follow(follower, listener([consent_notify.payload(GUILD, ALICE)]))
        stop.assert_awaited_once()


class Cache(DatabaseTest):
    async def test_a_reload_picks_up_a_stop_saved_by_another_process(self) -> None:
        bot = ConsentStore(self.db)
        await bot.grant(GUILD, ALICE)
        self.assertTrue(bot.has_consent(GUILD, ALICE))
        # The website's process deletes the row; this process's cache doesn't know.
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM consent WHERE guild_id = %s", (GUILD,))
        self.assertTrue(bot.has_consent(GUILD, ALICE))
        self.assertEqual(bot.cached_guilds(), [GUILD])
        await bot.reload(GUILD)
        self.assertFalse(bot.has_consent(GUILD, ALICE))

    async def test_a_reload_keeps_a_stop_made_here_that_is_not_saved_yet(self) -> None:
        bot = ConsentStore(self.db)
        await bot.grant(GUILD, ALICE)
        bot.stop_now(GUILD, ALICE)
        await bot.reload(GUILD)
        self.assertFalse(bot.has_consent(GUILD, ALICE))

    async def test_a_real_notification_reaches_the_follower(self) -> None:
        bot = ConsentStore(self.db)
        await bot.grant(GUILD, ALICE)
        listening = asyncio.Event()

        async def stop(guild_id: int, user_id: int) -> None:
            bot.stop_now(guild_id, user_id)  # what the Stop button does first, before any await
            await bot.revoke(guild_id, user_id)

        async def resync() -> None:
            listening.set()

        follower = SiteStops(
            is_ours=lambda guild_id: True,
            stop=stop,
            resync=resync,
            spawn=lambda work, name: asyncio.ensure_future(work),
        )
        task = asyncio.ensure_future(follower.follow(self.db.listen))
        try:
            await asyncio.wait_for(listening.wait(), 10)
            async with self.db.unscoped() as conn:
                await consent_notify.send(conn, GUILD, ALICE)
            for _ in range(100):
                if not bot.has_consent(GUILD, ALICE):
                    break
                await asyncio.sleep(0.1)
            self.assertFalse(bot.has_consent(GUILD, ALICE))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
