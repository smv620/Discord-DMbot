"""Website accounts in the database (#435): each person sees only their own rows, a
session is found only by its cookie hash, and installs are visible to their server and
to the person who installed DMbot."""

from psycopg import errors as pg_errors

from dmbot import entitlements
from tests.pg import DatabaseTest

ALICE, BOB = 1001, 1002
GUILD_A, GUILD_B = 111, 222


class WebAccounts(DatabaseTest):
    async def add_user(self, user_id: int) -> None:
        async with self.db.user(user_id) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, NULL, 0, 0)",
                (user_id,),
            )

    async def add_entitlement(self, user_id: int, **values: object) -> None:
        row: dict[str, object] = {
            "plan": "table",
            "status": "active",
            "hours_cap": 18,
            "campaign_cap": 1,
            "period_start": 100,
            "period_end": 200,
            "grace_ends_at": None,
            "provider": "fake",
            "last_event_at": 100,
            "updated_at": 100,
        }
        row.update(values)
        async with self.db.user(user_id) as conn:
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, campaign_cap,"
                " period_start, period_end, grace_ends_at, provider, last_event_at, updated_at)"
                " VALUES (%(user_id)s, %(plan)s, %(status)s, %(hours_cap)s, %(campaign_cap)s,"
                " %(period_start)s, %(period_end)s, %(grace_ends_at)s, %(provider)s,"
                " %(last_event_at)s, %(updated_at)s)",
                {"user_id": user_id, **row},
            )

    async def count(self, table: str, conn_ctx: object) -> int:
        async with conn_ctx as conn:  # type: ignore[attr-defined]
            cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
            row = await cur.fetchone()
            return int(row["n"])

    async def test_a_person_sees_only_their_own_plan(self) -> None:
        await self.add_user(ALICE)
        await self.add_user(BOB)
        await self.add_entitlement(ALICE)
        self.assertEqual(await self.count("entitlements", self.db.user(ALICE)), 1)
        self.assertEqual(await self.count("entitlements", self.db.user(BOB)), 0)
        self.assertEqual(await self.count("entitlements", self.db.unscoped()), 0)
        self.assertEqual(await self.count("web_users", self.db.guild(GUILD_A)), 0)

    async def test_cannot_write_another_persons_plan(self) -> None:
        await self.add_user(ALICE)
        await self.add_user(BOB)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.user(BOB) as conn:
                await conn.execute(
                    "INSERT INTO entitlements (user_id, plan, status, hours_cap, campaign_cap,"
                    " period_start, period_end, provider, last_event_at, updated_at)"
                    " VALUES (%s, 'guild', 'active', 87, 5, 0, 1, 'fake', 0, 0)",
                    (ALICE,),
                )

    async def test_reads_a_plan_and_knows_when_it_works(self) -> None:
        await self.add_user(ALICE)
        self.assertIsNone(await entitlements.get(self.db, ALICE))
        await self.add_entitlement(ALICE, status="grace", grace_ends_at=150)
        got = await entitlements.get(self.db, ALICE)
        assert got is not None
        self.assertEqual((got.plan, got.hours_cap, got.campaign_cap), ("table", 18, 1))
        self.assertTrue(got.usable(now=149))
        self.assertFalse(got.usable(now=150))

    async def test_grace_needs_its_end_date(self) -> None:
        await self.add_user(ALICE)
        with self.assertRaises(pg_errors.CheckViolation):
            await self.add_entitlement(ALICE, status="grace", grace_ends_at=None)

    async def test_unknown_plan_ids_are_refused(self) -> None:
        await self.add_user(ALICE)
        with self.assertRaises(pg_errors.CheckViolation):
            await self.add_entitlement(ALICE, plan="free-forever")

    async def test_a_session_is_found_only_by_its_hash(self) -> None:
        await self.add_user(ALICE)
        async with self.db.user(ALICE) as conn:
            await conn.execute(
                "INSERT INTO web_sessions (id_hash, user_id, created_at, expires_at)"
                " VALUES ('hash-a', %s, 0, 10)",
                (ALICE,),
            )
        async with self.db.session("hash-a") as conn:
            cur = await conn.execute("SELECT user_id FROM web_sessions")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [ALICE])
        async with self.db.session("hash-b") as conn:
            cur = await conn.execute("SELECT user_id FROM web_sessions")
            self.assertEqual(await cur.fetchall(), [])
        # The session hash alone can't reach the person's other rows.
        self.assertEqual(await self.count("web_users", self.db.session("hash-a")), 0)

    async def test_a_session_cannot_be_made_for_someone_else(self) -> None:
        await self.add_user(ALICE)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.session("hash-x") as conn:
                await conn.execute(
                    "INSERT INTO web_sessions (id_hash, user_id, created_at, expires_at)"
                    " VALUES ('hash-x', %s, 0, 10)",
                    (ALICE,),
                )

    async def test_deleting_a_person_deletes_their_plan_and_sessions(self) -> None:
        await self.add_user(ALICE)
        await self.add_entitlement(ALICE)
        async with self.db.user(ALICE) as conn:
            await conn.execute(
                "INSERT INTO web_sessions (id_hash, user_id, created_at, expires_at)"
                " VALUES ('h', %s, 0, 10)",
                (ALICE,),
            )
            await conn.execute("DELETE FROM web_users WHERE user_id = %s", (ALICE,))
        self.assertEqual(await self.count("entitlements", self.db.user(ALICE)), 0)
        self.assertEqual(await self.count("web_sessions", self.db.user(ALICE)), 0)

    async def test_installs_are_seen_by_their_server_and_their_installer(self) -> None:
        async with self.db.user(ALICE, guild_id=GUILD_A) as conn:
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

    async def test_cannot_claim_another_servers_install_from_outside(self) -> None:
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.user(BOB) as conn:
                await conn.execute(
                    "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                    " VALUES (%s, %s, 0, 'site')",
                    (GUILD_B, ALICE),
                )

    async def test_user_setting_does_not_leak_to_the_next_transaction(self) -> None:
        await self.add_user(ALICE)
        async with self.db.user(ALICE):
            pass
        self.assertEqual(await self.count("web_users", self.db.unscoped()), 0)
