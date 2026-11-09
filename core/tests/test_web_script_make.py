"""Make a session's script from the website (#3899): the offer, one press, the wait. Run as the
website's own database role: only a DM of the campaign can ask, press or read; a second press while
one is going is given the running job's id; a session with a script says so; scripts off and a plan
without story memory say so; the table is closed to the website's role. Real Postgres; invented
names."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from dmbot import entitlements
from dmbot.web import script_make
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_theatre_script import Script


class ScriptMake(Script):
    async def offer(self, who: Any | None = None, **kw: Any) -> dict[str, Any]:
        args: dict[str, Any] = {"theatre_on": True, "now": NOW, "enforce_plans": False}
        args.update(kw)
        return await script_make.offer(self.web, who or self.session, self.cref, 1, **args)

    async def press(self, who: Any | None = None, **kw: Any) -> str:
        args: dict[str, Any] = {"theatre_on": True, "now": NOW, "enforce_plans": False}
        args.update(kw)
        return await script_make.queue(self.web, who or self.session, self.cref, 1, **args)

    async def refused(self, call: Any, *a: Any, **kw: Any) -> str:  # type: ignore[override]
        with self.assertRaises(RecapError) as caught:
            await call(*a, **kw)
        return caught.exception.code

    async def test_a_session_with_no_script_offers_the_button_with_its_cost(self) -> None:
        got = await self.offer()
        self.assertEqual(got["state"], "none")
        self.assertTrue(got["allowed"])
        self.assertIn("AI call", got["cost"])
        self.assertIsNone(got["why"])

    async def test_a_session_with_a_script_says_it_exists_and_cannot_be_pressed(self) -> None:
        await self.make_script()
        self.assertEqual((await self.offer())["state"], "exists")
        self.assertEqual(await self.refused(self.press), "script_already")

    async def test_a_press_is_queued_and_a_second_one_is_given_the_running_job(self) -> None:
        first = await self.press()
        self.assertEqual(await self.press(), first)
        self.assertEqual((await self.offer())["state"], "running")
        self.assertEqual((await self.offer())["running"], first)

    async def test_scripts_off_say_so_and_queue_nothing(self) -> None:
        got = await self.offer(theatre_on=False)
        self.assertEqual((got["allowed"], got["why"], got["cost"]), (False, "off", None))
        self.assertEqual(await self.refused(self.press, theatre_on=False), "script_off")
        self.assertEqual((await self.offer())["state"], "none")  # (nothing was queued)

    async def test_a_plan_without_transcripts_is_refused_before_anything_is_queued(self) -> None:
        # The session list's own plan rule answers first: a Try It campaign never sees sessions.
        self.assertEqual(await self.refused(self.offer, enforce_plans=True), "not_in_plan")
        self.assertEqual(await self.refused(self.press, enforce_plans=True), "not_in_plan")
        self.assertEqual((await self.offer())["state"], "none")
        entitlements.configure_free_users({ALICE.id})  # the owner, on the free list
        self.addCleanup(entitlements.configure_free_users, ())
        self.assertTrue((await self.offer(enforce_plans=True))["allowed"])

    async def test_a_plan_with_transcripts_but_no_story_memory_says_so_and_queues_nothing(
        self,
    ) -> None:
        # The story rule is its own door: patched to deny, the rest of the plan being allowed.
        real = script_make.plan_rules.allowed

        def deny_story(rule: Any, access: Any) -> bool:
            return bool(rule != script_make.plan_rules.RULE_OF["story"] and real(rule, access))

        entitlements.configure_free_users({ALICE.id})
        self.addCleanup(entitlements.configure_free_users, ())
        with patch.object(script_make.plan_rules, "allowed", deny_story):
            got = await self.offer(enforce_plans=True)
            self.assertEqual((got["allowed"], got["why"]), (False, "plan"))
            self.assertEqual(await self.refused(self.press, enforce_plans=True), "script_plan")
        self.assertEqual((await self.offer())["state"], "none")

    async def test_a_run_lost_for_half_an_hour_lets_the_session_be_pressed_again(self) -> None:
        first = await self.press()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE script_makes SET created_at = created_at - 3600 WHERE id = %s", (first,)
            )
        self.assertEqual((await self.offer())["state"], "none")  # (not shown as running)
        again = await self.press()
        self.assertNotEqual(again, first)
        got = await script_make.read(self.web, self.session, self.cref, first)
        self.assertEqual((got["state"], got["error"]), ("failed", "lost"))

    async def test_a_done_run_does_not_block_a_new_press_and_another_dm_reads_the_same_job(
        self,
    ) -> None:
        mine = await self.press()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, %s)",
                (self.one.id, THURSDAY.id, BOB.id),
            )
        bob = await self.signed_in(BOB)
        self.assertEqual(
            (await script_make.read(self.web, bob, self.cref, mine))["state"], "waiting"
        )
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE script_makes SET state = 'done' WHERE id = %s", (mine,))
        self.assertNotEqual(await self.press(), mine)

    async def test_the_wait_is_read_with_its_part_and_a_failure_says_why(self) -> None:
        mine = await self.press()
        got = await script_make.read(self.web, self.session, self.cref, mine)
        self.assertEqual(got["state"], "waiting")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE script_makes SET state = 'working', part = 1, of_parts = 3,"
                " claimed_at = %s WHERE id = %s",
                (NOW * 1000, mine),
            )
        got = await script_make.read(self.web, self.session, self.cref, mine)
        self.assertEqual(got["state"], "working")
        self.assertEqual((got["progress"]["done"], got["progress"]["total"]), (1, 3))
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE script_makes SET state = 'failed', error = 'stopped' WHERE id = %s", (mine,)
            )
        got = await script_make.read(self.web, self.session, self.cref, mine)
        self.assertEqual((got["state"], got["error"]), ("failed", "stopped"))
        self.assertEqual(
            (await self.offer())["state"], "none"
        )  # (a stopped run can be tried again)

    async def test_a_player_is_refused_and_another_campaigns_session_is_not_found(self) -> None:
        bob = await self.signed_in(BOB)  # Bob DMs "Two" and only plays in "One"
        self.assertEqual(await self.refused(self.offer, bob), "not_allowed")
        self.assertEqual(await self.refused(self.press, bob), "not_allowed")
        mine = await self.press()
        self.assertEqual(
            await self.refused(script_make.read, self.web, bob, self.cref, mine), "not_found"
        )

    async def test_the_door_answers_nothing_by_sql_to_a_non_dm_and_for_a_foreign_session(
        self,
    ) -> None:
        bob = await self.signed_in(BOB)
        for door in ("state", "add"):  # (a player gets nothing from either)
            async with self.web.user(bob.user_id, session=bob.id_hash) as conn:
                await conn.execute(
                    "SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),)
                )
                cur = await conn.execute(
                    f"SELECT dmbot_web_script_make_{door}(%s, %s, %s) AS r",
                    (THURSDAY.id, self.one.id, self.sid),
                )
                row = await cur.fetchone()
                assert row is not None
                self.assertIsNone(row["r"], door)
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_script_make_add(%s, %s, %s) AS r",
                (THURSDAY.id, self.two.id, self.sid),  # (Alice is no DM of "Two")
            )
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["r"])

    async def test_the_table_is_not_open_to_the_website_role(self) -> None:
        await self.press()
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(Exception):  # noqa: B017 (permission denied, whichever error)
                await conn.execute("SELECT * FROM script_makes")

    async def test_ten_presses_a_minute_for_a_campaign_is_the_limit(self) -> None:
        # (each needs its own session: one run at a time for a session)
        for i in range(10):
            sid = await self.transcripts.open_session(THURSDAY.id, self.one.id, START_AT + i)
            async with self.db.guild(THURSDAY.id) as conn:
                await conn.execute(
                    "INSERT INTO script_makes (guild_id, campaign_id, id, session_id, user_id,"
                    " state, created_at) VALUES (%s, %s, %s, %s, %s, 'done',"
                    " floor(extract(epoch FROM clock_timestamp()))::BIGINT)",
                    (THURSDAY.id, self.one.id, f"{i:032x}", sid, self.session.user_id),
                )
        self.assertEqual(await self.refused(self.press), "too_soon")


START_AT = 1_700_100_000
