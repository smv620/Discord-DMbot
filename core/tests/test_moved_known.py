"""A connection that moves to other ends in a plain merge is not known to a player's character about
an end kept from them (#3073), and a merge between two shown entries keeps everything; a join keeps
whoever knew (Supervisor's ruling on #3037). Invented names."""

from __future__ import annotations

from dmbot.memory.gate import CONNECTION, ENTRY
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

    async def connect(self, a: str, *, secret: bool = True) -> str:
        made = await self.memory.add_relation(
            GUILD_A,
            self.c,
            a,
            "knows",
            self.captain,
            source="dm",
            confidence=1.0,
            status=CONFIRMED,
            secret=secret,
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


class MergeTwinFold(MergeMovesConnections):
    """Two facts that say the same thing fold into one in a plain merge (#3073)."""

    async def pc_perspective(self) -> str:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s",
                (GUILD_A, self.c, self.pc),
            )
            row = await cur.fetchone()
            assert row is not None
            return str(row["id"])

    async def test_a_belief_is_not_carried_onto_a_twin_the_players_were_not_shown(self) -> None:
        shown = await self.add("Arlo", type="character", role="npc", status=CONFIRMED)
        hidden = await self.add(
            "Brin", type="character", role="npc", status=CONFIRMED, hidden_from_players=True
        )
        folded = await self.connect(shown, secret=False)
        twin = await self.connect(hidden, secret=False)
        await self.memory.believe(
            GUILD_A, self.c, await self.pc_perspective(), folded, 1.0, "revealed_fact", source="dm"
        )
        self.assertTrue(await self.believes(folded))
        written = await self.memory.merge(
            GUILD_A, self.c, hidden, shown, source="dm", dm_said_same=True
        )
        self.assertFalse(await self.believes(twin))  # (Brin's connection is not the players')
        batch = written.batch
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        self.assertTrue(await self.believes(folded))
        self.assertFalse(await self.believes(twin))

    async def test_two_shown_facts_that_fold_keep_the_dms_list_on_the_twin(self) -> None:
        one = await self.add("Arlo", type="character", role="npc", status=CONFIRMED)
        two = await self.add("Arlo the Bold", type="character", role="npc", status=CONFIRMED)
        twin = await self.connect(two, secret=False)
        folded = await self.connect(one, secret=False)
        mara = self.ids["mara"]
        await self.know(CONNECTION, twin, [mara])
        await self.know(CONNECTION, folded, [self.pc])
        await self.memory.merge(GUILD_A, self.c, two, one, source="dm", dm_said_same=True)
        self.assertEqual(await self.knowers(twin), sorted([self.pc, mara]))

    async def test_an_audience_limited_list_is_kept_through_a_merge(self) -> None:
        one = await self.add("Silver key", type="clue", status=CONFIRMED)
        two = await self.add("The silver key", type="clue", status=CONFIRMED)
        await self.memory.add_relation(
            GUILD_A, self.c, self.pc, "knows", one, source="dm", confidence=1.0, status=CONFIRMED
        )
        await self.memory.set_audience_limited(GUILD_A, self.c, one, True, source="dm")
        await self.know(ENTRY, one, [self.pc])  # (the DM's list, written once it is limited)
        await self.memory.merge(GUILD_A, self.c, two, one, source="dm", dm_said_same=True)
        got = await self.memory.statement_knowers(GUILD_A, self.c, ENTRY, [two])
        self.assertEqual(sorted(got.get(two, [])), [self.pc])  # (and still limited to them)
        kept = await self.memory.entity(GUILD_A, self.c, two)
        self.assertTrue(kept is not None and kept.audience_limited)


class JoinKeepsWhoKnew(JoinBase):
    """A join is not a plain merge (Supervisor's ruling on #3037): whoever knew the unnamed person's
    connection still knows it, through the secret other name, even onto a hidden Ken."""

    async def hidden_connection(self, a: str, secret: bool = True) -> str:
        if not hasattr(self, "captain"):
            self.captain = await self.add(
                "Captain Vale", type="character", role="npc", status=CONFIRMED
            )
        made = await self.memory.add_relation(
            GUILD_A, self.c, a, "knows", self.captain, source="dm", confidence=1.0,
            status=CONFIRMED, secret=secret,
        )  # fmt: skip
        return str(made.value[0].id)

    async def test_the_guards_knower_keeps_the_row_onto_a_hidden_ken_and_after_undo(self) -> None:
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.ken, True, source="dm")
        rel = await self.hidden_connection(self.guard)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, rel, [self.sees_guard], source="dm"
        )
        written = await self.join()
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [rel])
        self.assertEqual(got.get(rel), [self.sees_guard])
        batch = written.batch  # type: ignore[attr-defined]
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [rel])
        self.assertEqual(got.get(rel), [self.sees_guard])

    async def test_a_folded_fact_gives_its_knowers_to_a_hidden_twin(self) -> None:
        twin = await self.hidden_connection(self.ken)
        mine = await self.hidden_connection(self.guard)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, mine, [self.sees_guard], source="dm"
        )
        await self.join()
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [twin])
        self.assertEqual(got.get(twin), [self.sees_guard])
