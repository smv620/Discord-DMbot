"""The website and a place's assumed parent (#4173 slice 3; Supervisor's ruling, 2026-11-13): an
assumed link waiting for its places is never one of the DM's questions on the website (the list,
its count, and Keep or Skip on it), and a kept one is never shown to a player until the DM says
it's right. Run as the website's own database role. Invented names only."""

from __future__ import annotations

from typing import Any

from dmbot.memory.backstory_links import NewLink
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import relationships
from dmbot.web.recaps import RecapError
from tests import test_web_link_actions as link_actions


class Assumed(link_actions.Editing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.inn = await self.add("Gull's Rest", "place")

    async def waiting_assumed(self) -> str:
        await self.backstory.add(
            self.g, self.one.id, [NewLink(self.inn, "located_in", self.hall, "", assumed=True)]
        )
        (found,) = await self.backstory.waiting(self.g, self.one.id, assumed=True)
        return found.id

    async def waiting_now(self) -> dict[str, Any]:
        return await relationships.read_waiting(
            self.web, self.session, self.cref, outside_engine=None
        )

    async def refused_queue(self, **body: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await relationships.queue_waiting(self.web, self.session, self.cref, body)
        return caught.exception.code

    async def test_never_a_question_on_the_website(self) -> None:
        waiting = await self.waiting_assumed()
        page = await self.waiting_now()
        self.assertNotIn(waiting, [b["id"] for b in page["backstory"]])
        self.assertEqual(page["counts"]["backstory"], 0)
        self.assertEqual(await self.refused_queue(kind="backstory", action="keep", id=waiting),
                         "not_found")  # fmt: skip

    async def edges(self, who: Any = None) -> set[str]:
        got = await self.graph(who, center=self.hall, hops=1)
        return {x["id"] for e in got["edges"] for x in e["links"]}

    async def test_a_player_never_sees_it_until_the_dm_says_its_right(self) -> None:
        written = await self.memory.add_relation(
            self.g, self.one.id, self.inn, "located_in", self.hall,
            source=DM_SOURCE, confidence=1.0, status="confirmed", assumed=True,
        )  # fmt: skip
        guess = written.value[0].id
        if self.hero is not None:
            await self.tell_the_table(self.hero)  # (what the table knows: never the guess)
        self.assertIn(guess, await self.edges())
        self.assertNotIn(guess, await self.edges(self.player))
        # the DM's own 👁️ Reveal is their word on it: then the players see it like any other
        await self.memory.update_relation(
            self.g, self.one.id, guess, source=DM_SOURCE, unrevealed=False
        )
        self.assertIn(guess, await self.edges(self.player))

    async def assumed_link(self) -> str:
        written = await self.memory.add_relation(
            self.g, self.one.id, self.inn, "located_in", self.hall,
            source=DM_SOURCE, confidence=1.0, status="confirmed", assumed=True,
        )  # fmt: skip
        return str(written.value[0].id)

    async def dm_links(self) -> dict[str, dict[str, Any]]:
        got = await relationships.read_web(self.web, self.session, self.cref, center=self.hall)
        return {x["id"]: x for e in got["edges"] for x in e["links"]}

    async def test_the_dm_is_told_it_is_assumed_and_right_clears_it_and_undo_puts_it_back(
        self,
    ) -> None:
        guess = await self.assumed_link()
        self.assertTrue((await self.dm_links())[guess]["assumed"])
        answer = await self.edit(guess, action="right")
        outcome = (await self.apply_all())[0]
        self.assertEqual(outcome.error, "")
        self.assertNotIn("assumed", (await self.dm_links())[guess])  # (the mark is gone)
        # one change, so the page's Undo puts the mark back
        done = await self.poll(answer)
        (batch,) = done["batches"]
        await self.memory.undo(self.g, self.one.id, batch, source=DM_SOURCE)
        self.assertTrue((await self.dm_links())[guess]["assumed"])

    async def test_a_second_press_and_a_player_press_change_nothing(self) -> None:
        guess = await self.assumed_link()
        self.assertEqual(
            await self.refused_edit(self.player, link=guess, action="right"), "forbidden"
        )
        self.assertEqual(await self.apply_all(), [])  # (nothing was queued)
        await self.edit(guess, action="right")
        await self.edit(guess, action="right")
        outcomes = await self.apply_all()
        self.assertEqual([o.error for o in outcomes], ["", ""])  # (the second finds it done)
