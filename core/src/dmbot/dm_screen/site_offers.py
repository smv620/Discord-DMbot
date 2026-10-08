"""Offers made on the website reach the person offered through the bot (#690).

The website saves the offer as not sent yet and announces it on
`offer_notify.CHANNEL`. The process that serves that server claims the offer for a few
minutes (so it's sent once, whichever process or sweep sees it first), sends the same
private message as the Discord button with `deliver_offer`, then marks it sent. If the
person isn't in the server or doesn't take messages, the offer is taken back and the
owner is told. If Discord only failed for a moment, the claim is let go and a later
sweep tries again; a claim left by a process that stopped mid-way lapses the same way.

Sweeps send any open offer of this process's servers that hasn't been sent: every time
it starts listening (announcements may have come while nobody listened), when Discord
makes a server available again or DMbot joins one, and once an hour.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from typing import Protocol

import discord

from dmbot.campaigns import Campaign, offer_notify
from dmbot.campaigns.models import HandoverOffer
from dmbot.dm_screen.handover import NO_PINGS, deliver_offer, md
from dmbot.logs import log_context

log = logging.getLogger(__name__)

# Seconds to wait before listening again after the connection failed, longer each time
# it fails before it has stayed up for STEADY_S.
RECONNECT_DELAY_S = (1.0, 5.0, 30.0, 60.0)
STEADY_S = 60.0
SWEEP_EVERY_S = 3600.0

# To the owner, when an offer they made on the website couldn't be sent. Keep in step
# with handover.UNREACHABLE (the same, for an offer made in Discord).
NOT_SENT = (
    "DMbot couldn't send **{name}** your offer of **{campaign}** (server **{server}**), so "
    "it was taken back. Check they're still in that server, and ask them to allow direct "
    "messages from that server's members (their privacy settings for that server). Then "
    "offer it again on the DMbot website."
)


class OfferStore(Protocol):
    async def claim_delivery(
        self, guild_id: int, offer_id: int, now: int
    ) -> HandoverOffer | None: ...

    async def confirm_delivery(self, guild_id: int, offer: HandoverOffer, now: int) -> None: ...

    async def release_delivery(self, guild_id: int, offer: HandoverOffer) -> None: ...

    async def undelivered_offers(self, guild_id: int, now: int) -> list[int]: ...

    async def get(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def withdraw_handover(
        self, guild_id: int, offer_id: int, user_id: int, now: int
    ) -> str: ...


Listen = Callable[[str, Callable[[], None]], AsyncGenerator[str, None]]
# True: sent. False: the person can't be reached. Raises discord.HTTPException when
# Discord failed for another reason (try again later).
Deliver = Callable[[discord.Guild, Campaign, HandoverOffer], Awaitable[bool]]


async def _deliver(guild: discord.Guild, campaign: Campaign, offer: HandoverOffer) -> bool:
    return await deliver_offer(guild, campaign, offer, raise_if_discord_fails=True)


class SiteOffers:
    """Sends the private message for offers made on the website. Run `follow` and
    `every_hour` for as long as the bot runs."""

    def __init__(
        self,
        store: OfferStore,
        *,
        get_guild: Callable[[int], discord.Guild | None],
        guild_ids: Callable[[], Iterable[int]],
        wait_until_ready: Callable[[], Awaitable[None]],
        spawn: Callable[[Awaitable[None], str], object],
        deliver: Deliver = _deliver,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], int] = lambda: int(time.time()),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._get_guild = get_guild
        self._guild_ids = guild_ids
        self._wait_until_ready = wait_until_ready
        self._spawn = spawn
        self._deliver = deliver
        self._sleep = sleep
        self._now = now
        self._clock = clock
        self._sweeping = asyncio.Lock()
        self._sweep_waiting = False

    async def follow(self, listen: Listen) -> None:
        """Send offers as they're announced, until cancelled. Every time the listener is
        (re)connected, a sweep sends what was announced while it wasn't listening."""
        failures = 0
        connected_at: float | None = None

        def on_listening() -> None:
            nonlocal connected_at
            connected_at = self._clock()
            self._spawn(self.sweep(), "offer-sweep")

        while True:
            connected_at = None
            try:
                # Closed straight away when cancelled, so its connection closes too.
                async with contextlib.aclosing(
                    listen(offer_notify.CHANNEL, on_listening)
                ) as stream:
                    async for raw in stream:
                        ids = offer_notify.parse(raw)
                        # Every process hears every announcement: act only on ours.
                        if ids is not None and self._get_guild(ids[0]) is not None:
                            self._spawn(self.send(*ids), "site-offer")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the connection dropped; carry on without crashing
                log.warning("Lost hand-over offer notifications (%s); will check again", exc)
            if connected_at is not None and self._clock() - connected_at >= STEADY_S:
                failures = 0  # it had been working: start the waits again from the shortest
            delay = RECONNECT_DELAY_S[min(failures, len(RECONNECT_DELAY_S) - 1)]
            failures += 1
            await self._sleep(delay)

    async def every_hour(self) -> None:
        """A sweep every hour, until cancelled: catches a claim that lapsed (a process
        stopped mid-way, or Discord failed for a moment)."""
        while True:
            await self._sleep(SWEEP_EVERY_S)
            await self.sweep()

    async def sweep(self) -> None:
        """Send every open offer of this process's servers that hasn't been sent. Waits
        until Discord has said which servers those are. One sweep at a time, and at most
        one more waiting (a connection that keeps dropping doesn't pile them up)."""
        if self._sweep_waiting:
            return
        self._sweep_waiting = True
        try:
            await self._wait_until_ready()
            await self._sweeping.acquire()
        finally:
            self._sweep_waiting = False
        try:
            for guild_id in list(self._guild_ids()):
                await self.sweep_server(guild_id)
        finally:
            self._sweeping.release()

    async def sweep_server(self, guild_id: int) -> None:
        """Send this server's open offers that haven't been sent. Never raises."""
        try:
            offer_ids = await self._store.undelivered_offers(guild_id, self._now())
        except Exception:
            with log_context(guild_id=guild_id):
                log.exception("Couldn't check for hand-over offers to send")
            return
        for offer_id in offer_ids:
            await self.send(guild_id, offer_id)

    async def send(self, guild_id: int, offer_id: int) -> None:
        """Send one offer's private message, if this process serves its server and nobody
        has claimed it. Never raises."""
        guild = self._get_guild(guild_id)
        if guild is None:  # another process's server, or one DMbot has left
            return
        with log_context(guild_id=guild_id):
            try:
                await self._send(guild, offer_id)
            except Exception:
                log.exception("Couldn't send a hand-over offer made on the website")

    async def _send(self, guild: discord.Guild, offer_id: int) -> None:
        offer = await self._store.claim_delivery(guild.id, offer_id, self._now())
        if offer is None:  # being sent or sent already, or answered, taken back, expired
            return
        try:
            campaign = await self._store.get(guild.id, offer.campaign_id)
            if campaign is None:  # deleted since: the offer went with it, no claim to let go
                return
            reached = await self._deliver(guild, campaign, offer)
        except BaseException as exc:
            # Not sent: let the claim go so a later sweep tries again.
            with contextlib.suppress(Exception):
                await self._store.release_delivery(guild.id, offer)
            if isinstance(exc, discord.HTTPException):
                log.warning("Discord failed sending hand-over offer %s (%s)", offer.id, exc)
                return
            raise
        if reached:
            await self._store.confirm_delivery(guild.id, offer, self._now())
            log.info("Sent a hand-over offer made on the website (offer %s)", offer.id)
            return
        result = await self._store.withdraw_handover(
            guild.id, offer.id, offer.from_user_id, self._now()
        )
        if result == "withdrawn":  # not if it was answered or expired meanwhile
            await _tell_owner(guild, offer, campaign)


async def _tell_owner(guild: discord.Guild, offer: HandoverOffer, campaign: Campaign) -> None:
    """Best effort: the owner may have left the server or closed their messages too. The
    account page shows the offer as taken back either way."""
    with contextlib.suppress(discord.HTTPException):
        owner = guild.get_member(offer.from_user_id) or await guild.fetch_member(offer.from_user_id)
        await owner.send(
            NOT_SENT.format(
                name=md(offer.to_name), campaign=md(campaign.name), server=md(guild.name)
            ),
            allowed_mentions=NO_PINGS,
        )
