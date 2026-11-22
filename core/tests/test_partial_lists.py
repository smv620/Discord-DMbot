"""A list that names only some characters adds to who knows something and never takes it from the
rest (#4977): an upload's secret spelling, known by the uploader's character, keeps everyone the DM
told; and the DM's 👥 pick on a shown connection, which offers the party's characters only, keeps
an NPC who knew it. Invented names."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from dmbot.memory import claims, knowers
from dmbot.memory._changes import RELATIONS
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from tests.test_memory_store import GUILD_A as GUILD
from tests.test_memory_store import ConfirmedMemoryTest


class PartialLists(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tala = await self.add("Tala", type="character", role="player_character", played_by=55)
        self.mira = await self.add("Mira", type="character", role="player_character", played_by=66)
        self.brin = await self.add("Brin", type="character", role="npc")
        self.oska = await self.add("Oska", type="character", role="npc")

    async def rows(self, column: str, item_id: str) -> set[str]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT p.entity_id FROM memory_knowers k"
                " JOIN memory_perspectives p ON p.id = k.perspective_id"
                f" WHERE k.campaign_id = %s AND k.{column} = %s",
                (self.c, item_id),
            )
            return {str(r["entity_id"]) for r in await cur.fetchall()}

    async def test_an_uploads_secret_name_keeps_everyone_the_dm_told(self) -> None:
        from dmbot.ui.name_background import _secret_names_known_by

        await self.memory.add_alias(
            GUILD, self.c, self.oska, "the Grey Hand", kind="nickname", source=DM_SOURCE,
            status=CONFIRMED, secret=True, dm_chose_secret=True,
        )  # fmt: skip
        (alias,) = [
            a
            for a in await self.memory.aliases(
                GUILD, self.c, entity_id=self.oska, include_secret=True
            )
            if a.secret
        ]
        await self.memory.set_alias_knowers(
            GUILD, self.c, alias.id, [self.mira, self.brin], source=DM_SOURCE,
            dm_chose_secret=True,
        )  # fmt: skip
        campaign = cast(Any, SimpleNamespace(guild_id=GUILD, id=self.c))
        await _secret_names_known_by(self.memory, campaign, self.oska, ["the Grey Hand"], self.tala)
        self.assertEqual(await self.rows("alias_id", alias.id), {self.tala, self.mira, self.brin})

    async def test_the_party_pick_on_a_shown_connection_keeps_an_npc_who_knew_it(self) -> None:
        made = await self.relate(self.tala, "knows", self.oska, status=CONFIRMED)
        link = made.value[0]
        await self.memory.add_guessed_knowers(  # (an NPC with a row: DMbot's guess, #2959)
            GUILD, self.c, "connection", link.id, {self.brin: 0.9}, source=DM_SOURCE
        )
        async with self.memory._write(GUILD, self.c, DM_SOURCE, dm_chose_secret=True) as w:
            await w.update(RELATIONS, link.id, {"unrevealed": True})  # (as the claim does first)
            await claims._narrow_to(w, link.id, [self.mira])
        got = await self.rows("relation_id", link.id)
        self.assertIn(self.brin, got)  # (the NPC still knows it)
        self.assertIn(self.mira, got)
        async with self.memory._read(GUILD, self.c) as scope:
            pcs = (await knowers.connection_knowers(scope, [link.id])).get(link.id, [])
        self.assertEqual(pcs, [self.mira])  # (exactly the party characters the DM ticked)
