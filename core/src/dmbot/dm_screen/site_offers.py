"""Offers made on the website reach the person offered through the bot (#690).

The website saves the offer as not sent yet and announces it on
`offer_notify.CHANNEL`. The process that serves that server claims the offer (so it's
sent once, whichever process or sweep sees it first) and sends the same private message
as the Discord button, with `deliver_offer`. If the person can't be reached, the offer
is taken back and the owner is told, if DMbot can reach them.

Every time it starts listening (start-up, or after the connection dropped), it also
sends any open offer of its servers that hasn't been sent: those may have been
announced while nobody was listening.
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
from dmbot.dm_screen.handover import NO_PINGS, deliver_offer
from dmbot.dm_screen.handover import _md as md
from dmbot.logs import log_context

log = logging.getLogger(__name__)

# Seconds to wait before listening again after the connection failed, longer each time
# it fails without ever connecting.
RECONNECT_DELAY_S = (1.0, 5.0, 30.0, 60.0)

# To the owner, when an offer they made on the website couldn't be sent.
NOT_SENT = (
    "🤝 DMbot couldn't send **{name}** your offer of **{campaign}** (server **{server}**): "
    "they may have left the server, or they don't accept messages from server members. "
    "The offer was taken back. Ask them to allow messages from that server, then offer it "
    "again."
)


class OfferStore(Protocol):
    async def claim_delivery(
        self, guild_id: int, offer_id: int, now: int
    ) -> HandoverOffer | None: ...

    async def undelivered_offers(self, guild_id: int, now: int) -> list[int]: ...

    async def get(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def withdraw_handover(
        self, guild_id: int, offer_id: int, user_id: int, now: int
    ) -> str: ...


Listen = Callable[[str, Callable[[], None]], AsyncGenerator[str, None]]
Deliver = Callable[[discord.Guild, Campaign, HandoverOffer], Awaitable[bool]]


class SiteOffers:
    """Sends the private message for offers made on the website. Run `follow` for as
    long as the bot runs."""

    def __init__(
        self,
        store: OfferStore,
        *,
        get_guild: Callable[[int], discord.Guild | None],
        guild_ids: Callable[[], Iterable[int]],
        wait_until_ready: Callable[[], Awaitable[None]],
        spawn: Callable[[Awaitable[None], str], object],
        deliver: Deliver = deliver_offer,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._store = store
        self._get_guild = get_guild
        self._guild_ids = guild_ids
        self._wait_until_ready = wait_until_ready
        self._spawn = spawn
        self._deliver = deliver
        self._sleep = sleep
        self._now = now
        self._sweeping = asyncio.Lock()

    async def follow(self, listen: Listen) -> None:
        """Send offers as they're announced, until cancelled. Every time the listener is
        (re)connected, a sweep sends what was announced while it wasn't listening."""
        failures = 0

        def on_listening() -> None:
            nonlocal failures
            failures = 0
            self._spawn(self.sweep(), "offer-sweep")

        while True:
            try:
                # Closed straight away when cancelled, so its connection closes too.
                async with contextlib.aclosing(
                    listen(offer_notify.CHANNEL, on_listening)
                ) as stream:
                    async for raw in stream:
                        ids = offer_notify.parse(raw)
                        if ids is not None:
                            self._spawn(self.send(*ids), "site-offer")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the connection dropped; carry on without crashing
                log.warning("Lost hand-over offer notifications (%s); will check again", exc)
            delay = RECONNECT_DELAY_S[min(failures, len(RECONNECT_DELAY_S) - 1)]
            failures += 1
            await self._sleep(delay)

    async def sweep(self) -> None:
        """Send every open offer of this process's servers that hasn't been sent. Waits
        until Discord has said which servers those are. One sweep at a time."""
        await self._wait_until_ready()
        async with self._sweeping:
            for guild_id in list(self._guild_ids()):
                try:
                    offer_ids = await self._store.undelivered_offers(guild_id, self._now())
                except Exception:
                    log.exception("Couldn't check for hand-over offers to send")
                    continue
                for offer_id in offer_ids:
                    await self.send(guild_id, offer_id)

    async def send(self, guild_id: int, offer_id: int) -> None:
        """Send one offer's private message, if this process serves its server and nobody
        has sent it yet. Never raises."""
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
        if offer is None:  # sent already, or answered, taken back or expired
            return
        campaign = await self._store.get(guild.id, offer.campaign_id)
        if campaign is None:  # deleted since (its offers go with it)
            return
        if await self._deliver(guild, campaign, offer):
            log.info("Sent a hand-over offer made on the website (offer %s)", offer.id)
            return
        await self._store.withdraw_handover(guild.id, offer.id, offer.from_user_id, self._now())
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
