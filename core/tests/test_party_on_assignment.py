"""The party is linked when the DM assigns the players, while the campaign has had no session or
import (#3036, Supervisor's ruling 2026-11-02): a lore-only start. After the first session the
"took part" rule applies again. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import CONFIRMED, REJECTED
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

PLAYERS = (101, 102, 103, 104)


class TheParty(ConfirmedMemoryTest):
    async def pc(self, name: str, player: int) -> Any:
        """A player character given to its player, then the step the assign screens run."""
        made = await self.add(
            name, type="character", role="player_character", played_by=player, status=CONFIRMED
        )
        await self.memory.party_on_assignment(GUILD_A, self.c)
        return made

    async def rows(self, predicate: str, *, status: str | None = None) -> list[Any]:
        """Every row of this kind, whatever its status (a removed one is still a row)."""
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id, status FROM memory_relations WHERE campaign_id = %s AND predicate = %s",
                (self.c, predicate),
            )
            found = await cur.fetchall()
        return [r for r in found if status is None or r["status"] == status]

    async def test_assigning_four_players_one_by_one_ends_with_six_pairs_and_four_members(
        self,
    ) -> None:
        for name, player in zip(("Aldric", "Bryn", "Cora", "Dain"), PLAYERS, strict=True):
            await self.pc(name, player)
        self.assertEqual(len(await self.rows("knows")), 6)
        self.assertEqual(len(await self.rows("member_of")), 4)

    async def test_an_npc_is_never_linked(self) -> None:
        await self.add("Greta", type="character", role="npc", status=CONFIRMED)
        await self.pc("Aldric", PLAYERS[0])
        await self.pc("Bryn", PLAYERS[1])
        self.assertEqual(len(await self.rows("knows")), 1)

    async def test_after_the_first_session_assigning_a_player_adds_membership_only(self) -> None:
        await self.pc("Aldric", PLAYERS[0])
        await self.pc("Bryn", PLAYERS[1])
        self.assertEqual(len(await self.rows("knows")), 1)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
                " VALUES (%s, %s, %s, 100)",
                ("e" * 32, GUILD_A, self.c),
            )
        await self.pc("Cora", PLAYERS[2])
        self.assertEqual(len(await self.rows("knows")), 1)  # (she meets them at a session)
        self.assertEqual(len(await self.rows("member_of")), 3)  # (but she is in the party now)

    async def test_a_pair_the_dm_removed_is_not_added_again(self) -> None:
        await self.pc("Aldric", PLAYERS[0])
        await self.pc("Bryn", PLAYERS[1])
        (pair,) = await self.rows("knows")
        await self.memory.update_relation(GUILD_A, self.c, pair["id"], source="dm", status=REJECTED)
        await self.pc("Cora", PLAYERS[2])
        knows = await self.rows("knows")
        self.assertEqual(len(await self.rows("knows", status=REJECTED)), 1)
        self.assertEqual(len(knows), 3)  # (the removed pair, and Cora with each of the two)

    async def test_assigning_the_same_player_again_changes_nothing(self) -> None:
        a = await self.pc("Aldric", PLAYERS[0])
        await self.pc("Bryn", PLAYERS[1])
        before = (len(await self.rows("knows")), len(await self.rows("member_of")))
        await self.memory.confirm_entity(
            GUILD_A, self.c, a, "player_character", source="dm", played_by=PLAYERS[0]
        )
        await self.memory.party_on_assignment(GUILD_A, self.c)
        self.assertEqual((len(await self.rows("knows")), len(await self.rows("member_of"))), before)
