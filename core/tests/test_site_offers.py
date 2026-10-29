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
from dmbot.campaigns.models import HANDOVER_DAYS, HandoverOffer, HandoverStatus
from dmbot.dm_screen import handover, site_offers
from dmbot.dm_screen.handover import TOLD_EXPIRED, TOLD_EXPIRED_UNSENT, deliver_offer
from dmbot.dm_screen.site_offers import NOT_SENT, SiteOffers
from dmbot.mentions import NO_PINGS

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
        self.offers: dict[tuple[int, int], HandoverOffer] = {}
        self.claim_lapsed = False
        self.released: list[tuple[int, int]] = []
        self.told: set[int] = set()
        self.campaign: Any = type(
            "C", (), {"id": "c1", "name": "Frost*maiden", "dm_screen_channel_id": None}
        )()
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
        self, guild_id: int, o: HandoverOffer, now: int, message_id: int
    ) -> HandoverOffer | None:
        self.claimed.discard((guild_id, o.id))
        self.sent.add((guild_id, o.id))
        self.message_ids[(guild_id, o.id)] = message_id
        if self.claim_lapsed:  # someone else claimed and sent it meanwhile
            return None
        decided = self.offers.get((guild_id, o.id))  # answered on the site meanwhile?
        status = decided.status if decided is not None else "open"
        return replace(o, status=status, message_id=message_id, delivered_at=now)

    async def get_offer(self, guild_id: int, offer_id: int, now: int) -> HandoverOffer | None:
        return self.offers.get((guild_id, offer_id))

    async def offers_to_end(self, guild_id: int, now: int) -> list[HandoverOffer]:
        return [o for o in self.to_end if o.id not in self.told]

    async def claim_end_notice(self, guild_id: int, offer_id: int, now: int) -> bool:
        if offer_id in self.told:
            return False
        self.told.add(offer_id)
        return True

    async def release_end_notice(self, guild_id: int, offer_id: int, told_at: int) -> None:
        self.told.discard(offer_id)

    async def release_delivery(self, guild_id: int, o: HandoverOffer) -> None:
        self.claimed.discard((guild_id, o.id))
        self.released.append((guild_id, o.id))

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
        self.posted: list[tuple[int, str]] = []
        self.ready = asyncio.Event()
        self.ready.set()
        self.tasks: list[asyncio.Task[None]] = []
        self.slept: list[float] = []
        self.clock = 0.0
        self.emails: list[tuple[int, str, str, str]] = []  # (owner, kind, subject, body)
        self.email_error: Exception | None = None

        async def owner_mail(owner: int, kind: str, subject: str, body: str) -> None:
            if self.email_error is not None:
                raise self.email_error
            self.emails.append((owner, kind, subject, body))

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
            post=self.post,
            owner_mail=owner_mail,
            sleep=sleep,
            now=lambda: NOW,
            clock=lambda: self.clock,
        )

    async def decide_quietly(self, guild_id: int, offer_id: int) -> None:
        """decided(), which must log no ERROR: one caught and logged would hide a crash."""
        with self.assertNoLogs("dmbot.dm_screen.site_offers", "ERROR"):
            await self.offers.decided(guild_id, offer_id)

    async def post(self, channel_id: int, text: str) -> bool:
        self.posted.append((channel_id, text))
        return True

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
    def ended(self, message_id: int | None = 4242, *, sent: bool = True) -> HandoverOffer:
        return replace(
            offer(),
            status="expired",
            decided_at=NOW,
            message_id=message_id,
            delivered_at=NOW if sent else None,
        )

    def told(self, text: str = TOLD_EXPIRED) -> str:
        return text.format(name="Bea\\_\\*", campaign="Frost\\*maiden", days=HANDOVER_DAYS)

    async def test_the_owner_is_told_and_the_buttons_come_off(self) -> None:
        self.store.waiting = {}
        self.store.to_end = [self.ended()]
        await self.offers.sweep_server(GUILD)
        guild = self.guilds[GUILD]
        self.assertEqual(guild.owner.sent, [self.told()])
        (edit,) = guild.buyer.edits
        self.assertEqual(edit[0], 4242)
        self.assertIsNone(edit[1]["view"])
        self.assertEqual(edit[1]["allowed_mentions"], NO_PINGS)  # never pings
        self.assertIn("**Oskar**'s offer of **Frost\\*maiden** has ended", edit[1]["content"])
        await self.offers.sweep_server(GUILD)  # announced once
        self.assertEqual(len(guild.owner.sent), 1)

    async def test_an_offer_never_sent_says_so(self) -> None:
        self.store.to_end = [self.ended(message_id=None, sent=False)]
        await self.offers.end_expired(GUILD)
        self.assertEqual(self.guilds[GUILD].owner.sent, [self.told(TOLD_EXPIRED_UNSENT)])
        self.assertEqual(self.guilds[GUILD].buyer.edits, [])

    async def test_a_discord_hiccup_tells_them_on_the_next_sweep(self) -> None:
        self.store.to_end = [self.ended()]
        guild = self.guilds[GUILD]
        real_send = guild.owner.send

        async def hiccup(text: str, **_: Any) -> None:
            raise http_error(503)

        guild.owner.send = hiccup  # type: ignore[method-assign]
        with self.assertLogs("dmbot.dm_screen.site_offers", "WARNING"):
            await self.offers.end_expired(GUILD)
        self.assertEqual(self.store.told, set())  # let go
        guild.owner.send = real_send  # type: ignore[method-assign]
        await self.offers.end_expired(GUILD)
        self.assertEqual(guild.owner.sent, [self.told()])

    async def test_a_database_hiccup_before_the_claim_leaves_it_for_the_next_sweep(
        self,
    ) -> None:
        self.store.to_end = [self.ended()]
        real_get = self.store.get

        async def down(guild_id: int, campaign_id: str) -> Campaign | None:
            raise RuntimeError("database down")

        async def release_down(guild_id: int, offer_id: int, told_at: int) -> None:
            raise RuntimeError("database down")  # so letting go would fail too

        real_release = self.store.release_end_notice
        self.store.get = down  # type: ignore[method-assign]
        self.store.release_end_notice = release_down  # type: ignore[method-assign]
        with self.assertLogs("dmbot.dm_screen.site_offers", "ERROR"):
            await self.offers.end_expired(GUILD)
        self.assertEqual(self.store.told, set())  # never claimed in the first place
        self.store.get = real_get  # type: ignore[method-assign]
        self.store.release_end_notice = real_release  # type: ignore[method-assign]
        await self.offers.end_expired(GUILD)
        self.assertEqual(self.guilds[GUILD].owner.sent, [self.told()])

    async def test_a_deleted_campaign_counts_as_told(self) -> None:
        self.store.to_end = [self.ended()]
        self.store.campaign = None
        await self.offers.end_expired(GUILD)
        self.assertEqual(self.store.told, {1})
        self.assertEqual(self.guilds[GUILD].owner.sent, [])

    async def test_people_who_left_are_skipped_quietly(self) -> None:
        self.store.to_end = [self.ended()]
        guild = self.guilds[GUILD]
        guild.get_member = lambda user_id: None  # type: ignore[method-assign]

        async def gone(user_id: int) -> FakeMember:
            raise http_error(404, discord.NotFound)

        guild.fetch_member = gone  # type: ignore[attr-defined]
        with self.assertNoLogs("dmbot.dm_screen.site_offers", "WARNING"):
            await self.offers.end_expired(GUILD)
        self.assertEqual(self.store.told, {1})  # nobody to tell: done

    async def test_another_process_s_server_is_left_alone(self) -> None:
        self.store.to_end = [self.ended()]
        await self.offers.end_expired(OTHER_GUILD)
        self.assertEqual(self.store.told, set())


class Decisions(Harness):
    """An offer answered or taken back on the website (#737): the other person hears."""

    def decided(
        self, status: HandoverStatus, message_id: int | None = 4242, *, sent: bool = True
    ) -> None:
        self.store.offers[(GUILD, 1)] = replace(
            offer(),
            status=status,
            message_id=message_id,
            decided_at=NOW,
            delivered_at=NOW if sent else None,
        )

    async def test_taken_back_before_it_was_ever_sent_tells_nobody(self) -> None:
        self.decided("withdrawn", message_id=None, sent=False)
        await self.decide_quietly(GUILD, 1)
        guild = self.guilds[GUILD]
        self.assertEqual((guild.buyer.sent, guild.buyer.edits, guild.owner.sent), ([], [], []))

    async def test_a_message_that_cant_be_changed_is_told_anew_when_taken_back(self) -> None:
        self.decided("withdrawn")
        guild = self.guilds[GUILD]

        def gone(message_id: int) -> Any:
            async def edit(**_: Any) -> None:
                raise http_error(404, discord.NotFound)

            return mock.Mock(edit=edit)

        guild.buyer.get_partial_message = gone  # type: ignore[method-assign]
        await self.decide_quietly(GUILD, 1)
        self.assertEqual(len(guild.buyer.sent), 1)

    async def test_the_owner_unreachable_still_updates_the_persons_message(self) -> None:
        self.decided("declined")
        guild = self.guilds[GUILD]

        async def closed(text: str, **_: Any) -> None:
            raise http_error(403, discord.Forbidden)

        guild.owner.send = closed  # type: ignore[method-assign]
        await self.decide_quietly(GUILD, 1)
        self.assertEqual(len(guild.buyer.edits), 1)

    async def test_accepted_with_no_kept_message_sends_the_person_nothing(self) -> None:
        self.decided("accepted", message_id=None)
        await self.decide_quietly(GUILD, 1)
        guild = self.guilds[GUILD]
        self.assertEqual(len(guild.owner.sent), 1)
        self.assertEqual((guild.buyer.sent, guild.buyer.edits), ([], []))

    async def test_a_deleted_campaign_or_a_failed_read_tells_nobody(self) -> None:
        self.decided("declined")
        self.store.campaign = None
        await self.decide_quietly(GUILD, 1)
        self.assertEqual(self.guilds[GUILD].owner.sent, [])

        async def down(guild_id: int, offer_id: int, now: int) -> HandoverOffer | None:
            raise RuntimeError("database down")

        self.store.get_offer = down  # type: ignore[method-assign]
        with self.assertLogs("dmbot.dm_screen.site_offers", "ERROR"):
            await self.offers.decided(GUILD, 1)

    async def test_accepted_tells_the_owner_and_updates_the_persons_message(self) -> None:
        self.decided("accepted")
        await self.decide_quietly(GUILD, 1)
        guild = self.guilds[GUILD]
        self.assertEqual(
            guild.owner.sent,
            [handover.TOLD_ACCEPTED.format(name="Bea\\_\\*", campaign="Frost\\*maiden")],
        )
        ((message_id, changes),) = guild.buyer.edits
        self.assertEqual(message_id, 4242)
        self.assertIsNone(changes["view"])  # the buttons go
        self.assertEqual(
            changes["content"],
            handover.ACCEPTED.format(campaign="Frost\\*maiden", server="The Table", owner="Oskar"),
        )
        self.assertEqual(guild.buyer.sent, [])

    async def test_an_email_offer_tells_the_owner_and_sends_the_person_nothing(self) -> None:
        # #2532: the person answered on the website, not through a Discord message.
        self.decided("accepted", message_id=None)
        self.store.offers[(GUILD, 1)] = replace(
            self.store.offers[(GUILD, 1)], via_email=True, to_name=""
        )
        await self.decide_quietly(GUILD, 1)
        guild = self.guilds[GUILD]
        self.assertEqual(
            guild.owner.sent,
            [
                handover.TOLD_ACCEPTED.format(
                    name="The person you emailed", campaign="Frost\\*maiden"
                )
            ],
        )

    async def test_an_expired_email_offer_tells_the_owner_in_plans_words(self) -> None:
        # #2532: no name to show, and the campaign never moved.
        guild = self.guilds[GUILD]
        by_email = replace(offer(), via_email=True, to_user_id=0, to_name="", delivered_at=NOW)
        await site_offers._tell_expired(guild, by_email, self.store.campaign)  # type: ignore[arg-type]
        [text] = guild.owner.sent
        self.assertEqual(
            text,
            "They didn't answer within 7 days, so the offer ended. The campaign is still yours. "
            "You can offer it again from ⚙️ Settings → 🗂️ Campaign management → 🤝 Hand over, or "
            "from your account page.",
        )
        self.assertNotIn("****", text)
        self.assertIn("didn't answer", text)
        self.assertEqual((guild.buyer.sent, guild.buyer.edits), ([], []))

    async def test_a_moved_campaign_tells_both_people_and_a_failed_move_tells_the_owner(
        self,
    ) -> None:
        # #2532 slice C: the old owner hears where it went; the new owner hears to run /dmbot start.
        from dmbot.campaigns.moves import Moved

        self.guilds[OTHER_GUILD] = FakeGuild(OTHER_GUILD)
        by_email = replace(
            offer(), via_email=True, to_name="Bea", move_to_guild_id=OTHER_GUILD, delivered_at=NOW
        )
        await self.offers._tell_moved(Moved("moved", by_email, "Frost*maiden"))
        self.assertEqual(
            self.guilds[GUILD].owner.sent,
            [
                handover.MOVED_OLD_OWNER.format(
                    campaign="Frost\\*maiden", server="The Table", name="Bea"
                )
            ],
        )
        self.assertEqual(
            self.guilds[OTHER_GUILD].buyer.sent,
            [
                handover.MOVED_NEW_OWNER.format(
                    campaign="Frost\\*maiden", server="The Table", owner="Oskar"
                )
            ],
        )
        self.guilds[GUILD].owner.sent.clear()
        self.guilds[OTHER_GUILD].buyer.sent.clear()
        failed = replace(by_email, to_user_id=BUYER)
        await self.offers._tell_moved(
            Moved(
                "reopened", failed, "Frost*maiden", "Your plan has no room for one more campaign."
            )
        )
        # The owner is told it did not happen, never why; the person who accepted is told why.
        [text] = self.guilds[GUILD].owner.sent
        self.assertEqual(text, handover.MOVE_FAILED.format(campaign="Frost\\*maiden"))
        self.assertNotIn("plan", text)
        [theirs] = self.guilds[OTHER_GUILD].buyer.sent
        self.assertIn("Your plan has no room for one more campaign.", theirs)

    async def test_declined_tells_the_owner(self) -> None:
        self.decided("declined")
        await self.decide_quietly(GUILD, 1)
        guild = self.guilds[GUILD]
        self.assertEqual(
            guild.owner.sent,
            [handover.TOLD_DECLINED.format(name="Bea\\_\\*", campaign="Frost\\*maiden")],
        )
        self.assertEqual(
            guild.buyer.edits[0][1]["content"], handover.DECLINED.format(owner="Oskar")
        )

    async def test_taken_back_tells_the_person_only(self) -> None:
        self.decided("withdrawn")
        await self.decide_quietly(GUILD, 1)
        guild = self.guilds[GUILD]
        told = handover.TOLD_WITHDRAWN.format(owner="Oskar", campaign="Frost\\*maiden")
        self.assertEqual(guild.owner.sent, [])
        self.assertEqual(guild.buyer.edits[0][1]["content"], told)

    async def test_taken_back_with_no_kept_message_is_still_told(self) -> None:
        self.decided("withdrawn", message_id=None)
        await self.decide_quietly(GUILD, 1)
        guild = self.guilds[GUILD]
        self.assertEqual(
            guild.buyer.sent,
            [handover.TOLD_WITHDRAWN.format(owner="Oskar", campaign="Frost\\*maiden")],
        )

    async def test_an_open_or_unknown_offer_tells_nobody(self) -> None:
        self.decided("open")
        await self.decide_quietly(GUILD, 1)
        await self.decide_quietly(GUILD, 2)  # not there
        await self.decide_quietly(OTHER_GUILD, 1)  # another process's server
        guild = self.guilds[GUILD]
        self.assertEqual((guild.owner.sent, guild.buyer.sent, guild.buyer.edits), ([], [], []))

    async def test_the_listener_hands_on_our_servers_answers(self) -> None:
        self.decided("declined")
        announced: asyncio.Queue[str] = asyncio.Queue()

        async def listen(channel: str, on_listening: Callable[[], None]) -> AsyncGenerator[str]:
            self.assertEqual(channel, offer_notify.DECIDED)
            on_listening()
            while True:
                yield await announced.get()

        follow = asyncio.ensure_future(self.offers.follow_decided(listen))
        announced.put_nowait(offer_notify.payload(OTHER_GUILD, 1))
        announced.put_nowait(offer_notify.payload(GUILD, 1))
        await self.settle()
        self.assertEqual(len(self.guilds[GUILD].owner.sent), 1)
        self.assertEqual(self.delivered, [])  # no sweep, no sending offers
        follow.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follow


class DecisionEdges(Harness):
    """#797: the startup window, a take-back while sending, the #dm-screen note."""

    def decided(
        self, status: HandoverStatus, *, message_id: int | None = 4242, sent: bool = True
    ) -> None:
        self.store.offers[(GUILD, 1)] = replace(
            offer(),
            status=status,
            message_id=message_id,
            decided_at=NOW,
            delivered_at=NOW if sent else None,
        )

    def screen(self) -> None:
        self.store.campaign = type(
            "C", (), {"id": "c1", "name": "Frost*maiden", "dm_screen_channel_id": 555}
        )()

    async def test_an_answer_before_the_bot_is_ready_is_told_once_ready(self) -> None:
        self.decided("declined")
        self.ready.clear()
        task = asyncio.ensure_future(self.offers.decided(GUILD, 1))
        await asyncio.sleep(0)
        self.assertEqual(self.guilds[GUILD].owner.sent, [])
        self.ready.set()
        await task
        self.assertEqual(len(self.guilds[GUILD].owner.sent), 1)

    async def test_the_listener_hands_on_answers_before_it_knows_its_servers(self) -> None:
        self.decided("declined")
        self.ready.clear()
        known = self.guilds
        self.guilds = {}  # Discord hasn't sent the server list yet
        announced: asyncio.Queue[str] = asyncio.Queue()

        async def listen(channel: str, on_listening: Callable[[], None]) -> AsyncGenerator[str]:
            on_listening()
            while True:
                yield await announced.get()

        follow = asyncio.ensure_future(self.offers.follow_decided(listen))
        for raw in ("not ours", "111:5:1", offer_notify.payload(GUILD, 1)):
            announced.put_nowait(raw)
        for _ in range(10):
            await asyncio.sleep(0)
        # One task, waiting for ready, for the one real announcement (none for garbled).
        self.assertEqual(len(self.tasks), 1)
        self.guilds = known
        self.ready.set()
        await self.settle()
        self.assertEqual(len(self.guilds[GUILD].owner.sent), 1)  # garbled ones ignored
        follow.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follow

    async def test_taken_back_while_its_message_was_going_out(self) -> None:
        sending, release = asyncio.Event(), asyncio.Event()

        async def slow(guild: discord.Guild, campaign: Campaign, o: HandoverOffer) -> Any:
            sending.set()
            await release.wait()
            return self.message

        self.offers._deliver = slow
        run = asyncio.ensure_future(self.offers.send(GUILD, 1))
        await asyncio.wait_for(sending.wait(), 2)
        self.decided("withdrawn", message_id=None, sent=False)  # on the site, meanwhile
        await self.decide_quietly(GUILD, 1)
        guild = self.guilds[GUILD]
        self.assertEqual((guild.buyer.sent, guild.buyer.edits), ([], []))  # not sent yet
        release.set()
        await run
        ((message_id, changes),) = guild.buyer.edits
        self.assertEqual(message_id, 4242)
        self.assertIsNone(changes["view"])  # no live buttons left
        told = handover.TOLD_WITHDRAWN.format(owner="Oskar", campaign="Frost\\*maiden")
        self.assertEqual(changes["content"], told)
        self.assertEqual(guild.owner.sent, [])

    async def test_accepted_while_its_message_was_going_out_tells_the_owner_once(self) -> None:
        sending, release = asyncio.Event(), asyncio.Event()

        async def slow(guild: discord.Guild, campaign: Campaign, o: HandoverOffer) -> Any:
            sending.set()
            await release.wait()
            return self.message

        self.offers._deliver = slow
        run = asyncio.ensure_future(self.offers.send(GUILD, 1))
        await asyncio.wait_for(sending.wait(), 2)
        self.decided("accepted", message_id=None, sent=False)
        await self.decide_quietly(GUILD, 1)  # tells the owner
        release.set()
        await run  # changes the person's message, without telling the owner again
        guild = self.guilds[GUILD]
        self.assertEqual(len(guild.owner.sent), 1)
        self.assertEqual(len(guild.buyer.edits), 1)

    async def test_a_lapsed_claim_leaves_the_message_alone(self) -> None:
        # Someone else claimed and sent it meanwhile: this send's confirm finds nothing,
        # so it changes nothing, even though the offer now reads taken back.
        self.decided("withdrawn")
        self.store.claim_lapsed = True
        with self.assertNoLogs("dmbot.dm_screen.site_offers", "ERROR"):
            await self.offers.send(GUILD, 1)
        guild = self.guilds[GUILD]
        self.assertEqual(self.delivered, [1])  # it was sent: the guard is what held
        self.assertEqual((guild.buyer.edits, guild.owner.sent), ([], []))

    async def test_a_failure_changing_the_message_after_sending_is_logged(self) -> None:
        self.decided("withdrawn")

        async def broken(*_: Any, **__: Any) -> None:
            raise RuntimeError("unexpected")

        with (
            mock.patch.object(site_offers, "_tell_decision", broken),
            self.assertLogs("dmbot.dm_screen.site_offers", "ERROR") as logs,
        ):
            await self.offers.send(GUILD, 1)  # never raises
        self.assertIn("Couldn't change offer 1's message", logs.output[0])
        self.assertEqual(self.store.sent, {(GUILD, 1)})  # it was sent, and stays so
        self.assertEqual(self.store.released, [])  # its claim was never given back

    async def test_the_dm_screen_note_failing_still_tells_the_owner(self) -> None:
        self.screen()
        self.decided("accepted")

        async def down(channel_id: int, text: str) -> bool:
            raise RuntimeError("Discord down")

        self.offers._post = down
        with self.assertLogs("dmbot.dm_screen.site_offers", "WARNING") as logs:
            await self.offers.decided(GUILD, 1)
        self.assertTrue(all("WARNING" in line for line in logs.output))  # no ERROR
        self.assertEqual(len(self.guilds[GUILD].owner.sent), 1)

    async def test_a_website_accept_notes_the_dm_screen_once(self) -> None:
        self.screen()
        self.decided("accepted")
        await self.decide_quietly(GUILD, 1)
        accepted = handover.SCREEN_ACCEPTED.format(name="Bea\\_\\*", campaign="Frost\\*maiden")
        self.assertEqual(self.posted, [(555, accepted)])
        for status in ("declined", "withdrawn"):
            self.posted.clear()
            self.decided(status)
            await self.decide_quietly(GUILD, 1)
            self.assertEqual(self.posted, [], status)

    async def test_answered_but_never_sent_tells_only_the_owner(self) -> None:
        for status in ("accepted", "declined"):
            guild = self.guilds[GUILD] = FakeGuild(GUILD)
            self.decided(status, message_id=None, sent=False)
            await self.decide_quietly(GUILD, 1)
            self.assertEqual(len(guild.owner.sent), 1, status)
            self.assertEqual((guild.buyer.sent, guild.buyer.edits), ([], []), status)

    async def test_an_edit_that_fails_after_their_own_answer_sends_nothing_new(self) -> None:
        for status in ("accepted", "declined"):
            guild = self.guilds[GUILD] = FakeGuild(GUILD)

            def gone(message_id: int) -> Any:
                async def edit(**_: Any) -> None:
                    raise http_error(404, discord.NotFound)

                return mock.Mock(edit=edit)

            guild.buyer.get_partial_message = gone  # type: ignore[method-assign]
            self.decided(status)
            await self.decide_quietly(GUILD, 1)
            self.assertEqual(guild.buyer.sent, [], status)

    async def test_taken_back_when_the_person_left_raises_nothing(self) -> None:
        self.decided("withdrawn")
        guild = self.guilds[GUILD]
        guild.get_member = lambda user_id: None  # type: ignore[method-assign]

        async def gone(user_id: int) -> FakeMember:
            raise http_error(404, discord.NotFound)

        guild.fetch_member = gone  # type: ignore[attr-defined]
        await self.decide_quietly(GUILD, 1)  # no ERROR logged

    async def test_the_decisions_listener_backs_off_and_never_sweeps(self) -> None:
        calls = 0

        async def listen(channel: str, on_listening: Callable[[], None]) -> AsyncGenerator[str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("connection refused")
            on_listening()
            if calls == 2:
                raise OSError("dropped right away")
            await asyncio.Event().wait()
            yield ""

        follow = asyncio.ensure_future(self.offers.follow_decided(listen))
        with self.assertLogs("dmbot.dm_screen.site_offers", "WARNING") as logs:
            await self.settle()
        self.assertEqual(self.slept, [1.0, 5.0])
        self.assertEqual(self.delivered, [])  # no sweep on (re)connect
        self.assertIn(offer_notify.DECIDED, logs.output[0])  # names the channel
        follow.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follow


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


class OwnerEmail(Harness):
    """The owner is told by email too (#2532, slice D), once, in plain text, without the address."""

    def answered(self, status: HandoverStatus, *, to_name: str = "Bea") -> None:
        self.store.offers[(GUILD, 1)] = replace(
            offer(),
            status=status,
            decided_at=NOW,
            delivered_at=NOW,
            message_id=4242,
            to_name=to_name,
        )

    async def test_an_accept_and_a_no_thanks_are_emailed_to_the_owner(self) -> None:
        cases: list[tuple[HandoverStatus, str]] = [
            ("accepted", "accepted"),
            ("declined", "no thanks"),
        ]
        for status, word in cases:
            self.emails.clear()
            self.answered(status)
            await self.decide_quietly(GUILD, 1)
            [(owner, kind, subject, body)] = self.emails
            self.assertEqual((owner, kind), (OWNER, f"handover-{status}:{GUILD}-1"))
            self.assertIn("Frost*maiden", subject + body)  # plain text: no Discord escapes
            self.assertNotIn("\\", body)
            self.assertNotIn("**", body)
            self.assertIn("Bea", body)
            self.assertTrue(word in body or word in subject)

    async def test_a_take_back_is_not_emailed(self) -> None:
        self.answered("withdrawn")
        await self.decide_quietly(GUILD, 1)
        self.assertEqual(self.emails, [])

    async def test_an_offer_by_email_has_no_name_and_the_email_says_so(self) -> None:
        self.answered("accepted", to_name="")
        await self.decide_quietly(GUILD, 1)
        [(_o, _k, _s, body)] = self.emails
        self.assertIn("The person you emailed", body)

    async def test_seven_days_unanswered_is_emailed_once(self) -> None:
        self.store.waiting = {}
        self.store.to_end = [
            replace(offer(), status="expired", decided_at=NOW, delivered_at=NOW, via_email=True)
        ]
        await self.offers.sweep_server(GUILD)
        await self.offers.sweep_server(GUILD)  # announced once
        [(owner, kind, _subject, body)] = self.emails
        self.assertEqual((owner, kind), (OWNER, f"handover-expired:{GUILD}-1"))
        self.assertIn("didn't answer within 7 days", body)
        self.assertIn("still yours", body)

    async def test_the_expired_email_gives_the_full_account_address(self) -> None:
        self.offers._site_url = "https://dmbot.example.com/"
        self.store.to_end = [replace(offer(), status="expired", delivered_at=NOW)]
        await self.offers.sweep_server(GUILD)
        [(_o, _k, _s, body)] = self.emails
        self.assertIn("https://dmbot.example.com/account", body)

    async def test_an_offer_that_never_reached_them_is_emailed_as_not_delivered(self) -> None:
        self.store.to_end = [replace(offer(), status="expired", delivered_at=None)]
        await self.offers.sweep_server(GUILD)
        [(_o, _k, _s, body)] = self.emails
        self.assertIn("couldn't get the offer to Bea", body)
        self.assertNotIn("didn't answer", body)

    async def test_a_cancel_during_the_email_does_not_send_the_notice_twice(self) -> None:
        self.store.to_end = [replace(offer(), status="expired", delivered_at=NOW)]

        async def stuck(*_: Any) -> None:
            raise asyncio.CancelledError

        self.offers._owner_mail = stuck
        with contextlib.suppress(asyncio.CancelledError):
            await self.offers.sweep_server(GUILD)
        self.offers._owner_mail = None
        await self.offers.sweep_server(GUILD)
        self.assertEqual(len(self.guilds[GUILD].owner.sent), 1)  # the notice was kept

    async def test_the_accept_email_comes_after_the_screen_note(self) -> None:
        order: list[str] = []
        self.answered("accepted")
        self.store.campaign.dm_screen_channel_id = 55

        async def post(channel_id: int, text: str) -> bool:
            order.append("screen")
            return True

        async def mail(*_: Any) -> None:
            order.append("email")

        self.offers._post = post
        self.offers._owner_mail = mail
        await self.decide_quietly(GUILD, 1)
        self.assertEqual(order, ["screen", "email"])

    async def test_a_move_to_another_server_is_emailed_with_the_servers_name(self) -> None:
        from dmbot.campaigns.moves import Moved

        self.guilds[OTHER_GUILD] = FakeGuild(OTHER_GUILD)
        moved = replace(offer(), via_email=True, to_name="Bea", move_to_guild_id=OTHER_GUILD)
        await self.offers._tell_moved(Moved("moved", moved, "Frost*maiden"))
        [(owner, kind, _subject, body)] = self.emails
        self.assertEqual((owner, kind), (OWNER, f"handover-moved:{GUILD}-1"))
        self.assertIn('"The Table"', body)
        failed = replace(moved, id=2)
        self.emails.clear()
        await self.offers._tell_moved(Moved("reopened", failed, "Frost*maiden", "no room"))
        self.assertEqual(self.emails, [])  # a move that did not happen is not emailed

    async def test_a_failed_email_never_stops_the_private_message_and_logs_only_its_type(
        self,
    ) -> None:
        self.email_error = RuntimeError("smtp said to ann@example.com")
        self.answered("accepted")
        with self.assertLogs("dmbot.dm_screen.handover", "WARNING") as logged:
            await self.offers.decided(GUILD, 1)
        self.assertEqual(len(self.guilds[GUILD].owner.sent), 1)  # still told in Discord
        text = "\n".join(logged.output)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("example.com", text)

    async def test_without_a_mailer_nothing_is_emailed_and_nothing_breaks(self) -> None:
        self.offers._owner_mail = None
        self.answered("accepted")
        await self.decide_quietly(GUILD, 1)
        self.assertEqual(len(self.guilds[GUILD].owner.sent), 1)
        self.assertEqual(self.emails, [])

    async def test_a_move_with_a_server_this_process_does_not_serve_is_still_emailed(self) -> None:
        from dmbot.campaigns.moves import Moved

        moved = replace(offer(), via_email=True, to_name="Bea", move_to_guild_id=OTHER_GUILD)
        await self.offers._tell_moved(Moved("moved", moved, "Frost*maiden"))  # no such guild here
        [(_o, _k, _s, body)] = self.emails
        self.assertIn("another Discord server", body)

    async def test_the_move_email_goes_after_the_private_messages(self) -> None:
        from dmbot.campaigns.moves import Moved

        self.guilds[OTHER_GUILD] = FakeGuild(OTHER_GUILD)
        seen: list[str] = []
        original = self.offers._owner_mail

        async def slow(owner: int, kind: str, subject: str, body: str) -> None:
            seen.append(f"email after {len(self.guilds[GUILD].owner.sent)} owner messages")
            assert original is not None
            await original(owner, kind, subject, body)

        self.offers._owner_mail = slow
        moved = replace(offer(), via_email=True, to_name="Bea", move_to_guild_id=OTHER_GUILD)
        await self.offers._tell_moved(Moved("moved", moved, "Frost*maiden"))
        self.assertEqual(seen, ["email after 1 owner messages"])

    async def test_an_expiry_whose_discord_message_failed_emails_once_after_the_retry(self) -> None:
        self.store.waiting = {}
        self.store.to_end = [
            replace(offer(), status="expired", decided_at=NOW, delivered_at=NOW, via_email=True)
        ]
        guild = self.guilds[GUILD]
        real_send = guild.owner.send

        async def hiccup(text: str, **_: Any) -> None:
            raise http_error(503)

        guild.owner.send = hiccup  # type: ignore[method-assign]
        with self.assertLogs("dmbot.dm_screen.site_offers", "WARNING"):
            await self.offers.sweep_server(GUILD)
        self.assertEqual(self.emails, [])  # not told yet: nothing is emailed for a failed attempt
        guild.owner.send = real_send  # type: ignore[method-assign]
        await self.offers.sweep_server(GUILD)
        self.assertEqual(len(self.emails), 1)

    async def test_the_same_answer_heard_twice_asks_for_one_email_each_time_and_the_log_dedupes(
        self,
    ) -> None:
        """The once-only guarantee is the mail log's (kind per offer), so both hearings use the
        same kind; the bot's helper sends and the second send is refused by the log."""
        self.answered("accepted")
        await self.decide_quietly(GUILD, 1)
        await self.decide_quietly(GUILD, 1)
        kinds = {kind for _o, kind, _s, _b in self.emails}
        self.assertEqual(kinds, {f"handover-accepted:{GUILD}-1"})


class TheEmailText(unittest.TestCase):
    def test_every_kind_is_plain_and_names_no_address(self) -> None:
        for kind in ("accepted", "declined", "expired", "moved"):
            subject, body = handover.owner_email(
                kind, name="Bea", campaign="Frostmaiden", server="The Table"
            )
            for text in (subject, body):
                self.assertNotIn("**", text)
                self.assertNotIn("@", text)
            self.assertIn("Frostmaiden", subject + body)

    def test_a_hostile_name_cannot_reorder_or_hide_the_words_around_it(self) -> None:
        hostile = "Bea\u202e\n\x00 see http://x.example/y\u200b now"
        subject, body = handover.owner_email("accepted", name=hostile, campaign="Frost\u202emaiden")
        for text in (subject, body):
            self.assertNotIn("\u202e", text)
            self.assertNotIn("\u200b", text)
            self.assertNotIn("\x00", text)
        self.assertNotIn("\n\n", body.split("accepted")[0])  # the name stays on one line
        self.assertIn("Frostmaiden", subject + body)

    def test_a_move_without_a_server_name_says_another_server(self) -> None:
        _s, body = handover.owner_email("moved", name="Bea", campaign="Frostmaiden")
        self.assertIn("another Discord server", body)

    def test_an_unknown_kind_is_a_mistake_not_a_blank_email(self) -> None:
        with self.assertRaises(ValueError):
            handover.owner_email("withdrawn", name="Bea", campaign="Frostmaiden")


class TheBotsEmailToTheOwner(unittest.IsolatedAsyncioTestCase):
    """`DMBot.email_hand_over_owner`: the address comes from the owner's account, the mail log
    keeps one row for each kind, and without an address or a mailer nothing happens."""

    def bot(self, *, mailer: Any = "mailer", meter: Any = "meter") -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            mailer=mock.AsyncMock() if mailer == "mailer" else mailer,
            meter=SimpleNamespace(db=object()) if meter == "meter" else meter,
        )

    async def test_it_sends_to_the_accounts_address_once_per_kind(self) -> None:
        from dmbot.bot import DMBot

        bot = self.bot()
        bot.mailer.send.return_value = "sent"
        with mock.patch(
            "dmbot.bot.accounts.account_email", mock.AsyncMock(return_value="owner@example.com")
        ):
            await DMBot.email_hand_over_owner(bot, OWNER, "handover-accepted:1-1", "S", "B")
        bot.mailer.send.assert_awaited_once_with(
            user_id=OWNER,
            kind="handover-accepted:1-1",
            to="owner@example.com",
            subject="S",
            body="B",
        )

    async def test_no_address_means_no_email(self) -> None:
        from dmbot.bot import DMBot

        bot = self.bot()
        with mock.patch("dmbot.bot.accounts.account_email", mock.AsyncMock(return_value=None)):
            await DMBot.email_hand_over_owner(bot, OWNER, "handover-accepted:1-1", "S", "B")
        bot.mailer.send.assert_not_awaited()

    async def test_no_mailer_or_no_database_means_no_email(self) -> None:
        from dmbot.bot import DMBot

        await DMBot.email_hand_over_owner(self.bot(mailer=None), OWNER, "k", "S", "B")
        await DMBot.email_hand_over_owner(self.bot(meter=None), OWNER, "k", "S", "B")


if __name__ == "__main__":
    unittest.main()
