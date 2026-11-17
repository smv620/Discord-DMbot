"""The tag on the names-to-check question and the DM's card (#3431 ruling 2, #3432 parts 2/3).
Stand-in store, invented names."""

from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory import tags
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import Entity
from dmbot.ui import name_card, names
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest


def _entity(name: str, *, description: str = "heard 3 times", status: str = "proposed") -> Entity:
    return Entity(
        id="e" * 32,
        type="character",
        name=name,
        description=description,
        status=status,
        merged_into=None,
        source="scan",
        created_at=0,
    )


CAMPAIGN: Any = SimpleNamespace(guild_id=1, id="c" * 32)
PLACE = tags.TagTie("located_in", "Termalaine", confirmed=True)


def _memory(ties: list[tags.TagTie]) -> Any:
    return SimpleNamespace(tag_ties=AsyncMock(return_value={"e" * 32: ties}))


class TheNamesToCheckQuestion(unittest.IsolatedAsyncioTestCase):
    async def test_a_description_is_shown_with_the_place_it_was_seen(self) -> None:
        shown = await names.tagged_name(_memory([PLACE]), CAMPAIGN, _entity("beggar woman"))
        self.assertEqual(shown, "beggar woman (in Termalaine)")

    async def test_the_heard_count_is_not_used_as_a_tag(self) -> None:
        # (the count already sits beside the name; a tag from it would say it twice)
        shown = await names.tagged_name(_memory([]), CAMPAIGN, _entity("beggar woman"))
        self.assertEqual(shown, "")

    async def test_a_proper_name_has_no_tag_and_no_lookup(self) -> None:
        memory = _memory([PLACE])
        self.assertEqual(await names.tagged_name(memory, CAMPAIGN, _entity("Mira Vane")), "")
        memory.tag_ties.assert_not_awaited()

    def test_the_question_title_uses_the_tag(self) -> None:
        text = names.suggestion_text(
            _entity("beggar woman"), 1, tagged="beggar woman (in Termalaine)"
        )
        self.assertTrue(text.startswith("📝 **beggar woman (in Termalaine)**"))
        plain = names.suggestion_text(_entity("beggar woman"), 1)
        self.assertTrue(plain.startswith("📝 **beggar woman**"))


class TheCardTitle(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.woman = await self.add("beggar woman")
        self.dock = await self.add("Termalaine", type="place")
        await self.memory.add_relation(
            GUILD_A, self.c, self.woman, "located_in", self.dock,
            source="dm", confidence=1.0, status="confirmed",
        )  # fmt: skip

    async def card(self, entity_id: str, title: str) -> str:
        view = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        shown: Any = view.for_viewer(True)
        text = name_card.card_text(shown, entity_id, [], secrets=True, title=title)
        assert text is not None
        return text

    async def test_the_card_title_is_the_tag_when_given(self) -> None:
        text = await self.card(self.woman, "beggar woman (in Termalaine)")
        self.assertIn("🪪 **beggar woman (in Termalaine)**", text)

    async def test_the_tag_is_built_from_the_confirmed_place(self) -> None:
        woman = next(e for e in await self.memory.entities(GUILD_A, self.c) if e.id == self.woman)
        ties = await self.memory.tag_ties(GUILD_A, self.c, [self.woman])
        self.assertEqual(
            tags.tag_for(woman, ties[self.woman], dm=True), "beggar woman (in Termalaine)"
        )

    async def test_a_place_she_left_is_never_her_tag(self) -> None:
        # (#4134: a tag says who they are now)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_relations SET to_session_at = 5 WHERE subject_id = %s",
                (self.woman,),
            )
        woman = next(e for e in await self.memory.entities(GUILD_A, self.c) if e.id == self.woman)
        ties = await self.memory.tag_ties(GUILD_A, self.c, [self.woman])
        self.assertNotIn("Termalaine", tags.tag_for(woman, ties.get(self.woman, []), dm=True))

    async def test_the_tag_is_looked_up_only_for_the_dm(self) -> None:
        # show_card asks for ties only when `secrets` is true (a player's card keeps the name)
        source = inspect.getsource(name_card.show_card)
        self.assertIn("secrets and tags.needs_tag(entity)", source)
