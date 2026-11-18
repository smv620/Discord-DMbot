"""The party record (#4537, party knowledge slice 2a; `docs/design/party_knowledge.md` §§3 and 6;
Supervisor's ruling on #4537, 2026-11-18): migration 0549's backfill on tables that hold rows, a
new player character's stretch, "Has left the party" with its Undo, and "the party" as one function
(`party_record.current_members`, `members_at`) that the gate and the who-knows writes ask. Invented
names and ids only. Needs Postgres."""

from __future__ import annotations

import json
import unittest
import uuid
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory import party_record
from dmbot.memory.gate import ENTRY, Viewer
from dmbot.memory.models import CONFIRMED, PLAYER_CHARACTER, MemoryRuleError
from dmbot.memory.party_record import Span
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import party_card
from tests.pg import REQUIRE_DB, TEST_URL, DatabaseTest

GUILD, DM, ANNA, BEN, CARA = 4545, 99, 11, 12, 13
BACKFILL = "0549_party_spans"


class TheBackfill(unittest.IsolatedAsyncioTestCase):
    """0549 run on a campaign that holds rows, held back until they are written (CLAUDE.md,
    #704 and #781): every table it reads has rows the migration must see."""

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2,
            migrations=tuple(m for m in MIGRATIONS if m[0] != BACKFILL),
        )  # fmt: skip
        self.memory = MemoryStore(self.db)
        campaigns = CampaignStore(self.db)
        self.c = (await campaigns.create(GUILD, "Saltmarsh", DM)).id
        self.quiet = (await campaigns.create(GUILD, "No sessions", DM)).id
        self.version = 90_000

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def npc(self, campaign: str, name: str) -> str:
        """An entry made as an NPC (no stretch can be written before 0549): it is turned into
        a player character below by hand, as the rows the migration meets would be."""
        made = await self.memory.add_entity(
            GUILD, campaign, name=name, type="npc", status=CONFIRMED, source="dm"
        )
        return str(made.value.id)

    async def make_pc(self, campaign: str, entity: str, created_at: int) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET type = 'character', role = %s, created_at = %s"
                " WHERE campaign_id = %s AND id = %s",
                (PLAYER_CHARACTER, created_at, campaign, entity),
            )

    async def change(
        self, campaign: str, entity: str, at: int, *, before: str | None, after: str
    ) -> None:
        """One logged change of the entry's role (only the columns the backfill reads)."""
        self.version += 1

        def row(role: str | None) -> Any:
            return None if role is None else json.dumps({"role": role, "status": CONFIRMED})

        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name,"
                " row_id, op, before, after, source, made_at) VALUES (%s, %s, %s, %s,"
                " 'memory_entities', %s, %s, %s, %s, 'dm', %s)",
                (GUILD, campaign, self.version, self.version, entity,
                 "insert" if before is None else "update", row(before), row(after), at),
            )  # fmt: skip

    async def session(self, campaign: str, start: int, end: int | None) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (guild_id, campaign_id, id, started_at, ended_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (GUILD, campaign, uuid.uuid4().hex, start, end),
            )

    async def stretches(self) -> dict[str, list[tuple[int, Any, str]]]:
        await self.db.migrate(MIGRATIONS)
        out: dict[str, list[tuple[int, Any, str]]] = {}
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT entity_id, from_at, to_at, source FROM memory_party_spans ORDER BY from_at"
            )
            for r in await cur.fetchall():
                out.setdefault(str(r["entity_id"]), []).append(
                    (int(r["from_at"]), r["to_at"], str(r["source"]))
                )
        return out

    async def test_each_player_character_gets_one_open_stretch_dated_by_its_change(self) -> None:
        await self.session(self.c, 4000, 6000)
        await self.session(self.c, 9000, None)  # (a session running: it is still going)
        during = await self.npc(self.c, "Vell")  # made one during a session: from its start
        between = await self.npc(self.c, "Tuck")  # made one between sessions: that moment
        again = await self.npc(self.c, "Mara")  # one, then not, then one again: the latest
        never_logged = await self.npc(self.c, "Ossa")  # no change kept: its own made time
        plain = await self.npc(self.c, "Guard")  # an NPC: nothing
        for e, at in ((during, 100), (between, 100), (again, 100), (never_logged, 3000)):
            await self.make_pc(self.c, e, at)
        await self.change(self.c, during, 5000, before="npc", after=PLAYER_CHARACTER)
        await self.change(self.c, between, 7000, before=None, after=PLAYER_CHARACTER)
        await self.change(self.c, again, 3000, before="npc", after=PLAYER_CHARACTER)
        await self.change(self.c, again, 3500, before=PLAYER_CHARACTER, after="npc")
        await self.change(self.c, again, 9500, before="npc", after=PLAYER_CHARACTER)
        got = await self.stretches()
        self.assertEqual(got[during], [(4000, None, "start")])
        self.assertEqual(got[between], [(7000, None, "start")])
        self.assertEqual(got[again], [(9000, None, "start")])  # (9500 is in the running session)
        self.assertEqual(got[never_logged], [(3000, None, "start")])
        self.assertNotIn(plain, got)

    async def test_a_campaign_with_no_sessions_keeps_the_time_as_it_is(self) -> None:
        lone = await self.npc(self.quiet, "Brann")
        await self.make_pc(self.quiet, lone, 1234)
        got = await self.stretches()
        self.assertEqual(got[lone], [(1234, None, "start")])  # (created_at, unrounded)

    async def test_a_rejected_player_character_gets_nothing(self) -> None:
        gone = await self.npc(self.c, "Gone")
        await self.make_pc(self.c, gone, 100)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'rejected' WHERE id = %s", (gone,)
            )
        self.assertNotIn(gone, await self.stretches())


class TheRecord(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 10_000
        self.memory = MemoryStore(self.db, clock=lambda: float(self.now))
        self.campaign = await CampaignStore(self.db).create(GUILD, "Saltmarsh", DM)
        self.c = self.campaign.id
        self.vell = await self.pc("Vell", ANNA)
        self.tuck = await self.pc("Tuck", BEN)

    async def pc(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD, self.c, name=name, type="character", role=PLAYER_CHARACTER, played_by=player,
            status=CONFIRMED, source="dm",
        )  # fmt: skip
        return str(made.value.id)

    async def test_a_new_player_character_is_in_the_party_from_now(self) -> None:
        [span] = await self.memory.party_spans(GUILD, self.c, self.vell)
        self.assertEqual((span.from_at, span.open, span.source), (10_000, True, "dm"))
        self.assertEqual(
            set(await self.memory.current_party(GUILD, self.c)), {self.vell, self.tuck}
        )

    async def test_one_made_during_a_session_counts_from_its_start(self) -> None:
        await TranscriptStore(self.db).open_session(GUILD, self.c, 9_000)
        cara = await self.pc("Cara", CARA)
        [span] = await self.memory.party_spans(GUILD, self.c, cara)
        self.assertEqual(span.from_at, 9_000)

    async def test_leaving_closes_the_stretch_takes_nothing_and_undoes(self) -> None:
        npc = await self.memory.add_entity(
            GUILD, self.c, name="Ferryman", type="npc", status=CONFIRMED, source="dm"
        )
        before = await self.memory.visible_to(GUILD, self.c, Viewer.character(self.vell))
        self.now = 20_000
        done = await self.memory.leave_party(GUILD, self.c, self.vell, "retired")
        self.assertEqual((done.value.to_at, done.value.ended), (20_000, "retired"))
        self.assertEqual(await self.memory.current_party(GUILD, self.c), [self.tuck])
        after = await self.memory.visible_to(GUILD, self.c, Viewer.character(self.vell))
        self.assertEqual(after.sureness, before.sureness)  # (nothing taken from Vell, §6)
        self.assertIn((ENTRY, str(npc.value.id)), after.sureness)
        assert done.batch is not None
        await self.memory.undo(GUILD, self.c, done.batch)
        self.assertEqual(
            set(await self.memory.current_party(GUILD, self.c)), {self.vell, self.tuck}
        )

    async def test_only_one_in_the_party_can_leave_and_only_for_a_word_offered(self) -> None:
        await self.memory.leave_party(GUILD, self.c, self.vell, "left")
        with self.assertRaises(MemoryRuleError):
            await self.memory.leave_party(GUILD, self.c, self.vell, "left")  # (left already)
        with self.assertRaises(MemoryRuleError):
            await self.memory.leave_party(GUILD, self.c, self.tuck, "bored")

    async def test_the_party_at_a_moment_follows_the_record(self) -> None:
        self.now = 20_000
        await self.memory.leave_party(GUILD, self.c, self.vell, "away")
        async with self.db.guild(GUILD) as conn:
            from dmbot.memory._changes import Scope

            scope = Scope(conn, GUILD, self.c, 0)
            self.assertEqual(
                set(await party_record.members_at(scope, 15_000)), {self.vell, self.tuck}
            )
            self.assertEqual(await party_record.members_at(scope, 25_000), [self.tuck])
            self.assertEqual(await party_record.members_at(scope, 5_000), [])  # (not yet)

    async def test_a_character_with_no_stretch_still_counts(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM memory_party_spans WHERE entity_id = %s", (self.vell,))
        self.assertEqual(
            set(await self.memory.current_party(GUILD, self.c)), {self.vell, self.tuck}
        )

    async def test_a_character_who_left_is_not_given_what_the_table_is_shown_after(self) -> None:
        self.now = 20_000
        await self.memory.leave_party(GUILD, self.c, self.vell, "left")
        shown = await self.memory.add_entity(
            GUILD, self.c, name="Harbourmaster", type="npc", status=CONFIRMED, source="dm"
        )
        sid = (ENTRY, str(shown.value.id))
        tuck = await self.memory.visible_to(GUILD, self.c, Viewer.character(self.tuck))
        vell = await self.memory.visible_to(GUILD, self.c, Viewer.character(self.vell))
        self.assertIn(sid, tuck.sureness)
        self.assertNotIn(sid, vell.sureness)  # (the flag writes for the current party, rule 6)

    async def test_a_character_who_left_keeps_what_is_hidden_and_shown_again(self) -> None:
        npc = await self.memory.add_entity(
            GUILD, self.c, name="Ferryman", type="npc", status=CONFIRMED, source="dm"
        )
        sid = (ENTRY, str(npc.value.id))
        self.now = 20_000
        await self.memory.leave_party(GUILD, self.c, self.vell, "away")
        for hidden in (True, False):  # (hiding sleeps every holder; showing wakes them all)
            await self.memory.set_hidden_from_players(
                GUILD, self.c, str(npc.value.id), hidden, source="dm", dm_chose_secret=True
            )
            vell = await self.memory.visible_to(GUILD, self.c, Viewer.character(self.vell))
            self.assertEqual(sid in vell.sureness, not hidden)


class TheCardLine(unittest.TestCase):
    def test_since_when_or_when_they_left(self) -> None:
        open_ = Span("a" * 32, "b" * 32, 1700, None, None, "dm")
        self.assertEqual(
            party_card.party_line([open_], {1700: 7}),
            "\n🧭 In the party since session 7 (<t:1700:D>).",
        )
        self.assertEqual(party_card.party_line([open_], {}), "\n🧭 In the party since <t:1700:D>.")
        gone = Span("a" * 32, "b" * 32, 1700, 2000, "retired", "dm")
        self.assertEqual(
            party_card.party_line([gone], {}), "\n🧭 Left the party on <t:2000:D> (retired)."
        )
        self.assertEqual(party_card.party_line([], {}), "")

    def test_the_reasons_are_the_stored_words(self) -> None:
        self.assertEqual(set(party_card.REASON_LABELS), set(party_record.ENDED))
        self.assertTrue(all(len(v) <= 80 for v in party_card.REASON_LABELS.values()))
        self.assertNotIn("she", party_card.WHY.split())  # (the character is named, ruling C)
