"""A connection's first knowers, added when the DM writes it again or a backstory tie is kept,
add to who knows it and never drop an NPC who already did (#4972: the list there is player
characters only). Invented names."""

from __future__ import annotations

from dmbot.memory import link_edits
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from tests.test_memory_store import GUILD_A as GUILD
from tests.test_memory_store import ConfirmedMemoryTest


class FirstKnowers(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tala = await self.add("Tala", type="character", role="player_character", played_by=55)
        self.brin = await self.add("Brin", type="character", role="npc")
        self.oska = await self.add("Oska", type="character", role="npc")
        self.vell = await self.add("Vell", type="character", role="npc")
        made = await self.relate(self.brin, "knows", self.oska, status=CONFIRMED)
        link = made.value[0]
        await link_edits.set_who_knows(  # (hidden, and only Vell, an NPC, knows it)
            self.memory, GUILD, self.c, link, "some", [self.vell]
        )
        self.link_id = link.id

    async def knowers(self) -> set[str]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT p.entity_id FROM memory_knowers k"
                " JOIN memory_perspectives p ON p.id = k.perspective_id"
                " WHERE k.campaign_id = %s AND k.relation_id = %s",
                (self.c, self.link_id),
            )
            return {str(r["entity_id"]) for r in await cur.fetchall()}

    async def test_written_again_with_a_player_who_knows_it_keeps_the_npc(self) -> None:
        await self.relate(
            self.brin, "knows", self.oska, status=CONFIRMED, source=DM_SOURCE,
            known_by=[self.tala],
        )  # fmt: skip
        self.assertEqual(await self.knowers(), {self.vell, self.tala})
