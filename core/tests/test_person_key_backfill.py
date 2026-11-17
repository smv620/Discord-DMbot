"""Giving the older trial and email rows their keyed hash (#4145, slice c): every table is filled,
the job pages through many people, skips a row whose key is already taken, does nothing without a
secret, writes nothing for a malformed pair, and can be run again. Needs Postgres."""

from __future__ import annotations

from dmbot import person_key, person_key_backfill
from tests.pg import DatabaseTest

SECRET = "b" * 40
NOW = 1_800_000_000


class Backfill(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        person_key.configure("")  # (rows are written the way they were before the secret)
        self.addCleanup(person_key.configure, "")

    async def rows(self, sql: str, *args: object) -> list[dict[str, object]]:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(sql, args)
            return list(await cur.fetchall())

    async def old_person(self, user: int) -> None:
        """A person with a row in each of the four tables and no key."""
        async with self.db.plan_writer(user) as conn:
            await conn.execute("INSERT INTO try_it_used (user_id) VALUES (%s)", (user,))
        async with self.db.unscoped() as conn:
            await conn.execute(
                "INSERT INTO trial_extensions (user_id, extended_at) VALUES (%s, %s)", (user, NOW)
            )
            await conn.execute(
                "INSERT INTO sent_emails (user_id, kind, sent_at) VALUES (%s, 'offer:1', 1),"
                " (%s, 'offer:2', 1)",
                (user, user),
            )
            await conn.execute(
                "INSERT INTO email_unsubscribed (user_id, at) VALUES (%s, 1)", (user,)
            )

    async def test_every_table_is_filled_for_every_person_and_a_rerun_changes_nothing(self) -> None:
        for user in (7001, 7002, 7003):
            await self.old_person(user)
        person_key.configure(SECRET)
        result = await person_key_backfill.run(self.db)
        self.assertEqual((result.people, result.rows, result.complete), (3, 15, True))
        for table in ("trial_extensions", "sent_emails", "email_unsubscribed"):
            rows = await self.rows(f"SELECT user_id, person_key FROM {table}")
            self.assertTrue(rows, table)
            for row in rows:
                self.assertEqual(
                    row["person_key"], person_key.key_for(int(str(row["user_id"]))), table
                )
        async with self.db.plan_writer(7001) as conn:  # (its own row, through its own policy)
            cur = await conn.execute("SELECT person_key FROM try_it_used")
            self.assertEqual(
                [r["person_key"] for r in await cur.fetchall()], [person_key.key_for(7001)]
            )
        [todo] = await self.rows("SELECT dmbot_person_key_todo(0, 1000) AS ids")
        self.assertEqual(todo["ids"], [])
        again = await person_key_backfill.run(self.db)
        self.assertEqual((again.people, again.rows), (0, 0))

    async def test_it_pages_through_more_people_than_one_batch(self) -> None:
        for user in range(7001, 7008):
            await self.old_person(user)
        person_key.configure(SECRET)
        result = await person_key_backfill.run(self.db, batch=3)
        self.assertEqual((result.people, result.complete), (7, True))
        [row] = await self.rows("SELECT count(*) AS n FROM sent_emails WHERE person_key IS NULL")
        self.assertEqual(row["n"], 0)

    async def test_a_run_that_hits_its_page_limit_says_so_and_the_next_one_goes_on(self) -> None:
        for user in range(7001, 7007):
            await self.old_person(user)
        person_key.configure(SECRET)
        first = await person_key_backfill.run(self.db, batch=2, max_pages=1)
        self.assertEqual((first.people, first.complete), (2, False))
        rest = await person_key_backfill.run(self.db, batch=2)
        self.assertEqual((rest.people, rest.complete), (4, True))

    async def test_without_a_secret_nothing_is_written(self) -> None:
        await self.old_person(7001)
        result = await person_key_backfill.run(self.db)
        self.assertEqual((result.people, result.rows), (0, 0))
        [row] = await self.rows("SELECT person_key FROM email_unsubscribed")
        self.assertIsNone(row["person_key"])

    async def test_a_taken_key_in_the_trial_table_leaves_that_row_alone_too(self) -> None:
        await self.old_person(7001)
        person_key.configure(SECRET)
        async with self.db.plan_writer(7099) as conn:
            await conn.execute(
                "INSERT INTO try_it_used (user_id, person_key) VALUES (7099, %s)",
                (person_key.key_for(7001),),
            )
        await person_key_backfill.run(self.db)
        async with self.db.plan_writer(7001) as conn:
            cur = await conn.execute("SELECT person_key FROM try_it_used")
            self.assertEqual([r["person_key"] for r in await cur.fetchall()], [None])

    async def test_the_gate_is_shut_outside_the_doors_and_after_them(self) -> None:
        await self.old_person(7001)
        person_key.configure(SECRET)
        async with self.db.unscoped() as conn:  # (try_it_used is held by row-level security)
            cur = await conn.execute("SELECT count(*) AS n FROM try_it_used")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
            result = await conn.execute("SELECT dmbot_person_key_todo(0, 10) AS ids")
            self.assertEqual((await result.fetchone() or {})["ids"], [7001])
            cur = await conn.execute("SELECT count(*) AS n FROM try_it_used")
            self.assertEqual(
                (await cur.fetchone() or {})["n"], 0
            )  # (the door closed behind itself)
            cur = await conn.execute("SELECT current_setting('dmbot.owner_sync', true) AS v")
            self.assertIn((await cur.fetchone() or {})["v"], (None, ""))

    async def test_a_batch_outside_one_to_a_thousand_is_clamped_not_read_as_done(self) -> None:
        for user in (7001, 7002):
            await self.old_person(user)
        person_key.configure(SECRET)
        result = await person_key_backfill.run(self.db, batch=5000)
        self.assertEqual(result.people, 2)

    async def test_a_key_already_on_another_row_is_skipped_not_an_error(self) -> None:
        await self.old_person(7001)
        person_key.configure(SECRET)
        taken = person_key.key_for(7001)
        async with self.db.unscoped() as conn:  # (the same person under another ID already has it)
            await conn.execute(
                "INSERT INTO email_unsubscribed (user_id, at, person_key) VALUES (7099, 1, %s)",
                (taken,),
            )
        result = await person_key_backfill.run(self.db)
        self.assertEqual(result.people, 1)
        [row] = await self.rows("SELECT person_key FROM email_unsubscribed WHERE user_id = 7001")
        self.assertIsNone(row["person_key"])  # (left alone: a unique index would refuse it)
        [filled] = await self.rows("SELECT person_key FROM sent_emails WHERE kind = 'offer:1'")
        self.assertEqual(filled["person_key"], taken)  # (the other tables are filled)

    async def test_the_fill_door_refuses_a_malformed_pair_and_changes_nothing(self) -> None:
        await self.old_person(7001)
        good = "ab" * 32
        for ids, keys in (
            ([7001], ["nope"]),
            ([7001], [good.upper()]),
            ([7001, 7002], [good]),
            ([0], [good]),
            ([7001], [None]),
        ):
            with self.subTest(ids=ids, keys=keys):
                [row] = await self.rows("SELECT dmbot_person_key_fill(%s, %s) AS n", ids, keys)
                self.assertEqual(row["n"], 0)
        [row] = await self.rows(
            "SELECT count(*) AS n FROM sent_emails WHERE person_key IS NOT NULL"
        )
        self.assertEqual(row["n"], 0)

    async def test_the_todo_door_pages_by_id_and_refuses_a_bad_batch(self) -> None:
        for user in (7001, 7002, 7003):
            await self.old_person(user)
        [row] = await self.rows("SELECT dmbot_person_key_todo(7001, 5) AS ids")
        self.assertEqual(row["ids"], [7002, 7003])
        for batch in (0, 1001):
            [bad] = await self.rows("SELECT dmbot_person_key_todo(0, %s) AS ids", batch)
            self.assertEqual(bad["ids"], [])

    async def test_rows_only_in_sent_emails_are_found_and_keyed(self) -> None:
        # (the shape of deploy 26's finding, #4383: nothing in the other three tables, only email
        # rows of the live kinds, one person with several)
        async with self.db.unscoped() as conn:
            for user, kind in (
                (7101, "recap-ready"), (7101, "recap-failed"), (7102, "sign-in"), (7103, "support"),
            ):  # fmt: skip
                await conn.execute(
                    "INSERT INTO sent_emails (user_id, kind, sent_at) VALUES (%s, %s, 1)",
                    (user, kind),
                )
        person_key.configure(SECRET)
        self.assertEqual((await person_key_backfill.left(self.db))["sent_emails"], 4)
        result = await person_key_backfill.run(self.db)
        self.assertEqual((result.people, result.rows), (3, 4))
        rows = await self.rows("SELECT user_id, person_key FROM sent_emails")
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row["person_key"], person_key.key_for(int(str(row["user_id"]))))
        self.assertEqual((await person_key_backfill.left(self.db))["sent_emails"], 0)

    async def test_the_count_door_says_what_is_left_per_table_and_counts_only(self) -> None:
        for user in (7001, 7002):
            await self.old_person(user)
        left = await person_key_backfill.left(self.db)
        self.assertEqual(
            left,
            {"try_it_used": 2, "trial_extensions": 2, "sent_emails": 4, "email_unsubscribed": 2},
        )
        [shut] = await self.rows("SELECT current_setting('dmbot.owner_sync', true) AS g")
        self.assertIn(shut["g"], (None, ""))  # (the gate is shut again afterwards)

    async def test_every_pass_logs_its_counts_even_when_it_found_nothing(self) -> None:
        person_key.configure(SECRET)
        with self.assertLogs("dmbot.person_key_backfill", level="INFO") as caught:
            await person_key_backfill.run_logged(self.db)
        [line] = caught.output
        self.assertIn("0 people, 0 rows given a key", line)
        self.assertIn("sent_emails 0", line)
        await self.old_person(7001)
        person_key.configure("")
        with self.assertNoLogs("dmbot.person_key_backfill", level="INFO"):
            await person_key_backfill.run_logged(self.db)  # (no secret: start-up already said so)

    async def test_a_pass_that_cannot_key_a_row_says_how_many_are_left(self) -> None:
        await self.old_person(7001)
        async with self.db.unscoped() as conn:  # (one key already taken: the fill skips that row)
            await conn.execute(
                "INSERT INTO sent_emails (user_id, kind, sent_at, person_key)"
                " VALUES (7999, 'offer:1', 1, %s)",
                (person_key.PersonKeys(SECRET).of(7001),),
            )
        person_key.configure(SECRET)
        with self.assertLogs("dmbot.person_key_backfill", level="INFO") as caught:
            await person_key_backfill.run_logged(self.db)
        self.assertIn("sent_emails 1", caught.output[0])  # (counted, not silent)
