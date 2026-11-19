"""The AI readers ask the gate, not the flags (#4537, party knowledge slice 5a;
`docs/design/party_knowledge.md` §9 rules 2 and 6; Supervisor's ruling on #4537, K to N): recall's
players' audience and meaning search answer for the table (the party at the running session, else
the current party), a newcomer not yet told is not answered with what the flags still call
"shown", an empty party sees only common knowledge, and no reader writes a row. Invented names and
ids only."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.memory import gate
from dmbot.memory.meaning import entry_texts
from dmbot.memory.models import CONFIRMED, DM
from dmbot.memory.recall import PLAYERS, RecallService
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest
from tests.test_party_join import CARA, GUILD, Party

ALONE = 700_000_000_000_000_041


class ANewcomer(Party):
    """Vell and Tuck know the harbourmaster; Cara joined and hasn't been told anything yet."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        async with self.db.guild(GUILD) as conn:  # (a description, so meaning search has text)
            await conn.execute(
                "UPDATE memory_entities SET description = 'Keeps the harbour' WHERE id = %s",
                (self.npc,),
            )
        self.cara = await self.pc("Cara", CARA)

    async def rows(self) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_knowers WHERE guild_id = %s", (GUILD,)
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def recall_ids(self, viewer: gate.Viewer | None = None) -> frozenset[str]:
        got = await RecallService(self.memory).recall(
            GUILD, self.c, seeds=["Harbourmaster"], audience=PLAYERS, viewer=viewer
        )
        return got.ids

    async def test_the_table_is_not_answered_with_what_the_newcomer_hasnt_been_told(self) -> None:
        self.assertIn(self.npc, await self.recall_ids(gate.Viewer.player([self.vell])))
        self.assertNotIn(self.npc, await self.recall_ids())  # (the table: Cara too, §8)
        texts = await entry_texts(self.db, GUILD, self.c, [self.npc], for_players=True)
        self.assertEqual(texts, {})
        mine = await entry_texts(
            self.db, GUILD, self.c, [self.npc], for_players=True,
            viewer=gate.Viewer.player([self.vell]),
        )  # fmt: skip
        self.assertEqual(set(mine), {self.npc})

    async def test_told_it_the_table_is_answered(self) -> None:
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        self.assertIn(self.npc, await self.recall_ids())

    async def test_a_hide_through_the_store_hides_it_from_the_table(self) -> None:
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        self.assertIn(self.npc, await self.recall_ids())
        await self.memory.set_hidden_from_players(
            GUILD, self.c, self.npc, True, source=DM, dm_chose_secret=True
        )
        self.assertNotIn(self.npc, await self.recall_ids())

    async def test_a_live_table_is_that_sessions_party(self) -> None:
        # (Cara joined after the live session began: its table is Vell and Tuck, who know it)
        sid = await self.sessions.open_session(GUILD, self.c, 20_000)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET line_count = 3, created_at = 20_000 WHERE id = %s",
                (sid,),
            )
        from dmbot.memory import party_record

        async with self.memory._write(GUILD, self.c, DM) as w:
            await party_record.move_start(w, self.cara, 20_500)
        got = await RecallService(self.memory).recall(
            GUILD, self.c, seeds=["Harbourmaster"], audience=PLAYERS, live_at=20_000
        )
        self.assertIn(self.npc, got.ids)
        self.assertNotIn(self.npc, await self.recall_ids())  # (no live table: today's party)

    async def test_no_reader_writes_a_row(self) -> None:
        before = await self.rows()
        await self.recall_ids()
        await RecallService(self.memory).roster(GUILD, self.c)
        await entry_texts(self.db, GUILD, self.c, [self.npc], for_players=True)
        self.assertEqual(await self.rows(), before)  # (§9 rule 6: never "repaired" by a reader)


class NoPartyYet(DatabaseTest):
    """A campaign with no player character: the players' audience sees only common knowledge,
    never "everything shown" (Supervisor's ruling M, fail closed)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.c = (
            await CampaignStore(self.db).create(ALONE, "Saltmarsh", 700_000_000_000_000_042)
        ).id
        self.shown = await self.made("Harbourmaster", "npc")
        self.world = await self.made("Saltmarsh", "place")
        await self.memory.add_common_knowledge(ALONE, self.c, "entry", [self.world], source=DM)

    async def made(self, name: str, kind: str) -> str:
        done = await self.memory.add_entity(
            ALONE, self.c, name=name, type=kind, status=CONFIRMED, source=DM
        )
        return str(done.value.id)

    async def test_only_common_knowledge_reaches_the_players(self) -> None:
        got = await RecallService(self.memory).recall(
            ALONE, self.c, seeds=["Harbourmaster", "Saltmarsh"], audience=PLAYERS
        )
        self.assertIn(self.world, got.ids)
        self.assertNotIn(self.shown, got.ids)

    async def test_a_common_entry_the_dm_hides_is_not_told(self) -> None:
        # (a hide keeps the common row on purpose, #4524: it grants nothing while hidden)
        await self.memory.set_hidden_from_players(
            ALONE, self.c, self.world, True, source=DM, dm_chose_secret=True
        )
        got = await RecallService(self.memory).recall(
            ALONE, self.c, seeds=["Saltmarsh"], audience=PLAYERS
        )
        self.assertNotIn(self.world, got.ids)
        texts = await entry_texts(self.db, ALONE, self.c, [self.world], for_players=True)
        self.assertEqual(texts, {})
