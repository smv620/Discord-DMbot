"""Free access in the database (#771): grants written only through the grant writer, read
by the person they cover, counted by every plan rule, and gone with the account."""

from __future__ import annotations

from typing import Any

from psycopg import errors

from dmbot import entitlements
from dmbot.campaigns.store import plan_works
from dmbot.db import Database
from dmbot.web import accounts, grants, sessions
from dmbot.web.me import build_me
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_accounts_db import INSERT_PLAN, PLAN_ROW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_role import ensure_web_role, web_url

OWNER, FRIEND, PAYER = 11, 22, 33
ADMIN = "admin@example.invalid"
NOW = 1_000
WORKS = {"period_start": 0, "period_end": 4_000_000_000}


class AccessTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())

    async def give_plan(self, user_id: int, **values: Any) -> None:
        async with self.db.user(user_id) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, NULL, 0, 0) ON CONFLICT DO NOTHING",
                (user_id,),
            )
        async with self.db.plan_writer(user_id) as conn:
            await conn.execute(INSERT_PLAN, {**PLAN_ROW, **WORKS, **values, "user_id": user_id})

    async def works(self, user_id: int, now: int = NOW) -> bool:
        async with self.db.guild(THURSDAY.id) as conn:  # as /dmbot start's transaction
            return await plan_works(conn, user_id, now)

    async def access(self, user_id: int, now: int = NOW) -> entitlements.Access:
        async with self.db.guild(THURSDAY.id) as conn:
            return await entitlements.effective(conn, user_id, now)


class Writing(AccessTest):
    async def test_give_change_and_revoke_are_logged(self) -> None:
        action = await grants.give(self.db, ADMIN, FRIEND, "guild", ends_at=None, note="", now=NOW)
        self.assertEqual(action, "grant")
        again = await grants.give(
            self.db, ADMIN, FRIEND, "unlimited", ends_at=NOW + 50, note="a  friend", now=NOW + 1
        )
        self.assertEqual(again, "change")
        self.assertTrue(await grants.revoke(self.db, ADMIN, FRIEND, now=NOW + 2))
        self.assertFalse(await grants.revoke(self.db, ADMIN, FRIEND, now=NOW + 3))  # already
        (row,) = await grants.grants(self.db)
        self.assertEqual((row.level, row.note, row.revoked_at), ("unlimited", "a friend", NOW + 2))
        log = await grants.log_entries(self.db)
        self.assertEqual([e.action for e in log], ["revoke", "change", "grant"])
        self.assertEqual({e.admin_email for e in log}, {ADMIN})
        # Given again after a revoke: a fresh grant.
        self.assertEqual(
            await grants.give(self.db, ADMIN, FRIEND, "guild", ends_at=None, note="", now=NOW + 4),
            "grant",
        )

    async def test_bad_requests_are_refused_in_plain_words(self) -> None:
        for kwargs in (
            {"level": "gold"},
            {"ends_at": NOW},
            {"note": "x" * 201},
        ):
            args: dict[str, Any] = {"level": "guild", "ends_at": None, "note": "", **kwargs}
            with self.assertRaises(grants.GrantError):
                await grants.give(
                    self.db,
                    ADMIN,
                    FRIEND,
                    args["level"],
                    ends_at=args["ends_at"],
                    note=args["note"],
                    now=NOW,
                )


class Checks(AccessTest):
    async def test_a_bad_id_or_admin_is_refused_in_plain_words(self) -> None:
        for user in (0, -1, 2**63):
            with self.assertRaises(grants.GrantError):
                await grants.give(self.db, ADMIN, user, "guild", ends_at=None, note="", now=NOW)
        with self.assertRaises(grants.GrantError):
            await grants.give(self.db, "", FRIEND, "guild", ends_at=None, note="", now=NOW)
        with self.assertRaises(grants.GrantError):
            await grants.revoke(self.db, "", FRIEND, now=NOW)

    async def test_the_person_setting_is_put_back_after_reading(self) -> None:
        async with self.db.user(OWNER) as conn:
            await entitlements.effective(conn, FRIEND, NOW)
            cur = await conn.execute("SELECT current_setting('dmbot.user_id', true) AS who")
            self.assertEqual((await cur.fetchone() or {})["who"], str(OWNER))


class Rules(AccessTest):
    async def test_a_guild_grant_and_a_bigger_paid_plan(self) -> None:
        await self.give_plan(PAYER, plan="pro", hours_cap=500, campaign_cap=50)
        await grants.give(self.db, ADMIN, PAYER, "guild", ends_at=None, note="", now=NOW)
        access = await self.access(PAYER)
        self.assertEqual((access.kind, access.hours_cap, access.campaign_cap), ("grant", 500, 50))

    async def test_the_free_list_needs_no_plan(self) -> None:
        self.assertFalse(await self.works(OWNER))
        entitlements.configure_free_users({OWNER})
        self.assertTrue(await self.works(OWNER))
        self.assertEqual((await self.access(OWNER)).kind, "free")

    async def test_a_grant_works_until_it_ends_or_is_revoked(self) -> None:
        await grants.give(self.db, ADMIN, FRIEND, "guild", ends_at=NOW + 10, note="", now=NOW)
        self.assertTrue(await self.works(FRIEND))
        self.assertEqual((await self.access(FRIEND)).until, NOW + 10)
        self.assertFalse(await self.works(FRIEND, NOW + 10))  # its end date
        await grants.give(self.db, ADMIN, FRIEND, "guild", ends_at=None, note="", now=NOW)
        await grants.revoke(self.db, ADMIN, FRIEND, now=NOW + 1)
        self.assertFalse(await self.works(FRIEND, NOW + 2))

    async def test_a_revoked_grant_falls_back_to_the_paid_plan(self) -> None:
        await self.give_plan(PAYER)
        await grants.give(self.db, ADMIN, PAYER, "unlimited", ends_at=None, note="", now=NOW)
        self.assertEqual((await self.access(PAYER)).kind, "grant")
        await grants.revoke(self.db, ADMIN, PAYER, now=NOW)
        access = await self.access(PAYER)
        self.assertEqual((access.kind, access.plan_name, access.hours_cap), ("paid", "Table", 18))


class Isolation(AccessTest):
    async def test_only_the_grant_writer_writes_grants(self) -> None:
        for door in (self.db.guild(THURSDAY.id), self.db.user(FRIEND), self.db.unscoped()):
            with self.assertRaises(errors.InsufficientPrivilege):
                async with door as conn:
                    await conn.execute(
                        "INSERT INTO access_grants (discord_user_id, level, granted_by, granted_at)"
                        " VALUES (%s, 'unlimited', 'someone', 0)",
                        (FRIEND,),
                    )

    async def test_a_person_reads_only_their_own_grant(self) -> None:
        await grants.give(self.db, ADMIN, FRIEND, "guild", ends_at=None, note="", now=NOW)
        await grants.give(self.db, ADMIN, PAYER, "guild", ends_at=None, note="", now=NOW)
        async with self.db.user(FRIEND) as conn:
            cur = await conn.execute("SELECT discord_user_id FROM access_grants")
            self.assertEqual([r["discord_user_id"] for r in await cur.fetchall()], [FRIEND])
            cur = await conn.execute("SELECT count(*) AS n FROM access_log")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)  # the log: admin only
        self.assertEqual(len(await grants.grants(self.db)), 2)  # the writer sees all

    async def test_deleting_the_account_deletes_the_grant(self) -> None:
        await self.give_plan(FRIEND)
        await grants.give(self.db, ADMIN, FRIEND, "guild", ends_at=None, note="", now=NOW)
        await grants.give(self.db, ADMIN, PAYER, "guild", ends_at=None, note="", now=NOW)
        await accounts.delete_person(self.db, FRIEND)
        self.assertEqual([g.discord_user_id for g in await grants.grants(self.db)], [PAYER])


class AccountPage(AccessTest):
    async def me(self) -> dict[str, Any]:
        token = await sessions.sign_in(self.db, ALICE, [THURSDAY], now=NOW, days=30)
        session = await sessions.find(self.db, token, now=NOW)
        assert session is not None
        return await build_me(self.db, session, now=NOW)

    async def test_access_for_each_kind(self) -> None:
        self.assertEqual((await self.me())["access"], {"kind": "none"})
        await grants.give(self.db, ADMIN, ALICE.id, "guild", ends_at=NOW + 99, note="", now=NOW)
        self.assertEqual((await self.me())["access"], {"kind": "grant", "until": NOW + 99})
        await grants.revoke(self.db, ADMIN, ALICE.id, now=NOW)
        await self.give_plan(ALICE.id)
        self.assertEqual((await self.me())["access"], {"kind": "paid"})
        entitlements.configure_free_users({ALICE.id})
        self.assertEqual((await self.me())["access"], {"kind": "free"})


class WebRole(AccessTest):
    """The website's own database role, as the internet-facing API connects."""

    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        await super().asyncSetUp()
        self.web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.addAsyncCleanup(self.web.close)

    async def test_no_grant_without_the_door_and_never_anothers(self) -> None:
        await grants.give(self.web, ADMIN, FRIEND, "guild", ends_at=None, note="", now=NOW)
        with self.assertRaises(errors.InsufficientPrivilege):
            async with self.web.user(PAYER) as conn:
                await conn.execute(
                    "INSERT INTO access_grants (discord_user_id, level, granted_by, granted_at)"
                    " VALUES (%s, 'unlimited', 'someone', 0)",
                    (PAYER,),
                )
        async with self.web.user(PAYER) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM access_grants")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
            cur = await conn.execute(
                "DELETE FROM access_grants WHERE discord_user_id = %s", (FRIEND,)
            )
            self.assertEqual(cur.rowcount, 0)  # never another person's
