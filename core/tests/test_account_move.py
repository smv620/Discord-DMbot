"""Moving an account to a new Discord account (#1230): what moves, what doesn't, and every
refusal. Needs Postgres."""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection, errors
from psycopg.rows import dict_row

from dmbot import account_move
from dmbot.account_id import to_dmbot_id
from dmbot.account_move import AccountMoves, MoveError
from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.db import Database
from dmbot.memory.backup import MemorySection
from dmbot.memory.store import MemoryStore
from dmbot.web import accounts, entitlements_writer, move_sync, sessions
from dmbot.web.discord import DiscordUser
from dmbot.web.moving import EmailedMoveCodes
from dmbot.web.payments import FakeProvider, PaymentError, PaymentEvent
from tests.pg import REQUIRE_DB, SUPERUSER_URL, DatabaseTest
from tests.test_web_accounts_db import INSERT_PLAN, PLAN_ROW
from tests.test_web_role import ensure_web_role, web_url

OLD, NEW, THIRD = 111_111_111_111_111_111, 222_222_222_222_222_222, 333_333_333_333_333_333
THIRD_NEW = 444_444_444_444_444_444
FOURTH = 555_555_555_555_555_555
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
        for uid in (OLD, NEW, THIRD, THIRD_NEW):
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
        row = {**PLAN_ROW, "user_id": user_id, "provider": "stripe", "campaign_cap": 5, **values}
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

    async def test_a_payment_for_the_old_id_lands_on_the_new_account(self) -> None:
        await self.moves.move(OLD, NEW, await self.code())
        event = PaymentEvent(
            provider="stripe", event_id="evt_1", kind="subscription_renewed", user_id=OLD,
            occurred_at=NOW + 50, plan="table", period_start=NOW, period_end=NOW + 30 * 86400,
            customer_id="cus_old1", subscription_id="sub_old1",
        )  # fmt: skip
        self.assertEqual(
            await entitlements_writer.apply_event(self.db, event, now=NOW + 60), "applied"
        )
        async with self.db.user(NEW) as conn:
            cur = await conn.execute("SELECT period_end FROM entitlements")
            rows = await cur.fetchall()
        self.assertEqual([r["period_end"] for r in rows], [NOW + 30 * 86400])
        self.assertEqual(
            await self.scalar(
                "SELECT count(*) FROM entitlements WHERE user_id = %s", OLD, person=OLD
            ),
            0,
        )

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

    # ---- one limit for the bot and the website (#1276) ---------------------------------

    class _Mail:
        """A mailer that sends nothing and says it did (the website's side needs one)."""

        def __init__(self, result: str = "sent") -> None:
            self.result = result

        async def send(self, **_: Any) -> str:
            return self.result

    async def website(self, result: str = "sent") -> EmailedMoveCodes:
        async with self.db.user(OLD) as conn:
            await conn.execute(
                "UPDATE web_users SET payment_email = 'pays@example.org', payment_email_at = 1"
                " WHERE user_id = %s",
                (OLD,),
            )
        return EmailedMoveCodes(self.db, self._Mail(result), clock=lambda: float(self.now))  # type: ignore[arg-type]

    async def test_the_fourth_ask_in_the_hour_is_refused_whichever_path_makes_it(self) -> None:
        web = await self.website()
        await self.moves.make_code(OLD)
        await self.moves.make_code(OLD)
        self.assertEqual(await web.send_code(OLD, "c" * 64), "sent")  # the third, on the website
        with self.assertRaises(MoveError) as err:
            await self.moves.make_code(OLD)
        self.assertEqual(str(err.exception), account_move.TOO_MANY_ASKS)
        self.assertEqual(await web.send_code(OLD, "c" * 64), "slow_down")

    async def test_the_count_survives_a_restart(self) -> None:
        for _ in range(account_move.ASKS):
            await self.code()
        restarted = AccountMoves(self.db, clock=lambda: self.now)
        with self.assertRaises(MoveError) as err:
            await restarted.make_code(OLD)
        self.assertEqual(str(err.exception), account_move.TOO_MANY_ASKS)
        self.now += account_move.ASK_WINDOW_S + 1  # an hour later it is allowed again
        await restarted.make_code(OLD)

    async def test_the_bots_refusals_stay_in_plain_words(self) -> None:
        for user, words in ((THIRD, account_move.NOT_PAID),):
            with self.assertRaises(MoveError) as err:
                await self.moves.make_code(user)
            self.assertEqual(str(err.exception), words)
        with self.assertRaises(MoveError) as err:
            await self.moves.make_code(999)  # no account at all
        self.assertEqual(str(err.exception), account_move.NO_ACCOUNT)

    async def test_giving_back_a_code_removes_only_that_code_and_its_ask(self) -> None:
        web = await self.website()
        mine = await self.moves.make_code(OLD)
        self.now += 5
        self.assertEqual(await web.send_code(OLD, "c" * 64), "sent")  # newer, and mailed
        await self.moves.cancel_code(OLD, mine)  # the bot's email failed
        stored = await self.scalar("SELECT code_hash FROM move_codes WHERE user_id = %s", OLD)
        self.assertNotEqual(stored, account_move.hash_code(mine))  # the website's code stays
        self.assertIsNotNone(stored)
        self.assertEqual(
            await self.scalar("SELECT count(*) FROM move_code_asks WHERE user_id = %s", OLD), 1
        )

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

    async def test_an_account_with_a_lapsed_plan_or_that_was_moved_to_is_its_own_history(
        self,
    ) -> None:
        await self.give_plan(THIRD, status="lapsed", lapsed_at=NOW)
        with self.assertRaises(MoveError) as err:
            await self.moves.move(OLD, THIRD, await self.code())
        self.assertEqual(str(err.exception), account_move.TARGET_HISTORY)
        # An account that an earlier move went to (and left again) is its own history too.
        await self.moves.move(OLD, NEW, await self.code())
        self.now = NOW + 31 * 86400
        await self.moves.move(NEW, THIRD_NEW, await self.moves.make_code(NEW))
        await self.add_user(FOURTH)
        await self.give_plan(FOURTH)
        with self.assertRaises(MoveError) as err:
            await self.moves.move(FOURTH, NEW, await self.moves.make_code(FOURTH))
        self.assertEqual(str(err.exception), account_move.TARGET_HISTORY)

    async def test_moving_back_works_after_30_days(self) -> None:
        await self.moves.move(OLD, NEW, await self.code())
        with self.assertRaises(MoveError):  # too soon
            await self.moves.move(NEW, OLD, await self.moves.make_code(NEW))
        self.now = NOW + 31 * 86400
        await self.moves.move(NEW, OLD, await self.moves.make_code(NEW))
        self.assertEqual(await self.scalar("SELECT plan FROM entitlements", person=OLD), "table")
        self.assertEqual(await self.moves.moved_to(OLD), None)  # it is the account again
        self.assertEqual(await self.moves.moved_to(NEW), OLD)
        a = await self.campaigns.get(GUILD_A, self.a)
        assert a
        self.assertEqual(a.owner_user_id, OLD)

    async def test_two_moves_with_one_code_make_exactly_one(self) -> None:
        import asyncio

        code = await self.code()
        results = await asyncio.gather(
            self.moves.move(OLD, NEW, code),
            self.moves.move(OLD, THIRD_NEW, code),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, MoveError)]
        self.assertEqual(len(failures), 1)
        winners = [
            uid
            for uid in (NEW, THIRD_NEW)
            if await self.scalar("SELECT count(*) FROM entitlements", person=uid)
        ]
        self.assertEqual(len(winners), 1)  # exactly one account holds the plan
        a = await self.campaigns.get(GUILD_A, self.a)
        assert a
        self.assertEqual(a.owner_user_id, winners[0])  # and its campaigns
        # Whichever lost is told plainly; the code can't be used twice.
        self.assertIn(str(failures[0]), (account_move.BAD_CODE, account_move.NO_ACCOUNT))

    async def test_nothing_else_of_the_old_account_moves(self) -> None:
        # A player entry and a DM place the old account has in campaigns it doesn't own, and
        # its recording agreement, stay where they are.
        await self.memory.add_entity(
            GUILD_A, self.other, name="Hero", type="player_character", source="dm", played_by=OLD
        )
        await self.campaigns.add_dm(GUILD_A, self.other, OLD)
        consent = ConsentStore(self.db)
        await consent.grant(GUILD_A, OLD)

        async def played() -> int:
            async with self.db.guild(GUILD_A) as conn:
                cur = await conn.execute(
                    "SELECT count(*) AS n FROM memory_entities WHERE campaign_id = %s"
                    " AND played_by = %s",
                    (self.other, OLD),
                )
                return int((await cur.fetchone() or {})["n"])

        before = await played()
        self.assertGreaterEqual(before, 1)
        await self.moves.move(OLD, NEW, await self.code())
        self.assertEqual(await played(), before)
        self.assertIn(OLD, (await self.campaigns.get(GUILD_A, self.other)).dm_user_ids)  # type: ignore[union-attr]
        agreed = await consent.consenting(GUILD_A)
        self.assertIn(OLD, agreed)
        self.assertNotIn(NEW, agreed)

    async def test_a_target_that_is_recording_stops_the_move(self) -> None:
        mine = (await self.campaigns.create(GUILD_B, "New Table", NEW)).id
        async with self.db.guild(GUILD_B) as conn:
            await conn.execute(
                "INSERT INTO active_sessions (guild_id, campaign_id, voice_channel_id,"
                " screen_channel_id, started_by, started_at) VALUES (%s, %s, 1, 2, %s, %s)",
                (GUILD_B, mine, NEW, NOW),
            )
        with self.assertRaises(MoveError) as err:
            await self.moves.move(OLD, NEW, await self.code())
        self.assertEqual(str(err.exception), account_move.TARGET_LISTENING)

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

    async def test_the_new_accounts_own_try_it_is_replaced_and_its_minutes_do_not_count(
        self,
    ) -> None:
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
            10,  # only the paid plan's minutes: the Try It minutes don't count toward it
        )

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

    async def test_the_old_id_cannot_make_a_website_account_and_others_can(self) -> None:
        web = await self.web()
        await self.moves.move(OLD, NEW, await self.code())
        with self.assertRaises(sessions.AccountMoved):
            await sessions.sign_in(
                web, DiscordUser(id=OLD, name="Old", email=None), [], now=NOW, days=1
            )
        await sessions.sign_in(
            web, DiscordUser(id=444, name="Fresh", email=None), [], now=NOW, days=1
        )
        # The moved account keeps its DMbot ID on the new Discord ID.
        self.assertEqual(await accounts.dmbot_id(web, NEW), to_dmbot_id(OLD))
        self.assertIsNone(await accounts.dmbot_id(web, OLD))

    async def test_a_link_made_before_the_move_finds_the_account_by_its_dmbot_id(self) -> None:
        web = await self.web()
        self.assertEqual(await accounts.user_of_dmbot_id(web, to_dmbot_id(OLD)), OLD)
        await self.moves.move(OLD, NEW, await self.code())
        self.assertEqual(await accounts.user_of_dmbot_id(web, to_dmbot_id(OLD)), NEW)
        self.assertIsNone(await accounts.user_of_dmbot_id(web, "ZZZZZZ"))

    async def test_the_website_cannot_move_or_peek_at_moves(self) -> None:
        web = await self.web()
        await self.moves.move(OLD, NEW, await self.code())
        for sql_text in (
            "SELECT dmbot_move_account(%s, %s, 0, 'x')",
            "SELECT * FROM dmbot_move_preview(%s)",
        ):
            with self.assertRaises(errors.InsufficientPrivilege):
                async with web.user(OLD) as conn:
                    await conn.execute(sql_text, (OLD, NEW)[: sql_text.count("%s")])
        for table in ("account_moves", "move_codes"):
            with self.assertRaises(errors.InsufficientPrivilege):
                async with web.user(OLD) as conn:
                    await conn.execute(f"SELECT * FROM {table}")

    async def test_a_plan_the_old_id_buys_later_is_not_sent_to_the_new_account(self) -> None:
        await self.moves.move(OLD, NEW, await self.code())
        event = PaymentEvent(
            provider="stripe", event_id="evt_2", kind="subscription_renewed", user_id=OLD,
            occurred_at=NOW + 50, plan="table", period_start=NOW, period_end=NOW + 99,
            customer_id="cus_other", subscription_id="sub_other",
        )  # fmt: skip
        await entitlements_writer.apply_event(self.db, event, now=NOW + 60)
        self.assertNotEqual(
            await self.scalar("SELECT period_end FROM entitlements", person=NEW), NOW + 99
        )

    async def test_no_offer_emails_goes_with_the_account(self) -> None:
        async with self.db.unscoped() as conn:
            await conn.execute(
                "INSERT INTO email_unsubscribed (user_id, at) VALUES (%s, 5)", (OLD,)
            )
        await self.moves.move(OLD, NEW, await self.code())
        self.assertEqual(
            await self.scalar("SELECT count(*) FROM email_unsubscribed WHERE user_id = %s", NEW), 1
        )

    async def test_too_many_campaigns_for_the_plan_stops_the_move(self) -> None:
        async with self.db.plan_writer(OLD) as conn:
            await conn.execute(
                "UPDATE entitlements SET campaign_cap = 2 WHERE user_id = %s", (OLD,)
            )
        await self.campaigns.create(GUILD_A, "Third Own", NEW)
        with self.assertRaises(MoveError) as err:
            await self.moves.move(OLD, NEW, await self.code())
        self.assertEqual(str(err.exception), account_move.TOO_MANY_CAMPAIGNS)

    async def test_open_hand_over_offers_end_with_the_move(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO campaign_handover_offers (guild_id, campaign_id, from_user_id,"
                " to_user_id, created_at, from_name, to_name)"
                " VALUES (%s, %s, %s, %s, %s, 'a', 'b')",
                (GUILD_A, self.a, OLD, THIRD, NOW),
            )
        await self.moves.move(OLD, NEW, await self.code())
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT status FROM campaign_handover_offers")
            self.assertEqual([r["status"] for r in await cur.fetchall()], ["withdrawn"])

    async def test_a_payment_follows_a_chain_of_two_moves(self) -> None:
        await self.moves.move(OLD, NEW, await self.code())
        self.now = NOW + 31 * 86400
        code = await self.moves.make_code(NEW)
        await self.moves.move(NEW, THIRD_NEW, code)
        event = PaymentEvent(
            provider="stripe", event_id="evt_3", kind="subscription_renewed", user_id=OLD,
            occurred_at=self.now + 5, plan="table", period_start=self.now,
            period_end=self.now + 77, customer_id="cus_old1", subscription_id="sub_old1",
        )  # fmt: skip
        await entitlements_writer.apply_event(self.db, event, now=self.now + 10)
        self.assertEqual(
            await self.scalar("SELECT period_end FROM entitlements", person=THIRD_NEW),
            self.now + 77,
        )

    async def test_the_payment_records_are_updated_after_the_move_and_retried_on_failure(
        self,
    ) -> None:
        web = await self.web()
        await self.moves.move(OLD, NEW, await self.code())
        fake = FakeProvider(b"s" * 32, "https://site.example.com")

        class Down(FakeProvider):
            async def retarget(self, **_: object) -> None:
                raise PaymentError("Stripe answered 500")

        down = Down(b"s" * 32, "https://site.example.com")
        self.assertEqual(await move_sync.sync_moves(web, down), 0)  # fails: marked tried
        # It waits (a minute after the first failure) before it is handed out again.
        self.assertEqual(await move_sync.sync_moves(web, fake), 0)
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE account_moves SET stripe_last_try_at = 0")  # long ago
        self.assertEqual(await move_sync.sync_moves(web, fake), 1)
        self.assertEqual(fake.retargeted, [("cus_old1", "sub_old1", NEW)])
        self.assertEqual(await move_sync.sync_moves(web, fake), 0)  # done once

    async def test_twenty_stuck_moves_do_not_crowd_out_a_new_one(self) -> None:
        web = await self.web()
        async with self.db.unscoped() as conn:
            await conn.execute(
                "INSERT INTO account_moves (old_user_id, new_user_id, dmbot_id, moved_at,"
                " customer_id, stripe_attempts, stripe_last_try_at)"
                " SELECT 1000 + g, 2000 + g, 'X' || g, 1, 'cus_' || g, 3,"
                " extract(epoch FROM now())::BIGINT - 10 FROM generate_series(1, 20) g"
            )
        await self.moves.move(OLD, NEW, await self.code())  # the 21st, never tried
        async with web.unscoped() as conn:
            cur = await conn.execute("SELECT old_user_id FROM dmbot_moves_to_sync()")
            handed = [r["old_user_id"] for r in await cur.fetchall()]
        self.assertEqual(handed, [OLD])  # the 20 are waiting out their backoff
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE account_moves SET stripe_last_try_at = 0")
        async with web.unscoped() as conn:
            cur = await conn.execute("SELECT old_user_id FROM dmbot_moves_to_sync()")
            handed = [r["old_user_id"] for r in await cur.fetchall()]
        self.assertEqual(len(handed), 20)  # never more than 20 a pass

    async def test_the_payment_email_goes_with_the_account(self) -> None:
        async with self.db.user(OLD) as conn:
            await conn.execute(
                "UPDATE web_users SET payment_email = 'payer@example.com',"
                " payment_email_at = 77 WHERE user_id = %s",
                (OLD,),
            )
        await self.moves.move(OLD, NEW, await self.code())
        self.assertEqual(await accounts.payment_email(self.db, NEW), "payer@example.com")
        self.assertEqual(
            await self.scalar("SELECT payment_email_at FROM web_users", person=NEW), 77
        )
        self.assertIsNone(await accounts.payment_email(self.db, OLD))

    async def test_a_to_b_to_c_and_back_to_b_works_and_a_stays_moved_away(self) -> None:
        await self.moves.move(OLD, NEW, await self.code())
        self.now = NOW + 31 * 86400
        await self.moves.move(NEW, THIRD_NEW, await self.moves.make_code(NEW))
        self.now = NOW + 62 * 86400
        await self.moves.move(THIRD_NEW, NEW, await self.moves.make_code(THIRD_NEW))
        self.assertEqual(await self.scalar("SELECT plan FROM entitlements", person=NEW), "table")
        self.assertEqual(await accounts.dmbot_id(self.db, NEW), to_dmbot_id(OLD))
        # The first account is still moved away (its DMbot ID is held by the new one).
        self.assertEqual(await self.moves.moved_to(OLD), NEW)
        web = await self.web()
        with self.assertRaises(sessions.AccountMoved):
            await sessions.sign_in(
                web, DiscordUser(id=OLD, name="Old", email=None), [], now=NOW, days=1
            )

    async def test_every_move_leaves_one_history_row_a_b_a_b_gives_three(self) -> None:
        async with self.db.user(OLD) as conn:
            await conn.execute(
                "UPDATE web_users SET payment_email = 'payer@example.com' WHERE user_id = %s",
                (OLD,),
            )
        await self.moves.move(OLD, NEW, await self.code())
        self.now = NOW + 31 * 86400
        await self.moves.move(NEW, OLD, await self.moves.make_code(NEW))
        self.now = NOW + 62 * 86400
        await self.moves.move(OLD, NEW, await self.moves.make_code(OLD))
        async with self.db.user(NEW) as conn:
            cur = await conn.execute(
                "SELECT old_user_id, new_user_id, email_used FROM account_id_history ORDER BY id"
            )
            rows = [
                (r["old_user_id"], r["new_user_id"], r["email_used"]) for r in await cur.fetchall()
            ]
        self.assertEqual(
            rows,
            [(OLD, NEW, "payer@example.com"), (NEW, OLD, "payer@example.com"),
             (OLD, NEW, "payer@example.com")],
        )  # fmt: skip
        # Another account sees none of them, and the history is never changed or deleted.
        async with self.db.user(THIRD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM account_id_history")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        async with self.db.user(NEW) as conn:
            for statement in (
                "UPDATE account_id_history SET email_used = 'x@example.com'",
                "DELETE FROM account_id_history",
            ):
                cur = await conn.execute(statement)
                self.assertEqual(cur.rowcount, 0, statement)

    async def test_the_website_reads_only_its_own_history_and_account_deletion_removes_it(
        self,
    ) -> None:
        web = await self.web()
        await self.moves.move(OLD, NEW, await self.code())
        async with web.user(NEW) as conn:
            cur = await conn.execute("SELECT old_user_id FROM dmbot_previous_ids()")
            self.assertEqual([r["old_user_id"] for r in await cur.fetchall()], [OLD])
            with self.assertRaises(errors.InsufficientPrivilege):
                await conn.execute("SELECT * FROM account_id_history")
        async with web.user(THIRD) as conn:
            cur = await conn.execute("SELECT old_user_id FROM dmbot_previous_ids()")
            self.assertEqual(await cur.fetchall(), [])  # someone else's history is not theirs
        await accounts.delete_person(web, NEW)
        # Physically gone (read as the database's administrator, who bypasses row security).
        async with await AsyncConnection.connect(
            SUPERUSER_URL, autocommit=True, row_factory=dict_row
        ) as admin:
            await admin.execute(f"SET search_path TO {self.schema}")
            cur = await admin.execute("SELECT count(*) AS n FROM account_id_history")
            row = await cur.fetchone()
            self.assertEqual((row or {})["n"], 0)

    async def test_the_export_lists_earlier_discord_accounts(self) -> None:
        from dmbot.web.export import build_export

        web = await self.web()
        async with self.db.user(OLD) as conn:
            await conn.execute(
                "UPDATE web_users SET payment_email = 'payer@example.com' WHERE user_id = %s",
                (OLD,),
            )
        await self.moves.move(OLD, NEW, await self.code())
        self.now = NOW + 31 * 86400
        await self.moves.move(NEW, OLD, await self.moves.make_code(NEW))
        token = await sessions.sign_in(
            web, DiscordUser(id=OLD, name="Old", email=None), [], now=NOW, days=1
        )
        session = await sessions.find(web, token, now=NOW)
        assert session is not None
        data = await build_export(web, session, now=NOW)
        earlier = data["account"]["previousDiscordIds"]
        self.assertEqual([e["discordId"] for e in earlier], [str(OLD), str(NEW)])
        self.assertEqual({e["email"] for e in earlier}, {"payer@example.com"})
