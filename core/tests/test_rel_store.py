"""The relationship reader's ties against a real Postgres (#3244 slice 6): they exist only while
their source lines do, they go with a person, a changed line and a session's retention, they are
never in a backup, a stopped person's words are never kept, and nothing reads them but the
comparison. Invented names and numbers."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import tables
from dmbot.transcript.models import Line
from dmbot.transcript.rel_store import MAX_SOURCES, RelStore, TieRow
from dmbot.transcript.store import TranscriptStore
from tests.test_memory_names import GUILD, NamesTest

TAVIN, DM_USER, MIRA = 8, 9, 10
START = 1_700_000_000
MS = START * 1000
AGREED = {TAVIN, DM_USER, MIRA}


def tie(*sources: int, a: str = "Tavin", b: str = "Kell") -> TieRow:
    return TieRow(a, b, "kin_of", "kin_of", "ai", None, f"{a} is {b}'s kin.", tuple(sources))


class TiesCase(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.transcripts = TranscriptStore(self.db)
        self.ties = RelStore(self.db)
        self.session = await self.transcripts.open_session(GUILD, self.campaign.id, START)
        await self.transcripts.end_session(GUILD, self.session, START + 100)
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [
                Line(MS + 1000, TAVIN, "ya so um", "Yes."),
                Line(MS + 2000, DM_USER, "how many", "How many?"),
                Line(MS + 3000, TAVIN, "it uses two", "It uses two."),
                Line(MS + 4000, MIRA, "I nod", "I nod."),
            ],
        )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s ORDER BY started_ms",
                (self.session,),
            )
            self.ids = [int(r["id"]) for r in await cur.fetchall()]

    async def count(self) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM rel_ties")
            row: Any = await cur.fetchone()
            return int(row["n"])

    async def save(self, *rows: TieRow, agreed: set[int] | None = None) -> Any:
        return await self.ties.replace(
            GUILD, self.session, rows, AGREED if agreed is None else agreed
        )


class TheTies(TiesCase):
    async def test_ties_are_saved_and_read_back_with_their_lines(self) -> None:
        a, b, c, _ = self.ids
        done = await self.save(tie(a, c), tie(b, a=" Mira ".strip(), b="Tavin"))
        self.assertEqual((done.kept, done.dropped), (2, 0))
        got = await self.ties.read(GUILD, self.session)
        self.assertEqual([(t.a, t.b) for t in got], [("Tavin", "Kell"), ("Mira", "Tavin")])
        self.assertEqual(got[0].source_ids, tuple(sorted((a, c))))

    async def test_saving_again_replaces_the_ties(self) -> None:
        await self.save(tie(self.ids[0]), tie(self.ids[2]))
        await self.save(tie(self.ids[0]))
        self.assertEqual(await self.count(), 1)

    async def test_a_tie_whose_source_is_gone_is_dropped_and_counted(self) -> None:
        await self.transcripts.delete_person(GUILD, MIRA)
        done = await self.save(tie(self.ids[3]), tie(self.ids[0]))
        self.assertEqual((done.kept, done.dropped), (1, 1))

    async def test_a_source_of_another_session_is_refused(self) -> None:
        other = await self.transcripts.open_session(GUILD, self.campaign.id, START + 500)
        await self.transcripts.add_lines(GUILD, other, [Line(MS + 9000, TAVIN, "x", "Hi.")])
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s", (other,)
            )
            foreign = int((await cur.fetchone())["id"])  # type: ignore[index]
        done = await self.save(tie(foreign))
        self.assertEqual((done.kept, done.dropped), (0, 1))

    async def test_a_person_who_no_longer_agrees_is_never_kept(self) -> None:
        done = await self.save(tie(self.ids[3]), tie(self.ids[0]), agreed={TAVIN})
        self.assertEqual((done.kept, done.dropped), (1, 1))

    async def test_a_line_that_is_not_there_is_refused(self) -> None:
        # (the check also refuses a DM sidebar line, as the script store does)
        done = await self.save(tie(10**12))
        self.assertEqual((done.kept, done.dropped), (0, 1))

    async def test_a_tie_citing_too_much_is_dropped_not_cut(self) -> None:
        many = tuple(range(1, MAX_SOURCES + 2))
        done = await self.save(tie(*many), tie(self.ids[0]))
        self.assertEqual(done.kept, 1)

    async def test_an_empty_or_unusable_run_does_not_wipe_the_ties_kept(self) -> None:
        await self.save(tie(self.ids[0]), tie(self.ids[2]))
        await self.save()
        await self.save(tie(10**12))
        self.assertEqual(await self.count(), 2)


class TheTiesGoWithTheWords(TiesCase):
    async def test_deleting_a_persons_transcripts_removes_every_tie_made_of_them(self) -> None:
        a, b, _, d = self.ids
        await self.save(tie(a, b), tie(d))
        await self.transcripts.delete_person(
            GUILD, DM_USER
        )  # (a tie Tavin spoke in, with a DM line)
        self.assertEqual(await self.count(), 1)

    async def test_a_dms_removal_of_one_speaker_stays_in_that_campaign(self) -> None:
        await self.save(tie(self.ids[3]))
        await self.transcripts.remove_speaker(GUILD, self.campaign.id, MIRA)
        self.assertEqual(await self.count(), 0)

    async def test_a_sessions_retention_removes_its_ties_and_the_session_stays(self) -> None:
        await self.save(tie(self.ids[0]))
        gone = await self.transcripts.delete_session_transcript(
            GUILD, self.campaign.id, self.session, START + 10_000_000, START + 10_000_001
        )
        self.assertGreater(gone, 0)
        self.assertEqual(await self.count(), 0)

    async def test_lines_taken_back_take_the_ties_made_of_them(self) -> None:
        await self.save(tie(self.ids[2]), tie(self.ids[0]))
        await self.transcripts.remove_lines(GUILD, self.session, [self.ids[2]])
        self.assertEqual(await self.count(), 1)

    async def test_a_rewrite_of_the_words_makes_the_ties_out_of_date(self) -> None:
        await self.save(tie(self.ids[0]))
        await self.transcripts.relabel_line(GUILD, self.session, TAVIN, MS + 1000, "Yes, indeed.")
        self.assertEqual(await self.count(), 0)

    async def test_a_change_of_who_said_it_makes_the_ties_out_of_date(self) -> None:
        await self.save(tie(self.ids[0]))
        await self.transcripts.set_entity(GUILD, self.session, TAVIN, MS + 1000, "Kell")
        self.assertEqual(await self.count(), 0)

    async def test_deleting_the_session_takes_its_ties(self) -> None:
        await self.save(tie(self.ids[0]))
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM transcript_sessions WHERE id = %s", (self.session,))
        self.assertEqual(await self.count(), 0)

    async def test_it_is_never_in_a_backup(self) -> None:
        self.assertIn("rel_ties", tables.NOT_IN_BACKUP)
        carried = {t for names in tables.SECTION_TABLES.values() for t in names}
        self.assertNotIn("rel_ties", carried)
