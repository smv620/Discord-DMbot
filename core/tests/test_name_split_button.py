"""The Discord "These are separate names" button on a name question (#3383): it is on a list and
only on a list, it splits for the DM alone, and the split can be taken back until a part is
answered. Invented names only."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import PROPOSED
from dmbot.ui import logic
from dmbot.ui import names as ui
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest

SPLIT = "✂️ These are separate names"
UNDO = "↩ Undo the split"
GROUP = "👥 They're a group"
YES_GROUP = "👥 Yes, that group"
UNDO_GROUP = "↩ Undo the group"


class SplitButton(NamesTest):
    async def waiting(self, *names: str, kind: str = "place") -> None:
        for name in names:
            await self.memory.add_entity(
                GUILD, self.campaign.id, type=kind, name=name, source="scan",
                description="Heard 2 times",
            )  # fmt: skip

    async def card(self, user: int = DM) -> tuple[ui.SuggestionReview, Any]:
        it = self.it(user)
        await ui.start_review(it, self.campaign.id, bulk=False)
        _text, kw = it.response.sent[0]
        return kw["view"], it

    def labels(self, view: ui.SuggestionReview) -> list[str]:
        return [str(getattr(b, "label", "")) for b in view.children]

    def button(self, view: ui.SuggestionReview, label: str) -> Any:
        return next(b for b in view.children if getattr(b, "label", "") == label)

    async def waiting_names(self) -> list[str]:
        found = await self.memory.entities(GUILD, self.campaign.id, statuses=[PROPOSED])
        return sorted(e.name for e in found)

    async def test_the_button_is_on_a_list_and_only_a_list(self) -> None:
        await self.waiting("Hrothgar")
        view, _it = await self.card()
        self.assertNotIn(SPLIT, self.labels(view))

    async def test_pressing_it_makes_a_question_for_each_part_and_asks_them_next(self) -> None:
        await self.waiting("Aldmoor, Quillgate, Fenwick")
        view, _it = await self.card()
        self.assertIn(SPLIT, self.labels(view))
        await self.button(view, SPLIT).callback(self.it())
        self.assertEqual(await self.waiting_names(), ["Aldmoor", "Fenwick", "Quillgate"])
        self.assertIn(view.name, ("Aldmoor", "Fenwick", "Quillgate"))  # the first part is asked
        self.assertIn(UNDO, self.labels(view))

    async def test_undo_brings_the_list_back_until_a_part_is_answered(self) -> None:
        await self.waiting("Aldmoor, Quillgate, Fenwick")
        view, _it = await self.card()
        await self.button(view, SPLIT).callback(self.it())
        await self.button(view, UNDO).callback(self.it())
        self.assertEqual(await self.waiting_names(), ["Aldmoor, Quillgate, Fenwick"])
        self.assertNotIn(UNDO, self.labels(view))
        await self.button(view, SPLIT).callback(self.it())
        await self.button(view, "⏳ Later").callback(self.it())  # an answer: no more undo
        self.assertNotIn(UNDO, self.labels(view))

    async def test_a_server_manager_who_is_not_a_dm_cannot_split(self) -> None:
        await self.waiting("Aldmoor, Quillgate, Fenwick")
        view, _it = await self.card()
        it = self.it(MANAGER)
        await self.button(view, SPLIT).callback(it)
        self.assertEqual(it.response.sent[-1][0], logic.NO_CAMPAIGN_ACCESS)
        self.assertEqual(await self.waiting_names(), ["Aldmoor, Quillgate, Fenwick"])

    async def test_a_server_manager_who_is_not_a_dm_cannot_undo_a_split(self) -> None:
        await self.waiting("Aldmoor, Quillgate, Fenwick")
        view, _it = await self.card()
        await self.button(view, SPLIT).callback(self.it())
        it = self.it(MANAGER)
        await self.button(view, UNDO).callback(it)
        self.assertEqual(it.response.sent[-1][0], logic.NO_CAMPAIGN_ACCESS)
        self.assertEqual(await self.waiting_names(), ["Aldmoor", "Fenwick", "Quillgate"])

    async def test_the_parts_are_asked_before_anything_else_and_the_list_is_gone(self) -> None:
        await self.waiting("Aldmoor, Quillgate, Fenwick", "Zed")
        view, _it = await self.card()
        first = view.queue[0]
        await self.button(view, SPLIT).callback(self.it())
        self.assertNotIn(first, view.queue)
        self.assertEqual(len(view.queue), 4)  # three parts and Zed

    async def test_the_group_choice_is_on_a_list_and_only_a_list(self) -> None:
        await self.waiting("Hrothgar")
        view, _it = await self.card()
        self.assertNotIn(GROUP, self.labels(view))
        self.assertNotIn(YES_GROUP, self.labels(view))

    async def test_a_list_can_be_made_a_group_and_that_can_be_undone(self) -> None:
        await self.waiting("Aldmoor, Quillgate", kind="character")
        view, _it = await self.card()
        self.assertIn(GROUP, self.labels(view))
        self.assertNotIn(YES_GROUP, self.labels(view))  # no known group fits
        entity_id = view.queue[0]
        it = self.it()
        await view.apply_group(it, entity_id, "The Two Towns")
        self.assertIn(UNDO_GROUP, self.labels(view))
        self.assertNotIn("Aldmoor, Quillgate", await self.waiting_names())
        await self.button(view, UNDO_GROUP).callback(self.it())
        self.assertEqual(await self.waiting_names(), ["Aldmoor, Quillgate"])

    async def test_a_server_manager_who_is_not_a_dm_cannot_make_a_group(self) -> None:
        await self.waiting("Aldmoor, Quillgate", kind="character")
        view, _it = await self.card()
        it = self.it(MANAGER)
        await self.button(view, GROUP).callback(it)
        self.assertEqual(it.response.sent[-1][0], logic.NO_CAMPAIGN_ACCESS)

    async def test_yes_that_group_joins_the_known_group_end_to_end(self) -> None:
        cid = self.campaign.id

        async def entity(name: str, kind: str, status: str) -> str:
            written = await self.memory.add_entity(
                GUILD, cid, type=kind, name=name, source="dm", status=status
            )
            return written.value.id

        group = await entity("The Ash Wolves", "faction", "confirmed")
        for member in ("Tavin", "Mira"):
            mid = await entity(member, "character", "confirmed")
            await self.memory.add_relation(
                GUILD, cid, mid, "member_of", group, source="dm", confidence=1.0,
                status="confirmed",
            )  # fmt: skip
        await entity("Tavin and Mira", "character", PROPOSED)
        view, _it = await self.card()
        self.assertIn(YES_GROUP, self.labels(view))  # the hint is shown
        self.assertEqual(view.group.group_name if view.group else "", "The Ash Wolves")
        await self.button(view, YES_GROUP).callback(self.it())
        self.assertEqual(await self.waiting_names(), [])  # the list is answered, no new names
        data = await self.memory.lookup_data(GUILD, cid)
        self.assertTrue(
            any(a.text == "Tavin and Mira" and a.entity_id == group for a in data.aliases)
        )  # it is another name of the group now
        self.assertEqual(view.queue, [])  # nothing left to ask: the review is over
