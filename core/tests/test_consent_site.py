"""Stops made on the website reach the bot (#1151): the notification is only a nudge, the
follower reads the database and stops whoever is gone (through the bot's withdraw path, which
tells the DM), and a reconnect catches a stop sent while it wasn't listening."""

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
BOB = 2002


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


class FakeConsent:
    """What the follower needs: who memory holds, and who the database lists."""

    def __init__(self, held: dict[int, set[int]], stored: dict[int, set[int]]) -> None:
        self.held = held
        self.in_database = stored
        self.reads = 0

    def cached_guilds(self) -> list[int]:
        return list(self.held)

    def agreed_set(self, guild_id: int) -> frozenset[int]:
        return frozenset(self.held.get(guild_id, set()))

    def consenting_now(self, guild_id: int) -> frozenset[int]:
        return self.agreed_set(guild_id)

    async def stored(self, guild_id: int) -> frozenset[int]:
        self.reads += 1
        return frozenset(self.in_database.get(guild_id, set()))


class Follower(unittest.IsolatedAsyncioTestCase):
    def build(self, consent: FakeConsent, stop: Any, *, ours: set[int] | None = None) -> SiteStops:
        def spawn(work: Awaitable[None], name: str) -> object:
            return asyncio.ensure_future(work)

        # Cancels the follow loop on the second wait, after one connection has been served.
        sleeps = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        return SiteStops(
            is_ours=lambda guild_id: guild_id in (ours if ours is not None else {GUILD}),
            consent=consent,
            stop=stop,
            spawn=spawn,
            sleep=sleeps,
        )

    async def run_follow(self, follower: SiteStops, listen: Any) -> None:
        with self.assertRaises(asyncio.CancelledError):
            await follower.follow(listen)
        await asyncio.sleep(0.05)  # let spawned work finish

    async def test_a_nudge_stops_whoever_the_database_no_longer_lists(self) -> None:
        consent = FakeConsent({GUILD: {ALICE, BOB}}, {GUILD: {BOB}})
        stop = AsyncMock()
        follower = self.build(consent, stop)
        await self.run_follow(follower, listener([consent_notify.payload(GUILD, BOB)]))
        # Alice is gone from the database, so she is stopped; the nudge named Bob and that
        # changes nothing: who stopped is read from the database, not from the payload.
        stop.assert_awaited_with(GUILD, ALICE)
        self.assertNotIn(BOB, [call.args[1] for call in stop.await_args_list])

    async def test_a_forged_nudge_stops_nobody_who_still_consents(self) -> None:
        consent = FakeConsent({GUILD: {ALICE, BOB}}, {GUILD: {ALICE, BOB}})
        stop = AsyncMock()
        follower = self.build(consent, stop)
        await self.run_follow(follower, listener([consent_notify.payload(GUILD, ALICE)]))
        stop.assert_not_awaited()

    async def test_the_stop_runs_while_memory_still_holds_the_person(self) -> None:
        # The withdraw path asks "were they recorded?" to decide whether to tell the DM, so the
        # follower must not clear memory first.
        consent = FakeConsent({GUILD: {ALICE}}, {GUILD: set()})
        seen: list[bool] = []

        async def stop(guild_id: int, user_id: int) -> None:
            seen.append(user_id in consent.consenting_now(guild_id))
            consent.held[guild_id].discard(user_id)  # as the real path does: out of memory

        follower = self.build(consent, stop)
        await self.run_follow(follower, listener([consent_notify.payload(GUILD, ALICE)]))
        self.assertEqual(seen, [True])

    async def test_a_nudge_for_a_foreign_server_is_ignored_but_connecting_covers_it(
        self,
    ) -> None:
        consent = FakeConsent({GUILD: {ALICE}, 222: {ALICE}}, {GUILD: set(), 222: set()})
        stop = AsyncMock()
        follower = self.build(consent, stop, ours=set())
        # Not ours: the nudge is ignored. (The reconnect check below still covers every cached
        # server, which is how a stop is caught when a server was unavailable at the nudge.)
        await self.run_follow(follower, listener([consent_notify.payload(222, ALICE), "junk"]))
        self.assertEqual(
            {call.args for call in stop.await_args_list}, {(GUILD, ALICE), (222, ALICE)}
        )

    async def test_every_connection_checks_every_cached_server(self) -> None:
        consent = FakeConsent({GUILD: {ALICE}}, {GUILD: set()})
        stop = AsyncMock()
        follower = self.build(consent, stop)
        await self.run_follow(follower, listener([]))  # nothing was announced
        stop.assert_awaited_with(GUILD, ALICE)  # a stop sent while it wasn't listening

    async def test_a_failed_save_is_logged_by_kind_and_the_next_person_is_still_stopped(
        self,
    ) -> None:
        consent = FakeConsent({GUILD: {ALICE, BOB}}, {GUILD: set()})
        inner = AsyncMock(side_effect=[RuntimeError("secret row text"), None])

        async def stop(guild_id: int, user_id: int) -> None:
            consent.held[guild_id].discard(user_id)  # stopped in memory whatever the save did
            await inner(guild_id, user_id)

        follower = self.build(consent, stop)
        with self.assertLogs("dmbot.consent_site", "ERROR") as logged:
            await self.run_follow(follower, listener([consent_notify.payload(GUILD, ALICE)]))
        text = " ".join(logged.output)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("secret row text", text)
        self.assertEqual([call.args[1] for call in inner.await_args_list], [ALICE, BOB])

    async def test_a_failed_read_is_logged_and_listening_goes_on(self) -> None:
        consent = FakeConsent({GUILD: {ALICE}}, {GUILD: set()})

        async def broken(guild_id: int) -> frozenset[int]:
            raise RuntimeError("boom")

        consent.stored = broken  # type: ignore[method-assign]
        stop = AsyncMock()
        follower = self.build(consent, stop)
        with self.assertLogs("dmbot.consent_site", "ERROR"):
            await self.run_follow(follower, listener([consent_notify.payload(GUILD, ALICE)]))
        stop.assert_not_awaited()


class Cache(DatabaseTest):
    async def test_the_database_can_be_read_without_changing_memory(self) -> None:
        bot = ConsentStore(self.db)
        await bot.grant(GUILD, ALICE)
        self.assertTrue(bot.has_consent(GUILD, ALICE))
        async with self.db.guild(GUILD) as conn:  # the website's process deletes the row
            await conn.execute("DELETE FROM consent WHERE guild_id = %s", (GUILD,))
        self.assertEqual(bot.cached_guilds(), [GUILD])
        self.assertEqual(await bot.stored(GUILD), frozenset())
        # Reading the database changes no memory: the bot stops the person itself, so the DM
        # is told, and only then does memory change.
        self.assertTrue(bot.has_consent(GUILD, ALICE))
        self.assertEqual(bot.consenting_now(GUILD), frozenset({ALICE}))

    async def test_the_in_transaction_step_runs_only_when_a_row_was_removed(self) -> None:
        bot = ConsentStore(self.db)
        await bot.grant(GUILD, ALICE)
        ran: list[int] = []

        async def note(conn: Any) -> None:
            ran.append(1)

        self.assertTrue(await bot.revoke(GUILD, ALICE, in_transaction=note))
        self.assertFalse(await bot.revoke(GUILD, ALICE, in_transaction=note))
        self.assertEqual(ran, [1])

    async def test_a_stop_while_the_listener_was_down_is_stopped_on_reconnect(self) -> None:
        bot = ConsentStore(self.db)
        await bot.grant(GUILD, ALICE)
        await bot.grant(GUILD, BOB)
        await bot.consenting(GUILD)  # memory holds both, as in a live session
        async with self.db.guild(GUILD) as conn:  # deleted while nobody listened: no nudge
            await conn.execute("DELETE FROM consent WHERE user_id = %s", (ALICE,))
        stopped: list[tuple[int, int, bool]] = []

        async def withdraw(guild_id: int, user_id: int) -> None:
            # What the bot's withdraw path sees: the person still held, so the DM is told.
            stopped.append((guild_id, user_id, bot.has_consent(guild_id, user_id)))
            bot.stop_now(guild_id, user_id)

        follower = SiteStops(
            is_ours=lambda guild_id: True,
            consent=bot,
            stop=withdraw,
            spawn=lambda work, name: asyncio.ensure_future(work),
        )
        await follower.recheck_all()
        self.assertEqual(stopped, [(GUILD, ALICE, True)])
        self.assertTrue(bot.has_consent(GUILD, BOB))

    async def test_a_real_notification_reaches_the_follower(self) -> None:
        bot = ConsentStore(self.db)
        await bot.grant(GUILD, ALICE)
        await bot.consenting(GUILD)
        listening = asyncio.Event()
        stopped = asyncio.Event()

        async def withdraw(guild_id: int, user_id: int) -> None:
            bot.stop_now(guild_id, user_id)
            stopped.set()

        def spawn(work: Awaitable[None], name: str) -> object:
            task = asyncio.ensure_future(work)
            if name == "consent-recheck":  # the connect check: the listener is now in effect
                task.add_done_callback(lambda _t: listening.set())
            return task

        follower = SiteStops(is_ours=lambda guild_id: True, consent=bot, stop=withdraw, spawn=spawn)
        task = asyncio.ensure_future(follower.follow(self.db.listen))
        try:
            await asyncio.wait_for(listening.wait(), 10)
            # The row is deleted and the nudge sent in one transaction, as the website does.
            await ConsentStore(self.db).revoke(
                GUILD, ALICE, in_transaction=lambda conn: consent_notify.send(conn, GUILD, ALICE)
            )
            await asyncio.wait_for(stopped.wait(), 10)
            self.assertFalse(bot.has_consent(GUILD, ALICE))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
