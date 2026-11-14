"""The names and things the table knows (#3244): the only list the theatrical transcript's AI is
given. A secret entry never appears. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import CONFIRMED
from tests.test_memory_names import GUILD, NamesTest


class TheTablesNames(NamesTest):
    async def add(self, name: str, **kw: Any) -> str:
        kw.setdefault("type", "npc")
        written = await self.memory.add_entity(
            GUILD, self.campaign.id, name=name, source="dm", status=CONFIRMED, **kw
        )
        return str(written.value.id)

    async def test_a_public_name_is_listed_and_a_hidden_one_never(self) -> None:
        await self.add("Tavin", type="character", role="player_character", played_by=8)
        await self.add("Mira", type="character", role="player_character", played_by=9)
        await self.add("Kell", type="character", role="npc")
        rook = await self.add("Rook", type="character", role="npc")
        await self.memory.set_hidden_from_players(GUILD, self.campaign.id, rook, True, source="dm")
        names = await self.memory.table_names(GUILD, self.campaign.id)
        self.assertIn("Kell", names)
        self.assertNotIn("Rook", names)
        self.assertEqual(names, sorted(set(names)))

    async def test_a_campaign_with_nothing_has_no_names(self) -> None:
        self.assertEqual(await self.memory.table_names(GUILD, self.campaign.id), [])
