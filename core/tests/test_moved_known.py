"""A connection that moves to other ends in a merge or join is not known to a player's character
about those ends (#3073): what the characters knew goes when an end is hidden or was unnamed, and
a merge between two shown entries keeps everything. Invented names."""

from __future__ import annotations

from dmbot.memory.gate import CONNECTION
from dmbot.memory.models import CONFIRMED
from tests.test_gate import Helpers
from tests.test_memory_store import GUILD_A
from tests.test_same_person_join import JoinBase


class MergeMovesConnections(Helpers):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ids = await self.table()
        self.pc = self.ids["okthandom"]
        self.captain = await self.add(
            "Captain Vale", type="character", role="npc", status=CONFIRMED
        )

    async def connect(self, a: str) -> str:
        made = await self.memory.add_relation(
            GUILD_A,
            self.c,
            a,
            "knows",
            self.captain,
            source="dm",
            confidence=1.0,
            status=CONFIRMED,
            secret=True,
        )
        return str(made.value[0].id)

    async def knowers(self, rel: str) -> list[str]:
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [rel])
        return sorted(got.get(rel, []))

    async def believes(self, rel: str) -> bool:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM (SELECT b.confidence FROM memory_beliefs b"
                " JOIN memory_perspectives p ON p.guild_id = b.guild_id"
                " AND p.campaign_id = b.campaign_id AND p.id = b.perspective_id"
                " WHERE b.guild_id = %s AND b.campaign_id = %s AND b.relation_id = %s"
                " AND p.entity_id = %s ORDER BY b.seq DESC LIMIT 1) now"
                " WHERE now.confidence >= 1.0) AS known",
                (GUILD_A, self.c, rel, self.pc),
            )
            row = await cur.fetchone()
            return bool(row and row["known"])

    async def test_a_shown_entry_merged_into_a_hidden_one_is_forgotten_and_undo_restores_it(
        self,
    ) -> None:
        shown = await self.add("Arlo", type="character", role="npc", status=CONFIRMED)
        hidden = await self.add(
            "Brin", type="character", role="npc", status=CONFIRMED, hidden_from_players=True
        )
        rel = await self.connect(shown)
        await self.memory.set_connection_knowers(GUILD_A, self.c, rel, [self.pc], source="dm")
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))
        written = await self.memory.merge(
            GUILD_A, self.c, hidden, shown, source="dm", dm_said_same=True
        )
        self.assertEqual(await self.knowers(rel), [])
        self.assertFalse(await self.believes(rel))
        batch = written.batch
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))

    async def test_a_merge_between_two_shown_entries_keeps_everything(self) -> None:
        one = await self.add("Arlo", type="character", role="npc", status=CONFIRMED)
        two = await self.add("Arlo the Bold", type="character", role="npc", status=CONFIRMED)
        rel = await self.connect(one)
        await self.memory.set_connection_knowers(GUILD_A, self.c, rel, [self.pc], source="dm")
        await self.memory.merge(GUILD_A, self.c, two, one, source="dm", dm_said_same=True)
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))


class JoinMovesBeliefs(JoinBase):
    async def test_a_belief_in_the_guards_connection_is_not_listed_under_kens_name(self) -> None:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, self.guard, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        rel = str(made.value[0].id)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, rel, [self.sees_guard], source="dm"
        )
        # (the guard is unnamed: the reader shows nothing under a name that is not confirmed)
        written = await self.join()
        mine = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        self.assertEqual(mine.connections, ())
        batch = written.batch  # type: ignore[attr-defined]
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [rel])
        self.assertEqual(got.get(rel), [self.sees_guard])


class JoinFoldsIntoTwin(JoinBase):
    async def test_a_hidden_twin_is_not_handed_the_guards_knowers(self) -> None:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        twin = await self.memory.add_relation(
            GUILD_A, self.c, self.ken, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        mine = await self.memory.add_relation(
            GUILD_A, self.c, self.guard, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, str(mine.value[0].id), [self.sees_guard], source="dm"
        )
        await self.join()
        twin_id = str(twin.value[0].id)
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [twin_id])
        self.assertEqual(got.get(twin_id, []), [])
