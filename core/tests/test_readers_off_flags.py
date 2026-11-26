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

    async def test_one_who_joined_during_the_live_session_is_at_the_table(self) -> None:
        # (Cara joined after the live session began. She sits at the table, so the table's answer
        # is for her too, and she doesn't know it: the careful side, Supervisor's ruling on #4878)
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
        self.assertNotIn(self.npc, got.ids)
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

    async def test_a_session_with_no_party_is_common_knowledge_only(self) -> None:
        # (`party_at` with no member fails closed, Supervisor's ruling on #4806)
        self.assertEqual(gate.Viewer.party_at([]), gate.Viewer.the_world())
        got = await RecallService(self.memory).recall(
            ALONE, self.c, seeds=["Harbourmaster", "Saltmarsh"], audience=PLAYERS,
            viewer=gate.Viewer.party_at([]),
        )  # fmt: skip
        self.assertEqual(got.ids & {self.world, self.shown}, {self.world})

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


class AFactionsParty(Party):
    """Vell and Tuck are both of the Gray Hand; the Hand knows a secret the DM hid. A group's row
    on a hidden statement never reaches a player character (Supervisor's ruling on #4806), as a
    common row is masked; shown again, it does."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.add_entity(
            GUILD, self.c, name="The Gray Hand", type="faction", status=CONFIRMED, source=DM
        )
        self.hand = str(made.value.id)
        for pc in (self.vell, self.tuck):
            await self.memory.add_relation(
                GUILD, self.c, pc, "member_of", self.hand, source=DM, confidence=1.0,
                status=CONFIRMED,
            )  # fmt: skip
        made = await self.memory.add_entity(
            GUILD, self.c, name="The vault", type="place", status=CONFIRMED, source=DM
        )
        self.vault = str(made.value.id)
        await self.memory.set_statement_knowers(
            GUILD, self.c, "entry", self.vault, [self.hand], source=DM, dm_chose_secret=True
        )

    async def sure(self, viewer: gate.Viewer) -> float | None:
        seen = await self.memory.visible_to(GUILD, self.c, viewer)
        return seen.how_sure(gate.ENTRY, self.vault)

    async def test_a_hidden_statement_doesnt_reach_the_factions_members(self) -> None:
        self.assertIsNotNone(await self.sure(gate.Viewer.the_party()))  # (shown: the Hand's)
        await self.memory.set_hidden_from_players(
            GUILD, self.c, self.vault, True, source=DM, dm_chose_secret=True
        )
        self.assertIsNone(await self.sure(gate.Viewer.the_party()))
        self.assertIsNone(await self.sure(gate.Viewer.player([self.vell])))
        got = await RecallService(self.memory).recall(
            GUILD, self.c, seeds=["The vault"], audience=PLAYERS
        )
        self.assertNotIn(self.vault, got.ids)
        self.assertIsNotNone(await self.sure(gate.Viewer.player([self.hand])))  # (its own row)
        await self.memory.set_hidden_from_players(
            GUILD, self.c, self.vault, False, source=DM, dm_chose_secret=True
        )
        self.assertIsNotNone(await self.sure(gate.Viewer.the_party()))  # (shown again)

    async def hide_the_vault(self) -> None:
        await self.memory.set_hidden_from_players(
            GUILD, self.c, self.vault, True, source=DM, dm_chose_secret=True
        )

    async def test_an_npc_member_still_gets_it_and_a_pc_who_left_does_not(self) -> None:
        made = await self.memory.add_entity(
            GUILD, self.c, name="Ash", type="character", role="npc", status=CONFIRMED, source=DM
        )
        ash = str(made.value.id)
        await self.memory.add_relation(
            GUILD, self.c, ash, "member_of", self.hand, source=DM, confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        await self.hide_the_vault()
        self.assertIsNotNone(await self.sure(gate.Viewer.character(ash)))  # (the DM seeing as Ash)
        await self.memory.leave_party(GUILD, self.c, self.tuck, "left")
        self.assertIsNone(await self.sure(gate.Viewer.character(self.tuck)))

    async def test_a_connection_to_a_hidden_entry_doesnt_reach_them(self) -> None:
        rel = await self.memory.add_relation(
            GUILD, self.c, self.hand, "located_in", self.vault, source=DM, confidence=1.0,
            status=CONFIRMED, known_by=[self.hand],
        )  # fmt: skip
        tie = str(rel.value[0].id)
        seen = await self.memory.visible_to(GUILD, self.c, gate.Viewer.player([self.vell]))
        self.assertIsNotNone(seen.how_sure(gate.CONNECTION, tie))
        await self.hide_the_vault()
        seen = await self.memory.visible_to(GUILD, self.c, gate.Viewer.player([self.vell]))
        self.assertIsNone(seen.how_sure(gate.CONNECTION, tie))

    async def test_a_secret_the_dm_gave_the_faction_still_reaches_its_members(self) -> None:
        # (a connection given to some, a group among them, is not a hide: "Only some people know
        # this" is how the DM gives it to the faction)
        made = await self.memory.add_entity(
            GUILD, self.c, name="The old mine", type="place", status=CONFIRMED, source=DM
        )
        rel = await self.memory.add_relation(
            GUILD, self.c, self.hand, "located_in", str(made.value.id), source=DM,
            confidence=1.0, status=CONFIRMED, secret=True, known_by=[self.hand],
        )  # fmt: skip
        seen = await self.memory.visible_to(GUILD, self.c, gate.Viewer.player([self.vell]))
        self.assertIsNotNone(seen.how_sure(gate.CONNECTION, str(rel.value[0].id)))
