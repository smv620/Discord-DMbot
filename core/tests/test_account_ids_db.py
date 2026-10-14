"""The DMbot ID in the database (#1228): made for every account, the same as the Python
function, never changed; and experience points from the hours meter. Needs Postgres."""

from __future__ import annotations

from psycopg import errors as pg_errors

from dmbot import account_id, usage
from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.schema import MIGRATIONS
from dmbot.web import accounts
from tests.pg import TEST_URL, DatabaseTest

IDS = (1, 35, 36, 123456789012345678, 1_500_000_000_000_000_000, 36**12 - 1)
OWNER = 777
NOW = 1_700_000_000


async def add_user(db: Database, user_id: int) -> None:
    async with db.user(user_id) as conn:
        await conn.execute(
            "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
            " VALUES (%s, NULL, 0, 0)",
            (user_id,),
        )


class DmbotId(DatabaseTest):
    async def test_the_database_writes_the_same_id_as_python(self) -> None:
        for n in IDS:
            await add_user(self.db, n)
            self.assertEqual(await accounts.dmbot_id(self.db, n), account_id.to_dmbot_id(n))

    async def test_ids_python_refuses_the_database_refuses_too(self) -> None:
        with self.assertRaises(ValueError):
            account_id.to_dmbot_id(36**12)
        with self.assertRaises(pg_errors.RaiseException):
            await add_user(self.db, 36**12)

    async def test_it_cannot_be_changed_or_chosen(self) -> None:
        await add_user(self.db, 42)
        with self.assertRaises(pg_errors.RaiseException):
            async with self.db.user(42) as conn:
                await conn.execute("UPDATE web_users SET dmbot_id = 'ZZZ' WHERE user_id = 42")
        async with self.db.user(43) as conn:  # a caller's own value is ignored
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at, dmbot_id)"
                " VALUES (43, NULL, 0, 0, 'ABC')"
            )
        self.assertEqual(await accounts.dmbot_id(self.db, 43), account_id.to_dmbot_id(43))

    async def test_an_account_that_is_not_there_has_none(self) -> None:
        self.assertIsNone(await accounts.dmbot_id(self.db, 99))


class Backfill(DatabaseTest):
    async def test_accounts_made_before_the_migration_get_their_id(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0063"]
        await self.db.close()  # start again from a schema as it was before 0063
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        for n in IDS:
            await add_user(self.db, n)
        await self.db.migrate()
        for n in IDS:
            self.assertEqual(await accounts.dmbot_id(self.db, n), account_id.to_dmbot_id(n))


class ExperiencePoints(DatabaseTest):
    async def test_total_minutes_over_every_month_and_campaign_for_one_owner(self) -> None:
        campaigns = CampaignStore(self.db)
        a = await campaigns.create(111, "Frostmaiden", OWNER)
        b = await campaigns.create(222, "Another", OWNER)
        for guild, campaign, started, minutes in (
            (111, a.id, NOW, 30),
            (222, b.id, NOW, 12),
            (111, a.id, NOW + 90 * 86400, 60),  # a later month
        ):
            await usage.add_minutes(
                self.db,
                guild_id=guild,
                campaign_id=campaign,
                owner_user_id=OWNER,
                session_started_at=started,
                minutes=minutes,
                now=started + 600,
            )
        self.assertEqual(await usage.total_minutes(self.db, OWNER), 102)
        self.assertEqual(await usage.total_minutes(self.db, 5), 0)  # someone with none
