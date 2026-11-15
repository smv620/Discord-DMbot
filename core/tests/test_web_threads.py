"""The story threads DMbot found in an upload, on the website (#4457, quests slice 1, core): a
database door that answers a DM of the campaign and nobody else, with the people of each
suggestion by name, and three queue actions (✅ Keep with the people left on, 🚫, ↩ Undo) that the
bot applies through the same store calls as Discord's 📜 card, each keep one Undo. Invented names.
Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.thread_read import Suggestion
from dmbot.memory.thread_suggestions import ThreadSuggestionStore
from dmbot.web import thread_suggestions as web_threads
from dmbot.web.recaps import RecapError, _scoped
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting

THREAD = Suggestion(
    "quest", "Find the Lost Heir", "Bring the heir home.", "Find the heir.", (), "now"
)


class Threads(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.threads = ThreadSuggestionStore(self.db)
        found = Suggestion(
            "quest", "The Lost Heir", "Bring the heir home.", "Find the heir.",
            (("quest giver", self.mira), ("target", self.keep)), "now",
        )  # fmt: skip
        await self.threads.offer(self.g, self.one.id, [found], "Frostmaiden notes", 1_000)

    async def door(self, who: Any = None) -> Any:
        session = who or self.session
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute("SELECT dmbot_web_threads(%s, %s) AS r", (self.g, self.one.id))
            found = await cur.fetchone()
        return found["r"] if found else None

    async def waiting(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = (await self.door())["suggestions"]
        return found

    async def add_action(self, action: str, text: str) -> Any:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, action, "", "", text, False),
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def act(self, action: str, text: str) -> wa.Outcome:
        got = await self.add_action(action, text)
        self.assertEqual(got["status"], "ok", got)
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        return outcome


class TheDoor(Threads):
    async def test_the_dm_sees_each_suggestion_with_its_people_by_name(self) -> None:
        (one,) = await self.waiting()
        self.assertEqual(
            (one["kind"], one["name"], one["aim"], one["quote"], one["upload"]),
            (
                "quest",
                "The Lost Heir",
                "Bring the heir home.",
                "Find the heir.",
                "Frostmaiden notes",
            ),
        )
        self.assertEqual(
            [(p["index"], p["role"]) for p in one["people"]], [(0, "quest giver"), (1, "target")]
        )
        self.assertTrue(all(p["name"] for p in one["people"]))
        self.assertNotIn(self.mira, str(one))  # (names, never entry ids)

    async def test_a_player_or_a_stranger_gets_nothing(self) -> None:
        # (Bob is a player here)
        self.assertIsNone(await self.door(await self.signed(BOB, THURSDAY)))

    async def test_a_person_gone_since_is_left_out_and_the_others_keep_their_places(
        self,
    ) -> None:
        await self.sql(
            "DELETE FROM memory_entities WHERE guild_id = %s AND id = %s", self.g, self.mira
        )
        (one,) = await self.waiting()
        self.assertEqual([p["index"] for p in one["people"]], [1])

    async def test_an_answered_suggestion_is_not_listed(self) -> None:
        (one,) = await self.waiting()
        self.assertEqual((await self.act("thread-not", one["id"])).error, "")
        self.assertEqual(await self.waiting(), [])

    async def test_only_the_action_shapes_are_taken(self) -> None:
        (one,) = await self.waiting()
        sid = one["id"]
        for action, text in (
            ("thread-keep", sid),  # (no people word)
            ("thread-keep", f"{sid} 0,x"),
            ("thread-not", f"{sid} extra"),
            ("thread-undo", "7"),
            ("thread-undo", f"keep {sid}"),
        ):
            got = await self.add_action(action, text)
            self.assertNotEqual(got["status"], "ok", (action, text))


class TheActions(Threads):
    async def test_keep_with_one_person_left_on_is_one_undo(self) -> None:
        (one,) = await self.waiting()
        outcome = await self.act("thread-keep", f"{one['id']} 0 My Own Name")
        self.assertEqual(outcome.error, "")
        self.assertEqual(await self.waiting(), [])
        (batch,) = outcome.batches
        self.assertEqual((await self.act("thread-undo", f"keep {batch}")).error, "")
        (back,) = await self.waiting()
        self.assertEqual(back["id"], one["id"])

    async def test_a_keep_whose_name_the_store_refuses_is_not_called_gone(self) -> None:
        (one,) = await self.waiting()
        got = await self.act("thread-keep", f"{one['id']} 0 @@@")
        self.assertIn(got.error, (wa.BAD_TARGET, ""))  # (refused as a name, or kept as typed)
        if got.error:
            self.assertEqual(len(await self.waiting()), 1)  # (still waiting: nothing was kept)

    async def test_a_second_undo_of_a_keep_is_done_not_changed(self) -> None:
        (one,) = await self.waiting()
        (batch,) = (await self.act("thread-keep", f"{one['id']} 0")).batches
        self.assertEqual((await self.act("thread-undo", f"keep {batch}")).error, "")
        self.assertEqual((await self.act("thread-undo", f"keep {batch}")).error, "")

    async def test_a_player_cannot_queue_a_thread_action(self) -> None:
        (one,) = await self.waiting()
        bob = await self.signed(BOB, THURSDAY)
        async with self.web.user(bob.user_id, session=bob.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, "thread-not", "", "", one["id"], False),
            )
            found = await cur.fetchone()
        self.assertIsNone(found and found["r"])  # (the door answers NULL: a DM only)

    async def test_keep_with_nobody_left_on_still_keeps_the_thread(self) -> None:
        (one,) = await self.waiting()
        self.assertEqual((await self.act("thread-keep", f"{one['id']} -")).error, "")

    async def test_a_second_keep_or_a_turn_down_after_it_is_gone(self) -> None:
        (one,) = await self.waiting()
        self.assertEqual((await self.act("thread-keep", f"{one['id']} 0,1")).error, "")
        self.assertEqual((await self.act("thread-keep", f"{one['id']} 0,1")).error, wa.GONE)
        self.assertEqual((await self.act("thread-not", one["id"])).error, wa.GONE)

    async def test_not_a_story_thread_is_never_offered_again_and_undo_puts_it_back(self) -> None:
        (one,) = await self.waiting()
        self.assertEqual((await self.act("thread-not", one["id"])).error, "")
        await self.threads.offer(self.g, self.one.id, [THREAD], "again", 2_000)
        (other,) = await self.waiting()  # (only the new one: the turned-down one stays out)
        self.assertNotEqual(other["id"], one["id"])
        self.assertEqual((await self.act("thread-undo", f"not {one['id']}")).error, "")
        self.assertEqual({s["id"] for s in await self.waiting()}, {one["id"], other["id"]})

    async def test_undo_takes_back_only_this_dms_own_keep(self) -> None:
        (one,) = await self.waiting()
        (batch,) = (await self.act("thread-keep", f"{one['id']} 0")).batches
        await self.sql(  # (a batch another website action of this DM recorded)
            "UPDATE name_actions SET action = 'map-terrain' WHERE guild_id = %s"
            " AND %s = ANY (result_batches)",
            self.g, batch,
        )  # fmt: skip
        self.assertEqual((await self.act("thread-undo", f"keep {batch}")).error, wa.GONE)


class TheWebModule(Threads):
    async def test_the_page_names_are_checked_before_anything_is_queued(self) -> None:
        for body in (
            {},
            {"id": "x", "on": []},
            {"id": "a" * 32, "on": "0"},
            {"id": "a" * 32, "on": [True]},
            {"id": "a" * 32, "on": [0, 0]},
            {"id": "a" * 32, "on": [100]},
            {"id": "a" * 32, "on": [0], "name": "x" * 101},
            {"id": "a" * 32, "on": [0], "name": 7},
        ):
            with self.assertRaises(RecapError, msg=str(body)):
                await web_threads.queue_keep(self.web, self.session, self.cref, body)
        with self.assertRaises(RecapError):
            await web_threads.queue_undo(
                self.web, self.session, self.cref, {"keep": "1", "not": "a"}
            )
