"""After an account is deleted the kept trial and unsubscribe records hold the keyed hash and no
longer the Discord ID (#4145, slice d), and a returning person is still recognised: no second free
trial, still unsubscribed. A row with no hash keeps its ID, as before. Needs Postgres."""

from __future__ import annotations

import psycopg

from dmbot import accounts, entitlements, mail, person_key
from dmbot.campaigns import CampaignStore
from dmbot.web import entitlements_writer
from tests.pg import DatabaseTest

ALICE, BOB = 7001, 7002
NOW = 1_800_000_000
SECRET = "d" * 40


class Forgotten(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(person_key.configure, "")
        self.addCleanup(entitlements.configure_free_users, ())
        for user in (ALICE, BOB):
            async with self.db.user(user) as conn:
                await conn.execute(
                    "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                    " VALUES (%s, NULL, 0, 0)",
                    (user,),
                )
        self.store = CampaignStore(self.db, clock=lambda: NOW)

    async def rows(self, sql: str, *args: object) -> list[dict[str, object]]:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(sql, args)
            return list(await cur.fetchall())

    async def keep_records(self, user: int) -> None:
        async with self.db.plan_writer(user) as conn:
            await conn.execute(
                "INSERT INTO try_it_used (user_id, person_key) VALUES (%s, %s)",
                (user, person_key.key_for(user)),
            )
        await mail.unsubscribe(self.db, user, now=NOW)
        async with self.db.unscoped() as conn:
            await conn.execute(
                "INSERT INTO trial_extensions (user_id, extended_at, person_key)"
                " VALUES (%s, 1, %s)",
                (user, person_key.key_for(user)),
            )
            await conn.execute(
                "INSERT INTO sent_emails (user_id, kind, sent_at, person_key)"
                " VALUES (%s, 'x', 1, %s)",
                (user, person_key.key_for(user)),
            )

    async def test_the_id_goes_and_the_key_stays_in_the_three_kept_records(self) -> None:
        person_key.configure(SECRET)
        for user in (ALICE, BOB):
            await self.keep_records(user)
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        for table in ("email_unsubscribed", "trial_extensions"):
            mine = await self.rows(f"SELECT user_id, person_key FROM {table} ORDER BY user_id")
            ids = [r["user_id"] for r in mine]
            self.assertEqual(sorted(i for i in ids if i is not None), [BOB], table)  # (Bob's stays)
            keys = {r["person_key"] for r in mine}
            self.assertEqual(keys, {person_key.key_for(ALICE), person_key.key_for(BOB)}, table)
        async with self.db.plan_writer(BOB) as conn:  # (Bob's own row, through its own policy)
            cur = await conn.execute("SELECT user_id FROM try_it_used")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [BOB])
        self.assertEqual(
            await self.rows("SELECT 1 FROM sent_emails WHERE user_id = %s", ALICE), []
        )  # (the email records of the person still go, as before)

    async def test_a_returning_person_gets_no_second_trial_and_stays_unsubscribed(self) -> None:
        person_key.configure(SECRET)
        await self.keep_records(ALICE)
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        async with self.db.user(ALICE) as conn:  # the same Discord ID signs up again
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, NULL, 0, 0)",
                (ALICE,),
            )
        result = await entitlements_writer.start_try_it(self.db, ALICE, now=NOW + 100)
        self.assertEqual((result.started, result.reason), (False, "used"))
        sender = mail.Mailer(self.db, mail.MailSettings(), clock=lambda: NOW)
        self.assertTrue(await sender.is_unsubscribed(ALICE))
        self.assertFalse(await sender.is_unsubscribed(BOB))

    async def test_a_row_with_no_key_keeps_its_id_as_before(self) -> None:
        person_key.configure("")  # (the secret was never set)
        await self.keep_records(ALICE)
        await accounts.delete_person(self.db, ALICE, now=NOW + 50)
        [row] = await self.rows("SELECT user_id, person_key FROM email_unsubscribed")
        self.assertEqual((row["user_id"], row["person_key"]), (ALICE, None))

    async def test_a_row_must_have_an_id_or_a_key(self) -> None:
        async with self.db.unscoped() as conn:
            with self.assertRaises(psycopg.errors.CheckViolation):
                await conn.execute("INSERT INTO email_unsubscribed (user_id, at) VALUES (NULL, 1)")

    async def test_the_clearing_door_does_nothing_without_a_signed_in_person(self) -> None:
        person_key.configure(SECRET)
        await self.keep_records(ALICE)
        [row] = await self.rows("SELECT dmbot_forget_person_ids() AS n")
        self.assertEqual(row["n"], 0)
        [kept] = await self.rows("SELECT user_id FROM email_unsubscribed")
        self.assertEqual(kept["user_id"], ALICE)
