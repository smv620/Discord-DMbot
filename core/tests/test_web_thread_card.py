"""A kept story thread's card on the website (#4476, quests slice 2, core): a database door that
answers a DM of the campaign and nobody else, with the DM's view of the thread's parts (the rule
`thread_parts.parts_for(the_dm())` keeps), the people they name by name, what is owed and how many
objectives are done worked out with the bot's own rules, and the thread's branches. Invented names.
Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.models import DM
from dmbot.memory.thread_read import STATE
from dmbot.web import relationships, thread_card
from dmbot.web.recaps import RecapError, _scoped
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, GORRAK, THURSDAY
from tests.test_web_name_actions import Acting


class Card(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        thread = await self.memory.keep_thread(
            self.g, self.one.id, "The Lost Heir", "quest", STATE["quest"], aim="", parts=[],
            source=DM,
        )  # fmt: skip
        self.thread = thread.value.id

    async def door(self, who: Any = None, thread: str = "") -> Any:
        session = who or self.session
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_thread_card(%s, %s, %s) AS r",
                (self.g, self.one.id, thread or self.thread),
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def add_part(self, kind: str, words: str, **more: Any) -> Any:
        return await self.memory.add_thread_part(
            self.g, self.one.id, self.thread, kind, words, source=DM, **more
        )


class TheDoor(Card):
    async def test_the_dm_gets_the_thread_with_its_name_and_nothing_else_yet(self) -> None:
        got = await self.door()
        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["thread"]["name"], "The Lost Heir")
        self.assertEqual((got["parts"], got["bigger"], got["branches"]), ([], None, []))

    async def test_a_player_or_a_stranger_gets_nothing(self) -> None:
        self.assertIsNone(await self.door(await self.signed(BOB, THURSDAY)))  # (a player here)
        self.assertIsNone(await self.door(await self.signed(BOB, GORRAK)))  # (not in this server)

    async def test_a_thread_that_is_not_a_kept_entry_is_gone(self) -> None:
        self.assertEqual((await self.door(thread="a" * 32))["status"], "gone")

    async def test_the_web_module_says_not_allowed_and_not_found(self) -> None:
        with self.assertRaises(RecapError) as no:
            await thread_card.read(
                self.web, await self.signed(BOB, THURSDAY), self.cref, self.thread
            )
        self.assertEqual(no.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as gone:
            await thread_card.read(self.web, self.session, self.cref, "b" * 32)
        self.assertEqual(gone.exception.code, "not_found")
        with self.assertRaises(RecapError):
            await thread_card.read(self.web, self.session, self.cref, "not an id")

    async def test_a_part_shows_its_words_state_and_the_person_it_is_for(self) -> None:
        await self.add_part("objective", "Find the heir", about_id=self.mira)
        await self.add_part("reward", "A seat at the table", who_id=self.mira)
        card = await thread_card.read(self.web, self.session, self.cref, self.thread)
        step, reward = sorted(card["parts"], key=lambda p: p["kind"])
        self.assertEqual(
            (step["kind"], step["words"], step["state"]), ("objective", "Find the heir", "to_do")
        )
        self.assertEqual(step["about"]["name"], "Mira Voss")
        self.assertEqual(reward["who"]["name"], "Mira Voss")
        self.assertNotIn("dueMinute", step)  # (a game date is never a raw minute on the page)
        self.assertEqual((card["done"], card["of"]), (0, 1))

    async def test_what_is_owed_is_worked_out_with_the_bots_own_rules(self) -> None:
        promise = await self.add_part("reward", "100 gold")
        await self.add_part("reward_given", "40 gold", under_id=promise.value.id)
        card = await thread_card.read(self.web, self.session, self.cref, self.thread)
        owed = [p["owed"] for p in card["parts"] if p["kind"] == "reward"]
        self.assertEqual(owed, [60.0])


class TheSheet(Card):
    """The Story Graph's quest sheet (#4540): description, state, who is in it, what waits."""

    async def test_the_sheet_has_the_description_state_and_people(
        self,
    ) -> None:
        made = await self.memory.keep_thread(
            self.g, self.one.id, "Keegan's job", "quest", STATE["quest"],
            aim="Bring the heir home.", parts=[], source=DM,
        )  # fmt: skip
        quest = made.value.id
        # (the target first, so the order the page shows is the role order, not the order written)
        await self.memory.involve(self.g, self.one.id, quest, self.keep, "target", source=DM)
        await self.memory.involve(self.g, self.one.id, quest, self.mira, "quest giver", source=DM)
        card = await thread_card.read(self.web, self.session, self.cref, quest)
        self.assertEqual(
            (card["thread"]["description"], card["thread"]["kind"], card["thread"]["stateWords"]),
            ("Bring the heir home.", "Quest", "Happening now"),
        )
        self.assertEqual(
            [p["role"] for p in card["people"]], ["quest giver", "target"]
        )  # giver first
        self.assertEqual(card["waiting"], 0)

    async def test_a_thread_with_no_description_gives_an_empty_one_and_no_people(self) -> None:
        card = await thread_card.read(self.web, self.session, self.cref, self.thread)
        self.assertEqual((card["thread"]["description"], card["people"]), ("", []))


class TheChanges(Card):
    """The website's changes to a thread's parts and branches (#4476, migration 0514): each queued
    on the door, applied by the bot through the store's own calls, each one change and one Undo."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        self.clock_now: int | None = 10_000  # (the game clock's minute; None is no clock)

    async def game_now(self, _campaign: str) -> int | None:
        return self.clock_now

    async def queue_part(self, action: str, text: str) -> Any:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, action, "", "", text, False),
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def act(self, action: str, text: str) -> wa.Outcome:
        got = await self.queue_part(action, text)
        self.assertEqual(got["status"], "ok", got)
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(
            self.memory, self.g, {ALICE.id}, claimed, actions=self.actions, game_now=self.game_now
        )
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        return outcome

    async def parts(self) -> list[dict[str, Any]]:
        card = await thread_card.read(self.web, self.session, self.cref, self.thread)
        parts: list[dict[str, Any]] = card["parts"]
        return parts

    async def test_adding_a_step_shows_on_the_card_and_undo_takes_it_off(self) -> None:
        made = await self.act("part-add", f"{self.thread} objective - - - - - - Find the heir")
        self.assertEqual(made.error, "")
        (part,) = await self.parts()
        self.assertEqual(
            (part["kind"], part["words"], part["state"]), ("objective", "Find the heir", "to_do")
        )
        (batch,) = made.batches
        self.assertEqual((await self.act("part-undo", str(batch))).error, "")
        self.assertEqual(await self.parts(), [])
        self.assertEqual((await self.act("part-undo", str(batch))).error, "")  # (a retry is done)

    async def test_a_time_limits_date_is_set_changed_cleared_and_each_undone(self) -> None:
        await self.act("part-add", f"{self.thread} time_limit - - hard - - - Before the new moon")
        (limit,) = await self.parts()
        self.assertFalse(limit["dated"])
        set_to = await self.act("part-date", f"{limit['id']} 3")
        self.assertEqual(set_to.error, "")
        (limit,) = await self.parts()
        self.assertTrue(limit["dated"])
        (batch,) = set_to.batches
        self.assertEqual((await self.act("part-undo", str(batch))).error, "")
        self.assertFalse((await self.parts())[0]["dated"])
        # (a date moved is a change in the story: the part has a new row; read its id again)
        await self.act("part-date", f"{limit['id']} 1")
        (limit,) = await self.parts()
        self.assertEqual((await self.act("part-date", f"{limit['id']} 7")).error, "")
        (limit,) = await self.parts()
        cleared = await self.act("part-date", f"{limit['id']} -")
        self.assertEqual(cleared.error, "")
        self.assertFalse((await self.parts())[0]["dated"])

    async def put_clock(self, minute: int) -> None:
        await self.sql(
            "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest, updated_at)"
            " VALUES (%s, %s, %s, 0, 1)",
            self.g, self.one.id, minute,
        )  # fmt: skip

    async def test_a_dated_limit_reads_as_the_cards_own_countdown_words(self) -> None:
        await self.put_clock(10_000)
        await self.act("part-add", f"{self.thread} time_limit - - hard - - - Before the new moon")
        (limit,) = await self.parts()
        self.assertEqual(limit["dateWords"], "")  # (no date yet)
        await self.act("part-date", f"{limit['id']} 3")
        (limit,) = await self.parts()
        self.assertEqual(limit["dateWords"], "3 days left (due Day 10)")
        self.assertNotIn("10000", str(limit))  # (the minute never leaves)
        await self.sql(
            "UPDATE game_clocks SET minute = %s WHERE guild_id = %s AND campaign_id = %s",
            20_000, self.g, self.one.id,
        )  # fmt: skip
        (limit,) = await self.parts()
        self.assertEqual(limit["dateWords"], "time's up (was due Day 10)")

    async def test_a_split_party_counts_on_the_group_furthest_ahead(self) -> None:
        await self.put_clock(10_000)
        await self.act("part-add", f"{self.thread} time_limit - - - - - - By the thaw")
        (limit,) = await self.parts()
        await self.act("part-date", f"{limit['id']} 3")
        await self.sql(
            "INSERT INTO game_clock_groups (guild_id, campaign_id, position, name, minute,"
            " last_long_rest) VALUES (%s, %s, 1, 'North', %s, 0)",
            self.g, self.one.id, 14_320 - 60,
        )  # fmt: skip
        (limit,) = await self.parts()
        self.assertEqual(limit["dateWords"], "1 hour left (due Day 10)")

    async def test_a_dated_limit_with_the_clock_gone_says_it_cannot_count(self) -> None:
        await self.put_clock(10_000)
        await self.act("part-add", f"{self.thread} time_limit - - - - - - By the thaw")
        (limit,) = await self.parts()
        await self.act("part-date", f"{limit['id']} 3")
        await self.sql("DELETE FROM game_clocks WHERE guild_id = %s", self.g)
        (limit,) = await self.parts()
        self.assertEqual(limit["dateWords"], thread_card.NO_CLOCK)

    async def test_a_harptos_calendar_words_the_date_and_a_bad_one_falls_back_to_day_n(
        self,
    ) -> None:
        await self.put_clock(10_000)
        await self.sql(
            "UPDATE game_clocks SET calendar_kind = 'harptos', calendar_year = 1492,"
            " calendar_ordinal = 100 WHERE guild_id = %s",
            self.g,
        )  # fmt: skip
        await self.act("part-add", f"{self.thread} time_limit - - - - - - By the thaw")
        (limit,) = await self.parts()
        await self.act("part-date", f"{limit['id']} 3")
        (limit,) = await self.parts()
        self.assertRegex(limit["dateWords"], r"^3 days left \(due .*1492 DR\)$")
        # (a leap day in a common year is a date the calendar has not got: Day N, as Discord says)
        await self.sql(
            "UPDATE game_clocks SET calendar_year = 1493, calendar_ordinal = 365"
            " WHERE guild_id = %s",
            self.g,
        )  # fmt: skip
        (limit,) = await self.parts()
        self.assertEqual(limit["dateWords"], "3 days left (due Day 10)")

    async def test_a_calendar_of_its_own_words_the_date_and_a_damaged_one_is_day_n(self) -> None:
        from psycopg.types.json import Jsonb

        from dmbot.timebot import calendar as cal
        from dmbot.timebot import layout as lay

        await self.put_clock(10_000)
        seasons = lay.four_seasons(90)
        custom = cal.Calendar(
            cal.CUSTOM, layout=seasons, layout_start=seasons.start_on(1201, "Summer", 2)
        )
        await self.sql(
            "UPDATE game_clocks SET calendar_kind = 'custom', calendar_year = 1201,"
            " calendar_ordinal = 91, calendar_def = %s WHERE guild_id = %s",
            Jsonb(cal.def_of(custom)), self.g,
        )  # fmt: skip
        await self.act("part-add", f"{self.thread} time_limit - - - - - - By the thaw")
        (limit,) = await self.parts()
        await self.act("part-date", f"{limit['id']} 3")
        (limit,) = await self.parts()
        self.assertRegex(limit["dateWords"], r"^3 days left \(due Summer \d+(st|nd|rd|th), 1201\)$")
        await self.sql(
            "UPDATE game_clocks SET calendar_def = %s WHERE guild_id = %s",
            Jsonb({"layout": {}, "weekday": 0}), self.g,
        )  # fmt: skip
        (limit,) = await self.parts()
        self.assertEqual(limit["dateWords"], "3 days left (due Day 10)")

    async def test_the_no_clock_words_are_discords(self) -> None:
        from dmbot.ui import thread_parts

        self.assertEqual(thread_card.NO_CLOCK, thread_parts.NO_CLOCK)

    async def test_the_clock_door_has_nothing_for_no_clock_or_another_guild(self) -> None:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_game_clock(%s, %s) AS r", (self.g, self.one.id)
            )
            none = await cur.fetchone()
            cur = await conn.execute(
                "SELECT dmbot_web_game_clock(%s, %s) AS r", (self.g + 1, self.one.id)
            )
            other = await cur.fetchone()
        assert none is not None
        self.assertEqual(none["r"], {})
        self.assertIsNone(other and other["r"])

    async def test_the_clock_door_answers_a_dm_and_nobody_else(self) -> None:
        await self.put_clock(10_000)
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_game_clock(%s, %s) AS r", (self.g, self.one.id)
            )
            found = await cur.fetchone()
        assert found is not None
        self.assertEqual(found["r"]["minute"], 10_000)
        bob = await self.signed(BOB, THURSDAY)
        async with self.web.user(bob.user_id, session=bob.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_game_clock(%s, %s) AS r", (self.g, self.one.id)
            )
            found = await cur.fetchone()
        self.assertIsNone(found and found["r"])

    async def test_the_clock_door_gives_a_custom_calendars_definition_and_a_plain_one_none(
        self,
    ) -> None:
        from psycopg.types.json import Jsonb

        from dmbot.timebot import calendar as cal
        from dmbot.timebot import layout as lay

        await self.put_clock(10_000)
        seasons = lay.four_seasons(90)
        custom = cal.Calendar(
            cal.CUSTOM, layout=seasons, layout_start=seasons.start_on(1201, "Summer", 2)
        )
        for kind, definition, expect in (
            ("days", None, None),
            ("custom", Jsonb(cal.def_of(custom)), cal.def_of(custom)),
        ):
            await self.sql(
                "UPDATE game_clocks SET calendar_kind = %s, calendar_year = %s,"
                " calendar_ordinal = %s, calendar_def = %s WHERE guild_id = %s",
                kind, None if expect is None else 1201, None if expect is None else 91,
                definition, self.g,
            )  # fmt: skip
            async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
                await _scoped(conn, self.g)
                cur = await conn.execute(
                    "SELECT dmbot_web_game_clock(%s, %s) AS r", (self.g, self.one.id)
                )
                found = await cur.fetchone()
            assert found is not None
            self.assertEqual((found["r"]["kind"], found["r"]["def"]), (kind, expect))

    async def test_with_no_game_clock_nothing_is_dated_and_it_says_so(self) -> None:
        await self.act("part-add", f"{self.thread} time_limit - - - - - - By the thaw")
        (limit,) = await self.parts()
        self.clock_now = None
        self.assertEqual((await self.act("part-date", f"{limit['id']} 3")).error, wa.NO_CLOCK)
        self.assertFalse((await self.parts())[0]["dated"])

    async def test_a_date_for_a_part_gone_or_not_a_time_limit_is_refused(self) -> None:
        await self.act("part-add", f"{self.thread} objective - - - - - - Find the heir")
        (step,) = await self.parts()
        self.assertNotEqual((await self.act("part-date", f"{step['id']} 3")).error, "")
        self.assertEqual((await self.act("part-date", f"{'a' * 32} 3")).error, wa.GONE)

    async def test_more_days_than_the_bot_takes_is_refused_not_written(self) -> None:
        await self.act("part-add", f"{self.thread} time_limit - - - - - - By the thaw")
        (limit,) = await self.parts()
        self.assertEqual((await self.act("part-date", f"{limit['id']} 5000")).error, wa.BAD_TARGET)
        self.assertFalse((await self.parts())[0]["dated"])

    async def test_the_dates_shape_is_checked_by_the_door(self) -> None:
        for text in (f"{'a' * 32} 0", f"{'a' * 32} 10000", f"{'a' * 32} x", f"{'a' * 32}", "5 3"):
            got = await self.queue_part("part-date", text)
            self.assertNotEqual(got["status"], "ok", text)

    async def test_the_answer_route_reads_a_change_and_gives_its_undo_batch(self) -> None:
        answer = await thread_card.change(
            self.web, self.session, self.cref, self.thread,
            {"action": "add", "kind": "objective", "words": "Find the heir"},
        )  # fmt: skip
        waiting = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual((waiting["state"], waiting["batch"]), ("waiting", None))
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        done = await wa.apply(
            self.memory, self.g, {ALICE.id}, claimed, actions=self.actions, game_now=self.game_now
        )
        await self.actions.finish(self.g, claimed, error=done.error, result_batches=done.batches)
        read = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual((read["state"], read["error"]), ("done", ""))
        self.assertEqual(read["batch"], done.batches[0])

    async def test_a_state_a_show_and_a_remove(self) -> None:
        made = await self.act("part-add", f"{self.thread} objective - - - - - - Cross the pass")
        self.assertEqual(made.error, "")
        part_id = await self._id()
        self.assertEqual((await self.act("part-state", f"{part_id} done")).error, "")
        # (a state change is a change in the story: the part stands as a new row with a new id)
        part_id = await self._id()
        card = await thread_card.read(self.web, self.session, self.cref, self.thread)
        self.assertEqual((card["done"], card["of"]), (1, 1))
        self.assertEqual((await self.act("part-show", f"{part_id} 1")).error, "")
        part_id = await self._id()
        self.assertTrue((await self.parts())[0]["tableKnows"])
        removed = await self.act("part-remove", part_id)
        self.assertEqual(removed.error, "")
        self.assertEqual(await self.parts(), [])
        (batch,) = removed.batches
        self.assertEqual((await self.act("part-undo", str(batch))).error, "")
        self.assertEqual(len(await self.parts()), 1)

    async def test_a_fix_in_place_and_a_change_in_the_story(self) -> None:
        await self.act("part-add", f"{self.thread} reward - - - - - - 100 gold")
        part_id = (await self.parts())[0]["id"]
        self.assertEqual((await self.act("part-fix", f"{part_id} words 100 gold coins")).error, "")
        self.assertEqual((await self.parts())[0]["words"], "100 gold coins")
        self.assertEqual((await self.act("part-fix", f"{part_id} due up_front")).error, "")
        self.assertEqual((await self.parts())[0]["due"], "up_front")
        story = await self.act("part-fix", f"{await self._id()} words_story 150 gold coins")
        self.assertEqual(story.error, "")
        (now,) = await self.parts()  # (the old words are history: one standing part)
        self.assertEqual(now["words"], "150 gold coins")

    async def _id(self) -> str:
        return str((await self.parts())[0]["id"])

    async def test_a_branch_is_made_moved_off_and_a_loop_is_refused(self) -> None:
        other = await self.memory.keep_thread(
            self.g, self.one.id, "The Frozen Pass", "quest", STATE["quest"], aim="", parts=[],
            source=DM,
        )  # fmt: skip
        side = other.value.id
        self.assertEqual((await self.act("branch-move", f"{side} {self.thread}")).error, "")
        card = await thread_card.read(self.web, self.session, self.cref, self.thread)
        self.assertEqual([b["name"] for b in card["branches"]], ["The Frozen Pass"])
        loop = await self.act("branch-move", f"{self.thread} {side}")
        self.assertEqual(
            loop.error, wa.BAD_TARGET
        )  # (a thread cannot be a branch of its own branch)
        self.assertEqual((await self.act("branch-not", side)).error, "")
        card = await thread_card.read(self.web, self.session, self.cref, self.thread)
        self.assertEqual(card["branches"], [])

    async def test_a_part_that_is_gone_is_gone_and_an_undo_takes_back_only_this_dms_own_part_change(
        self,
    ) -> None:
        self.assertEqual((await self.act("part-state", f"{'a' * 32} done")).error, wa.GONE)
        self.assertEqual((await self.act("part-undo", "999999")).error, wa.GONE)
        # (a batch the bot made itself, outside the website's part actions)
        direct = await self.memory.add_thread_part(
            self.g, self.one.id, self.thread, "objective", "Made in Discord", source=DM
        )
        self.assertEqual((await self.act("part-undo", str(direct.batch))).error, wa.GONE)
        # (another DM's change, and the Undo's own batch)
        mine = await self.act("part-add", f"{self.thread} objective - - - - - - Find the heir")
        (batch,) = mine.batches
        await self.sql(
            "UPDATE name_actions SET user_id = %s WHERE guild_id = %s"
            " AND %s = ANY (result_batches)",
            BOB.id, self.g, batch,
        )  # fmt: skip
        self.assertEqual((await self.act("part-undo", str(batch))).error, wa.GONE)

    async def test_a_player_cannot_queue_a_part_change(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        async with self.web.user(bob.user_id, session=bob.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, "part-remove", "", "", "a" * 32, False),
            )
            found = await cur.fetchone()
        self.assertIsNone(found and found["r"])  # (the door answers NULL: a DM only)

    async def test_only_the_action_shapes_are_taken(self) -> None:
        t = self.thread
        for action, text in (
            ("part-add", f"{t} dragon - - - - - - Words"),  # (not a kind)
            ("part-add", f"{t} objective - - - - - -"),  # (no words)
            ("part-fix", f"{'a' * 32} colour red"),
            ("part-show", f"{'a' * 32} 2"),
            ("part-state", "x done"),
            ("branch-move", f"{t}"),
            ("part-undo", "x"),
        ):
            got = await self.queue_part(action, text)
            self.assertNotEqual(got["status"], "ok", (action, text))

    async def test_the_web_module_checks_before_anything_is_queued(self) -> None:
        for body in (
            {},
            {"action": "add", "kind": "dragon", "words": "x"},
            {"action": "add", "kind": "objective", "words": ""},
            {"action": "add", "kind": "objective", "words": "x", "who": "nope"},
            {"action": "fix", "part": "a" * 32, "field": "colour", "value": "red"},
            {"action": "state", "part": "a" * 32},
            {"action": "show", "part": "a" * 32, "shown": "yes"},
            {"action": "branch-move", "bigger": self.thread},
            {"action": "undo", "batch": "-1"},
            {"action": "date", "part": "a" * 32, "days": 0},
            {"action": "date", "part": "a" * 32, "days": 3651},
            {"action": "date", "part": "a" * 32, "days": True},
            {"action": "date", "part": "a" * 32, "days": "3"},
            {"action": "date", "days": 3},
        ):
            with self.assertRaises(RecapError, msg=str(body)):
                await thread_card.change(self.web, self.session, self.cref, self.thread, body)
        answer = await thread_card.change(
            self.web, self.session, self.cref, self.thread,
            {"action": "add", "kind": "objective", "words": "Find the heir"},
        )  # fmt: skip
        self.assertTrue(answer)
        dates: list[dict[str, Any]] = [
            {"action": "date", "part": "a" * 32, "days": 3},
            {"action": "date", "part": "a" * 32, "days": None},
        ]
        for date_body in dates:
            self.assertTrue(
                await thread_card.change(self.web, self.session, self.cref, self.thread, date_body)
            )
