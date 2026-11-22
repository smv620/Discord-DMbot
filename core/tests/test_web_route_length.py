"""A route's stated length on the website (#4392, migration 0589, the web half of #4255): the door
gives the lengths for the ids a page draws and leaves out a length DMbot only read for anyone who is
not a DM of the campaign, the three actions run the store's own `set_route_length` and
`keep_route_length` with one Undo, and every shape is checked. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.web import route_lengths, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import THURSDAY
from tests.test_web_thread_state import ThreadWeb


class Routes(ThreadWeb):
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

    def drawn(self) -> dict[str, Any]:
        return {
            "edges": [{"links": [{"id": self.road}]}],
            "nodes": [{"id": self.pass_}, {"id": self.targos}],
            "tray": [],
        }

    async def lengths(self, who: Any = None, *, full: bool = True) -> dict[str, Any]:
        page = self.drawn()
        await route_lengths.with_route_lengths(
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

    async def press(
        self, on: str, target: str, words: str | None = None, keep: bool = False
    ) -> Any:
        if keep:
            await route_lengths.queue_keep(
                self.web, self.session, self.cref, {"on": on, "id": target}
            )
        else:
            await route_lengths.queue_set(
                self.web, self.session, self.cref, {"on": on, "id": target, "words": words}
            )
        (outcome,) = await self.bot_loop()
        return outcome


class TheDoor(Routes):
    async def test_the_dm_sees_a_read_length_marked_and_a_players_page_has_none(self) -> None:
        await self.memory.set_route_length(
            self.g, self.one.id, self.road, "two days by cart", on="connection", source="background"
        )
        mine = await self.lengths()
        self.assertEqual(
            mine["edges"][0]["links"][0]["length"], {"words": "two days by cart", "read": True}
        )
        theirs = await self.lengths(await self.bob_session(), full=False)
        self.assertNotIn("length", theirs["edges"][0]["links"][0])

    async def test_the_dm_is_told_which_are_routes_even_with_no_length_yet(self) -> None:
        mine = await self.lengths()
        link = mine["edges"][0]["links"][0]
        self.assertEqual((link["route"], "length" in link), (True, False))
        self.assertTrue(mine["nodes"][0]["route"])  # (Icewind Pass is a route place)
        self.assertNotIn("route", mine["nodes"][1])  # (Targos is not)
        theirs = await self.lengths(await self.bob_session(), full=False)
        self.assertNotIn("route", theirs["edges"][0]["links"][0])

    async def test_a_length_the_dm_set_shows_to_a_player_without_the_read_flag(self) -> None:
        await self.memory.set_route_length(
            self.g, self.one.id, self.pass_, "a day on foot", on="place", source="dm"
        )
        mine = await self.lengths()
        self.assertEqual(mine["nodes"][0]["length"], {"words": "a day on foot", "read": False})
        theirs = await self.lengths(await self.bob_session(), full=False)
        self.assertEqual(theirs["nodes"][0]["length"], {"words": "a day on foot"})
        self.assertNotIn("length", theirs["nodes"][1])  # (Targos is not a route)


class TheActions(Routes):
    async def test_set_changes_the_length_with_one_undo(self) -> None:
        outcome = await self.press("connection", self.road, "two days by cart")
        self.assertFalse(outcome.error)
        (batch,) = outcome.batches
        got = (await self.lengths())["edges"][0]["links"][0]["length"]
        self.assertEqual(got, {"words": "two days by cart", "read": False})
        await route_lengths.queue_undo(self.web, self.session, self.cref, str(batch))
        (undone,) = await self.bot_loop()
        self.assertFalse(undone.error)
        self.assertNotIn("length", (await self.lengths())["edges"][0]["links"][0])

    async def test_empty_words_take_it_off_and_the_same_words_again_change_nothing(self) -> None:
        await self.press("place", self.pass_, "a day on foot")
        again = await self.press("place", self.pass_, "a day on foot")
        self.assertEqual((again.error, again.batches), ("", ()))
        gone = await self.press("place", self.pass_, "")
        self.assertFalse(gone.error)
        self.assertNotIn("length", (await self.lengths())["nodes"][0])

    async def test_keep_makes_a_read_length_the_dms_and_a_second_keep_is_changed(self) -> None:
        await self.memory.set_route_length(
            self.g, self.one.id, self.road, "two days by cart", on="connection", source="background"
        )
        kept = await self.press("connection", self.road, keep=True)
        self.assertFalse(kept.error)
        got = (await self.lengths())["edges"][0]["links"][0]["length"]
        self.assertEqual(got["read"], False)
        again = await self.press("connection", self.road, keep=True)
        self.assertEqual(again.error, wa.CHANGED)

    async def test_a_route_that_is_not_there_is_gone(self) -> None:
        outcome = await self.press("connection", "a" * 32, "far")
        self.assertEqual(outcome.error, wa.GONE)
        outcome = await self.press("place", self.targos, "far")  # (a place that is no route)
        self.assertEqual(outcome.error, wa.GONE)

    async def test_an_undo_of_a_change_that_is_not_this_dms_is_gone(self) -> None:
        await route_lengths.queue_undo(self.web, self.session, self.cref, "999999")
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, wa.GONE)

    async def test_only_a_dm_queues_and_bad_bodies_are_refused(self) -> None:
        bob = await self.bob_session()
        with self.assertRaises(RecapError) as err:
            await route_lengths.queue_set(
                self.web, bob, self.cref, {"on": "place", "id": self.pass_, "words": "far"}
            )
        self.assertEqual(err.exception.code, "not_allowed")
        for body in (
            {"on": "road", "id": self.road, "words": "x"}, {"on": "place", "id": "nope"},
            {"on": "place", "id": self.pass_, "words": 5}, {"id": self.pass_}, 5,
        ):  # fmt: skip
            with self.assertRaises(RecapError) as bad:
                await route_lengths.queue_set(self.web, self.session, self.cref, body)
            self.assertEqual(bad.exception.code, "bad_action")
        with self.assertRaises(RecapError) as kept:
            await route_lengths.queue_keep(
                self.web, bob, self.cref, {"on": "place", "id": self.pass_}
            )
        self.assertEqual(kept.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as undone:
            await route_lengths.queue_undo(self.web, bob, self.cref, "5")
        self.assertEqual(undone.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as long:
            await route_lengths.queue_set(
                self.web, self.session, self.cref,
                {"on": "place", "id": self.pass_, "words": "x" * 61},
            )  # fmt: skip
        self.assertEqual(long.exception.code, "too_long")
        with self.assertRaises(RecapError) as undo:
            await route_lengths.queue_undo(self.web, self.session, self.cref, "12x")
        self.assertEqual(undo.exception.code, "not_found")
        self.assertIsNone(await self.actions.claim_next(self.g))
