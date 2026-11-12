"""The keyed hash kept beside the Discord ID in the trial and email records (#4145, slice b): rows
are written with it once the secret is set, found by it, refused when malformed, and never need it
when no secret is set. Needs Postgres."""

from __future__ import annotations

import psycopg
from psycopg import errors as pg_errors

from dmbot import mail, person_key, trial_jobs
from tests.pg import DatabaseTest

SECRET = "k" * 40
ALICE, BOB = 7001, 7002
NOW = 1_800_000_000
KEY = "cd" * 32


class PersonKeyRecords(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        person_key.configure(SECRET)
        self.addCleanup(person_key.configure, "")

    async def rows(self, sql: str, *args: object) -> list[dict[str, object]]:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(sql, args)
            return list(await cur.fetchall())

    async def test_an_unsubscribe_keeps_the_key_and_is_found_by_it_alone(self) -> None:
        await mail.unsubscribe(self.db, ALICE, now=NOW)
        [row] = await self.rows("SELECT user_id, person_key FROM email_unsubscribed")
        self.assertEqual(row["person_key"], person_key.key_for(ALICE))
        # A row for the same person under another ID (what slice d leaves after a deletion) counts.
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE email_unsubscribed SET user_id = %s", (BOB,))
        sender = mail.Mailer(self.db, mail.MailSettings(), clock=lambda: NOW)
        self.assertTrue(await sender.is_unsubscribed(ALICE))  # (found by the key)
        self.assertFalse(await sender.is_unsubscribed(7003))

    async def test_the_claim_door_stores_the_key_finds_a_duplicate_by_it_and_refuses_junk(
        self,
    ) -> None:
        async def claim(who: int, key: str | None) -> str:
            rows = await self.rows(
                "SELECT dmbot_claim_email(%s, %s, false, 400, 300, %s) AS status",
                who, "offer:1", key,
            )  # fmt: skip
            return str(rows[0]["status"])

        self.assertEqual(await claim(ALICE, KEY), "sent")
        self.assertEqual(await claim(ALICE, KEY), "duplicate")
        self.assertEqual(await claim(BOB, KEY), "duplicate")  # (the same person under another ID)
        self.assertEqual(await claim(BOB, "ef" * 32), "sent")  # (another person)
        self.assertEqual(await claim(ALICE, "nope"), "refused")
        self.assertEqual(await claim(ALICE, KEY.upper()), "refused")
        keys = {r["person_key"] for r in await self.rows("SELECT person_key FROM sent_emails")}
        self.assertEqual(keys, {KEY, "ef" * 32})

    async def test_the_old_five_argument_call_still_works_with_no_key(self) -> None:
        rows = await self.rows(
            "SELECT dmbot_claim_email(%s, %s, false, 400, 300) AS status", ALICE, "offer:2"
        )
        self.assertEqual(rows[0]["status"], "sent")
        [row] = await self.rows("SELECT person_key FROM sent_emails WHERE kind = 'offer:2'")
        self.assertIsNone(row["person_key"])

    async def test_release_gives_back_a_claim_by_id_or_by_key(self) -> None:
        await self.rows(
            "SELECT dmbot_claim_email(%s, %s, false, 400, 300, %s)", ALICE, "offer:3", KEY
        )
        await self.rows("SELECT dmbot_release_email(%s, %s, %s)", BOB, "offer:3", KEY)
        self.assertEqual(await self.rows("SELECT 1 FROM sent_emails WHERE kind = 'offer:3'"), [])

    async def test_a_private_message_record_is_claimed_and_released_with_the_key(self) -> None:
        self.assertTrue(await trial_jobs.claim(self.db, ALICE, "dm:trial-1", NOW))
        [row] = await self.rows("SELECT person_key FROM sent_emails WHERE kind = 'dm:trial-1'")
        self.assertEqual(row["person_key"], person_key.key_for(ALICE))
        self.assertFalse(await trial_jobs.claim(self.db, ALICE, "dm:trial-1", NOW))
        await trial_jobs.release(self.db, ALICE, "dm:trial-1")
        self.assertEqual(await self.rows("SELECT 1 FROM sent_emails"), [])

    async def test_a_second_trial_for_the_same_person_conflicts_on_the_key(self) -> None:
        async def insert(who: int, key: str | None) -> bool:
            async with self.db.plan_writer(who) as conn:
                cur = await conn.execute(
                    "INSERT INTO try_it_used (user_id, person_key) VALUES (%s, %s)"
                    " ON CONFLICT DO NOTHING RETURNING user_id",
                    (who, key),
                )
                return await cur.fetchone() is not None

        self.assertTrue(await insert(ALICE, KEY))
        self.assertFalse(
            await insert(BOB, KEY)
        )  # (the same person under a new ID: no second trial)
        self.assertTrue(await insert(BOB, "ef" * 32))

    async def test_a_key_is_unique_and_malformed_keys_are_refused_by_the_tables(self) -> None:
        async with self.db.unscoped() as conn:
            await conn.execute(
                "INSERT INTO email_unsubscribed (user_id, at, person_key) VALUES (%s, 1, %s)",
                (ALICE, KEY),
            )
            with self.assertRaises(pg_errors.UniqueViolation):
                await conn.execute(
                    "INSERT INTO email_unsubscribed (user_id, at, person_key) VALUES (%s, 1, %s)",
                    (BOB, KEY),
                )
        async with self.db.unscoped() as conn:
            with self.assertRaises(psycopg.errors.CheckViolation):
                await conn.execute(
                    "INSERT INTO email_unsubscribed (user_id, at, person_key) VALUES (%s, 1, %s)",
                    (BOB, "zz"),
                )

    async def test_with_no_secret_nothing_is_stored_and_everything_works_as_before(self) -> None:
        person_key.configure("")
        await mail.unsubscribe(self.db, ALICE, now=NOW)
        [row] = await self.rows("SELECT person_key FROM email_unsubscribed")
        self.assertIsNone(row["person_key"])
        self.assertIsNone(person_key.key_for(ALICE))

    async def test_an_invalid_id_has_no_key_so_a_read_finds_nothing(self) -> None:
        for bad in (0, -1, 2**63):
            self.assertIsNone(person_key.key_for(bad))

    async def test_a_null_key_matches_no_row_that_has_none(self) -> None:
        person_key.configure("")
        self.assertTrue(await trial_jobs.claim(self.db, ALICE, "dm:trial-9", NOW))
        sender = mail.Mailer(self.db, mail.MailSettings(), clock=lambda: NOW)
        self.assertTrue(await sender.already_sent(ALICE, "dm:trial-9"))
        self.assertFalse(await sender.already_sent(BOB, "dm:trial-9"))  # (NULL = NULL is not true)

    async def test_already_sent_and_the_extension_notice_find_a_row_by_the_key_alone(self) -> None:
        async with self.db.unscoped() as conn:
            await conn.execute(
                "INSERT INTO sent_emails (user_id, kind, sent_at, person_key)"
                " VALUES (%s, %s, 1, %s)",
                (BOB, "offer:7", person_key.key_for(ALICE)),
            )
            await conn.execute(
                "INSERT INTO trial_extensions (user_id, extended_at, person_key)"
                " VALUES (%s, 1, %s)",
                (BOB, person_key.key_for(ALICE)),
            )
        sender = mail.Mailer(self.db, mail.MailSettings(), clock=lambda: NOW)
        self.assertTrue(await sender.already_sent(ALICE, "offer:7"))
        self.assertEqual(
            await trial_jobs.take_extension_notice(self.db, ALICE, NOW), trial_jobs.EXTENSION_NOTICE
        )
        self.assertIsNone(
            await trial_jobs.take_extension_notice(self.db, ALICE, NOW)
        )  # (told once)

    async def test_release_still_takes_the_old_two_argument_form(self) -> None:
        await self.rows("SELECT dmbot_claim_email(%s, %s, false, 400, 300)", ALICE, "offer:8")
        await self.rows("SELECT dmbot_release_email(%s, %s)", ALICE, "offer:8")
        self.assertEqual(await self.rows("SELECT 1 FROM sent_emails WHERE kind = 'offer:8'"), [])
