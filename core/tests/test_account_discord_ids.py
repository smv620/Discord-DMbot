"""Several Discord accounts on one DMbot account (#2533, slice 1): the tables, the one resolver, the
add and remove doors, and the refusal of a removed or added Discord ID making an account of its
own. Needs Postgres. Nothing here changes what the bot or the website does yet."""

from __future__ import annotations

from psycopg import errors as pg_errors

from dmbot import accounts
from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from dmbot.web import sessions
from dmbot.web.discord import DiscordUser
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_role import ensure_web_role, web_url

FIRST = 5001
SECOND = 5002
THIRD = 5003
OTHER = 6001
NOW = 1_700_000_000


async def add_user(db: Database, user_id: int) -> None:
    async with db.user(user_id) as conn:
        await conn.execute(
            "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
            " VALUES (%s, NULL, 0, 0)",
            (user_id,),
        )


async def link(db: Database, acct: int, discord_id: int) -> str:
    async with db.user(acct) as conn:
        cur = await conn.execute(
            "SELECT dmbot_account_link(%s, %s, %s) AS r", (acct, discord_id, NOW)
        )
        row = await cur.fetchone()
    assert row is not None
    return str(row["r"])


async def unlink(db: Database, acct: int, discord_id: int) -> str:
    async with db.user(acct) as conn:
        cur = await conn.execute(
            "SELECT dmbot_account_unlink(%s, %s, %s) AS r", (acct, discord_id, NOW)
        )
        row = await cur.fetchone()
    assert row is not None
    return str(row["r"])


async def history(db: Database, acct: int) -> list[tuple[int, str]]:
    async with db.user(acct) as conn:
        cur = await conn.execute("SELECT discord_id, action FROM account_link_history ORDER BY id")
        return [(int(r["discord_id"]), str(r["action"])) for r in await cur.fetchall()]


class Resolver(DatabaseTest):
    async def test_an_account_is_its_own_number_and_an_unknown_id_is_nobody(self) -> None:
        await add_user(self.db, FIRST)
        self.assertEqual(await accounts.account_of(self.db, FIRST), FIRST)
        self.assertIsNone(await accounts.account_of(self.db, OTHER))

    async def test_an_added_discord_id_gives_the_account_it_was_added_to(self) -> None:
        await add_user(self.db, FIRST)
        self.assertEqual(await link(self.db, FIRST, SECOND), "ok")
        self.assertEqual(await accounts.account_of(self.db, SECOND), FIRST)
        self.assertEqual(await accounts.account_of(self.db, FIRST), FIRST)

    async def test_a_discord_id_cannot_be_on_two_accounts(self) -> None:
        await add_user(self.db, FIRST)
        await add_user(self.db, OTHER)
        self.assertEqual(await link(self.db, FIRST, SECOND), "ok")
        self.assertEqual(await link(self.db, OTHER, SECOND), "taken")
        self.assertEqual(await link(self.db, FIRST, SECOND), "taken")
        self.assertEqual(await accounts.account_of(self.db, SECOND), FIRST)
        with self.assertRaises(pg_errors.UniqueViolation):
            async with self.db.unscoped() as conn:
                await conn.execute("SET LOCAL dmbot.owner_sync = 'move'")
                await conn.execute(
                    "INSERT INTO account_discord_ids (discord_id, account_id, added_at)"
                    " VALUES (%s, %s, 0)",
                    (SECOND, OTHER),
                )

    async def test_an_account_number_cannot_be_added_to_itself_or_another_account(self) -> None:
        await add_user(self.db, FIRST)
        await add_user(self.db, OTHER)
        self.assertEqual(await link(self.db, FIRST, FIRST), "taken")
        self.assertEqual(await link(self.db, FIRST, OTHER), "has_account")


class Refusals(DatabaseTest):
    async def test_only_the_account_itself_can_add_or_remove(self) -> None:
        await add_user(self.db, FIRST)
        await add_user(self.db, OTHER)
        async with self.db.user(OTHER) as conn:  # someone else's account number
            cur = await conn.execute(
                "SELECT dmbot_account_link(%s, %s, %s) AS r", (FIRST, SECOND, NOW)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["r"], "not_yours")
        self.assertIsNone(await accounts.account_of(self.db, SECOND))
        self.assertEqual(await link(self.db, FIRST, SECOND), "ok")
        async with self.db.user(OTHER) as conn:
            cur = await conn.execute(
                "SELECT dmbot_account_unlink(%s, %s, %s) AS r", (FIRST, SECOND, NOW)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["r"], "not_yours")
        self.assertEqual(await accounts.account_of(self.db, SECOND), FIRST)

    async def test_a_discord_account_that_owns_a_campaign_cannot_be_added(self) -> None:
        await add_user(self.db, FIRST)
        await CampaignStore(self.db).create(111, "Frostmaiden", SECOND)
        self.assertEqual(await link(self.db, FIRST, SECOND), "has_campaigns")
        self.assertIsNone(await accounts.account_of(self.db, SECOND))

    async def test_a_discord_account_with_a_plan_cannot_be_added(self) -> None:
        await add_user(self.db, FIRST)
        async with self.db.unscoped() as conn:
            await conn.execute("SET LOCAL dmbot.owner_sync = 'move'")
            await conn.execute(
                "INSERT INTO owner_hours (owner_user_id, month_start, minutes) VALUES (%s, 0, 5)",
                (SECOND,),
            )
        self.assertEqual(await link(self.db, FIRST, SECOND), "has_plan")

    async def test_an_account_that_moved_away_cannot_be_added(self) -> None:
        await add_user(self.db, FIRST)
        async with self.db.unscoped() as conn:
            await conn.execute("SET LOCAL dmbot.owner_sync = 'move'")
            await conn.execute(
                "INSERT INTO account_moves (dmbot_id, old_user_id, new_user_id, moved_at)"
                " VALUES ('x', %s, %s, 0)",
                (SECOND, THIRD),
            )
        self.assertEqual(await link(self.db, FIRST, SECOND), "taken")


class Removing(DatabaseTest):
    async def test_never_the_last_discord_account(self) -> None:
        await add_user(self.db, FIRST)
        self.assertEqual(await unlink(self.db, FIRST, FIRST), "last")
        self.assertEqual(await link(self.db, FIRST, SECOND), "ok")
        self.assertEqual(await unlink(self.db, FIRST, FIRST), "ok")
        self.assertEqual(await unlink(self.db, FIRST, SECOND), "last")
        self.assertEqual(await accounts.account_of(self.db, SECOND), FIRST)

    async def test_an_added_one_can_be_removed_and_then_added_again(self) -> None:
        await add_user(self.db, FIRST)
        await link(self.db, FIRST, SECOND)
        self.assertEqual(await unlink(self.db, FIRST, SECOND), "ok")
        self.assertIsNone(await accounts.account_of(self.db, SECOND))
        self.assertEqual(await unlink(self.db, FIRST, SECOND), "not_linked")
        self.assertEqual(await link(self.db, FIRST, SECOND), "ok")

    async def test_a_removed_first_id_is_nobody_and_the_account_keeps_working(self) -> None:
        await add_user(self.db, FIRST)
        await link(self.db, FIRST, SECOND)
        self.assertEqual(await unlink(self.db, FIRST, FIRST), "ok")
        self.assertIsNone(await accounts.account_of(self.db, FIRST))
        self.assertEqual(await accounts.account_of(self.db, SECOND), FIRST)
        self.assertEqual(await unlink(self.db, FIRST, FIRST), "not_linked")
        # It cannot be added to this account again, nor to another (it is reserved for good).
        await add_user(self.db, OTHER)
        self.assertEqual(await link(self.db, OTHER, FIRST), "taken")
        self.assertEqual(await link(self.db, FIRST, FIRST), "taken")

    async def test_a_removed_first_id_cannot_make_an_account_by_signing_in(self) -> None:
        await add_user(self.db, FIRST)
        await link(self.db, FIRST, SECOND)
        await unlink(self.db, FIRST, FIRST)
        with self.assertRaises(pg_errors.RaiseException) as caught:
            await add_user(self.db, FIRST)
        self.assertEqual(caught.exception.diag.message_hint, "account_removed")

    async def test_an_added_id_cannot_make_an_account_by_signing_in(self) -> None:
        await add_user(self.db, FIRST)
        await link(self.db, FIRST, SECOND)
        with self.assertRaises(pg_errors.RaiseException) as caught:
            await add_user(self.db, SECOND)
        self.assertEqual(caught.exception.diag.message_hint, "account_linked")

    async def test_every_add_and_remove_is_in_the_history(self) -> None:
        await add_user(self.db, FIRST)
        await link(self.db, FIRST, SECOND)
        await link(self.db, FIRST, THIRD)
        await unlink(self.db, FIRST, SECOND)
        await unlink(self.db, FIRST, FIRST)
        self.assertEqual(
            await history(self.db, FIRST),
            [(SECOND, "added"), (THIRD, "added"), (SECOND, "removed"), (FIRST, "removed")],
        )
        self.assertEqual(await history(self.db, OTHER), [])  # not another account's


class Isolation(DatabaseTest):
    async def test_an_account_reads_only_its_own_rows(self) -> None:
        await add_user(self.db, FIRST)
        await add_user(self.db, OTHER)
        await link(self.db, FIRST, SECOND)
        async with self.db.user(OTHER) as conn:
            for table in ("account_discord_ids", "account_reserved", "account_link_history"):
                cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
                row = await cur.fetchone()
                assert row is not None
                self.assertEqual(row["n"], 0, table)
        async with self.db.user(FIRST) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM account_discord_ids")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 1)

    async def test_deleting_the_account_deletes_its_rows(self) -> None:
        await add_user(self.db, FIRST)
        await link(self.db, FIRST, SECOND)
        async with self.db.unscoped() as conn:
            await conn.execute("SET LOCAL dmbot.owner_sync = 'move'")
            await conn.execute("DELETE FROM web_users WHERE user_id = %s", (FIRST,))
            cur = await conn.execute("SELECT count(*) AS n FROM account_discord_ids")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)


class WebsiteSignIn(DatabaseTest):
    async def web(self) -> Database:
        """The website's own database role, as the internet-facing API connects."""
        if not SUPERUSER_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_SUPERUSER_URL is needed to test the website's role")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test the website's role")
        await ensure_web_role()
        web = await Database.open(web_url(), schema=self.schema, max_size=2, migrate=False)
        self.addAsyncCleanup(web.close)
        return web

    async def test_a_removed_or_added_discord_id_gets_the_plain_refusal(self) -> None:
        web = await self.web()
        await add_user(self.db, FIRST)
        await link(self.db, FIRST, SECOND)
        await unlink(self.db, FIRST, FIRST)
        for gone in (FIRST, SECOND):
            with self.assertRaises(sessions.AccountRemoved):
                await sessions.sign_in(
                    web, DiscordUser(id=gone, name="Gone", email=None), [], now=NOW, days=1
                )
        # Anyone else still signs in as before.
        await sessions.sign_in(
            web, DiscordUser(id=OTHER, name="Fresh", email=None), [], now=NOW, days=1
        )
