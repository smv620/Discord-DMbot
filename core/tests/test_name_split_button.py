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


class SplitButton(NamesTest):
    async def waiting(self, *names: str) -> None:
        for name in names:
            await self.memory.add_entity(
                GUILD, self.campaign.id, type="place", name=name, source="scan",
                description="Heard 2 times",
            )  # fmt: skip

    async def card(self, user: int = DM) -> tuple[ui.SuggestionReview, Any]:
        it = self.it(user)
        await ui.start_review(it, self.campaign.id)
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
