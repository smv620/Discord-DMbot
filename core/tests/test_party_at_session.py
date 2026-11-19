"""What is read to the table about a session is for the party AT that session (#4537, party
knowledge slice 3b; `docs/design/party_knowledge.md` §8 and §14 row 3; Supervisor's ruling on
#4537). The proof §14 names: a recap of an old session is unchanged by a later join. Invented names
and ids only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dmbot.campaigns import CampaignStore
from dmbot.memory import gate
from dmbot.memory.models import CONFIRMED, DM, PLAYER_CHARACTER
from dmbot.memory.store import MemoryStore
from dmbot.recaps.inputs import recap_people
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

GUILD = 700_000_000_000_000_031
DM_ID, ANNA, BEN, CARA = (
    700_000_000_000_000_301,
    700_000_000_000_000_302,
    700_000_000_000_000_303,
    700_000_000_000_000_304,
)


class TheViewer(unittest.TestCase):
    def test_the_party_at_a_session_is_its_members_and_none_is_the_party(self) -> None:
        self.assertEqual(gate.Viewer.party_at(["a", "b"]), gate.Viewer.the_table(["a", "b"]))
        self.assertEqual(gate.Viewer.party_at([]), gate.Viewer.the_party())


class ThePeopleOfARecap(unittest.TestCase):
    def test_a_later_joiner_is_not_in_an_old_recaps_people(self) -> None:
        def pc(i: str, name: str, player: int) -> SimpleNamespace:
            return SimpleNamespace(id=i, name=name, played_by=player, role="player_character")

        names = SimpleNamespace(
            entities={"v": pc("v", "Vell", ANNA), "c": pc("c", "Cara", CARA)}, names=[], fixes={}
        )
        _, everyone, _ = recap_people(names, {"Vell", "Cara"})
        _, then, party = recap_people(names, {"Vell", "Cara"}, {"v"})
        self.assertEqual(everyone, ["Cara", "Vell"])  # (no party record given: as before)
        self.assertEqual(then, ["Vell"])
        self.assertEqual(party, {ANNA: ("Vell",)})


class AJoinAtSessionThree(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 500
        self.memory = MemoryStore(self.db, clock=lambda: float(self.now))
        self.memory.party_join = True  # (a newcomer is asked about, never copied: §5)
        self.c = (await CampaignStore(self.db).create(GUILD, "Saltmarsh", DM_ID)).id
        self.sessions = TranscriptStore(self.db)
        self.vell = await self.pc("Vell", ANNA)
        self.tuck = await self.pc("Tuck", BEN)
        made = await self.memory.add_entity(
            GUILD, self.c, name="The drowned bell", type="quest", status=CONFIRMED, source=DM
        )
        self.thread = str(made.value.id)  # (shown: the party of sessions 1 and 2 knows it)
        await self.played(1_000, 1_500)
        await self.played(2_000, 2_500)
        self.now = 3_100
        sid = await self.sessions.open_session(GUILD, self.c, 3_000)
        await self.lines(sid)
        self.cara = await self.pc("Cara", CARA)  # (joins during session 3: from its start)

    async def pc(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD, self.c, name=name, type="character", role=PLAYER_CHARACTER, played_by=player,
            status=CONFIRMED, source=DM,
        )  # fmt: skip
        return str(made.value.id)

    async def lines(self, sid: str) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET line_count = 3 WHERE id = %s", (sid,)
            )

    async def played(self, start: int, end: int) -> None:
        sid = await self.sessions.open_session(GUILD, self.c, start)
        await self.lines(sid)
        await self.sessions.end_session(GUILD, sid, end)

    async def test_session_one_is_read_for_its_party_and_session_three_for_its_own(self) -> None:
        self.assertEqual(
            set(await self.memory.members_at(GUILD, self.c, 1_000)), {self.vell, self.tuck}
        )
        self.assertEqual(
            set(await self.memory.members_at(GUILD, self.c, 3_000)),
            {self.vell, self.tuck, self.cara},
        )

    async def test_an_old_recaps_threads_are_unchanged_by_the_join(self) -> None:
        then = await self.memory.members_at(GUILD, self.c, 1_000)
        old = await self.memory.visible_to(GUILD, self.c, gate.Viewer.party_at(then))
        self.assertIsNotNone(old.how_sure(gate.ENTRY, self.thread))  # (Vell and Tuck knew it)
        now = await self.memory.members_at(GUILD, self.c, 3_000)
        new = await self.memory.visible_to(GUILD, self.c, gate.Viewer.party_at(now))
        self.assertIsNone(new.how_sure(gate.ENTRY, self.thread))  # (Cara doesn't know it yet)

    async def test_a_character_who_left_after_still_counts_for_the_old_session(self) -> None:
        self.now = 4_000
        await self.memory.leave_party(GUILD, self.c, self.tuck, "retired")
        self.assertIn(self.tuck, await self.memory.members_at(GUILD, self.c, 1_000))
        self.assertNotIn(self.tuck, await self.memory.current_party(GUILD, self.c))

    async def test_a_session_older_than_every_stretch_has_no_party_on_record(self) -> None:
        # (an imported session dated before its characters were made: the recap then falls back
        # to every player character, `load_inputs_or_why`, and the viewer to today's party)
        self.assertEqual(await self.memory.members_at(GUILD, self.c, 100), [])
        self.assertEqual(gate.Viewer.party_at([]), gate.Viewer.the_party())

    async def test_one_who_left_at_a_sessions_start_is_not_in_its_party(self) -> None:
        self.now = 6_000
        sid = await self.sessions.open_session(GUILD, self.c, 5_000)
        await self.lines(sid)
        await self.memory.leave_party(GUILD, self.c, self.tuck, "away")  # (dated 5_000)
        self.assertNotIn(self.tuck, await self.memory.members_at(GUILD, self.c, 5_000))

    async def test_a_table_answer_is_for_todays_party_unless_it_changed_mid_session(
        self,
    ) -> None:
        from dmbot.memory import party_record

        async with self.db.guild(GUILD) as conn:
            from dmbot.memory._changes import Scope

            scope = Scope(conn, GUILD, self.c, 0)
            # (session 3 is running and Cara joined from its start: the same party)
            self.assertIsNone(await party_record.table_members(scope))
        async with self.memory._write(GUILD, self.c, DM) as w:  # (Cara dated later than the start)
            await party_record.move_start(w, self.cara, 3_050)
        async with self.db.guild(GUILD) as conn:
            scope = Scope(conn, GUILD, self.c, 0)
            then = await party_record.table_members(scope)
            self.assertEqual(set(then or []), {self.vell, self.tuck})


if __name__ == "__main__":
    unittest.main()
