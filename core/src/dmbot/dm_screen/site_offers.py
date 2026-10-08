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
makes a server available again or DMbot joins one, and once an hour. They also end
offers whose 7 days are up, wherever they were made: the owner is told, and the
buttons come off the person's message (at most an hour late).
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
from dmbot.campaigns.models import HANDOVER_DAYS, HandoverOffer
from dmbot.dm_screen.handover import (
    ACCEPTED,
    DECLINED,
    NO_PINGS,
    OFFER_EXPIRED,
    SCREEN_ACCEPTED,
    TOLD_ACCEPTED,
    TOLD_DECLINED,
    TOLD_EXPIRED,
    TOLD_EXPIRED_UNSENT,
    TOLD_WITHDRAWN,
    deliver_offer,
    md,
)
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

    async def confirm_delivery(
        self, guild_id: int, offer: HandoverOffer, now: int, message_id: int
    ) -> HandoverOffer | None: ...

    async def offers_to_end(self, guild_id: int, now: int) -> list[HandoverOffer]: ...

    async def get_offer(self, guild_id: int, offer_id: int, now: int) -> HandoverOffer | None: ...

    async def claim_end_notice(self, guild_id: int, offer_id: int, now: int) -> bool: ...

    async def release_end_notice(self, guild_id: int, offer_id: int, told_at: int) -> None: ...

    async def release_delivery(self, guild_id: int, offer: HandoverOffer) -> None: ...

    async def undelivered_offers(self, guild_id: int, now: int) -> list[int]: ...

    async def get(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def withdraw_handover(
        self, guild_id: int, offer_id: int, user_id: int, now: int
    ) -> str: ...


Listen = Callable[[str, Callable[[], None]], AsyncGenerator[str, None]]
# The message sent, or None when the person can't be reached. Raises
# discord.HTTPException when Discord failed for another reason (try again later).
Deliver = Callable[[discord.Guild, Campaign, HandoverOffer], Awaitable[discord.Message | None]]


async def _deliver(
    guild: discord.Guild, campaign: Campaign, offer: HandoverOffer
) -> discord.Message | None:
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
        post: Callable[[int, str], Awaitable[object]] | None = None,
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
        self._post = post  # a message in a channel: the #dm-screen note on an accept
        self._sleep = sleep
        self._now = now
        self._clock = clock
        self._sweeping = asyncio.Lock()
        self._sweep_waiting = False

    async def follow(self, listen: Listen) -> None:
        """Send offers as they're announced, until cancelled. Every time the listener is
        (re)connected, a sweep sends what was announced while it wasn't listening."""
        await self._follow(
            listen,
            offer_notify.CHANNEL,
            lambda: self._spawn(self.sweep(), "offer-sweep"),
            lambda ids: self._spawn(self.send(*ids), "site-offer"),
        )

    async def follow_decided(self, listen: Listen) -> None:
        """Tell the other person when an offer is answered or taken back on the website
        (#737), until cancelled. One announced while nobody listened isn't told: the
        account page shows it either way."""
        await self._follow(
            listen,
            offer_notify.DECIDED,
            lambda: None,
            lambda ids: self._spawn(self.decided(*ids), "site-decision"),
            # Not yet: before Discord sends the server list, no server looks like ours,
            # and a restart has that window every time. decided() waits, then checks. So
            # before ready each answer (for any server) is one waiting task: bounded by
            # how fast people click, and close() cancels them.
            ours_only=False,
        )

    async def _follow(
        self,
        listen: Listen,
        channel: str,
        on_connect: Callable[[], object],
        act: Callable[[tuple[int, int]], object],
        *,
        ours_only: bool = True,
    ) -> None:
        failures = 0
        connected_at: float | None = None

        def on_listening() -> None:
            nonlocal connected_at
            connected_at = self._clock()
            on_connect()

        while True:
            connected_at = None
            try:
                # Closed straight away when cancelled, so its connection closes too.
                async with contextlib.aclosing(listen(channel, on_listening)) as stream:
                    async for raw in stream:
                        ids = offer_notify.parse(raw)
                        # Every process hears every announcement: act only on ours.
                        if ids is not None and (
                            not ours_only or self._get_guild(ids[0]) is not None
                        ):
                            act(ids)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the connection dropped; carry on without crashing
                log.warning("Lost notifications on %s (%s); will listen again", channel, exc)
            if connected_at is not None and self._clock() - connected_at >= STEADY_S:
                failures = 0  # it had been working: start the waits again from the shortest
            delay = RECONNECT_DELAY_S[min(failures, len(RECONNECT_DELAY_S) - 1)]
            failures += 1
            await self._sleep(delay)

    async def decided(self, guild_id: int, offer_id: int) -> None:
        """An offer answered or taken back on the website: the same private messages the
        Discord buttons send. Accepted or declined: the owner hears, and the person's
        offer message says what they chose. Taken back: the person's offer message says
        so, unless it was never sent (then they never knew of it). Never raises; each
        message is best effort. No claim: while two processes both serve the server (a
        rolling deploy), the owner could hear twice. Waits until Discord has said which
        servers are this process's (an answer made while the bot starts isn't lost)."""
        await self._wait_until_ready()
        guild = self._get_guild(guild_id)
        if guild is None:
            return
        with log_context(guild_id=guild_id):
            try:
                offer = await self._store.get_offer(guild_id, offer_id, self._now())
                campaign = (
                    None if offer is None else await self._store.get(guild_id, offer.campaign_id)
                )
                if offer is None or campaign is None:
                    return
                await _tell_decision(guild, offer, campaign)
                if offer.status == "accepted":  # as the Discord Accept button does
                    await self._screen_note(campaign, offer)
            except Exception as exc:
                # Only the error's kind: a database error's text can quote the row (names).
                log.error(
                    "Couldn't tell about a website answer to offer %s (%s)",
                    offer_id,
                    type(exc).__name__,
                )

    async def _screen_note(self, campaign: Campaign, offer: HandoverOffer) -> None:
        """The accept noted in the campaign's #dm-screen, as for the Discord button (it
        changes whose plan the table uses). Best effort."""
        if self._post is None or campaign.dm_screen_channel_id is None:
            return
        names = {"name": md(offer.to_name), "campaign": md(campaign.name)}
        try:
            await self._post(campaign.dm_screen_channel_id, SCREEN_ACCEPTED.format(**names))
        except Exception as exc:
            log.warning("Couldn't note a website accept on the DM screen (%s)", type(exc).__name__)

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
        """Send this server's open offers that haven't been sent, and end those whose
        days are up. Never raises."""
        try:
            offer_ids = await self._store.undelivered_offers(guild_id, self._now())
        except Exception:
            with log_context(guild_id=guild_id):
                log.exception("Couldn't check for hand-over offers to send")
            offer_ids = []
        for offer_id in offer_ids:
            await self.send(guild_id, offer_id)
        await self.end_expired(guild_id)

    async def end_expired(self, guild_id: int) -> None:
        """Tell the owner of each offer whose days are up, and take the buttons off the
        person's message. Each is announced once; if Discord failed for a moment, the
        next sweep tries again. Never raises."""
        guild = self._get_guild(guild_id)
        if guild is None:
            return
        with log_context(guild_id=guild_id):
            try:
                ended = await self._store.offers_to_end(guild_id, self._now())
            except Exception:
                log.exception("Couldn't check for hand-over offers that ran out of days")
                return
            for offer in ended:
                await self._end(guild, offer)

    async def _end(self, guild: discord.Guild, offer: HandoverOffer) -> None:
        told_at = self._now()
        try:
            # Read first: a database hiccup here leaves the notice unclaimed for the next
            # sweep, rather than claimed with nobody told.
            campaign = await self._store.get(guild.id, offer.campaign_id)
            if not await self._store.claim_end_notice(guild.id, offer.id, told_at):
                return  # someone else is telling them
        except Exception:
            log.exception("Couldn't announce the end of hand-over offer %s", offer.id)
            return
        try:
            if campaign is not None:  # deleted since: its offers go with it
                await _tell_expired(guild, offer, campaign)
        except BaseException as exc:
            # Not told: let it go, so the next sweep tries again.
            with contextlib.suppress(Exception):
                await self._store.release_end_notice(guild.id, offer.id, told_at)
            if not isinstance(exc, Exception):
                raise
            if isinstance(exc, discord.HTTPException):
                log.warning("Discord failed announcing offer %s's end (%s)", offer.id, exc)
            else:
                log.exception("Couldn't announce the end of hand-over offer %s", offer.id)

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
            message = await self._deliver(guild, campaign, offer)
        except BaseException as exc:
            # Not sent: let the claim go so a later sweep tries again.
            with contextlib.suppress(Exception):
                await self._store.release_delivery(guild.id, offer)
            if isinstance(exc, discord.HTTPException):
                log.warning("Discord failed sending hand-over offer %s (%s)", offer.id, exc)
                return
            raise
        if message is not None:
            now_it = await self._store.confirm_delivery(guild.id, offer, self._now(), message.id)
            log.info("Sent a hand-over offer made on the website (offer %s)", offer.id)
            # None: this claim had lapsed and someone sent it again; the message just sent
            # keeps its buttons, which only say the offer is settled if pressed.
            if now_it is not None and now_it.status != "open":
                # Answered or taken back on the website while it was going out: then
                # decided() saw it unsent and told only the owner. The message just sent
                # has live buttons: say what happened instead (#797). In one narrow
                # ordering decided() edits it too, with the same words: harmless.
                try:
                    await _tell_decision(guild, now_it, campaign, tell_owner=False)
                except Exception as exc:  # the offer was sent: say this part failed
                    log.error(
                        "Couldn't change offer %s's message after a website answer (%s)",
                        offer.id,
                        type(exc).__name__,
                    )
            return
        result = await self._store.withdraw_handover(
            guild.id, offer.id, offer.from_user_id, self._now()
        )
        if result == "withdrawn":  # not if it was answered or expired meanwhile
            await _tell_owner(guild, offer, campaign)


async def _tell_decision(
    guild: discord.Guild, offer: HandoverOffer, campaign: Campaign, *, tell_owner: bool = True
) -> None:
    """The same words as the Discord buttons. The owner hears of an accept or a no thanks
    (unless `tell_owner` is off: already told). The person's offer message, if it was ever
    sent: edited to say what happened (its buttons go); if it can't be edited, a new
    message only for a take-back (they chose an accept or a no thanks themselves, so the
    site told them)."""
    owner, person = md(offer.from_name), md(offer.to_name)
    name = md(campaign.name)
    if offer.status == "accepted":
        to_owner: str | None = TOLD_ACCEPTED.format(name=person, campaign=name)
        to_person = ACCEPTED.format(campaign=name, server=md(guild.name), owner=owner)
    elif offer.status == "declined":
        to_owner = TOLD_DECLINED.format(name=person, campaign=name)
        to_person = DECLINED.format(owner=owner)
    elif offer.status == "withdrawn":
        to_owner = None
        to_person = TOLD_WITHDRAWN.format(owner=owner, campaign=name)
    else:  # still open, or ended another way: nothing to tell
        return
    if to_owner is not None and tell_owner:
        with contextlib.suppress(discord.HTTPException):
            member = await _member(guild, offer.from_user_id)
            await member.send(to_owner, allowed_mentions=NO_PINGS)
    if offer.delivered_at is None:  # never sent to them: nothing of theirs to change
        return
    with contextlib.suppress(discord.HTTPException):
        member = await _member(guild, offer.to_user_id)
        if offer.message_id is not None:  # the offer message: its buttons go
            try:
                channel = member.dm_channel or await member.create_dm()
                await channel.get_partial_message(offer.message_id).edit(
                    content=to_person, view=None, allowed_mentions=NO_PINGS
                )
                return
            except discord.HTTPException:  # deleted, say: tell them in a new one instead
                if offer.status != "withdrawn":
                    return  # they chose it themselves; the site told them
        if offer.status == "withdrawn":  # no message to change: tell them anyway
            await member.send(to_person, allowed_mentions=NO_PINGS)


async def _member(guild: discord.Guild, user_id: int) -> discord.Member:
    return guild.get_member(user_id) or await guild.fetch_member(user_id)


async def _tell_expired(guild: discord.Guild, offer: HandoverOffer, campaign: Campaign) -> None:
    """The owner hears the offer ended; the person's message loses its buttons. An owner
    who left the server or closed their messages counts as told; any other Discord error
    is raised (try again later). The buttons are best effort: pressing them after the
    end only says it ended."""
    told = TOLD_EXPIRED if offer.delivered_at is not None else TOLD_EXPIRED_UNSENT
    try:
        owner = await _member(guild, offer.from_user_id)
        await owner.send(
            told.format(name=md(offer.to_name), campaign=md(campaign.name), days=HANDOVER_DAYS),
            allowed_mentions=NO_PINGS,
        )
    except (discord.Forbidden, discord.NotFound):
        pass
    if offer.message_id is None:  # sent before messages were kept
        return
    with contextlib.suppress(discord.HTTPException):
        person = await _member(guild, offer.to_user_id)
        channel = person.dm_channel or await person.create_dm()
        await channel.get_partial_message(offer.message_id).edit(
            content=OFFER_EXPIRED.format(
                owner=md(offer.from_name), campaign=md(campaign.name), days=HANDOVER_DAYS
            ),
            view=None,
            allowed_mentions=NO_PINGS,
        )


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
