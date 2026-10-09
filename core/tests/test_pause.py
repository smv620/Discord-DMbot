"""Pausing a campaign (#957): the owner pauses and unpauses, a paused campaign can't start
and doesn't count toward the cap, and a plan that shrinks or ends pauses what is over its
cap (the most recently played kept), telling the owner once. Needs Postgres."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from dmbot import campaign_cap, hours, usage
from dmbot.bot import DMBot
from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.campaigns.store import NO_ROOM_TO_UNPAUSE, NOT_THE_OWNER_PAUSE
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.sessions import SessionStore
from tests.test_owner_campaigns import (
    ALICE,
    BOB,
    CAROL,
    GUILD_A,
    GUILD_B,
    NOW,
    OwnerCampaignsTest,
)


class PauseTest(OwnerCampaignsTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # As the bot runs with plans enforced: unpausing needs room under the cap.
        self.store = CampaignStore(self.db, clock=lambda: NOW, has_free_slot=self.has_room)

    @staticmethod
    async def has_room(conn: Any, user_id: int, now: int) -> bool:
        return await campaign_cap.has_room_for_one_more(conn, user_id, now)

    async def make(self, guild: int, name: str, owner: int, played: int | None = None) -> Campaign:
        campaign = await self.store.create(guild, name, owner)
        if played is not None:
            async with self.db.guild(guild) as conn:
                await conn.execute(
                    "UPDATE campaigns SET last_played_at = %s, created_at = %s"
                    " WHERE guild_id = %s AND id = %s",
                    (played, played, guild, campaign.id),
                )
        return campaign

    async def counted(self, owner: int) -> int:
        async with self.db.meter(GUILD_A, owner) as conn:
            return await campaign_cap.owned_count(conn, owner)

    async def paused_ids(self, owner: int) -> set[str]:
        found: set[str] = set()
        for guild in (GUILD_A, GUILD_B):
            async with self.db.guild(guild) as conn:
                cur = await conn.execute(
                    "SELECT id FROM campaigns WHERE owner_user_id = %s AND paused", (owner,)
                )
                found |= {r["id"] for r in await cur.fetchall()}
        return found

    async def table_paused(self, owner: int) -> set[str]:
        async with self.db.user(owner) as conn:
            cur = await conn.execute("SELECT campaign_id FROM owner_campaigns WHERE paused")
            return {r["campaign_id"] for r in await cur.fetchall()}

    async def settle(self, owner: int) -> usage.Settled | None:
        return await usage.settle_cap(self.db, GUILD_A, owner, NOW)


class PauseAndUnpause(PauseTest):
    async def test_the_owner_pauses_and_unpauses_and_the_count_follows(self) -> None:
        await self.give_plan(ALICE, 2)
        a = await self.make(GUILD_A, "One", ALICE)
        await self.make(GUILD_B, "Two", ALICE)
        self.assertEqual(await self.counted(ALICE), 2)
        paused = await self.store.set_paused(GUILD_A, a.id, ALICE, True, NOW)
        self.assertTrue(paused.paused)
        self.assertEqual(await self.counted(ALICE), 1)  # a paused one takes no place
        self.assertEqual(await self.table_paused(ALICE), {a.id})  # the trigger kept it in step
        again = await self.store.set_paused(GUILD_A, a.id, ALICE, False, NOW)
        self.assertFalse(again.paused)
        self.assertEqual((await self.counted(ALICE), await self.table_paused(ALICE)), (2, set()))
        await self.assert_in_step(ALICE)

    async def test_pausing_keeps_the_campaign_and_its_data(self) -> None:
        await self.give_plan(ALICE, 2)
        a = await self.make(GUILD_A, "One", ALICE)
        await self.store.add_dm(GUILD_A, a.id, BOB)
        await self.store.set_paused(GUILD_A, a.id, ALICE, True, NOW)
        kept = await self.store.get(GUILD_A, a.id)
        assert kept is not None
        self.assertEqual(
            (kept.name, kept.owner_user_id, BOB in kept.dm_user_ids), ("One", ALICE, True)
        )

    async def test_unpause_is_refused_at_the_cap_in_plain_words(self) -> None:
        await self.give_plan(ALICE, 1)
        a = await self.make(GUILD_A, "Old", ALICE, played=100)
        await self.store.set_paused(GUILD_A, a.id, ALICE, True, NOW)
        await self.make(GUILD_B, "New", ALICE, played=200)  # takes the one place
        with self.assertRaises(CampaignError) as raised:
            await self.store.set_paused(GUILD_A, a.id, ALICE, False, NOW)
        self.assertEqual(str(raised.exception), NO_ROOM_TO_UNPAUSE)
        self.assertIn("⏸️ **Pause this campaign**", NO_ROOM_TO_UNPAUSE)
        self.assertEqual(await self.paused_ids(ALICE), {a.id})  # still paused

    async def test_a_co_dm_and_a_stranger_cannot_pause(self) -> None:
        await self.give_plan(ALICE, 2)
        a = await self.make(GUILD_A, "One", ALICE)
        await self.store.add_dm(GUILD_A, a.id, BOB)
        for who in (BOB, CAROL):
            with self.assertRaises(CampaignError) as raised:
                await self.store.set_paused(GUILD_A, a.id, who, True, NOW)
            self.assertEqual(str(raised.exception), NOT_THE_OWNER_PAUSE)
        self.assertEqual(await self.paused_ids(ALICE), set())

    async def test_a_campaign_with_no_owner_cannot_be_paused(self) -> None:
        a = await self.make(GUILD_A, "One", ALICE)
        await self.disown(GUILD_A, a.id)
        with self.assertRaises(CampaignError):
            await self.store.set_paused(GUILD_A, a.id, ALICE, True, NOW)

    async def test_pausing_again_changes_nothing(self) -> None:
        await self.give_plan(ALICE, 1)
        a = await self.make(GUILD_A, "One", ALICE)
        await self.store.set_paused(GUILD_A, a.id, ALICE, True, NOW)
        await self.store.set_paused(GUILD_A, a.id, ALICE, True, NOW)
        self.assertEqual(await self.table_paused(ALICE), {a.id})


class SettlingTheCap(PauseTest):
    async def five(self) -> list[Campaign]:
        """Five campaigns across two servers, played at 100..500 (oldest first)."""
        return [
            await self.make(GUILD_A if n % 2 else GUILD_B, f"C{n}", ALICE, played=n * 100)
            for n in range(1, 6)
        ]

    async def test_a_downgrade_from_five_to_two_pauses_the_three_oldest_played(self) -> None:
        await self.give_plan(ALICE, 5)
        made = await self.five()
        async with self.db.plan_writer(ALICE) as conn:
            await conn.execute(
                "UPDATE entitlements SET campaign_cap = 2 WHERE user_id = %s", (ALICE,)
            )
        settled = await self.settle(ALICE)
        assert settled is not None
        self.assertEqual(settled.cap, 2)
        self.assertEqual({p.campaign_id for p in settled.paused}, {c.id for c in made[:3]})
        self.assertEqual(await self.paused_ids(ALICE), {c.id for c in made[:3]})
        self.assertEqual(await self.counted(ALICE), 2)
        await self.assert_in_step(ALICE)
        self.assertEqual(await self.table_paused(ALICE), {c.id for c in made[:3]})

    async def test_settling_twice_pauses_nothing_more(self) -> None:
        await self.give_plan(ALICE, 1)
        await self.make(GUILD_A, "One", ALICE, played=100)
        await self.make(GUILD_B, "Two", ALICE, played=200)
        first = await self.settle(ALICE)
        second = await self.settle(ALICE)
        assert first is not None and second is not None
        self.assertEqual((len(first.paused), len(second.paused)), (1, 0))

    async def test_a_plan_that_ended_falls_back_to_try_its_one_campaign(self) -> None:
        # Never had a plan, or it lapsed: Try It's cap of one applies to what they own.
        made = [await self.make(GUILD_A, f"C{n}", ALICE, played=n * 100) for n in (1, 2, 3)]
        settled = await self.settle(ALICE)
        assert settled is not None
        self.assertEqual(settled.cap, 1)
        self.assertEqual({p.campaign_id for p in settled.paused}, {made[0].id, made[1].id})
        self.assertEqual(await self.counted(ALICE), 1)

    async def test_the_most_recently_played_stay_and_ties_break_by_creation(self) -> None:
        await self.give_plan(ALICE, 1)
        x = await self.make(GUILD_A, "X", ALICE, played=100)
        y = await self.make(GUILD_A, "Y", ALICE, played=100)
        await self.settle(ALICE)
        kept = {x.id, y.id} - await self.paused_ids(ALICE)
        self.assertEqual(len(kept), 1)
        again = await self.settle(ALICE)
        assert again is not None
        self.assertEqual(again.paused, [])

    async def test_a_campaign_being_listened_to_is_kept_first(self) -> None:
        await self.give_plan(ALICE, 1)
        live = await self.make(GUILD_A, "Live", ALICE, played=100)  # the older one
        await self.make(GUILD_B, "Newer", ALICE, played=200)
        async with self.db.meter(GUILD_A, ALICE) as conn:
            await conn.execute("SELECT * FROM dmbot_pause_over_cap(1, %s)", ([live.id],))
        self.assertNotIn(live.id, await self.paused_ids(ALICE))
        self.assertEqual(len(await self.paused_ids(ALICE)), 1)

    async def test_two_settles_at_once_pause_each_campaign_once(self) -> None:
        await self.give_plan(ALICE, 1)
        for n in (1, 2, 3):
            await self.make(GUILD_A, f"C{n}", ALICE, played=n * 100)
        results = await asyncio.gather(self.settle(ALICE), self.settle(ALICE))
        counts = sorted(len(r.paused) for r in results if r is not None)
        self.assertEqual(counts, [0, 2])  # the owner is told about the two, once
        self.assertEqual(await self.counted(ALICE), 1)

    async def test_it_pauses_only_this_owners_campaigns(self) -> None:
        await self.give_plan(ALICE, 1)
        await self.give_plan(BOB, 5)
        await self.make(GUILD_A, "A1", ALICE, played=100)
        await self.make(GUILD_A, "A2", ALICE, played=200)
        b1 = await self.make(GUILD_A, "B1", BOB, played=100)
        b2 = await self.make(GUILD_B, "B2", BOB, played=200)
        await self.settle(ALICE)
        self.assertEqual(await self.paused_ids(BOB), set())
        self.assertEqual(await self.table_paused(BOB), set())
        self.assertEqual(await self.owned(BOB), {b1.id, b2.id})

    async def test_a_person_cannot_pause_another_persons_campaigns_through_the_door(self) -> None:
        a = await self.make(GUILD_A, "A1", ALICE, played=100)
        await self.make(GUILD_A, "A2", ALICE, played=200)
        async with self.db.meter(GUILD_A, BOB) as conn:  # Bob's door, Alice's campaigns
            cur = await conn.execute("SELECT * FROM dmbot_pause_over_cap(0)")
            self.assertEqual(await cur.fetchall(), [])
        self.assertEqual(await self.paused_ids(ALICE), set())
        self.assertIsNotNone(a)

    async def test_no_person_set_means_nothing_is_paused(self) -> None:
        await self.make(GUILD_A, "A1", ALICE, played=100)
        async with self.db.guild(GUILD_A) as conn:  # a server door has no person set
            cur = await conn.execute("SELECT * FROM dmbot_pause_over_cap(0)")
            self.assertEqual(await cur.fetchall(), [])
        self.assertEqual(await self.paused_ids(ALICE), set())

    async def test_the_pause_policy_is_closed_outside_the_function(self) -> None:
        a = await self.make(GUILD_A, "A1", ALICE)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET paused = TRUE WHERE guild_id = %s AND id = %s",
                (GUILD_A, a.id),
            )  # the server door may write its own server's rows (that is the pause button)
        self.assertEqual(await self.paused_ids(ALICE), {a.id})


class BotSide(PauseTest):
    SITE = "https://dmbot.example"

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s", enforce_plans=True, site_url=self.SITE),
            ConsentStore(self.db),
            self.store,
            SessionStore(self.db),
            meter=usage.Meter(self.db),
        )
        self.sent = AsyncMock()
        self.user = SimpleNamespace(send=self.sent)
        patcher = patch.object(self.bot, "get_user", return_value=self.user)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_paused_campaign_cannot_start_and_the_words_name_the_button(self) -> None:
        await self.give_plan(ALICE, 2)
        a = await self.make(GUILD_A, "One", ALICE)
        await self.store.set_paused(GUILD_A, a.id, ALICE, True, NOW)
        paused = cast(Campaign, await self.store.get(GUILD_A, a.id))
        self.assertEqual(await self.bot.plan_refusal(GUILD_A, paused, ALICE), hours.PAUSED_OWNER)
        self.assertIn("▶️ **Unpause**", hours.PAUSED_OWNER)
        self.assertEqual(await self.bot.plan_refusal(GUILD_A, paused, BOB), hours.PAUSED_OTHER)

    async def test_a_paused_campaign_cannot_start_with_plans_not_enforced_either(self) -> None:
        a = await self.make(GUILD_A, "One", ALICE)
        await self.store.set_paused(GUILD_A, a.id, ALICE, True, NOW)
        paused = cast(Campaign, await self.store.get(GUILD_A, a.id))
        loose = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            self.store,
            SessionStore(self.db),
        )
        self.assertEqual(await loose.plan_refusal(GUILD_A, paused, ALICE), hours.PAUSED_OWNER)

    async def test_a_downgrade_pauses_on_the_next_start_and_tells_the_owner_once(self) -> None:
        await self.give_plan(ALICE, 3)
        made = [await self.make(GUILD_A, f"C{n}", ALICE, played=n * 100) for n in (1, 2, 3)]
        async with self.db.plan_writer(ALICE) as conn:
            await conn.execute(
                "UPDATE entitlements SET campaign_cap = 1 WHERE user_id = %s", (ALICE,)
            )
        newest = cast(Campaign, await self.store.get(GUILD_A, made[2].id))
        self.assertIsNone(await self.bot.plan_refusal(GUILD_A, newest, ALICE))  # the kept one
        self.sent.assert_awaited_once()
        note = cast(Any, self.sent.await_args).args[0]
        self.assertIn("C1", note)
        self.assertIn("C2", note)
        self.assertNotIn("C3", note)
        self.assertIn("▶️ **Unpause**", note)
        # Starting a paused one is refused in the pause words, and nothing more is sent.
        oldest = cast(Campaign, await self.store.get(GUILD_A, made[0].id))
        self.assertEqual(await self.bot.plan_refusal(GUILD_A, oldest, ALICE), hours.PAUSED_OWNER)
        self.sent.assert_awaited_once()

    async def test_a_message_that_cannot_be_sent_never_breaks_the_start(self) -> None:
        await self.give_plan(ALICE, 1)
        await self.make(GUILD_A, "Old", ALICE, played=100)
        new = await self.make(GUILD_A, "New", ALICE, played=200)
        for failure in (OSError("down"), TimeoutError()):
            self.sent.side_effect = failure
            self.assertIsNone(await self.bot.plan_refusal(GUILD_A, new, ALICE))
        self.assertEqual(await self.counted(ALICE), 1)  # the pause stands

    async def test_a_slow_or_broken_settle_fails_open(self) -> None:
        await self.give_plan(ALICE, 1)
        a = await self.make(GUILD_A, "One", ALICE)
        with (
            patch.object(self.bot.meter, "settle_cap", side_effect=RuntimeError("down")),
            self.assertLogs("dmbot.bot", "ERROR"),
        ):
            self.assertIsNone(await self.bot.plan_refusal(GUILD_A, a, ALICE))
            self.assertIsNone(await self.bot.create_refusal(GUILD_A, BOB))

    async def test_unpause_at_the_cap_is_refused_in_the_cap_words(self) -> None:
        await self.give_plan(ALICE, 1)
        a = await self.make(GUILD_A, "Old", ALICE, played=100)
        await self.store.set_paused(GUILD_A, a.id, ALICE, True, NOW)
        await self.make(GUILD_B, "New", ALICE, played=200)
        with self.assertRaises(CampaignError) as raised:
            await self.bot.set_paused(GUILD_A, a.id, ALICE, False)
        self.assertEqual(
            str(raised.exception),
            hours.campaigns_refusal(
                1, 1, is_owner=True, site_url=self.SITE, can_change_plan=True, unpausing=True
            ),
        )
        self.assertIn("⏸️ **Pause this campaign**", str(raised.exception))

    async def test_a_running_campaign_cannot_be_paused(self) -> None:
        await self.give_plan(ALICE, 2)
        a = await self.make(GUILD_A, "One", ALICE)
        self.bot.tables[GUILD_A] = cast(Any, SimpleNamespace(campaign_id=a.id))
        with self.assertRaises(CampaignError):
            await self.bot.set_paused(GUILD_A, a.id, ALICE, True)
        self.assertEqual(await self.paused_ids(ALICE), set())
