"""The 📝 Check new names button offers "Add all" whenever two or more guessed names qualify, of
any kinds, and a name that sounds like a known one stays out of it (#3683)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from dmbot.memory.models import PROPOSED
from dmbot.ui import names as ui


def entity(eid: str, name: str, etype: str, role: str = "") -> Any:
    return SimpleNamespace(
        id=eid, name=name, type=etype, role=role, source="scan", status=PROPOSED, description=""
    )


class TheOffer(unittest.IsolatedAsyncioTestCase):
    async def offer(
        self, waiting: list[Any], alike: set[str] | None = None
    ) -> tuple[bool, Any, str]:
        sent: list[Any] = []

        async def send(_it: object, text: str, view: Any) -> None:
            sent.append((text, view))

        async def match(_memory: object, _campaign: object, e: Any) -> object | None:
            return object() if e.id in (alike or set()) else None

        campaign = SimpleNamespace(id="c1", guild_id=1)
        with (
            patch.object(ui, "_send", send),
            patch.object(ui, "_review_match", match),
            patch.object(ui, "spelled_fix", lambda _e: False),
        ):
            offered = await ui._offer_add_all(
                cast(Any, AsyncMock()), cast(Any, SimpleNamespace()), cast(Any, campaign), waiting
            )
        text, view = sent[0] if sent else ("", None)
        return offered, view, text

    async def test_names_of_different_kinds_get_the_button(self) -> None:
        # (it used to need two of the SAME kind)
        offered, view, _ = await self.offer(
            [entity("a", "Karn", "place"), entity("b", "Mira", "character", "npc")]
        )
        self.assertTrue(offered)
        labels = [getattr(c, "label", "") for c in view.children]
        self.assertIn("✅ Add all 2", labels)

    async def test_three_names_one_sounding_like_a_known_one_still_offer_the_two(self) -> None:
        offered, view, text = await self.offer(
            [
                entity("a", "Karn", "place"),
                entity("b", "Mira", "character", "npc"),
                entity("c", "Marin", "character", "npc"),
            ],
            alike={"c"},
        )
        self.assertTrue(offered)
        self.assertEqual(sorted(i for ids in view.ids.values() for i in ids), ["a", "b"])
        self.assertIn("1 name that sounds like one you know stays for you", text)
        self.assertIn("✅ Add all 2", [getattr(c, "label", "") for c in view.children])

    async def test_one_qualifying_name_has_no_button_and_ideas_and_player_characters_stay_out(
        self,
    ) -> None:
        offered, view, _ = await self.offer(
            [
                entity("a", "Karn", "place"),
                entity("b", "Tess", "character", "player_character"),
                entity("c", "Fate", "concept"),
            ]
        )
        self.assertFalse(offered)
        self.assertIsNone(view)

    async def test_two_of_one_kind_still_works(self) -> None:
        offered, view, _ = await self.offer(
            [entity("a", "Karn", "place"), entity("b", "Dell", "place")]
        )
        self.assertTrue(offered)
        self.assertEqual(view.ids, {"place": ["a", "b"]})

    async def test_two_that_sound_like_known_ones_say_stay_not_stays(self) -> None:
        _, _, text = await self.offer(
            [
                entity("a", "Karn", "place"),
                entity("b", "Dell", "place"),
                entity("c", "Marin", "character", "npc"),
                entity("d", "Maro", "character", "npc"),
            ],
            alike={"c", "d"},
        )
        self.assertIn("2 names that sound like one you know stay for you", text)
