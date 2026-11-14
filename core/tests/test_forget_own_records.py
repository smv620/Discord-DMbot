"""The person's own records on account deletion (0494, #4148, as Supervisor ruled on #4411): the
admin's access log keeps its rows with the Discord ID at 0, and the moved marker goes once its
payment update is done or gives up (a row still waiting is marked and goes with its update). The
deleted tables (`plot_chat`, `web_delete_notices`) are in test_account_deletion_tables.py.
Invented ids. Needs Postgres."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from dmbot import entitlements
from dmbot.web import accounts
from tests.pg import DatabaseTest

ALICE, BOB, CARA = 7301, 7302, 7303
NOW = 1_800_000_000
FORGET = import_module("dmbot.migrations.0494_forget_own_records")


class ForgetOwnRecords(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        for user in (ALICE, BOB):
            async with self.db.user(user) as conn:
                await conn.execute(
                    "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                    " VALUES (%s, NULL, 0, 0)",
                    (user,),
                )
        async with self.db.grant_writer() as conn:
            for user in (ALICE, BOB):
                await conn.execute(
                    "INSERT INTO access_log (admin_email, action, discord_user_id, at)"
                    " VALUES ('admin@example.com', 'grant', %s, 1)",
                    (user,),
                )

    async def move(self, old: int, new: int, **cells: Any) -> None:
        """A row of the moved marker (an account that moved from `old` to `new`)."""
        values = {
            "dmbot_id": f"d{old}", "moved_at": 1, "customer_id": "cus_x",
            "subscription_id": "sub_x", "stripe_done_at": None, "stripe_attempts": 0, **cells,
        }  # fmt: skip
        async with self.db.unscoped() as conn:
            await conn.execute(
                "INSERT INTO account_moves (old_user_id, new_user_id, dmbot_id, moved_at,"
                " customer_id, subscription_id, stripe_done_at, stripe_attempts)"
                " VALUES (%(o)s, %(n)s, %(dmbot_id)s, %(moved_at)s, %(customer_id)s,"
                " %(subscription_id)s, %(stripe_done_at)s, %(stripe_attempts)s)",
                {"o": old, "n": new, **values},
            )

    async def moves(self) -> dict[int, bool]:
        """The marker's rows by old ID, and whether each is marked as forgotten."""
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT old_user_id, forgotten_at FROM account_moves")
            return {r["old_user_id"]: r["forgotten_at"] is not None for r in await cur.fetchall()}

    async def log(self) -> list[int]:
        async with self.db.grant_writer() as conn:
            cur = await conn.execute("SELECT discord_user_id AS u FROM access_log ORDER BY id")
            return [r["u"] for r in await cur.fetchall()]

    async def test_the_access_log_keeps_its_rows_with_the_id_at_zero(self) -> None:
        self.assertEqual(await self.log(), [ALICE, BOB])
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(await self.log(), [0, BOB])

    async def test_a_finished_move_goes_with_either_person_but_not_other_peoples(self) -> None:
        await self.move(ALICE, 9001, stripe_done_at=5)  # (Alice was the old account)
        await self.move(9002, ALICE, stripe_done_at=5)  # (and the new one of another)
        await self.move(BOB, CARA, stripe_done_at=5)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(await self.moves(), {BOB: False})

    async def test_a_move_with_nothing_to_update_goes_at_once(self) -> None:
        await self.move(ALICE, 9001, customer_id=None, subscription_id=None)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(await self.moves(), {})

    async def test_a_move_that_gave_up_goes_at_once(self) -> None:
        await self.move(ALICE, 9001, stripe_attempts=FORGET.GIVE_UP)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(await self.moves(), {})

    async def test_a_move_still_to_update_is_marked_and_goes_when_it_is_done(self) -> None:
        await self.move(ALICE, 9001)
        await self.move(BOB, CARA)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(await self.moves(), {ALICE: True, BOB: False})
        async with self.db.unscoped() as conn:
            await conn.execute("SELECT dmbot_move_synced(%s)", (BOB,))
            await conn.execute("SELECT dmbot_move_synced(%s)", (ALICE,))
        self.assertEqual(await self.moves(), {BOB: False})  # (Bob's stays: he is still here)

    async def test_a_forgotten_move_goes_when_its_tries_run_out_not_before(self) -> None:
        await self.move(ALICE, 9001)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        async with self.db.unscoped() as conn:
            for _ in range(FORGET.GIVE_UP - 1):
                await conn.execute("SELECT dmbot_move_failed(%s)", (ALICE,))
        self.assertEqual(await self.moves(), {ALICE: True})
        async with self.db.unscoped() as conn:
            await conn.execute("SELECT dmbot_move_failed(%s)", (ALICE,))
        self.assertEqual(await self.moves(), {})

    async def test_a_move_of_a_person_still_here_is_untouched_by_a_sync(self) -> None:
        await self.move(BOB, CARA)
        async with self.db.unscoped() as conn:
            for _ in range(FORGET.GIVE_UP + 1):
                await conn.execute("SELECT dmbot_move_failed(%s)", (BOB,))
            await conn.execute("SELECT dmbot_move_synced(%s)", (BOB,))
        self.assertEqual(await self.moves(), {BOB: False})
