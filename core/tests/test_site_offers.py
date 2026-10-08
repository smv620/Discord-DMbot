"""Offers made on the website reach the person through the bot (#690), without Discord
or a database."""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import replace
from typing import Any, cast
from unittest import mock

import discord

from dmbot.campaigns import Campaign, offer_notify
from dmbot.campaigns.models import HANDOVER_DAYS, HandoverOffer
from dmbot.dm_screen import site_offers
from dmbot.dm_screen.handover import OFFER_EXPIRED, TOLD_EXPIRED, deliver_offer
from dmbot.dm_screen.site_offers import NOT_SENT, SiteOffers

GUILD, OTHER_GUILD = 111, 222
OWNER, BUYER = 7, 9
NOW = 1_000


def offer(offer_id: int = 1, guild_id: int = GUILD, claimed_at: int | None = None) -> HandoverOffer:
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
        claimed_at=claimed_at,
    )


def http_error(status: int, cls: type[discord.HTTPException] = discord.HTTPException) -> Any:
    response = mock.Mock(status=status, reason="nope")
    return cls(response, "nope")


class FakeStore:
    """Offers waiting per server; a claim holds until confirmed or released."""

    def __init__(self, waiting: dict[int, list[int]] | None = None) -> None:
        self.waiting = waiting or {}
        self.claimed: set[tuple[int, int]] = set()
        self.sent: set[tuple[int, int]] = set()
        self.withdrawn: list[tuple[int, int, int]] = []
        self.withdraw_result = "withdrawn"
        self.message_ids: dict[tuple[int, int], int | None] = {}
        self.to_end: list[HandoverOffer] = []
        self.campaign: Any = type("C", (), {"id": "c1", "name": "Frost*maiden"})()
        self.fail_reads: set[int] = set()

    def _unsent(self, guild_id: int, offer_id: int) -> bool:
        key = (guild_id, offer_id)
        return offer_id in self.waiting.get(guild_id, []) and key not in self.claimed | self.sent

    async def claim_delivery(self, guild_id: int, offer_id: int, now: int) -> HandoverOffer | None:
        if not self._unsent(guild_id, offer_id):
            return None
        self.claimed.add((guild_id, offer_id))
        return offer(offer_id, guild_id, claimed_at=now)

    async def confirm_delivery(
        self, guild_id: int, o: HandoverOffer, now: int, message_id: int | None = None
    ) -> None:
        self.claimed.discard((guild_id, o.id))
        self.sent.add((guild_id, o.id))
        self.message_ids[(guild_id, o.id)] = message_id

    async def offers_to_end(self, guild_id: int, now: int) -> list[HandoverOffer]:
        ended, self.to_end = self.to_end, []  # each is handed out once
        return ended

    async def release_delivery(self, guild_id: int, o: HandoverOffer) -> None:
        self.claimed.discard((guild_id, o.id))

    async def undelivered_offers(self, guild_id: int, now: int) -> list[int]:
        if guild_id in self.fail_reads:
            raise RuntimeError("database down")
        return [i for i in self.waiting.get(guild_id, []) if self._unsent(guild_id, i)]

    async def get(self, guild_id: int, campaign_id: str) -> Campaign | None:
        return cast(Campaign | None, self.campaign)

    async def withdraw_handover(self, guild_id: int, offer_id: int, user_id: int, now: int) -> str:
        self.withdrawn.append((guild_id, offer_id, user_id))
        return self.withdraw_result


class FakeMember:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edits: list[tuple[int, dict[str, Any]]] = []
        self.dm_channel = self

    async def send(self, text: str, **_: Any) -> None:
        self.sent.append(text)

    def get_partial_message(self, message_id: int) -> Any:
        async def edit(**changes: Any) -> None:
            self.edits.append((message_id, changes))

        return mock.Mock(edit=edit)


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.name = "The Table"
        self.owner = FakeMember()
        self.buyer = FakeMember()

    def get_member(self, user_id: int) -> FakeMember | None:
        return {OWNER: self.owner, BUYER: self.buyer}.get(user_id)


class Harness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = FakeStore({GUILD: [1]})
        self.guilds = {GUILD: FakeGuild(GUILD)}
        self.delivered: list[int] = []
        # What delivering does: True (sent), False (can't reach them), or an error.
        self.outcome: bool | BaseException = True
        self.message = mock.Mock(id=4242)
        self.ready = asyncio.Event()
        self.ready.set()
        self.tasks: list[asyncio.Task[None]] = []
        self.slept: list[float] = []
        self.clock = 0.0

        async def deliver(
            guild: discord.Guild, campaign: Campaign, o: HandoverOffer
        ) -> discord.Message | None:
            self.delivered.append(o.id)
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return cast(discord.Message, self.message) if self.outcome else None

        async def wait_until_ready() -> None:
            await self.ready.wait()

        def spawn(work: Awaitable[None], name: str) -> None:
            self.tasks.append(asyncio.ensure_future(work))

        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)
            await asyncio.sleep(0)

        self.offers = SiteOffers(
            self.store,
            get_guild=lambda gid: cast(discord.Guild | None, self.guilds.get(gid)),
            guild_ids=lambda: list(self.guilds),
            wait_until_ready=wait_until_ready,
            spawn=spawn,
            deliver=deliver,
            sleep=sleep,
            now=lambda: NOW,
            clock=lambda: self.clock,
        )

    async def settle(self) -> None:
        for _ in range(20):
            await asyncio.sleep(0)
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
        self.assertEqual(self.store.sent, {(GUILD, 1)})
        self.assertEqual(self.store.message_ids, {(GUILD, 1): 4242})  # for its buttons, later
        self.assertEqual(self.store.withdrawn, [])

    async def test_another_process_s_server_is_left_alone(self) -> None:
        self.store.waiting[OTHER_GUILD] = [2]
        await self.offers.send(OTHER_GUILD, 2)
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.store.claimed, set())  # never claimed, so its own bot sends it

    async def test_someone_unreachable_gets_the_offer_taken_back_and_the_owner_told(self) -> None:
        self.outcome = False
        await self.offers.send(GUILD, 1)
        self.assertEqual(self.store.withdrawn, [(GUILD, 1, OWNER)])
        (note,) = self.guilds[GUILD].owner.sent
        self.assertEqual(
            note, NOT_SENT.format(name="Bea\\_\\*", campaign="Frost\\*maiden", server="The Table")
        )
        self.assertIn("on the DMbot website", note)  # where to try again

    async def test_an_offer_answered_meanwhile_is_not_reported_as_taken_back(self) -> None:
        self.outcome = False
        self.store.withdraw_result = "gone"  # accepted on the site, or expired, meanwhile
        await self.offers.send(GUILD, 1)
        self.assertEqual(self.guilds[GUILD].owner.sent, [])

    async def test_a_discord_hiccup_lets_the_claim_go_for_a_later_try(self) -> None:
        self.outcome = http_error(503)
        with self.assertLogs("dmbot.dm_screen.site_offers", "WARNING"):
            await self.offers.send(GUILD, 1)
        self.assertEqual(self.store.withdrawn, [])  # never taken back for a hiccup
        self.assertEqual(self.store.claimed, set())
        self.outcome = True
        await self.offers.sweep()
        self.assertEqual(self.delivered, [1, 1])
        self.assertEqual(self.store.sent, {(GUILD, 1)})

    async def test_a_send_cut_short_lets_the_claim_go(self) -> None:
        self.outcome = asyncio.CancelledError()  # shutting down mid-send
        with self.assertRaises(asyncio.CancelledError):
            await self.offers.send(GUILD, 1)
        self.assertEqual(self.store.claimed, set())
        self.assertEqual(self.store.sent, set())

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


class DeliverOffer(unittest.IsolatedAsyncioTestCase):
    """The shared private message: only "not there" and "no messages" count as
    unreachable when the caller can try again."""

    async def outcome(self, error: Any, *, raise_if_discord_fails: bool) -> Any:
        member = mock.Mock(send=mock.AsyncMock(side_effect=error))
        guild = mock.Mock(id=GUILD, get_member=mock.Mock(return_value=member))
        guild.name = "The Table"
        campaign = mock.Mock(id="c1")
        campaign.name = "Frost"
        return await deliver_offer(
            guild, campaign, offer(), raise_if_discord_fails=raise_if_discord_fails
        )

    async def test_closed_messages_or_gone_are_unreachable(self) -> None:
        for error in (http_error(403, discord.Forbidden), http_error(404, discord.NotFound)):
            self.assertIsNone(await self.outcome(error, raise_if_discord_fails=True))

    async def test_the_message_sent_comes_back(self) -> None:
        sent = mock.Mock(id=77)
        member = mock.Mock(send=mock.AsyncMock(return_value=sent))
        guild = mock.Mock(id=GUILD, get_member=mock.Mock(return_value=member))
        guild.name = "The Table"
        campaign = mock.Mock(id="c1")
        campaign.name = "Frost"
        self.assertIs(await deliver_offer(guild, campaign, offer()), sent)

    async def test_other_discord_errors_are_raised_only_when_asked(self) -> None:
        self.assertIsNone(await self.outcome(http_error(503), raise_if_discord_fails=False))
        with self.assertRaises(discord.HTTPException):
            await self.outcome(http_error(503), raise_if_discord_fails=True)


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

    async def test_sweeps_never_pile_up(self) -> None:
        self.ready.clear()
        sweeps = [asyncio.ensure_future(self.offers.sweep()) for _ in range(5)]
        await asyncio.sleep(0)
        self.assertEqual(sum(s.done() for s in sweeps), 4)  # one waits, the rest give way
        self.ready.set()
        await asyncio.gather(*sweeps)
        self.assertEqual(self.delivered, [1])

    async def test_one_server_s_sweep(self) -> None:
        self.guilds[OTHER_GUILD] = FakeGuild(OTHER_GUILD)
        self.store.waiting = {GUILD: [1], OTHER_GUILD: [2]}
        await self.offers.sweep_server(OTHER_GUILD)
        self.assertEqual(self.delivered, [2])

    async def test_every_hour(self) -> None:
        hourly = asyncio.ensure_future(self.offers.every_hour())
        await self.settle()
        hourly.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hourly
        self.assertEqual(self.slept[0], site_offers.SWEEP_EVERY_S)
        self.assertEqual(self.delivered, [1])


class Expiring(Harness):
    def ended(self, message_id: int | None = 4242) -> HandoverOffer:
        return replace(offer(), status="expired", decided_at=NOW, message_id=message_id)

    async def test_the_owner_is_told_and_the_buttons_come_off(self) -> None:
        self.store.waiting = {}
        self.store.to_end = [self.ended()]
        await self.offers.sweep_server(GUILD)
        guild = self.guilds[GUILD]
        self.assertEqual(
            guild.owner.sent,
            [TOLD_EXPIRED.format(name="Bea\\_\\*", campaign="Frost\\*maiden", days=HANDOVER_DAYS)],
        )
        self.assertEqual(
            guild.buyer.edits,
            [(4242, {"content": OFFER_EXPIRED.format(days=HANDOVER_DAYS), "view": None})],
        )
        await self.offers.sweep_server(GUILD)  # announced once
        self.assertEqual(len(guild.owner.sent), 1)

    async def test_an_offer_without_a_kept_message_still_tells_the_owner(self) -> None:
        self.store.to_end = [self.ended(message_id=None)]
        await self.offers.end_expired(GUILD)
        self.assertEqual(len(self.guilds[GUILD].owner.sent), 1)
        self.assertEqual(self.guilds[GUILD].buyer.edits, [])

    async def test_people_who_left_are_skipped_quietly(self) -> None:
        self.store.to_end = [self.ended()]
        guild = self.guilds[GUILD]
        guild.get_member = lambda user_id: None  # type: ignore[method-assign]

        async def gone(user_id: int) -> FakeMember:
            raise http_error(404, discord.NotFound)

        guild.fetch_member = gone  # type: ignore[attr-defined]
        await self.offers.end_expired(GUILD)  # nothing raised, nothing logged as an error

    async def test_another_process_s_server_is_left_alone(self) -> None:
        self.store.to_end = [self.ended()]
        await self.offers.end_expired(OTHER_GUILD)
        self.assertEqual(len(self.store.to_end), 1)  # never claimed here


class Following(Harness):
    async def test_listening_sweeps_then_sends_each_announcement(self) -> None:
        self.store.waiting = {GUILD: [1, 2], OTHER_GUILD: [5]}
        announced: asyncio.Queue[str] = asyncio.Queue()

        async def listen(channel: str, on_listening: Callable[[], None]) -> AsyncGenerator[str]:
            self.assertEqual(channel, offer_notify.CHANNEL)
            on_listening()
            while True:
                yield await announced.get()

        follow = asyncio.ensure_future(self.offers.follow(listen))
        await self.settle()
        self.assertEqual(sorted(self.delivered), [1, 2])  # the sweep
        self.store.waiting[GUILD].append(4)
        for raw in ("not ours", offer_notify.payload(OTHER_GUILD, 5)):
            announced.put_nowait(raw)
        announced.put_nowait(offer_notify.payload(GUILD, 4))
        await self.settle()
        self.assertEqual(sorted(self.delivered), [1, 2, 4])
        follow.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follow

    async def test_a_link_that_keeps_dropping_waits_longer_each_time(self) -> None:
        calls = 0

        async def listen(channel: str, on_listening: Callable[[], None]) -> AsyncGenerator[str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("connection refused")  # never connected
            on_listening()
            if calls in (2, 3):
                raise OSError("dropped right away")  # connected, but not for long
            if calls == 4:
                self.clock += site_offers.STEADY_S  # up for a good while, then dropped
                raise OSError("dropped later")
            await asyncio.Event().wait()
            yield ""

        follow = asyncio.ensure_future(self.offers.follow(listen))
        with self.assertLogs("dmbot.dm_screen.site_offers", "WARNING"):
            await self.settle()
        self.assertEqual(calls, 5)
        self.assertEqual(self.slept, [1.0, 5.0, 30.0, 1.0])
        self.assertEqual(self.delivered, [1])  # swept four times, sent once
        follow.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follow


if __name__ == "__main__":
    unittest.main()
