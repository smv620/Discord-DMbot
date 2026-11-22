"""A road's condition on the website (#4451, migration 0591, the web half of #4258 slice 4a): the
door tells a player only of a condition the DM kept and showed, numbers the session it began in and
names what it ended; the three actions run the store's own `set_route_condition`,
`keep_road_condition` and `show_road_condition` with one Undo (`route-undo`); every shape is
checked. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import map_facts as mf
from dmbot.memory import web_actions as wa
from dmbot.memory.map_fact_store import Read
from dmbot.memory.models import new_id
from dmbot.web import relationships, road_conditions, route_lengths, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import THURSDAY
from tests.test_web_thread_state import ThreadWeb


class Roads(ThreadWeb):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.targos = await self.add("Targos", "place")
        self.bryn = await self.add("Bryn Shander", "place")
        made = await self.memory.add_relation(
            self.g, self.one.id, self.targos, "route_to", self.bryn, detail="road",
            source="dm", confidence=1.0, status="confirmed",
        )  # fmt: skip
        self.road: str = made.value[0].id
        self.pass_ = await self.add("Icewind Pass", "place")
        await self.memory.set_place_form(self.g, self.one.id, self.pass_, "route", source="dm")

    async def began(self, started_at: int) -> None:
        """A session (with a line, so it has a number) that starts at `started_at`."""
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at,"
                " line_count) VALUES (%s, %s, %s, %s, 3)",
                (new_id(), self.g, self.one.id, started_at),
            )

    def drawn(self) -> dict[str, Any]:
        return {
            "edges": [{"links": [{"id": self.road}]}],
            "nodes": [{"id": self.pass_}, {"id": self.targos}],
            "tray": [],
        }

    async def conditions(self, who: Any = None, *, full: bool = True) -> dict[str, Any]:
        page = self.drawn()
        await road_conditions.with_road_conditions(
            self.web, who or self.session, self.cref, page, full=full
        )
        return page

    async def bob_session(self) -> Any:
        who: Any = BOB
        found = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert found is not None
        return found

    async def road_press(self, on: str, target: str, value: str, words: str = "") -> Any:
        await road_conditions.queue_set(
            self.web, self.session, self.cref,
            {"on": on, "id": target, "value": value, "words": words},
        )  # fmt: skip
        (outcome,) = await self.bot_loop()
        return outcome

    async def keep_it(self) -> Any:
        await road_conditions.queue_keep(self.web, self.session, self.cref, {"id": self.road})
        (outcome,) = await self.bot_loop()
        return outcome

    async def show_it(self, shown: bool) -> Any:
        await road_conditions.queue_show(
            self.web, self.session, self.cref, {"id": self.road, "shown": shown}
        )
        (outcome,) = await self.bot_loop()
        return outcome

    async def road_link(self, who: Any = None, *, full: bool = True) -> dict[str, Any]:
        link: dict[str, Any] = (await self.conditions(who, full=full))["edges"][0]["links"][0]
        return link


class TheDoor(Roads):
    async def test_the_dm_is_told_which_are_roads_and_a_player_nothing_before_a_condition(
        self,
    ) -> None:
        mine = await self.conditions()
        link = mine["edges"][0]["links"][0]
        self.assertEqual((link["road"], "condition" in link), (True, False))
        self.assertTrue(mine["nodes"][0]["road"])  # (Icewind Pass is a route place)
        self.assertNotIn("road", mine["nodes"][1])  # (Targos is not)
        theirs = await self.conditions(await self.bob_session(), full=False)
        self.assertNotIn("road", theirs["edges"][0]["links"][0])

    async def test_a_change_says_since_which_session_and_what_it_was(self) -> None:
        await self.began(1_000)
        await self.began(5_000)
        await self.road_press("connection", self.road, "open")
        await self.road_press("connection", self.road, "closed")
        got = (await self.road_link())["condition"]
        self.assertEqual((got["value"], got["before"]), ("closed", {"value": "open", "words": ""}))
        self.assertEqual((got["since"], got["read"]), (2, False))

    async def test_a_player_is_told_only_of_one_the_dm_kept_and_showed(self) -> None:
        bob = await self.bob_session()
        await self.memory.add_read_map_facts(
            self.g, self.one.id, [Read(self.pass_, mf.CONDITION, "closed")], source="background"
        )
        mine = (await self.conditions())["nodes"][0]["condition"]
        self.assertEqual((mine["value"], mine["read"]), ("closed", True))
        theirs = await self.conditions(bob, full=False)
        self.assertNotIn("condition", theirs["nodes"][0])  # (DMbot's read is the DM's alone)
        await self.road_press("connection", self.road, "other", "half washed out at the ford")
        self.assertNotIn("condition", await self.road_link(bob, full=False))  # (not shown yet)
        await self.show_it(True)
        seen = (await self.road_link(bob, full=False))["condition"]
        self.assertEqual((seen["value"], seen["words"]), ("other", "half washed out at the ford"))
        self.assertNotIn("read", seen)
        await self.show_it(False)
        self.assertNotIn("condition", await self.road_link(bob, full=False))

    async def test_a_players_before_is_dropped_when_the_row_it_ended_was_hidden(self) -> None:
        bob = await self.bob_session()
        await self.began(1_000)
        await self.road_press("connection", self.road, "open")
        await self.road_press("connection", self.road, "closed")
        await self.show_it(True)
        seen = (await self.road_link(bob, full=False))["condition"]
        self.assertEqual(
            (seen["value"], seen["before"]), ("closed", {"value": "open", "words": ""})
        )
        await self.show_it(False)
        self.assertNotIn("condition", await self.road_link(bob, full=False))


class TheActions(Roads):
    async def test_the_answer_the_page_polls_carries_the_change_for_undo(self) -> None:
        """The answers route knows these actions (it refused `road-` and `route-` before) and
        gives the change number the page's Undo takes, for a road's condition and a route's
        length alike."""
        answer = await road_conditions.queue_set(
            self.web, self.session, self.cref,
            {"on": "place", "id": self.pass_, "value": "closed", "words": ""},
        )  # fmt: skip
        (outcome,) = await self.bot_loop()
        got = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual(
            (got["state"], got["error"], got["batch"]), ("done", "", outcome.batches[0])
        )
        length = await route_lengths.queue_set(
            self.web, self.session, self.cref,
            {"on": "place", "id": self.pass_, "words": "two days by cart"},
        )  # fmt: skip
        (made,) = await self.bot_loop()
        seen = await relationships.read_answer(self.web, self.session, self.cref, length)
        self.assertEqual((seen["state"], seen["batch"]), ("done", made.batches[0]))

    async def test_the_longest_words_go_through_and_spaces_are_squeezed(self) -> None:
        words = "x" * 60
        self.assertFalse((await self.road_press("place", self.pass_, "other", words)).error)
        self.assertEqual((await self.conditions())["nodes"][0]["condition"]["words"], words)
        await self.road_press("place", self.pass_, "other", " half \n washed   out ")
        got = (await self.conditions())["nodes"][0]["condition"]
        self.assertEqual(got["words"], "half washed out")

    async def test_set_changes_it_with_one_undo_and_none_takes_it_off(self) -> None:
        outcome = await self.road_press("place", self.pass_, "hard")
        self.assertFalse(outcome.error)
        (batch,) = outcome.batches
        self.assertEqual((await self.conditions())["nodes"][0]["condition"]["value"], "hard")
        await route_lengths.queue_undo(self.web, self.session, self.cref, str(batch))
        (undone,) = await self.bot_loop()
        self.assertFalse(undone.error)
        self.assertNotIn("condition", (await self.conditions())["nodes"][0])
        await self.road_press("place", self.pass_, "gone")
        again = await self.road_press("place", self.pass_, "gone")
        self.assertEqual((again.error, again.batches), ("", ()))
        off = await self.road_press("place", self.pass_, "none")
        self.assertFalse(off.error)
        self.assertNotIn("condition", (await self.conditions())["nodes"][0])

    async def test_keep_makes_a_read_one_the_dms(self) -> None:
        await self.memory.add_read_map_facts(
            self.g,
            self.one.id,
            [Read("", mf.CONDITION, "closed", relation_id=self.road)],
            source="background",
        )
        self.assertTrue((await self.road_link())["condition"]["read"])
        kept = await self.keep_it()
        self.assertFalse(kept.error)
        self.assertFalse((await self.road_link())["condition"]["read"])

    async def test_a_road_that_is_not_there_or_not_a_route_is_gone(self) -> None:
        self.assertEqual((await self.road_press("connection", "a" * 32, "open")).error, wa.GONE)
        self.assertEqual((await self.road_press("place", self.targos, "open")).error, wa.GONE)

    async def test_an_undo_of_a_change_that_is_not_this_dms_is_gone(self) -> None:
        await route_lengths.queue_undo(self.web, self.session, self.cref, "999999")
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)

    async def test_only_a_dm_queues_and_bad_bodies_are_refused(self) -> None:
        bob = await self.bob_session()
        good = {"on": "place", "id": self.pass_, "value": "closed"}
        with self.assertRaises(RecapError) as err:
            await road_conditions.queue_set(self.web, bob, self.cref, good)
        self.assertEqual(err.exception.code, "not_allowed")
        for body in (
            {**good, "on": "road"}, {**good, "id": "nope"}, {**good, "value": "wet"},
            {**good, "value": "other"}, {**good, "words": "snow"},
            {**good, "value": "other", "words": 5}, {"id": self.pass_}, 5,
        ):  # fmt: skip
            with self.assertRaises(RecapError) as bad:
                await road_conditions.queue_set(self.web, self.session, self.cref, body)
            self.assertEqual(bad.exception.code, "bad_action")
        with self.assertRaises(RecapError) as long:
            await road_conditions.queue_set(
                self.web, self.session, self.cref, {**good, "value": "other", "words": "x" * 61}
            )
        self.assertEqual(long.exception.code, "too_long")
        for call in (
            road_conditions.queue_keep(self.web, bob, self.cref, {"id": self.road}),
            road_conditions.queue_show(self.web, bob, self.cref, {"id": self.road, "shown": True}),
        ):
            with self.assertRaises(RecapError) as no:
                await call
            self.assertEqual(no.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as shown:
            await road_conditions.queue_show(
                self.web, self.session, self.cref, {"id": self.road, "shown": "yes"}
            )
        self.assertEqual(shown.exception.code, "bad_action")
        self.assertIsNone(await self.actions.claim_next(self.g))
