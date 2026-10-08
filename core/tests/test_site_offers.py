"""Offers made on the website reach the person through the bot (#690), without Discord
or a database."""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, cast

import discord

from dmbot.campaigns import Campaign, offer_notify
from dmbot.campaigns.models import HandoverOffer
from dmbot.dm_screen.site_offers import NOT_SENT, SiteOffers

GUILD, OTHER_GUILD = 111, 222
OWNER, BUYER = 7, 9
NOW = 1_000


def offer(offer_id: int = 1, guild_id: int = GUILD) -> HandoverOffer:
    return HandoverOffer(
        id=offer_id,
        guild_id=guild_id,
        campaign_id="c1",
        from_user_id=OWNER,
        to_user_id=BUYER,
        from_name="Oskar",
        to_name="Bea_*",
        created_at=NOW,
        status="open",
        decided_at=None,
    )


class FakeStore:
    def __init__(self, waiting: dict[int, list[int]] | None = None) -> None:
        self.waiting = waiting or {}
        self.claimed: set[tuple[int, int]] = set()
        self.withdrawn: list[tuple[int, int, int]] = []
        self.campaign: Any = cast(Any, type("C", (), {"id": "c1", "name": "Frost*maiden"})())
        self.fail_reads: set[int] = set()

    async def claim_delivery(self, guild_id: int, offer_id: int, now: int) -> HandoverOffer | None:
        if (guild_id, offer_id) in self.claimed or offer_id not in self.waiting.get(guild_id, []):
            return None
        self.claimed.add((guild_id, offer_id))
        return offer(offer_id, guild_id)

    async def undelivered_offers(self, guild_id: int, now: int) -> list[int]:
        if guild_id in self.fail_reads:
            raise RuntimeError("database down")
        return [i for i in self.waiting.get(guild_id, []) if (guild_id, i) not in self.claimed]

    async def get(self, guild_id: int, campaign_id: str) -> Campaign | None:
        return cast(Campaign | None, self.campaign)

    async def withdraw_handover(self, guild_id: int, offer_id: int, user_id: int, now: int) -> str:
        self.withdrawn.append((guild_id, offer_id, user_id))
        return "withdrawn"


class FakeMember:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str, **_: Any) -> None:
        self.sent.append(text)


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.name = "The Table"
        self.owner = FakeMember()

    def get_member(self, user_id: int) -> FakeMember | None:
        return self.owner if user_id == OWNER else None


class Harness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = FakeStore({GUILD: [1]})
        self.guilds = {GUILD: FakeGuild(GUILD)}
        self.delivered: list[int] = []
        self.reachable = True
        self.ready = asyncio.Event()
        self.ready.set()
        self.tasks: list[asyncio.Task[None]] = []

        async def deliver(guild: discord.Guild, campaign: Campaign, o: HandoverOffer) -> bool:
            self.delivered.append(o.id)
            return self.reachable

        async def wait_until_ready() -> None:
            await self.ready.wait()

        def spawn(work: Awaitable[None], name: str) -> None:
            self.tasks.append(asyncio.ensure_future(work))

        self.offers = SiteOffers(
            self.store,
            get_guild=lambda gid: cast(discord.Guild | None, self.guilds.get(gid)),
            guild_ids=lambda: list(self.guilds),
            wait_until_ready=wait_until_ready,
            spawn=spawn,
            deliver=deliver,
            now=lambda: NOW,
        )

    async def settle(self) -> None:
        while self.tasks:
            pending, self.tasks = self.tasks, []
            await asyncio.gather(*pending)


class Payload(unittest.TestCase):
    def test_only_the_two_ids_go_out_and_come_back(self) -> None:
        self.assertEqual(offer_notify.payload(GUILD, 5), "111:5")
        self.assertEqual(offer_notify.parse("111:5"), (GUILD, 5))

    def test_anything_else_is_ignored(self) -> None:
        for raw in ("", "111", "111:5:1", "a:5", "111:-5", "0:5", "111:٣", "1" * 25 + ":5"):
            self.assertIsNone(offer_notify.parse(raw), raw)


class Sending(Harness):
    async def test_an_announced_offer_is_sent_once(self) -> None:
        await self.offers.send(GUILD, 1)
        await self.offers.send(GUILD, 1)  # announced again, or the sweep found it too
        self.assertEqual(self.delivered, [1])
        self.assertEqual(self.store.withdrawn, [])

    async def test_another_process_s_server_is_left_alone(self) -> None:
        self.store.waiting[OTHER_GUILD] = [2]
        await self.offers.send(OTHER_GUILD, 2)
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.store.claimed, set())  # never claimed, so its own bot sends it

    async def test_someone_unreachable_gets_the_offer_taken_back_and_the_owner_told(self) -> None:
        self.reachable = False
        await self.offers.send(GUILD, 1)
        self.assertEqual(self.store.withdrawn, [(GUILD, 1, OWNER)])
        (note,) = self.guilds[GUILD].owner.sent
        self.assertEqual(
            note, NOT_SENT.format(name="Bea\\_\\*", campaign="Frost\\*maiden", server="The Table")
        )

    async def test_a_deleted_campaign_sends_nothing(self) -> None:
        self.store.campaign = None
        await self.offers.send(GUILD, 1)
        self.assertEqual(self.delivered, [])

    async def test_a_failure_is_logged_never_raised(self) -> None:
        async def broken(guild_id: int, offer_id: int, now: int) -> HandoverOffer | None:
            raise RuntimeError("database down")

        self.store.claim_delivery = broken  # type: ignore[method-assign]
        with self.assertLogs("dmbot.dm_screen.site_offers", "ERROR"):
            await self.offers.send(GUILD, 1)


class Sweeping(Harness):
    async def test_the_sweep_sends_what_was_announced_while_nobody_listened(self) -> None:
        self.store.waiting = {GUILD: [1, 3]}
        self.guilds[OTHER_GUILD] = FakeGuild(OTHER_GUILD)
        self.store.fail_reads = {OTHER_GUILD}  # one server failing doesn't stop the rest
        with self.assertLogs("dmbot.dm_screen.site_offers", "ERROR"):
            await self.offers.sweep()
        self.assertEqual(self.delivered, [1, 3])

    async def test_the_sweep_waits_until_discord_says_which_servers_are_ours(self) -> None:
        self.ready.clear()
        sweep = asyncio.ensure_future(self.offers.sweep())
        await asyncio.sleep(0)
        self.assertEqual(self.delivered, [])
        self.ready.set()
        await sweep
        self.assertEqual(self.delivered, [1])


class Following(Harness):
    async def test_listening_sweeps_then_sends_each_announcement(self) -> None:
        self.store.waiting = {GUILD: [1, 2]}
        announced: asyncio.Queue[str] = asyncio.Queue()

        async def listen(channel: str, on_listening: Callable[[], None]) -> AsyncGenerator[str]:
            self.assertEqual(channel, offer_notify.CHANNEL)
            on_listening()
            while True:
                yield await announced.get()

        follow = asyncio.ensure_future(self.offers.follow(listen))
        await asyncio.sleep(0)
        await self.settle()
        self.assertEqual(sorted(self.delivered), [1, 2])  # the sweep
        self.store.waiting[GUILD].append(4)
        announced.put_nowait("not ours")
        announced.put_nowait(offer_notify.payload(GUILD, 4))
        for _ in range(5):
            await asyncio.sleep(0)
        await self.settle()
        self.assertEqual(sorted(self.delivered), [1, 2, 4])
        follow.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follow

    async def test_a_dropped_connection_listens_again_and_sweeps_again(self) -> None:
        calls = 0
        slept: list[float] = []

        async def listen(channel: str, on_listening: Callable[[], None]) -> AsyncGenerator[str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("connection refused")  # never connected
            on_listening()
            if calls == 2:
                raise OSError("connection lost")
            await asyncio.Event().wait()
            yield ""

        async def sleep(seconds: float) -> None:
            slept.append(seconds)

        self.offers._sleep = sleep
        follow = asyncio.ensure_future(self.offers.follow(listen))
        with self.assertLogs("dmbot.dm_screen.site_offers", "WARNING"):
            for _ in range(10):
                await asyncio.sleep(0)
        await self.settle()
        self.assertEqual(calls, 3)
        self.assertEqual(slept, [1.0, 1.0])  # connecting resets the wait
        self.assertEqual(self.delivered, [1])  # swept twice, sent once
        follow.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follow


if __name__ == "__main__":
    unittest.main()
