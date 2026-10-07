"""Website accounts in the database (#435): each person sees only their own rows, only
the plan writer changes a plan, a session is found only by its unexpired cookie hash,
and installs are written only for the server being installed."""

from __future__ import annotations

import unittest
from contextlib import AbstractAsyncContextManager

from psycopg import errors as pg_errors

from dmbot import entitlements
from dmbot.db import Conn
from dmbot.entitlements import RENEWAL_SLACK_SECONDS, Entitlement
from tests.pg import DatabaseTest

ALICE, BOB = 1001, 1002
GUILD_A, GUILD_B = 111, 222
FAR_FUTURE = 4_000_000_000

PLAN_ROW: dict[str, object] = {
    "plan": "table",
    "status": "active",
    "hours_cap": 18,
    "campaign_cap": 1,
    "period_start": 100,
    "period_end": 200,
    "grace_ends_at": None,
    "lapsed_at": None,
    "plan_changed_at": 100,
    "provider": "fake",
    "last_event_at": 100,
    "updated_at": 100,
}
INSERT_PLAN = (
    "INSERT INTO entitlements (user_id, plan, status, hours_cap, campaign_cap, period_start,"
    " period_end, grace_ends_at, lapsed_at, plan_changed_at, provider, last_event_at,"
    " updated_at) VALUES (%(user_id)s, %(plan)s, %(status)s, %(hours_cap)s, %(campaign_cap)s,"
    " %(period_start)s, %(period_end)s, %(grace_ends_at)s, %(lapsed_at)s, %(plan_changed_at)s,"
    " %(provider)s, %(last_event_at)s, %(updated_at)s)"
)


class WebAccounts(DatabaseTest):
    async def add_user(self, user_id: int) -> None:
        async with self.db.user(user_id) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, NULL, 0, 0)",
                (user_id,),
            )

    async def add_plan(self, user_id: int, **values: object) -> None:
        async with self.db.plan_writer(user_id) as conn:
            await conn.execute(INSERT_PLAN, {**PLAN_ROW, **values, "user_id": user_id})

    async def add_session(self, user_id: int, id_hash: str, expires_at: int) -> None:
        async with self.db.user(user_id) as conn:
            await conn.execute(
                "INSERT INTO web_sessions (id_hash, user_id, created_at, expires_at)"
                " VALUES (%s, %s, 0, %s)",
                (id_hash, user_id, expires_at),
            )

    async def count(self, table: str, scope: AbstractAsyncContextManager[Conn]) -> int:
        async with scope as conn:
            cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
            row = await cur.fetchone()
            assert row is not None
            return int(row["n"])

    # People and plans

    async def test_a_person_sees_only_their_own_plan(self) -> None:
        await self.add_user(ALICE)
        await self.add_user(BOB)
        await self.add_plan(ALICE)
        self.assertEqual(await self.count("entitlements", self.db.user(ALICE)), 1)
        self.assertEqual(await self.count("entitlements", self.db.user(BOB)), 0)
        self.assertEqual(await self.count("entitlements", self.db.unscoped()), 0)
        self.assertEqual(await self.count("web_users", self.db.guild(GUILD_A)), 0)

    async def test_a_person_cannot_change_their_own_plan(self) -> None:
        await self.add_user(ALICE)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.user(ALICE) as conn:
                await conn.execute(INSERT_PLAN, {**PLAN_ROW, "user_id": ALICE})
        await self.add_plan(ALICE)
        async with self.db.user(ALICE) as conn:
            cur = await conn.execute("UPDATE entitlements SET plan = 'pro', hours_cap = 217")
            self.assertEqual(cur.rowcount, 0)  # the row isn't writable outside the writer
            cur = await conn.execute("DELETE FROM entitlements")
            self.assertEqual(cur.rowcount, 0)
        got = await entitlements.get(self.db, ALICE)
        assert got is not None
        self.assertEqual(got.plan, "table")

    async def test_the_plan_writer_cannot_write_someone_elses_plan(self) -> None:
        await self.add_user(ALICE)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.plan_writer(BOB) as conn:
                await conn.execute(INSERT_PLAN, {**PLAN_ROW, "user_id": ALICE})

    async def test_reads_a_plan_with_its_extra_hours(self) -> None:
        await self.add_user(ALICE)
        self.assertIsNone(await entitlements.get(self.db, ALICE))
        await self.add_plan(ALICE)
        async with self.db.plan_writer(ALICE) as conn:
            await conn.execute("UPDATE entitlements SET extra_hours = 10")
        got = await entitlements.get(self.db, ALICE)
        assert got is not None
        self.assertEqual((got.plan, got.hours_this_period, got.campaign_cap), ("table", 28, 1))

    async def test_status_needs_its_date(self) -> None:
        await self.add_user(ALICE)
        with self.assertRaises(pg_errors.CheckViolation):
            await self.add_plan(ALICE, status="grace", grace_ends_at=None)
        with self.assertRaises(pg_errors.CheckViolation):
            await self.add_plan(ALICE, status="lapsed", lapsed_at=None)

    async def test_unknown_plan_ids_are_refused(self) -> None:
        await self.add_user(ALICE)
        with self.assertRaises(pg_errors.CheckViolation):
            await self.add_plan(ALICE, plan="free-forever")

    async def test_payment_events_are_recorded_once_across_people(self) -> None:
        await self.add_user(ALICE)
        await self.add_user(BOB)
        insert = (
            "INSERT INTO payment_events (provider, event_id, user_id, received_at)"
            " VALUES ('fake', 'evt-1', %s, 0) ON CONFLICT DO NOTHING RETURNING event_id"
        )
        async with self.db.plan_writer(ALICE) as conn:
            cur = await conn.execute(insert, (ALICE,))
            self.assertEqual(len(await cur.fetchall()), 1)
        async with self.db.plan_writer(BOB) as conn:  # the same event replayed under Bob
            cur = await conn.execute(insert, (BOB,))
            self.assertEqual(await cur.fetchall(), [])

    async def test_deleting_a_person_deletes_their_plan_sessions_and_events(self) -> None:
        await self.add_user(ALICE)
        await self.add_plan(ALICE)
        await self.add_session(ALICE, "h", FAR_FUTURE)
        async with self.db.user(ALICE, install_guild=GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, %s, 0, 'site')",
                (GUILD_A, ALICE),
            )
            await conn.execute("DELETE FROM web_users WHERE user_id = %s", (ALICE,))
        self.assertEqual(await self.count("entitlements", self.db.user(ALICE)), 0)
        self.assertEqual(await self.count("web_sessions", self.db.user(ALICE)), 0)
        async with self.db.guild(GUILD_A) as conn:  # the install stays, without the person
            cur = await conn.execute("SELECT installed_by_user_id FROM installs")
            self.assertEqual([r["installed_by_user_id"] for r in await cur.fetchall()], [None])

    # Sessions

    async def test_a_session_is_found_only_by_its_unexpired_hash(self) -> None:
        await self.add_user(ALICE)
        await self.add_session(ALICE, "live", FAR_FUTURE)
        await self.add_session(ALICE, "old", 1)
        async with self.db.session("live") as conn:
            cur = await conn.execute("SELECT user_id FROM web_sessions")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [ALICE])
        self.assertEqual(await self.count("web_sessions", self.db.session("old")), 0)
        self.assertEqual(await self.count("web_sessions", self.db.session("other")), 0)
        # The cookie alone reaches nothing else of the person's.
        self.assertEqual(await self.count("web_users", self.db.session("live")), 0)

    async def test_a_cookie_cannot_change_or_end_its_session(self) -> None:
        await self.add_user(ALICE)
        await self.add_user(BOB)
        await self.add_session(ALICE, "live", FAR_FUTURE)
        async with self.db.session("live") as conn:
            cur = await conn.execute("UPDATE web_sessions SET user_id = %s", (BOB,))
            self.assertEqual(cur.rowcount, 0)
            cur = await conn.execute("DELETE FROM web_sessions")
            self.assertEqual(cur.rowcount, 0)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.session("new") as conn:
                await conn.execute(
                    "INSERT INTO web_sessions (id_hash, user_id, created_at, expires_at)"
                    " VALUES ('new', %s, 0, %s)",
                    (ALICE, FAR_FUTURE),
                )

    async def test_cleanup_deletes_only_expired_sessions(self) -> None:
        await self.add_user(ALICE)
        await self.add_session(ALICE, "live", FAR_FUTURE)
        await self.add_session(ALICE, "old", 1)
        async with self.db.cleanup() as conn:
            cur = await conn.execute("DELETE FROM web_sessions")
            self.assertEqual(cur.rowcount, 1)
            self.assertEqual(await self.count("web_users", self.db.cleanup()), 0)
        self.assertEqual(await self.count("web_sessions", self.db.user(ALICE)), 1)

    # Installs

    async def test_installs_are_seen_by_their_server_and_their_installer(self) -> None:
        await self.add_user(ALICE)
        async with self.db.user(ALICE, install_guild=GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, %s, 0, 'site')",
                (GUILD_A, ALICE),
            )
        async with self.db.guild(GUILD_B) as conn:  # the bot joining through a plain link
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, NULL, 0, 'link')",
                (GUILD_B,),
            )
        self.assertEqual(await self.count("installs", self.db.user(ALICE)), 1)
        self.assertEqual(await self.count("installs", self.db.user(BOB)), 0)
        self.assertEqual(await self.count("installs", self.db.guild(GUILD_A)), 1)
        self.assertEqual(await self.count("installs", self.db.guild(GUILD_B)), 1)
        self.assertEqual(await self.count("installs", self.db.unscoped()), 0)

    async def test_a_person_cannot_claim_a_server_they_are_not_installing(self) -> None:
        await self.add_user(BOB)
        # Bob names himself as installer of a server that isn't his install server.
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.user(BOB) as conn:
                await conn.execute(
                    "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                    " VALUES (%s, %s, 0, 'site')",
                    (GUILD_B, BOB),
                )
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.user(BOB, install_guild=GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                    " VALUES (%s, %s, 0, 'site')",
                    (GUILD_B, BOB),
                )

    async def test_a_person_cannot_move_or_delete_their_install_elsewhere(self) -> None:
        await self.add_user(BOB)
        async with self.db.user(BOB, install_guild=GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, %s, 0, 'site')",
                (GUILD_A, BOB),
            )
        async with self.db.user(BOB) as conn:  # no install server set
            cur = await conn.execute("UPDATE installs SET guild_id = %s", (GUILD_B,))
            self.assertEqual(cur.rowcount, 0)
            cur = await conn.execute("DELETE FROM installs")
            self.assertEqual(cur.rowcount, 0)

    async def test_the_install_server_unlocks_no_campaign_data(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (%s, 5, 0)",
                (GUILD_A,),
            )
        self.assertEqual(await self.count("consent", self.db.user(BOB, install_guild=GUILD_A)), 0)

    async def test_try_it_once_even_after_deleting_the_account(self) -> None:
        await self.add_user(ALICE)
        add = "INSERT INTO try_it_used (user_id, used_at) VALUES (%s, 0)"
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.user(ALICE) as conn:  # only the plan writer marks it
                await conn.execute(add, (ALICE,))
        async with self.db.plan_writer(ALICE) as conn:
            await conn.execute(add, (ALICE,))
        async with self.db.user(ALICE) as conn:
            cur = await conn.execute("DELETE FROM try_it_used")
            self.assertEqual(cur.rowcount, 0)
            cur = await conn.execute("UPDATE try_it_used SET used_at = 5")
            self.assertEqual(cur.rowcount, 0)
            await conn.execute("DELETE FROM web_users WHERE user_id = %s", (ALICE,))
        self.assertEqual(await self.count("try_it_used", self.db.user(ALICE)), 1)

    async def test_a_person_cannot_forge_or_forget_payment_events(self) -> None:
        await self.add_user(ALICE)
        add = (
            "INSERT INTO payment_events (provider, event_id, user_id, received_at)"
            " VALUES ('fake', %s, %s, 0)"
        )
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.user(ALICE) as conn:  # pre-inserting would block a real event
                await conn.execute(add, ("evt-9", ALICE))
        async with self.db.plan_writer(ALICE) as conn:
            await conn.execute(add, ("evt-1", ALICE))
        async with self.db.user(ALICE) as conn:  # forgetting would let it apply again
            cur = await conn.execute("DELETE FROM payment_events")
            self.assertEqual(cur.rowcount, 0)
        self.assertEqual(await self.count("payment_events", self.db.user(ALICE)), 1)

    async def test_the_website_names_only_the_signed_in_person_as_installer(self) -> None:
        await self.add_user(ALICE)
        await self.add_user(BOB)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.user(BOB, install_guild=GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                    " VALUES (%s, %s, 0, 'site')",
                    (GUILD_A, ALICE),
                )

    async def test_linking_fills_an_empty_installer_but_never_takes_one_over(self) -> None:
        await self.add_user(ALICE)
        await self.add_user(BOB)
        async with self.db.guild(GUILD_A) as conn:  # joined through a plain link
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, NULL, 0, 'link')",
                (GUILD_A,),
            )
        link = "UPDATE installs SET installed_by_user_id = %s WHERE guild_id = %s"
        async with self.db.user(ALICE, install_guild=GUILD_A) as conn:
            cur = await conn.execute(link, (ALICE, GUILD_A))
            self.assertEqual(cur.rowcount, 1)
        async with self.db.user(BOB, install_guild=GUILD_A) as conn:
            cur = await conn.execute(link, (BOB, GUILD_A))
            self.assertEqual(cur.rowcount, 0)
            cur = await conn.execute("DELETE FROM installs")
            self.assertEqual(cur.rowcount, 0)

    async def test_a_plan_can_be_read_inside_a_server_transaction(self) -> None:
        await self.add_user(ALICE)
        await self.add_plan(ALICE)
        async with self.db.guild(GUILD_A) as conn:  # like /dmbot start (#437)
            got = await entitlements.read(conn, ALICE)
            assert got is not None
            self.assertEqual(got.plan, "table")
            self.assertIsNone(await entitlements.read(conn, BOB))

    async def test_settings_do_not_leak_to_the_next_transaction(self) -> None:
        await self.add_user(ALICE)
        async with self.db.plan_writer(ALICE):
            pass
        self.assertEqual(await self.count("web_users", self.db.unscoped()), 0)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.user(ALICE) as conn:
                await conn.execute(INSERT_PLAN, {**PLAN_ROW, "user_id": ALICE})


def plan(**changes: object) -> Entitlement:
    base: dict[str, object] = {
        "user_id": ALICE,
        "plan": "table",
        "status": "active",
        "hours_cap": 18,
        "extra_hours": 0,
        "campaign_cap": 1,
        "period_start": 0,
        "period_end": 1000,
        "grace_ends_at": None,
        "lapsed_at": None,
        "plan_changed_at": 0,
    }
    base.update(changes)
    return Entitlement(**base)  # type: ignore[arg-type]


class Usable(unittest.TestCase):
    def test_active_until_shortly_after_the_period(self) -> None:
        self.assertTrue(plan().usable(now=999))
        self.assertTrue(plan().usable(now=1000 + RENEWAL_SLACK_SECONDS - 1))
        self.assertFalse(plan().usable(now=1000 + RENEWAL_SLACK_SECONDS))

    def test_try_it_ends_with_its_period(self) -> None:
        self.assertTrue(plan(plan="try-it").usable(now=999))
        self.assertFalse(plan(plan="try-it").usable(now=1000))

    def test_grace_until_its_end(self) -> None:
        self.assertTrue(plan(status="grace", grace_ends_at=150).usable(now=149))
        self.assertFalse(plan(status="grace", grace_ends_at=150).usable(now=150))

    def test_lapsed_never(self) -> None:
        self.assertFalse(plan(status="lapsed", lapsed_at=10).usable(now=0))
