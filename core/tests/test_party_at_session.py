"""What is read to the table about a session is for the party AT that session (#4537, party
knowledge slice 3b; `docs/design/party_knowledge.md` §8 and §14 row 3; Supervisor's ruling on
#4537). The proof §14 names: a recap of an old session is unchanged by a later join. Invented names
and ids only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory import gate
from dmbot.memory.models import CONFIRMED, DM, PLAYER_CHARACTER
from dmbot.memory.store import MemoryStore
from dmbot.recaps.inputs import recap_name_sets, recap_people
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
    def test_the_party_at_a_session_is_its_members_and_none_is_common_knowledge(self) -> None:
        self.assertEqual(gate.Viewer.party_at(["a", "b"]), gate.Viewer.the_table(["a", "b"]))
        self.assertEqual(gate.Viewer.party_at([]), gate.Viewer.the_world())  # (fail closed, #4806)


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


class TheNamesOfARecap(unittest.TestCase):
    """#4877: the names a recap may use follow the party at its session, not today's."""

    @staticmethod
    def entry(entity_id: str, text: str, *, hidden: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            entity_id=entity_id, text=text, secret=False, clue=False, confirmed=True,
            person=False, entry_hidden=hidden,
        )  # fmt: skip

    def test_a_name_that_party_knew_is_allowed_whatever_today_says(self) -> None:
        names = SimpleNamespace(names=[self.entry("b", "The drowned bell", hidden=True)])
        allowed, secret = recap_name_sets(names, "", shown={"b"})
        self.assertEqual((allowed, secret), (["The drowned bell"], []))

    def test_a_name_that_party_didnt_know_stays_out_whatever_today_says(self) -> None:
        names = SimpleNamespace(names=[self.entry("b", "The drowned bell")])
        allowed, secret = recap_name_sets(names, "", shown=set())
        self.assertEqual((allowed, secret), ([], ["The drowned bell"]))
        self.assertEqual(recap_name_sets(names, ""), (["The drowned bell"], []))  # (no party read)

    def test_said_at_the_table_it_is_allowed_all_the_same(self) -> None:
        names = SimpleNamespace(names=[self.entry("b", "Bell")])
        self.assertEqual(recap_name_sets(names, " bell ", shown=set()), (["Bell"], []))


class ThePostCheck(unittest.IsolatedAsyncioTestCase):
    """#4877: the check at Post and the audio filter read the session's party, as the draft did
    (#2050: the locks agree), and refuse when they can't."""

    @staticmethod
    def features(party: Any) -> Any:
        from unittest.mock import AsyncMock

        # (today's flag says "not known yet"; the party at that session knew it, and the other)
        names = SimpleNamespace(
            names=[
                TheNamesOfARecap.entry("b", "The drowned bell", hidden=True),
                TheNamesOfARecap.entry("k", "Kestrel Hall"),
            ]
        )
        bot: Any = SimpleNamespace(
            lookup=SimpleNamespace(get=AsyncMock(return_value=names)),
            memory=SimpleNamespace(party_at_session=party),
            transcripts=None,
            _recanted=AsyncMock(return_value=()),
        )
        return bot

    async def test_post_reads_the_party_at_that_session(self) -> None:
        from unittest.mock import AsyncMock

        from dmbot.recaps.discord import RecapFeatures

        party = AsyncMock(return_value=SimpleNamespace(members=("v",), entries=frozenset({"b"})))
        bot = self.features(party)
        campaign: Any = SimpleNamespace(guild_id=GUILD, id="c")
        secret = await RecapFeatures.recap_secret_names(bot, campaign, 100)
        allowed = await RecapFeatures.recap_allowed_names(bot, campaign, 100)
        self.assertEqual((allowed, secret), (["The drowned bell"], ["Kestrel Hall"]))
        party.assert_awaited_with(GUILD, "c", 100)

    async def test_post_refuses_when_the_party_cant_be_read(self) -> None:
        from unittest.mock import AsyncMock

        from dmbot.recaps.discord import RecapFeatures

        bot = self.features(AsyncMock(side_effect=RuntimeError("down")))
        campaign: Any = SimpleNamespace(guild_id=GUILD, id="c")
        with self.assertLogs("dmbot.bot", level="ERROR"):
            self.assertIsNone(await RecapFeatures.recap_secret_names(bot, campaign, 100))
        with self.assertLogs("dmbot.bot", level="ERROR"):
            self.assertEqual(await RecapFeatures.recap_allowed_names(bot, campaign, 100), [])

    async def test_no_party_at_it_keeps_the_flags(self) -> None:
        from unittest.mock import AsyncMock

        from dmbot.recaps.discord import RecapFeatures

        bot = self.features(
            AsyncMock(return_value=SimpleNamespace(members=(), entries=frozenset()))
        )
        campaign: Any = SimpleNamespace(guild_id=GUILD, id="c")
        self.assertEqual(
            await RecapFeatures.recap_secret_names(bot, campaign, 100), ["The drowned bell"]
        )


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

    async def lines(self, sid: str, made: int | None = None) -> None:
        """A session with lines; its row made at `made` (a live one: at its start)."""
        async with self.db.guild(GUILD) as conn:
            sql = (
                "UPDATE transcript_sessions SET line_count = 3,"
                " created_at = COALESCE(%s, started_at) WHERE id = %s"
            )
            await conn.execute(sql, (made, sid))

    async def played(self, start: int, end: int, made: int | None = None) -> None:
        sid = await self.sessions.open_session(GUILD, self.c, start)
        await self.lines(sid, made)
        await self.sessions.end_session(GUILD, sid, end)

    async def test_session_one_is_read_for_its_party_and_session_three_for_its_own(self) -> None:
        self.assertEqual(
            set(await self.memory.members_at(GUILD, self.c, 1_000)), {self.vell, self.tuck}
        )
        self.assertEqual(
            set(await self.memory.members_at(GUILD, self.c, 3_000)),
            {self.vell, self.tuck, self.cara},
        )

    async def test_one_read_gives_the_sessions_party_and_what_it_knew(self) -> None:
        old = await self.memory.party_at_session(GUILD, self.c, 1_000)
        self.assertEqual(set(old.members), {self.vell, self.tuck})
        self.assertIn(self.thread, old.entries)  # (Vell and Tuck knew it)
        new = await self.memory.party_at_session(GUILD, self.c, 3_000)
        self.assertEqual(set(new.members), {self.vell, self.tuck, self.cara})
        self.assertNotIn(self.thread, new.entries)  # (Cara doesn't know it yet)

    async def test_the_members_own_names_are_known_to_their_own_session(self) -> None:
        # (the review's case: Cara joined and was asked, never copied, so she holds no row on
        # Vell or Tuck; the recap still names the characters at its own table, #4877)
        new = await self.memory.party_at_session(GUILD, self.c, 3_000)
        self.assertTrue({self.vell, self.tuck, self.cara} <= new.entries)

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

    async def test_an_imported_session_older_than_every_stretch_counts_everyone(self) -> None:
        # (Supervisor's ruling E: imported after the characters existed, so all of them count)
        await self.played(100, 200, made=3_200)
        got = set(await self.memory.members_at(GUILD, self.c, 100))
        self.assertEqual(got, {self.vell, self.tuck, self.cara})
        self.assertEqual(gate.Viewer.party_at([]), gate.Viewer.the_world())  # (fail closed, #4806)

    async def test_an_imported_session_counts_who_existed_not_who_joined_later(self) -> None:
        # (ruling E's mixed case: imported at 2_800, before Cara joined live at 3_000)
        await self.played(200, 300, made=2_800)
        got = set(await self.memory.members_at(GUILD, self.c, 200))
        self.assertEqual(got, {self.vell, self.tuck})

    async def test_a_live_session_doesnt_count_one_who_joined_after_it(self) -> None:
        self.assertNotIn(self.cara, await self.memory.members_at(GUILD, self.c, 2_000))

    async def test_a_row_from_before_the_column_counts_every_stretch(self) -> None:
        async with self.db.guild(GUILD) as conn:  # (made unknown: the careful side)
            await conn.execute(
                "UPDATE transcript_sessions SET created_at = NULL WHERE guild_id = %s"
                " AND started_at = 1000",
                (GUILD,),
            )
        self.assertIn(self.cara, await self.memory.members_at(GUILD, self.c, 1_000))

    async def test_one_who_left_at_a_sessions_start_is_not_in_its_party(self) -> None:
        self.now = 6_000
        sid = await self.sessions.open_session(GUILD, self.c, 5_000)
        await self.lines(sid)
        await self.memory.leave_party(GUILD, self.c, self.tuck, "away")  # (dated 5_000)
        self.assertNotIn(self.tuck, await self.memory.members_at(GUILD, self.c, 5_000))

    async def table(self, live_at: int | None) -> list[str] | None:
        from dmbot.memory import party_record
        from dmbot.memory._changes import Scope

        async with self.db.guild(GUILD) as conn:
            return await party_record.table_members(Scope(conn, GUILD, self.c, 0), live_at)

    async def test_a_table_answer_is_for_todays_party_when_nobody_left(self) -> None:
        from dmbot.memory import party_record

        # (session 3 is running and Cara joined from its start: the same party)
        self.assertIsNone(await self.table(3_000))
        self.assertIsNone(await self.table(None))  # (no live table: today's party, ruling F)
        async with self.memory._write(GUILD, self.c, DM) as w:  # (Cara dated after the start)
            await party_record.move_start(w, self.cara, 3_050)
        # (a join during the session: today's party already holds her, the careful side)
        self.assertIsNone(await self.table(3_000))

    async def end_session_three(self) -> None:
        """Session 3 ends at 3_900: a later live table has no session row until its first save,
        so a join or leave then is dated to the moment, not to the table's start."""
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET ended_at = 3900 WHERE guild_id = %s"
                " AND started_at = 3000",
                (GUILD,),
            )

    async def test_a_join_before_the_first_save_is_still_at_the_table(self) -> None:
        # (the reviewer's case: a live table at 7_000 with no session row yet, so the join is dated
        # to the moment, after the start; she sits at the table and is never left out)
        await self.end_session_three()
        self.now = 7_060
        dara = await self.pc("Dara", 4_444)
        got = await self.table(7_000)
        self.assertIsNone(got)
        self.assertIn(dara, await self.memory.current_party(GUILD, self.c))

    async def test_one_who_left_mid_session_still_counts_at_the_table(self) -> None:
        await self.end_session_three()
        self.now = 7_100  # (a live table from 7_000, before its first save; Tuck leaves)
        await self.memory.leave_party(GUILD, self.c, self.tuck, "away")
        got = await self.table(7_000)
        self.assertEqual(set(got or []), {self.vell, self.tuck, self.cara})

    async def test_everyone_leaving_mid_session_keeps_the_tables_party(self) -> None:
        await self.end_session_three()
        self.now = 7_100
        for who in (self.vell, self.tuck, self.cara):
            await self.memory.leave_party(GUILD, self.c, who, "away")
        self.assertEqual(await self.memory.current_party(GUILD, self.c), [])
        got = await self.table(7_000)
        self.assertEqual(set(got or []), {self.vell, self.tuck, self.cara})

    async def test_a_session_with_no_row_reads_the_stretches_alone(self) -> None:
        # (no session started at 2_200: `session_members` falls back to `members_at`)
        self.assertEqual(
            set(await self.memory.members_at(GUILD, self.c, 2_200)), {self.vell, self.tuck}
        )

    async def test_what_the_party_at_a_session_knows_is_read_in_one_call(self) -> None:
        old = await self.memory.visible_to_party_at(GUILD, self.c, 1_000)
        self.assertIsNotNone(old.how_sure(gate.ENTRY, self.thread))  # (Vell and Tuck knew it)
        new = await self.memory.visible_to_party_at(GUILD, self.c, 3_000)
        self.assertIsNone(new.how_sure(gate.ENTRY, self.thread))  # (Cara doesn't know it yet)

    async def test_a_live_rows_first_save_counts_who_was_made_before_it(self) -> None:
        # (0552's comment: a live row is made at the first transcript save, after the start)
        await self.end_session_three()
        self.now = 8_030
        eve = await self.pc("Eve", 5_555)  # (made between the start and the first save)
        sid = await self.sessions.open_session(GUILD, self.c, 8_000)
        await self.lines(sid, made=8_060)
        self.assertIn(eve, await self.memory.members_at(GUILD, self.c, 8_000))


if __name__ == "__main__":
    unittest.main()
