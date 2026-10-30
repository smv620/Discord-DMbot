"""The DM's card for a possible match (#3037): plain words, the promise that nothing new is shown
to players, yes joins, no is remembered, only a DM answers, and after a yes every player view shows
nothing it did not before. Invented names."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory.models import CONFIRMED
from dmbot.memory.recall import PLAYERS, RecallService
from dmbot.ui.same_person_questions import KEEPS, SamePersonQuestions
from tests.test_memory_names import DM, GUILD, NamesTest


class TheMatchCard(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        made = await self.memory.add_unnamed(
            GUILD, self.campaign.id, what="guard at the north gate", role="guard"
        )
        self.guard = made.entity.id
        self.ken = (
            await self.memory.add_entity(
                GUILD, self.campaign.id, name="Ken", type="character", role="npc",
                status=CONFIRMED, source="dm", description="A guard who keeps the north gate",
            )
        ).value.id  # fmt: skip
        self.captain = (
            await self.memory.add_entity(
                GUILD, self.campaign.id, name="Captain Vale", type="character", role="npc",
                status=CONFIRMED, source="dm",
            )
        ).value.id  # fmt: skip
        await self.memory.add_relation(
            GUILD, self.campaign.id, self.guard, "knows", self.captain, source="dm",
            confidence=1.0, status=CONFIRMED,
        )  # fmt: skip

    async def card(self) -> Any:
        view = SamePersonQuestions(self.campaign.id)
        self.assertTrue(await view.load(self.it(DM)))
        return view

    def press(self, view: Any, label: str) -> Any:
        return next(b for b in view.children if b.label.startswith(label))

    async def player_views(self) -> dict[str, Any]:
        """What the players' readers give: connections, other names, the roster, the recall the
        table answers and recaps are built from."""
        gid, cid = GUILD, self.campaign.id
        relations = await self.memory.relations(
            gid, cid, entity_id=self.ken, confirmed_only=True, include_secret=False
        )
        names = await self.memory.aliases(gid, cid, entity_id=self.ken, include_secret=False)
        roster = await RecallService(self.memory).roster(gid, cid)
        recall = await RecallService(self.memory).recall(
            gid, cid, seeds=[self.ken], audience=PLAYERS, opted_in={7}
        )
        return {
            "relations": sorted(r.id for r in relations),
            "names": sorted(a.text for a in names),
            "roster": sorted((r.name, r.also_called) for r in roster),
            "recall": sorted((i.kind, i.text) for i in recall.items),
        }

    async def test_the_card_asks_in_plain_words_and_promises_nothing_new_for_players(self) -> None:
        text = (await self.card()).text()
        self.assertIn("Is guard at the north gate", text)
        self.assertIn("the same as Ken?", text)
        self.assertIn(KEEPS, text)
        self.assertEqual(
            KEEPS, "Joining them keeps what each player knew. Nothing new is shown to players."
        )

    async def test_yes_joins_them_and_no_player_view_shows_anything_new(self) -> None:
        before = await self.player_views()
        view = await self.card()
        await self.press(view, "✅").callback(self.it(DM))
        self.assertEqual(await self.player_views(), before)
        self.assertEqual(await self.memory.unnamed(GUILD, self.campaign.id), [])  # joined
        secret = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=self.ken, include_secret=True
        )
        self.assertTrue(any(a.secret and "guard at the north gate" in a.text for a in secret))

    async def test_no_is_remembered_and_asked_no_more(self) -> None:
        view = await self.card()
        await self.press(view, "❌").callback(self.it(DM))
        self.assertEqual(await self.memory.same_person_offers(GUILD, self.campaign.id), [])
        again = SamePersonQuestions(self.campaign.id)
        self.assertFalse(await again.load(self.it(DM)))

    async def test_a_player_cannot_answer(self) -> None:
        view = await self.card()
        await self.press(view, "✅").callback(self.it(99))
        self.assertEqual(len(await self.memory.unnamed(GUILD, self.campaign.id)), 1)  # not joined

    async def test_a_player_is_not_asked(self) -> None:
        view = SamePersonQuestions(self.campaign.id)
        self.assertFalse(await view.load(self.it(99)))

    async def test_a_refusal_says_what_is_in_the_way_and_the_card_stays(self) -> None:
        await self.memory.add_unnamed(
            GUILD, self.campaign.id, what="dog", anchor_id=self.guard, role="pet"
        )
        view = await self.card()
        it = self.it(DM)
        await self.press(view, "✅").callback(it)
        self.assertEqual(len(await self.memory.unnamed(GUILD, self.campaign.id)), 2)  # not joined
        told = " ".join(str(c) for c in it.followup.send.await_args_list)
        self.assertIn("dog", told)
