"""The DM's card for a worked-out family link (#2960): asks in plain words, says nothing is
remembered until yes, yes confirms, no is final, only a DM answers. Invented names."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.memory.models import CONFIRMED, PROPOSED, REJECTED
from dmbot.ui.worked_out_questions import WorkedOutQuestions
from tests.test_memory_names import DM, GUILD, NamesTest


class TheWorkedOutCard(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        ids = {}
        for name in ("Aldric", "Bryn", "Mira"):
            made = await self.memory.add_entity(
                GUILD, self.campaign.id, name=name, type="character", role="npc",
                status=CONFIRMED, source="dm",
            )  # fmt: skip
            ids[name] = made.value.id
        for a, role, b in (("Aldric", "mother", "Bryn"), ("Bryn", "sister", "Mira")):
            await self.memory.add_relation(
                GUILD, self.campaign.id, ids[a], "kin_of", ids[b], source="dm", confidence=1.0,
                status=CONFIRMED, detail=role, directional=True,
            )  # fmt: skip

    async def card(self) -> Any:
        view = WorkedOutQuestions(self.campaign.id)
        self.assertTrue(await view.load(self.it(DM)))
        return view

    async def rows(self) -> list[tuple[str, str]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT status, detail FROM memory_relations WHERE campaign_id = %s"
                " AND source = 'worked_out'",
                (self.campaign.id,),
            )
            return [(str(r["status"]), str(r["detail"])) for r in await cur.fetchall()]

    async def test_it_asks_in_plain_words_and_says_nothing_is_remembered_yet(self) -> None:
        text = (await self.card()).text()
        self.assertIn("Is Aldric a parent of Mira?", text)
        self.assertIn("Nobody said this", text)
        self.assertIn("Nothing is remembered until you say yes", text)

    async def test_yes_confirms_it(self) -> None:
        view = await self.card()
        await next(b for b in view.children if b.label.startswith("✅")).callback(self.it(DM))
        self.assertEqual(await self.rows(), [(CONFIRMED, "parent")])

    async def test_no_is_final(self) -> None:
        view = await self.card()
        await next(b for b in view.children if b.label.startswith("❌")).callback(self.it(DM))
        self.assertEqual(await self.rows(), [(REJECTED, "parent")])
        await self.memory.rework_family(GUILD, self.campaign.id)
        self.assertEqual(await self.rows(), [(REJECTED, "parent")])

    async def test_a_player_cannot_answer(self) -> None:
        view = await self.card()
        await next(b for b in view.children if b.label.startswith("✅")).callback(self.it(99))
        self.assertEqual(await self.rows(), [(PROPOSED, "parent")])

    async def test_a_failed_check_before_a_yes_saves_nothing(self) -> None:
        view = await self.card()
        with patch.object(
            self.memory, "rework_family", AsyncMock(side_effect=RuntimeError("down"))
        ):
            it = self.it(DM)
            await next(b for b in view.children if b.label.startswith("✅")).callback(it)
        self.assertEqual(await self.rows(), [(PROPOSED, "parent")])  # asked again, not confirmed
