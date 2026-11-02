"""Keeping a backstory's family links works out the family links that follow, at once (#3238):
before this the suggestions appeared only when the DM next opened the connections screen, so a DM
who kept the links and looked for them found none. Real Postgres; invented names."""

from __future__ import annotations

from dmbot.memory import backstory_keep as keep
from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.memory.models import PROPOSED, WORKED_OUT_SOURCE
from dmbot.ui import names as ui
from tests.test_memory_names import GUILD, NamesTest


class KeepingFamily(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = BackstoryLinks(self.db)
        self.rolo = await ui.save_name(self.memory, self.campaign, "Rolo", "npc", [], [])
        self.tavin = await ui.save_name(self.memory, self.campaign, "Tavin", "npc", [], [])
        self.quill = await ui.save_name(self.memory, self.campaign, "Quill", "npc", [], [])

    async def queue(self) -> None:
        await self.store.add(
            GUILD,
            self.campaign.id,
            [
                NewLink(self.rolo.id, "kin_of", self.tavin.id, "his father Rolo", detail="parent"),
                NewLink(
                    self.rolo.id, "kin_of", self.quill.id, "Rolo's brother Quill", detail="sibling"
                ),
            ],
        )

    async def kept(self) -> keep.KeptAll:
        waiting = await self.store.waiting(GUILD, self.campaign.id, symmetric=["kin_of"])
        self.assertEqual(len(waiting), 2)
        return await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, waiting)

    async def worked_out(self) -> list[tuple[str, str, str]]:
        rows = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        return [
            (r.subject_id, r.detail, r.object_id)
            for r in rows
            if r.source == WORKED_OUT_SOURCE and r.status == PROPOSED
        ]

    async def test_keeping_the_links_works_out_the_uncle_at_once_and_counts_it(self) -> None:
        await self.queue()
        done = await self.kept()
        self.assertEqual(done.worked_out, 1)
        self.assertEqual(await self.worked_out(), [(self.quill.id, "aunt or uncle", self.tavin.id)])

    async def test_undoing_the_keep_takes_the_suggestion_with_the_links(self) -> None:
        await self.queue()
        done = await self.kept()
        back = await keep.undo_all(self.memory, self.store, GUILD, self.campaign.id, done)
        self.assertEqual(back, 2)
        self.assertEqual(await self.worked_out(), [])

    async def test_keeping_something_that_is_not_family_does_not_work_anything_out(self) -> None:
        await self.store.add(
            GUILD, self.campaign.id, [NewLink(self.rolo.id, "knows", self.tavin.id, "he knows him")]
        )
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        done = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, waiting)
        self.assertEqual(done.worked_out, 0)

    async def test_the_count_is_every_suggestion_waiting_not_only_the_new_ones(self) -> None:
        await self.queue()
        first = await self.kept()
        self.assertEqual(first.worked_out, 1)
        extra = await ui.save_name(self.memory, self.campaign, "Wren", "npc", [], [])
        await self.store.add(
            GUILD,
            self.campaign.id,
            [NewLink(self.rolo.id, "kin_of", extra.id, "Rolo's sister Wren", detail="sibling")],
        )
        waiting = await self.store.waiting(GUILD, self.campaign.id, symmetric=["kin_of"])
        again = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, waiting)
        # the first suggestion still waits and the new sibling adds two more (Quill and Wren are
        # siblings of each other, and Wren is an aunt or uncle of Tavin too): the total is said
        self.assertEqual(again.worked_out, len(await self.worked_out()))
        self.assertGreater(again.worked_out, 1)
