"""The campaign cap's count (#437 part 2c): `owner_campaigns` follows `campaigns` through
every way a campaign is made, restored, handed over, deleted or removed with its server,
it is readable only with its owner set, and the checks that use it (hand-over, take-over,
restore, start) count from it."""

from __future__ import annotations

import asyncio
from typing import Any

from psycopg import errors as pg_errors

from dmbot import campaign_cap, entitlements, hours
from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.store import NO_ROOM_TO_RESTORE
from dmbot.db import Database, drop_schema
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL, DatabaseTest
from tests.test_web_accounts_db import INSERT_PLAN, PLAN_ROW

GUILD_A, GUILD_B = 111, 222
ALICE, BOB, CAROL = 7, 8, 9
NOW = 1_000
WORKS = {"period_start": 0, "period_end": 4_000_000_000}


class OwnerCampaignsTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        self.store = CampaignStore(self.db, clock=lambda: NOW)

    async def give_plan(self, user_id: int, cap: int) -> None:
        async with self.db.user(user_id) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, NULL, 0, 0)",
                (user_id,),
            )
        async with self.db.plan_writer(user_id) as conn:
            await conn.execute(
                INSERT_PLAN, {**PLAN_ROW, **WORKS, "campaign_cap": cap, "user_id": user_id}
            )

    async def owned(self, owner: int) -> set[str]:
        """What the table says this person owns, read as that person."""
        async with self.db.user(owner) as conn:
            cur = await conn.execute("SELECT campaign_id FROM owner_campaigns")
            return {r["campaign_id"] for r in await cur.fetchall()}

    async def truth(self, owner: int, *guilds: int) -> set[str]:
        """What `campaigns` says, server by server."""
        found: set[str] = set()
        for guild in guilds:
            async with self.db.guild(guild) as conn:
                cur = await conn.execute(
                    "SELECT id FROM campaigns WHERE owner_user_id = %s", (owner,)
                )
                found |= {r["id"] for r in await cur.fetchall()}
        return found

    async def assert_in_step(self, *owners: int) -> None:
        for owner in owners:
            self.assertEqual(
                await self.owned(owner), await self.truth(owner, GUILD_A, GUILD_B), owner
            )

    async def disown(self, guild: int, campaign_id: str) -> None:
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE guild_id = %s AND id = %s",
                (guild, campaign_id),
            )


class InStep(OwnerCampaignsTest):
    async def test_creating_a_campaign_adds_its_row(self) -> None:
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        b = await self.store.create(GUILD_B, "Another", ALICE)
        self.assertEqual(await self.owned(ALICE), {a.id, b.id})  # across both servers
        await self.assert_in_step(ALICE, BOB)

    async def test_deleting_a_campaign_removes_its_row(self) -> None:
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        b = await self.store.create(GUILD_A, "Another", ALICE)
        await self.store.delete(GUILD_A, a.id)
        self.assertEqual(await self.owned(ALICE), {b.id})
        await self.assert_in_step(ALICE)

    async def test_a_hand_over_moves_the_row(self) -> None:
        await self.give_plan(BOB, 2)
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        offer = await self.store.offer_handover(
            GUILD_A, a.id, ALICE, BOB, NOW, from_name="Alice", to_name="Bob"
        )
        self.assertEqual(await self.store.accept_handover(GUILD_A, offer.id, BOB, NOW), "accepted")
        self.assertEqual((await self.owned(ALICE), await self.owned(BOB)), (set(), {a.id}))
        await self.assert_in_step(ALICE, BOB)

    async def test_taking_on_a_campaign_with_no_owner_adds_the_row(self) -> None:
        await self.give_plan(BOB, 2)
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        await self.disown(GUILD_A, a.id)
        self.assertEqual(await self.owned(ALICE), set())  # an ownerless campaign has no row
        await self.store.add_dm(GUILD_A, a.id, BOB)
        self.assertEqual(await self.store.take_ownership(GUILD_A, a.id, BOB, NOW), "taken")
        self.assertEqual(await self.owned(BOB), {a.id})
        await self.assert_in_step(ALICE, BOB)

    async def test_restoring_a_backup_adds_the_row_for_the_importer(self) -> None:
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        backup = await self.store.export(GUILD_A, a.id)
        restored = await self.store.import_backup(GUILD_B, backup, BOB)
        self.assertEqual(await self.owned(BOB), {restored.id})
        self.assertEqual(await self.owned(ALICE), {a.id})  # the original is untouched
        await self.assert_in_step(ALICE, BOB)

    async def test_replacing_a_campaign_with_no_owner_gives_it_to_the_importer(self) -> None:
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        backup = await self.store.export(GUILD_A, a.id)
        await self.store.add_dm(GUILD_A, a.id, BOB)
        await self.disown(GUILD_A, a.id)
        await self.store.import_backup(GUILD_A, backup, BOB, replace_campaign_id=a.id)
        self.assertEqual(await self.owned(BOB), {a.id})
        await self.assert_in_step(ALICE, BOB)

    async def test_removing_a_servers_data_removes_its_rows_only(self) -> None:
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        b = await self.store.create(GUILD_B, "Another", ALICE)
        async with self.db.guild(GUILD_A) as conn:  # everything of one server goes
            await conn.execute("DELETE FROM campaigns WHERE guild_id = %s", (GUILD_A,))
        self.assertEqual(await self.owned(ALICE), {b.id})
        self.assertNotIn(a.id, await self.owned(ALICE))
        await self.assert_in_step(ALICE)


class Isolation(OwnerCampaignsTest):
    async def test_only_the_owner_reads_their_rows(self) -> None:
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        self.assertEqual(await self.owned(ALICE), {a.id})
        self.assertEqual(await self.owned(BOB), set())  # another person sees nothing
        async with self.db.guild(GUILD_A) as conn:  # nor does the server itself
            cur = await conn.execute("SELECT count(*) AS n FROM owner_campaigns")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM owner_campaigns")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_a_server_scoped_connection_cannot_write_it(self) -> None:
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO owner_campaigns (owner_user_id, campaign_id) VALUES (%s, 'x')",
                    (BOB,),
                )
        async with self.db.guild(GUILD_A) as conn:  # a delete finds no row it may touch
            await conn.execute("DELETE FROM owner_campaigns")
        async with self.db.user(ALICE) as conn:  # nor can the owner's own connection
            await conn.execute("DELETE FROM owner_campaigns WHERE owner_user_id = %s", (ALICE,))
        self.assertEqual(await self.owned(ALICE), {a.id})

    async def test_the_count_goes_through_the_meter_door_with_the_owner_set(self) -> None:
        await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        await self.store.create(GUILD_B, "Another", ALICE)
        async with self.db.meter(GUILD_A, ALICE) as conn:
            self.assertEqual(await campaign_cap.owned_count(conn, ALICE), 2)
        async with self.db.meter(GUILD_A, BOB) as conn:
            self.assertEqual(await campaign_cap.owned_count(conn, ALICE), 2)  # asked as that owner
            self.assertEqual(await campaign_cap.owned_count(conn, BOB), 0)

    async def test_the_count_puts_the_person_back_as_it_found_them(self) -> None:
        await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        async with self.db.guild(GUILD_A) as conn:
            await campaign_cap.owned_count(conn, ALICE)
            cur = await conn.execute("SELECT current_setting('dmbot.user_id', true) AS u")
            self.assertIn((await cur.fetchone() or {})["u"], (None, ""))


class Backfill(DatabaseTest):
    """Migrations run with no server set: 0034's backfill must open every table it reads
    and writes, and close them again."""

    async def test_campaigns_from_before_0034_get_their_rows(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0034"]
        await self.db.close()  # start again from a schema as it was before 0034
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        store = CampaignStore(self.db, clock=lambda: NOW)
        a = await store.create(GUILD_A, "Frostmaiden", ALICE)
        b = await store.create(GUILD_B, "Another", ALICE)
        c = await store.create(GUILD_B, "Third", BOB)
        orphan = await store.create(GUILD_A, "Nobody's", CAROL)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", (orphan.id,)
            )
        await self.db.migrate()
        for owner, expected in ((ALICE, {a.id, b.id}), (BOB, {c.id}), (CAROL, set())):
            async with self.db.user(owner) as conn:
                cur = await conn.execute("SELECT campaign_id FROM owner_campaigns")
                self.assertEqual({r["campaign_id"] for r in await cur.fetchall()}, expected)
        async with self.db.unscoped() as conn:  # the openings are gone again
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        # and the trigger is live from then on
        d = await store.create(GUILD_A, "Fourth", BOB)
        async with self.db.user(BOB) as conn:
            cur = await conn.execute("SELECT campaign_id FROM owner_campaigns")
            self.assertEqual({r["campaign_id"] for r in await cur.fetchall()}, {c.id, d.id})


class Counting(OwnerCampaignsTest):
    """The checks count from the table (one function, `campaign_cap.room`)."""

    async def room(self, owner: int) -> campaign_cap.Room:
        async with self.db.guild(GUILD_A) as conn:
            return await campaign_cap.room(conn, owner, NOW)

    async def test_room_for_one_more_up_to_the_cap(self) -> None:
        await self.give_plan(ALICE, 2)
        await self.store.create(GUILD_A, "One", ALICE)
        self.assertTrue((await self.room(ALICE)).fits(1))
        await self.store.create(GUILD_B, "Two", ALICE)  # a second server counts too
        room = await self.room(ALICE)
        self.assertEqual((room.cap, room.owned), (2, 2))
        self.assertFalse(room.fits(1))
        self.assertTrue(room.fits(0))  # starting one they own is fine at the cap

    async def test_owning_more_than_the_cap_blocks_even_a_start(self) -> None:
        await self.give_plan(ALICE, 1)
        await self.store.create(GUILD_A, "One", ALICE)
        await self.store.create(GUILD_B, "Two", ALICE)  # e.g. after a downgrade
        self.assertFalse((await self.room(ALICE)).fits(0))

    async def test_no_plan_has_no_room(self) -> None:
        await self.store.create(GUILD_A, "One", ALICE)
        room = await self.room(ALICE)
        self.assertFalse(room.works)
        self.assertFalse(room.fits(0))

    async def test_a_cap_of_none_is_no_limit(self) -> None:
        entitlements.configure_free_users([ALICE])
        for i in range(3):
            await self.store.create(GUILD_A, f"C{i}", ALICE)
        room = await self.room(ALICE)
        self.assertIsNone(room.cap)
        self.assertTrue(room.fits(5))


class Enforced(OwnerCampaignsTest):
    """The store's checks when plans are enforced: hand-over, take-over and restore."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.strict = CampaignStore(
            self.db,
            clock=lambda: NOW,
            has_free_slot=campaign_cap.has_room_for_one_more,
            restore_needs_slot=True,
        )

    async def test_a_hand_over_needs_room_under_the_buyers_cap(self) -> None:
        await self.give_plan(BOB, 1)
        await self.store.create(GUILD_B, "Bob's own", BOB)  # at the cap already
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        offer = await self.store.offer_handover(
            GUILD_A, a.id, ALICE, BOB, NOW, from_name="Alice", to_name="Bob"
        )
        self.assertEqual(
            await self.strict.accept_handover(GUILD_A, offer.id, BOB, NOW), "no_free_slot"
        )
        await self.store.decline_handover(GUILD_A, offer.id, BOB, NOW)
        await self.give_plan(CAROL, 2)  # a plan with room takes it
        offer2 = await self.store.offer_handover(
            GUILD_A, a.id, ALICE, CAROL, NOW, from_name="Alice", to_name="Carol"
        )
        self.assertEqual(
            await self.strict.accept_handover(GUILD_A, offer2.id, CAROL, NOW), "accepted"
        )

    async def test_two_hand_overs_at_once_cannot_push_a_buyer_over_the_cap(self) -> None:
        await self.give_plan(BOB, 2)
        await self.store.create(GUILD_B, "Bob's own", BOB)  # one short of the cap
        first = await self.store.create(GUILD_A, "First", ALICE)
        second = await self.store.create(GUILD_A, "Second", CAROL)
        offers = [
            await self.store.offer_handover(
                GUILD_A, c.id, by, BOB, NOW, from_name="Seller", to_name="Bob"
            )
            for c, by in ((first, ALICE), (second, CAROL))
        ]
        results = await asyncio.gather(
            *(self.strict.accept_handover(GUILD_A, o.id, BOB, NOW) for o in offers)
        )
        self.assertEqual(sorted(results), ["accepted", "no_free_slot"])
        self.assertEqual(len(await self.owned(BOB)), 2)  # at the cap, not over it
        await self.assert_in_step(ALICE, BOB, CAROL)

    async def test_the_sync_setting_is_put_back_as_it_was(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("SELECT set_config('dmbot.owner_sync', 'before', true)")
            await conn.execute(
                "INSERT INTO campaigns (id, guild_id, name, name_key, created_at,"
                " target_ruleset, fallback_ruleset, owner_user_id)"
                " VALUES ('c1', %s, 'N', 'n', 0, '2024', '2014', %s)",
                (GUILD_A, ALICE),
            )
            cur = await conn.execute("SELECT current_setting('dmbot.owner_sync') AS v")
            self.assertEqual((await cur.fetchone() or {})["v"], "before")

    async def test_taking_on_needs_room(self) -> None:
        await self.give_plan(BOB, 1)
        await self.store.create(GUILD_B, "Bob's own", BOB)
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        await self.disown(GUILD_A, a.id)
        await self.store.add_dm(GUILD_A, a.id, BOB)
        self.assertEqual(await self.strict.take_ownership(GUILD_A, a.id, BOB, NOW), "no_free_slot")

    async def test_a_restore_needs_room_when_enforced_only(self) -> None:
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        backup: dict[str, Any] = await self.store.export(GUILD_A, a.id)
        await self.give_plan(BOB, 1)
        await self.store.create(GUILD_B, "Bob's own", BOB)  # at the cap
        with self.assertRaisesRegex(CampaignError, NO_ROOM_TO_RESTORE[:30]):
            await self.strict.import_backup(GUILD_B, backup, BOB)
        self.assertEqual(len(await self.owned(BOB)), 1)  # nothing was added
        await self.store.import_backup(GUILD_B, backup, BOB)  # not enforced: restores
        self.assertEqual(len(await self.owned(BOB)), 2)

    async def test_a_restore_with_room_goes_ahead(self) -> None:
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        backup = await self.store.export(GUILD_A, a.id)
        await self.give_plan(BOB, 2)
        restored = await self.strict.import_backup(GUILD_B, backup, BOB)
        self.assertEqual(await self.owned(BOB), {restored.id})

    async def test_replacing_a_campaign_you_own_needs_no_new_room(self) -> None:
        await self.give_plan(ALICE, 1)
        a = await self.store.create(GUILD_A, "Frostmaiden", ALICE)
        backup = await self.store.export(GUILD_A, a.id)
        await self.strict.import_backup(GUILD_A, backup, ALICE, replace_campaign_id=a.id)
        self.assertEqual(await self.owned(ALICE), {a.id})


class Door(OwnerCampaignsTest):
    """The one way in: a count that answers only for the person set, the lock that makes
    two checks for one person take turns, and the guard against TRUNCATE."""

    async def test_the_second_check_for_one_person_waits_for_the_first(self) -> None:
        # Deterministic (Supervisor, #927): the first check holds its transaction open inside
        # `has_room_for_one_more`; the second must block on the per-person lock until the
        # first commits, and then see the campaign the first one added. Without the lock the
        # second returns at once, with the old count.
        await self.give_plan(BOB, 1)
        async with self.db.guild(GUILD_A) as one:
            self.assertTrue(await campaign_cap.has_room_for_one_more(one, BOB, NOW))

            async def second() -> bool:
                async with self.db.guild(GUILD_B) as two:
                    return await campaign_cap.has_room_for_one_more(two, BOB, NOW)

            waiting = asyncio.ensure_future(second())
            done, _ = await asyncio.wait({waiting}, timeout=0.5)
            self.assertFalse(done, "the second check did not wait for the first")
            await one.execute(
                "INSERT INTO campaigns (id, guild_id, name, name_key, created_at,"
                " target_ruleset, fallback_ruleset, owner_user_id)"
                " VALUES ('c1', %s, 'N', 'n', 0, '2024', '2014', %s)",
                (GUILD_A, BOB),
            )
        self.assertFalse(await asyncio.wait_for(waiting, 5))  # sees the new campaign: full

    async def test_the_count_function_answers_only_for_the_person_set(self) -> None:
        await self.store.create(GUILD_A, "Mine", ALICE)
        await self.store.create(GUILD_B, "Bob's", BOB)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT dmbot_owned_campaigns() AS n")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)  # no person set: nobody's
            self.assertEqual(await campaign_cap.owned_count(conn, ALICE), 1)
            self.assertEqual(await campaign_cap.owned_count(conn, BOB), 1)
            self.assertEqual(await campaign_cap.owned_count(conn, CAROL), 0)

    async def test_the_person_switch_is_not_public(self) -> None:
        self.assertFalse(hasattr(entitlements, "as_person"))

    async def test_truncating_campaigns_is_refused(self) -> None:
        await self.store.create(GUILD_A, "Mine", ALICE)
        with self.assertRaisesRegex(pg_errors.RaiseException, "Use DELETE"):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute("TRUNCATE campaigns CASCADE")


class Words(OwnerCampaignsTest):
    def test_the_refusal_names_the_cap_what_they_have_and_the_way_out(self) -> None:
        self.assertEqual(
            hours.campaigns_refusal(
                2, 3, is_owner=True, site_url="https://dmbot.example", can_change_plan=True
            ),
            f"Your plan covers 2 campaigns, and you have 3. To start this one, {hours.PAUSE_WAY} "
            "or change your plan here: https://dmbot.example/account",
        )
        self.assertEqual(
            hours.campaigns_refusal(1, 1, is_owner=True, can_change_plan=True, creating=True),
            f"Your plan covers 1 campaign, and you have 1. To make a new one, {hours.PAUSE_WAY} "
            "or change your plan on DMbot's website",
        )

    def test_a_plan_that_cant_change_is_not_offered_a_change(self) -> None:
        text = hours.campaigns_refusal(3, 4, is_owner=True)
        self.assertEqual(
            text,
            f"Your plan covers 3 campaigns, and you have 4. To start this one, {hours.PAUSE_WAY}.",
        )

    def test_anyone_else_is_told_nothing_about_the_plan(self) -> None:
        self.assertEqual(hours.campaigns_refusal(2, 3, is_owner=False), hours.START_BLOCKED)
