"""The "What was said" page's 👥 Some know (#4534): the answer on each card, the picker with
DMbot's guess ticked, Save as one change with one ↩️, and the layout every card keeps (✅ row 1,
👥 row 2, 🤫 row 3, ❌ row 4). Made-up names; no AI call."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.ui import claims_review
from tests.test_memory_store import DM, GUILD_A, ConfirmedMemoryTest


def said(subject: str, relationship: str, obj: str, how: str = "dm_said") -> Extracted:
    return Extracted(subject, relationship, obj, "", ("1",), how)


class SomeKnow(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.bram = await self.add("Bram", type="character")
        self.town = await self.add("Veldmoor", type="place")
        self.bow = await self.add("Heartseeker", type="item")
        self.wren = await self.pc("Wren", 7)
        self.ash = ""  # (made by `both`: with one character there is no "some")

    async def both(self) -> None:
        self.ash = await self.pc("Ash", 8)

    async def pc(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD_A, self.c, name=name, type="character", role="player_character",
            played_by=player, status="confirmed", source="dm",
        )  # fmt: skip
        return made.value.id

    async def ask(self, *found: Extracted, speaker: str = "7") -> None:
        await self.claims.propose(
            GUILD_A, self.c, list(found), {"1": int(speaker)}, opted_in={int(speaker)},
            prompt_version="p", model="m", session_started_at=1,
        )  # fmt: skip

    def interaction(self, user: int = DM) -> Any:
        sent: list[tuple[str, Any]] = []

        async def reply(text: str = "", **kw: Any) -> None:
            sent.append((str(kw.get("content", text)), kw.get("view")))

        it = MagicMock()
        it.user = SimpleNamespace(id=user)
        it.guild = SimpleNamespace(id=GUILD_A)
        it.client = SimpleNamespace(
            memory=self.memory,
            campaigns=self.campaigns,
            consent=SimpleNamespace(consenting=AsyncMock(return_value={7, 8})),
        )
        it.response.is_done = lambda: False
        it.response.defer = AsyncMock()
        it.response.send_message = reply
        it.response.edit_message = reply
        it.followup.send = reply
        it.edit_original_response = reply
        it.sent = sent
        return it

    async def page(self, *found: Extracted) -> tuple[Any, claims_review.ClaimsReview, str]:
        await self.ask(*found)
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c, GUILD_A)
        text = await view.current_text(it)
        assert text is not None
        return it, view, text

    async def facts(self) -> list[Any]:
        """The connections the answers wrote (not the "plays" ones the characters came with)."""
        every = await self.memory.relations(GUILD_A, self.c, include_secret=True)
        return [r for r in every if r.predicate != "plays"]

    @staticmethod
    def labels(view: Any, row: int) -> list[str]:
        return [str(getattr(c, "label", "")) for c in view.children if c.row == row]

    async def test_each_card_has_its_four_answers_on_their_own_rows(self) -> None:
        await self.both()
        _, view, _ = await self.page(said("Bram", "is in", "Veldmoor"))
        self.assertEqual(self.labels(view, 1), ["✅ 1"])
        self.assertEqual(self.labels(view, 2), ["👥 1"])
        self.assertEqual(self.labels(view, 3), ["🤫 1"])
        self.assertEqual(self.labels(view, 4), ["❌ 1"])

    async def test_a_state_has_no_audience_so_no_some_know_or_secret(self) -> None:
        await self.both()
        _, view, _ = await self.page(said("Bram", "is", "dead"))
        self.assertEqual(self.labels(view, 2), [])
        self.assertEqual(self.labels(view, 3), [])

    async def test_with_one_character_there_is_no_some(self) -> None:
        _, view, _ = await self.page(said("Bram", "is in", "Veldmoor"))
        self.assertEqual(self.labels(view, 2), [])

    async def test_the_card_says_who_dmbot_thinks_knows_it_before_the_dm_answers(self) -> None:
        await self.both()
        _, _, text = await self.page(said("Bram", "is in", "Veldmoor"))
        self.assertIn("👥 DMbot thinks only Wren knows this.", text)

    async def test_the_picker_opens_with_the_guess_ticked_and_changes_nothing(self) -> None:
        await self.both()
        it, view, _ = await self.page(said("Bram", "is in", "Veldmoor"))
        await view._pick(it, view.page[0].id)
        text, picker = it.sent[-1]
        self.assertIsInstance(picker, claims_review.KnowersPicker)
        ticked = [o.label for o in picker.select.options if o.default]
        self.assertEqual(ticked, ["Wren"])
        self.assertIn("Who knows this?", text)
        self.assertEqual(await self.facts(), [])  # (nothing written)

    async def test_save_answers_as_true_with_exactly_those_known_and_one_undo(self) -> None:
        await self.both()
        it, view, _ = await self.page(
            said("Bram", "is in", "Veldmoor"), said("Bram", "carries", "Heartseeker")
        )
        claim = view.page[0]
        await view._pick(it, claim.id)
        picker = it.sent[-1][1]
        picker.chosen = [self.ash, self.wren]
        await picker._save(it)
        text = it.sent[-1][0]
        self.assertIn(
            f"👥 Remembered: {claims_review.statement(claim)}. Known to: Ash, Wren.", text
        )
        (rel,) = await self.facts()
        self.assertTrue(rel.unrevealed)
        knows = await self.memory.statement_knowers(GUILD_A, self.c, "connection", [rel.id])
        self.assertCountEqual(knows.get(rel.id, []), [self.wren, self.ash])
        self.assertIn("↩️ 1", self.labels(view, 1))  # (the ✅ slot is the take-back now)
        await view._take_back(it, claim.id)
        knows = await self.memory.statement_knowers(GUILD_A, self.c, "connection", [rel.id])
        self.assertEqual(knows.get(rel.id, []), [])

    async def test_a_refusal_keeps_the_picker_and_says_why(self) -> None:
        await self.both()
        await self.memory.add_relation(
            GUILD_A, self.c, self.bram, "located_in", self.town, source="dm",
            status="confirmed", confidence=1.0,
        )  # fmt: skip
        it, view, _ = await self.page(said("Bram", "is in", "Veldmoor"))
        await view._pick(it, view.page[0].id)
        picker = it.sent[-1][1]
        picker.chosen = [self.wren]
        await picker._save(it)
        self.assertIn("see this already", it.sent[-1][0])
        self.assertFalse(picker.is_finished())
        self.assertNotIn(view.page[0].id, view.answered)

    async def test_back_returns_to_the_page_unchanged(self) -> None:
        await self.both()
        it, view, _ = await self.page(said("Bram", "is in", "Veldmoor"))
        await view._pick(it, view.page[0].id)
        picker = it.sent[-1][1]
        await picker._back(it)
        self.assertIs(it.sent[-1][1], view)
        self.assertEqual(await self.facts(), [])

    async def test_only_the_dm_gets_the_picker(self) -> None:
        await self.both()
        _it, view, _ = await self.page(said("Bram", "is in", "Veldmoor"))
        stranger = self.interaction(user=99)
        before = len(stranger.sent)
        await view._pick(stranger, view.page[0].id)
        self.assertFalse(any(isinstance(v, claims_review.KnowersPicker) for _, v in stranger.sent))
        self.assertEqual(len(stranger.sent), before + 1)  # (told no, once)


if __name__ == "__main__":
    unittest.main()
