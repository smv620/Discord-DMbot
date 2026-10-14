"""Moving an account to a new Discord account (#1230): what moves, what doesn't, and every
refusal. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot import account_move
from dmbot.account_id import to_dmbot_id
from dmbot.account_move import AccountMoves, MoveError
from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.memory.backup import MemorySection
from dmbot.memory.store import MemoryStore
from dmbot.web import entitlements_writer
from tests.pg import DatabaseTest
from tests.test_web_accounts_db import INSERT_PLAN, PLAN_ROW

OLD, NEW, THIRD = 111_111_111_111_111_111, 222_222_222_222_222_222, 333_333_333_333_333_333
GUILD_A, GUILD_B = 1001, 2002
NOW = 1_800_000_000


class Moves(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = NOW
        self.moves = AccountMoves(self.db, clock=lambda: self.now)
        self.campaigns = CampaignStore(self.db, clock=lambda: self.now)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db, clock=lambda: self.now)
        for uid in (OLD, NEW, THIRD):
            await self.add_user(uid)
        await self.give_plan(OLD)
        self.a = (await self.campaigns.create(GUILD_A, "Frostmaiden", OLD)).id
        self.b = (await self.campaigns.create(GUILD_B, "Sunken City", OLD)).id
        self.other = (await self.campaigns.create(GUILD_A, "Not Theirs", THIRD)).id
        await self.campaigns.add_dm(GUILD_A, self.a, THIRD)  # a second DM of the first campaign

    async def add_user(self, user_id: int) -> None:
        async with self.db.user(user_id) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, %s, 5, 6)",
                (user_id, f"u{user_id}@example.com"),
            )

    async def give_plan(self, user_id: int, **values: object) -> None:
        row = {**PLAN_ROW, "user_id": user_id, "provider": "stripe", **values}
        async with self.db.plan_writer(user_id) as conn:
            await conn.execute(INSERT_PLAN, row)
            await conn.execute(
                "UPDATE entitlements SET provider_customer_id = 'cus_old1',"
                " provider_subscription_id = 'sub_old1' WHERE user_id = %s",
                (user_id,),
            )

    async def scalar(self, sql_text: str, *args: Any, person: int | None = None) -> Any:
        """One value. `person`: read as that account (its own rows are all that can be seen
        of the account tables); otherwise no person is set."""
        scope = self.db.user(person) if person is not None else self.db.unscoped()
        async with scope as conn:
            cur = await conn.execute(sql_text, args)
            row = await cur.fetchone()
        return None if row is None else next(iter(row.values()))

    async def add_hours(self, user_id: int, minutes: int) -> None:
        async with self.db.meter(GUILD_A, user_id) as conn:
            await conn.execute(
                "INSERT INTO owner_hours (owner_user_id, month_start, minutes) VALUES (%s, 0, %s)",
                (user_id, minutes),
            )

    async def code(self) -> str:
        return await self.moves.make_code(OLD)

    async def test_everything_listed_moves_and_nothing_else_does(self) -> None:
        # Hours, a player's entry, a DM place, a session record and a consent record.
        await self.memory.add_entity(
            GUILD_A, self.a, name="Testa", type="player_character", source="dm", played_by=OLD
        )
        await ConsentStore(self.db).grant(GUILD_A, OLD)
        await self.add_hours(OLD, 77)
        code = await self.code()
        preview = await self.moves.preview(OLD)
        self.assertEqual(
            (preview.plan, sorted(preview.campaigns)), ("table", ["Frostmaiden", "Sunken City"])
        )
        await self.moves.move(OLD, NEW, code)

        # The plan, with the Stripe link, and the hours.
        async with self.db.user(NEW) as conn:
            cur = await conn.execute(
                "SELECT plan, provider_customer_id, provider_subscription_id FROM entitlements"
            )
            plan = await cur.fetchone()
        assert plan is not None
        self.assertEqual(
            (plan["plan"], plan["provider_customer_id"], plan["provider_subscription_id"]),
            ("table", "cus_old1", "sub_old1"),
        )
        self.assertEqual(
            await self.scalar(
                "SELECT count(*) FROM entitlements WHERE user_id = %s", OLD, person=OLD
            ),
            0,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT minutes FROM owner_hours WHERE owner_user_id = %s", NEW, person=NEW
            ),
            77,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT count(*) FROM owner_hours WHERE owner_user_id = %s", OLD, person=OLD
            ),
            0,
        )
        # The DMbot ID stays; the old account row is gone.
        self.assertEqual(
            await self.scalar("SELECT dmbot_id FROM web_users WHERE user_id = %s", NEW, person=NEW),
            to_dmbot_id(OLD),
        )
        self.assertEqual(
            await self.scalar("SELECT count(*) FROM web_users WHERE user_id = %s", OLD, person=OLD),
            0,
        )
        # Both owned campaigns, in both servers, and the DM place; not the other campaign.
        a = await self.campaigns.get(GUILD_A, self.a)
        b = await self.campaigns.get(GUILD_B, self.b)
        other = await self.campaigns.get(GUILD_A, self.other)
        assert a and b and other
        self.assertEqual((a.owner_user_id, b.owner_user_id, other.owner_user_id), (NEW, NEW, THIRD))
        self.assertIn(NEW, a.dm_user_ids)
        self.assertIn(THIRD, a.dm_user_ids)  # the other DM stays
        self.assertNotIn(OLD, a.dm_user_ids)
        self.assertEqual(
            await self.scalar(
                "SELECT count(*) FROM owner_campaigns WHERE owner_user_id = %s",
                NEW,
                person=NEW,
            ),
            2,
        )
        # The player's entry now points at the new account.
        players = [e.played_by for e in await self.memory.entities(GUILD_A, self.a)]
        self.assertIn(NEW, players)
        self.assertNotIn(OLD, players)
        # Consent does not move; the marker and the one-trial rule are left.
        self.assertIsNotNone(await ConsentStore(self.db).granted_at(GUILD_A, OLD))
        self.assertIsNone(await ConsentStore(self.db).granted_at(GUILD_A, NEW))
        self.assertEqual(
            await self.scalar("SELECT new_user_id FROM account_moves WHERE old_user_id = %s", OLD),
            NEW,
        )
        for who in (OLD, NEW):  # each sees only its own row: both have used their one trial
            self.assertEqual(
                await self.scalar(
                    "SELECT count(*) FROM try_it_used WHERE user_id = %s", who, person=who
                ),
                1,
            )
        self.assertEqual(await self.moves.moved_to(OLD), NEW)
        notice = await self.moves.take_moved_notice(OLD)
        self.assertIn("moved to another Discord account on", notice or "")
        self.assertIsNone(await self.moves.take_moved_notice(OLD))  # once

    async def test_an_account_that_never_paid_cannot_ask(self) -> None:
        with self.assertRaises(MoveError) as err:
            await self.moves.make_code(THIRD)
        self.assertEqual(str(err.exception), account_move.NOT_PAID)
        await entitlements_writer.start_try_it(self.db, THIRD, now=NOW)
        with self.assertRaises(MoveError):
            await self.moves.make_code(THIRD)  # Try It is not a paid plan

    async def test_a_code_works_once_and_not_after_its_half_hour(self) -> None:
        code = await self.code()
        self.now += account_move.CODE_LIFETIME_S + 1
        with self.assertRaises(MoveError):
            await self.moves.move(OLD, NEW, code)
        self.now = NOW
        fresh = await self.code()  # a new code replaces the old one
        with self.assertRaises(MoveError):
            await self.moves.move(OLD, NEW, code)
        await self.moves.move(OLD, NEW, fresh)
        with self.assertRaises(MoveError):
            await self.moves.move(OLD, NEW, fresh)  # used

    async def test_the_code_is_stored_hashed_and_forgiving_to_type(self) -> None:
        code = await self.code()
        stored = await self.scalar("SELECT code_hash FROM move_codes WHERE user_id = %s", OLD)
        self.assertNotIn(code, stored)
        self.assertEqual(
            await self.moves.find(NEW, f" {code[:4].lower()}-{code[4:].lower()} "), OLD
        )

    async def test_wrong_codes_are_limited_per_person(self) -> None:
        await self.code()
        for _ in range(account_move.GUESSES):
            with self.assertRaises(MoveError):
                await self.moves.find(NEW, "AAAAAAAA")
        with self.assertRaises(MoveError) as err:
            await self.moves.find(NEW, "AAAAAAAA")
        self.assertEqual(str(err.exception), account_move.TOO_MANY)

    async def test_codes_asked_for_are_limited(self) -> None:
        for _ in range(account_move.ASKS):
            await self.code()
        with self.assertRaises(MoveError) as err:
            await self.code()
        self.assertEqual(str(err.exception), account_move.TOO_MANY_ASKS)

    async def test_a_second_move_inside_30_days_is_refused(self) -> None:
        await self.moves.move(OLD, NEW, await self.code())
        # The new account asks to move on at once: refused for 30 days.
        with self.assertRaises(MoveError) as err:
            await self.moves.make_code(NEW)
        self.assertEqual(str(err.exception), account_move.TOO_SOON)
        self.now += account_move.MOVE_GAP_S + 1
        await self.moves.make_code(NEW)  # allowed again

    async def test_try_it_is_refused_on_the_old_id(self) -> None:
        await self.moves.move(OLD, NEW, await self.code())
        started = await entitlements_writer.start_try_it(self.db, OLD, now=NOW + 5)
        self.assertEqual((started.started, started.reason), (False, "used"))

    async def test_a_new_account_with_a_paid_plan_or_a_moved_one_is_refused(self) -> None:
        await self.give_plan(THIRD)
        code = await self.code()
        with self.assertRaises(MoveError) as err:
            await self.moves.move(OLD, THIRD, code)
        self.assertEqual(str(err.exception), account_move.NEW_HAS_PLAN)
        self.assertEqual(
            await self.scalar(
                "SELECT count(*) FROM entitlements WHERE user_id = %s", OLD, person=OLD
            ),
            1,
        )
        with self.assertRaises(MoveError):
            await self.moves.move(OLD, OLD, await self.code())  # the same account

    async def test_a_running_session_stops_the_move(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO active_sessions (guild_id, campaign_id, voice_channel_id,"
                " screen_channel_id, started_by, started_at) VALUES (%s, %s, 1, 2, %s, %s)",
                (GUILD_A, self.a, OLD, NOW),
            )
        with self.assertRaises(MoveError) as err:
            await self.moves.move(OLD, NEW, await self.code())
        self.assertEqual(str(err.exception), account_move.LISTENING)
        a = await self.campaigns.get(GUILD_A, self.a)
        assert a
        self.assertEqual(a.owner_user_id, OLD)  # nothing moved

    async def test_the_new_accounts_own_try_it_is_replaced_and_its_hours_add_up(self) -> None:
        await entitlements_writer.start_try_it(self.db, NEW, now=NOW)
        await self.add_hours(OLD, 10)
        await self.add_hours(NEW, 5)
        await self.moves.move(OLD, NEW, await self.code())
        async with self.db.user(NEW) as conn:
            cur = await conn.execute("SELECT plan FROM entitlements")
            self.assertEqual([r["plan"] for r in await cur.fetchall()], ["table"])
        self.assertEqual(
            await self.scalar(
                "SELECT minutes FROM owner_hours WHERE owner_user_id = %s", NEW, person=NEW
            ),
            15,
        )
