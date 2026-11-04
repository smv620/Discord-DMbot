"""A feeling kept with a connection becomes a reputation from the story (#3175, slice 3).

"Belleros knows Cerric (hostile)" pressed Keep by the DM stores how Belleros feels about Cerric as
an impression of weight `FEELING_WEIGHT`; a standing the DM set always wins, and one Undo takes back
both. Invented names; needs Postgres.
"""

from __future__ import annotations

from typing import Any

from dmbot.memory import backstory_keep as keep
from dmbot.memory import standing
from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.memory.models import BACKGROUND_SOURCE, CONFIRMED, NewName
from tests.test_memory_names import GUILD, NamesTest


class TheFeeling(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = BackstoryLinks(self.db)
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Belleros", "npc", CONFIRMED), NewName("Cerric", "npc", CONFIRMED),
             NewName("Oakmere", "place", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        bel, cer, oak = made.value
        assert bel and cer and oak
        self.bel: str = bel
        self.cer: str = cer
        self.oak: str = oak

    async def press(self, a: str, b: str, detail: str) -> keep.KeptAll:
        await self.store.add(GUILD, self.campaign.id, [NewLink(a, "knows", b, "q", detail=detail)])
        waiting = [w for w in await self.store.waiting(GUILD, self.campaign.id) if w.ready]
        return await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, waiting)

    async def history(self, a: str, b: str) -> list[Any]:
        return await self.memory.opinion_history(GUILD, self.campaign.id, a, b, attitude=False)

    async def test_a_hostile_word_kept_is_a_reputation_from_the_story(self) -> None:
        await self.press(self.bel, self.cer, "hostile")
        (one,) = await self.history(self.bel, self.cer)
        self.assertEqual((one.score, one.weight, one.source), (-80, 30, BACKGROUND_SOURCE))
        self.assertIsNone(one.closed_at)
        self.assertEqual(await self.history(self.cer, self.bel), [])  # (one way only)

    async def test_neutral_stores_nothing_and_wary_stores_the_middle_step(self) -> None:
        await self.press(self.bel, self.cer, "neutral")
        self.assertEqual(await self.history(self.bel, self.cer), [])
        await self.press(self.cer, self.bel, "wary")
        (one,) = await self.history(self.cer, self.bel)
        self.assertEqual(one.score, 0)

    async def test_a_standing_the_dm_set_is_never_overwritten(self) -> None:
        await self.memory.set_impression(
            GUILD, self.campaign.id, self.bel, self.cer, standing.FRIENDLY, source="dm"
        )
        await self.press(self.bel, self.cer, "hostile")
        (one,) = await self.history(self.bel, self.cer)
        self.assertEqual((one.score, one.source), (40, "dm"))
        self.assertEqual(
            len(await self.memory.relations(GUILD, self.campaign.id, include_secret=True)), 1
        )  # (kept)

    async def test_a_later_feeling_closes_the_old_one_and_keeps_the_history(self) -> None:
        await self.press(self.bel, self.cer, "hostile")
        # the change: the same pair, the opposite word
        done = await self.press(self.bel, self.cer, "friendly")
        rows = await self.history(self.bel, self.cer)
        self.assertEqual([(r.score, r.closed_at is None) for r in rows], [(-80, False), (40, True)])
        self.assertEqual(
            await keep.undo_all(self.memory, self.store, GUILD, self.campaign.id, done), 1
        )
        back = await self.history(self.bel, self.cer)
        self.assertEqual([(r.score, r.closed_at is None) for r in back][-1], (-80, True))

    async def test_one_undo_takes_back_the_connection_and_its_reputation(self) -> None:
        done = await self.press(self.bel, self.cer, "hostile")
        self.assertEqual(
            await keep.undo_all(self.memory, self.store, GUILD, self.campaign.id, done), 1
        )
        self.assertEqual(
            [r for r in await self.history(self.bel, self.cer) if r.closed_at is None], []
        )
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)  # waits again

    async def test_ends_that_cannot_hold_a_standing_keep_the_connection_alone(self) -> None:
        await self.press(self.bel, self.oak, "hostile")  # a place is no one a character judges
        self.assertEqual(await self.history(self.bel, self.oak), [])
        self.assertEqual(
            len(await self.memory.relations(GUILD, self.campaign.id, include_secret=True)), 1
        )

    async def test_the_same_word_twice_is_one_reputation_and_no_extra_history(self) -> None:
        await self.press(self.bel, self.cer, "hostile")
        await self.press(self.bel, self.cer, "hostile")  # (the same pair again: kept as it was)
        rows = await self.history(self.bel, self.cer)
        self.assertEqual([(r.score, r.closed_at is None) for r in rows], [(-80, True)])

    async def test_a_place_as_the_one_judged_or_an_out_of_range_score_stores_nothing(self) -> None:
        feel = self.memory.add_feeling
        c = self.campaign.id
        self.assertIsNone(await feel(GUILD, c, self.bel, self.oak, -80))  # (a place is not judged)
        self.assertIsNone(await feel(GUILD, c, self.bel, self.bel, -80))  # (the same name)
        self.assertIsNone(await feel(GUILD, c, self.bel, self.cer, 500))  # (not a score)
        self.assertIsNotNone(await feel(GUILD, c, self.oak, self.bel, -40))  # (a place may judge)

    async def test_a_standing_set_after_the_press_does_not_break_undo(self) -> None:
        done = await self.press(self.bel, self.cer, "hostile")
        await self.memory.set_impression(
            GUILD, self.campaign.id, self.bel, self.cer, standing.FRIENDLY, source="dm"
        )
        await keep.undo_all(self.memory, self.store, GUILD, self.campaign.id, done)  # no crash
        open_rows = [r for r in await self.history(self.bel, self.cer) if r.closed_at is None]
        self.assertEqual([r.source for r in open_rows], ["dm"])  # the DM's word is still there

    async def test_a_word_off_the_list_stores_nothing(self) -> None:
        await self.press(self.bel, self.cer, "best buddies")
        self.assertEqual(await self.history(self.bel, self.cer), [])
