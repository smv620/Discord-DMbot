"""A kept story thread's card on the website (#4476, quests slice 2, core): a database door that
answers a DM of the campaign and nobody else, with the DM's view of the thread's parts (the rule
`thread_parts.parts_for(the_dm())` keeps), the people they name by name, what is owed and how many
objectives are done worked out with the bot's own rules, and the thread's branches. Invented names.
Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import DM
from dmbot.memory.thread_read import STATE
from dmbot.web import thread_card
from dmbot.web.recaps import RecapError, _scoped
from tests.test_recap_web import BOB
from tests.test_web_api import GORRAK, THURSDAY
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
        await self.memory.involve(self.g, self.one.id, quest, self.mira, "quest giver", source=DM)
        await self.memory.involve(self.g, self.one.id, quest, self.keep, "target", source=DM)
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
