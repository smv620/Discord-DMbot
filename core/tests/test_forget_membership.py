"""Who is in a campaign, on account deletion (0496, #4148; Supervisor's ruling on the issue): the
deleted account always comes out of the DM list, even as the only DM; open hand-over offers to or
from it are cancelled and answered ones keep nobody in its place. Proves the three things the ruling
asks for: (a) an only-DM deletion leaves the campaign owner-left, its backup made and restorable,
(b) a campaign with two DMs keeps the other one, (c) the readers of `campaign_dms` cope with none.
Invented ids. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot import entitlements
from dmbot.campaigns import CampaignStore
from dmbot.web import accounts
from tests.pg import DatabaseTest

ALICE, BOB, CARA = 7401, 7402, 7403
GUILD = 411
NOW = 1_800_000_000


class ForgetMembership(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        entitlements.configure_free_users((ALICE, BOB, CARA))
        self.store = CampaignStore(self.db, clock=lambda: NOW)
        for user in (ALICE, BOB, CARA):
            async with self.db.user(user) as conn:
                await conn.execute(
                    "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                    " VALUES (%s, NULL, 0, 0)",
                    (user,),
                )
        self.solo = await self.store.create(GUILD, "Alice alone", ALICE)
        self.pair = await self.store.create(GUILD, "Alice and Bob", ALICE)
        await self.store.add_dm(GUILD, self.pair.id, BOB)

    async def offer(self, campaign: str, a: int, b: int, status: str) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO campaign_handover_offers (guild_id, campaign_id, from_user_id,"
                " to_user_id, from_name, to_name, created_at, status)"
                " VALUES (%s, %s, %s, %s, %s, %s, 1, %s)",
                (GUILD, campaign, a, b, f"name{a}", f"name{b}", status),
            )

    async def names(self) -> list[tuple[str, str]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT from_name, to_name FROM campaign_handover_offers ORDER BY id"
            )
            return [(r["from_name"], r["to_name"]) for r in await cur.fetchall()]

    async def offers(self) -> list[tuple[int, int, str]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT from_user_id, to_user_id, status FROM campaign_handover_offers ORDER BY id"
            )
            return [(r["from_user_id"], r["to_user_id"], r["status"]) for r in await cur.fetchall()]

    async def dms(self, campaign: str) -> set[int]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT user_id FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s",
                (GUILD, campaign),
            )
            return {r["user_id"] for r in await cur.fetchall()}

    # (a) the only DM leaves

    async def test_an_only_dm_comes_out_and_the_campaign_is_owner_left_with_a_backup(self) -> None:
        self.assertEqual(await self.dms(self.solo.id), {ALICE})
        await accounts.delete_person(self.db, ALICE, now=NOW + 5)
        self.assertEqual(await self.dms(self.solo.id), set())
        left = await self.store.get(GUILD, self.solo.id)
        assert left is not None
        self.assertEqual(left.dm_user_ids, frozenset())
        self.assertIsNone(left.owner_user_id)
        self.assertEqual(left.owner_left_at, NOW + 5)
        # the backup is made, holds no DM, and restores for whoever restores it
        backup = await self.store.export(GUILD, self.solo.id)
        self.assertEqual(backup["sections"]["dms"], [])
        restored = await self.store.import_backup(GUILD, backup, CARA)
        self.assertEqual(restored.dm_user_ids, frozenset({CARA}))

    # (b) a campaign with two DMs

    async def test_with_two_dms_the_other_one_stays(self) -> None:
        self.assertEqual(await self.dms(self.pair.id), {ALICE, BOB})
        await accounts.delete_person(self.db, ALICE, now=NOW + 5)
        self.assertEqual(await self.dms(self.pair.id), {BOB})
        kept = await self.store.get(GUILD, self.pair.id)
        assert kept is not None
        self.assertEqual(kept.dm_user_ids, frozenset({BOB}))

    # hand-over offers

    async def test_open_offers_are_cancelled_and_answered_ones_keep_nobody(self) -> None:
        await self.offer(self.pair.id, ALICE, BOB, "open")
        await self.offer(self.pair.id, ALICE, BOB, "declined")
        await self.offer(self.pair.id, CARA, ALICE, "accepted")
        await self.offer(self.pair.id, BOB, CARA, "expired")  # (not hers: untouched)
        await self.offer(self.solo.id, BOB, ALICE, "open")  # (an open one to her: cancelled)
        await accounts.delete_person(self.db, ALICE, now=NOW + 5)
        self.assertEqual(
            await self.offers(),
            [(0, BOB, "declined"), (CARA, 0, "accepted"), (BOB, CARA, "expired")],
        )
        # her display name goes with her ID; the other people's stay
        self.assertEqual(
            await self.names(),
            [("", f"name{BOB}"), (f"name{CARA}", ""), (f"name{BOB}", f"name{CARA}")],
        )

    async def test_two_people_who_have_both_left_can_share_an_answered_offer(self) -> None:
        await self.offer(self.pair.id, ALICE, CARA, "accepted")
        await accounts.delete_person(self.db, ALICE, now=NOW + 5)
        await accounts.delete_person(self.db, CARA, now=NOW + 6)
        self.assertEqual(await self.offers(), [(0, 0, "accepted")])

    # (c) the readers of campaign_dms with no DM

    async def test_every_reader_of_the_dm_list_copes_with_none(self) -> None:
        await accounts.delete_person(self.db, ALICE, now=NOW + 5)
        # `CampaignStore` (every loader reads `campaign_dms` through the roster): an empty set, so
        # `is_dm` is false for everyone and `list_campaigns` still lists the campaign.
        listed = {c.id: c for c in await self.store.list_campaigns(GUILD)}
        self.assertEqual(listed[self.solo.id].dm_user_ids, frozenset())
        self.assertFalse(any(listed[self.solo.id].is_dm(u) for u in (ALICE, BOB, CARA)))
        # The website's and the bots' membership checks are `SELECT 1 ... WHERE user_id = %s`
        # (nothing found, so "not a DM"); its lists join `campaign_dms` (the campaign is simply
        # not listed to anyone); the DM count of the campaign page is a count (0).
        async with self.db.guild(GUILD) as conn:
            for user in (ALICE, BOB, CARA):
                cur = await conn.execute(
                    "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s"
                    " AND user_id = %s",
                    (GUILD, self.solo.id, user),
                )
                self.assertIsNone(await cur.fetchone())
            cur = await conn.execute(
                "SELECT c.id, (SELECT count(*) FROM campaign_dms m WHERE m.guild_id = c.guild_id"
                " AND m.campaign_id = c.id) AS dm_count FROM campaigns c"
                " JOIN campaign_dms d ON d.campaign_id = c.id AND d.guild_id = c.guild_id"
                " WHERE c.guild_id = %s AND c.id = %s",
                (GUILD, self.solo.id),
            )
            self.assertIsNone(await cur.fetchone())  # (the page lists it to no one)
            cur = await conn.execute(
                "SELECT count(*) AS n FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s",
                (GUILD, self.solo.id),
            )
            row: Any = await cur.fetchone()
            self.assertEqual(row["n"], 0)
