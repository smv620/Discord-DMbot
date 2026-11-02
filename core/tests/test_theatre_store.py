"""The theatrical transcript's storage (#3244, slice 2) against a real Postgres: a row exists only
while its sources do, it goes with a person and with a session's retention, it is a DM-only
working copy that is never in a backup. Invented names and numbers."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import tables
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.theatre_store import Row, TheatreStore
from tests.test_memory_names import GUILD, NamesTest

TAVIN, DM_USER, MIRA = 8, 9, 10
START = 1_700_000_000
MS = START * 1000


class TheScript(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.transcripts = TranscriptStore(self.db)
        self.theatre = TheatreStore(self.db)
        self.session = await self.transcripts.open_session(GUILD, self.campaign.id, START)
        await self.transcripts.end_session(GUILD, self.session, START + 100)
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [
                Line(MS + 1000, TAVIN, "ya so um", "Yes."),
                Line(MS + 2000, DM_USER, "how many charges", "How many charges?"),
                Line(MS + 3000, TAVIN, "it uses two", "It uses two."),
                Line(MS + 4000, MIRA, "I nod", "I nod."),
            ],
        )
        self.ids = await self.line_ids()

    async def line_ids(self) -> list[int]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s ORDER BY started_ms",
                (self.session,),
            )
            return [int(r["id"]) for r in await cur.fetchall()]

    def rows(self) -> list[Row]:
        a, b, c, d = self.ids
        return [
            Row(TAVIN, "Yes, you're [Kell] right.", (a,)),
            Row(DM_USER, "How many charges?", (b,)),
            Row(TAVIN, "It [the cloak] uses 2 [charges].", (c,)),
            Row(MIRA, "I nod.", (d,)),
        ]

    async def count(self) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM theatre_lines")
            row: Any = await cur.fetchone()
            return int(row["n"])

    async def test_a_script_is_saved_in_order_and_read_back(self) -> None:
        done = await self.theatre.replace(GUILD, self.session, self.rows())
        self.assertEqual((done.kept, done.dropped), (4, 0))
        got = await self.theatre.read(GUILD, self.session)
        self.assertEqual([r.position for r in got], [0, 1, 2, 3])
        self.assertEqual(got[2].text, "It [the cloak] uses 2 [charges].")
        self.assertEqual(got[2].source_ids, (self.ids[2],))
        self.assertEqual(got[2].user_ids, (TAVIN,))

    async def test_saving_again_replaces_the_script(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.theatre.replace(GUILD, self.session, self.rows()[:1])
        self.assertEqual(await self.count(), 1)

    async def test_a_row_whose_source_is_gone_is_dropped_and_counted(self) -> None:
        # the AI was away while a person erased their words: those lines are gone when it saves
        await self.transcripts.delete_person(GUILD, MIRA)
        done = await self.theatre.replace(GUILD, self.session, self.rows())
        self.assertEqual((done.kept, done.dropped), (3, 1))
        self.assertNotIn(
            MIRA, [u for r in await self.theatre.read(GUILD, self.session) for u in r.user_ids]
        )

    async def test_a_source_of_another_session_is_refused(self) -> None:
        other = await self.transcripts.open_session(GUILD, self.campaign.id, START + 5000)
        await self.transcripts.add_lines(GUILD, other, [Line(MS + 9_000_000, TAVIN, "x", "x")])
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s", (other,)
            )
            foreign = int((await cur.fetchone())["id"])  # type: ignore[index]
        done = await self.theatre.replace(GUILD, self.session, [Row(TAVIN, "Hi.", (foreign,))])
        self.assertEqual((done.kept, done.dropped), (0, 1))

    async def test_a_speaker_who_is_not_among_the_sources_is_refused(self) -> None:
        done = await self.theatre.replace(GUILD, self.session, [Row(MIRA, "Hi.", (self.ids[0],))])
        self.assertEqual((done.kept, done.dropped), (0, 1))

    async def test_an_empty_line_is_not_kept(self) -> None:
        done = await self.theatre.replace(GUILD, self.session, [Row(TAVIN, "  ", (self.ids[0],))])
        self.assertEqual((done.kept, done.dropped), (0, 1))

    async def test_deleting_a_persons_transcripts_removes_every_row_made_of_them(self) -> None:
        a, b, _, d = self.ids
        rows = [Row(TAVIN, "Yes, and how many?", (a, b)), Row(MIRA, "I nod.", (d,))]
        await self.theatre.replace(GUILD, self.session, rows)
        await self.transcripts.delete_person(
            GUILD, DM_USER
        )  # a row Tavin spoke, made with a line of the DM's
        got = await self.theatre.read(GUILD, self.session)
        self.assertEqual([r.text for r in got], ["I nod."])

    async def test_a_sessions_retention_removes_its_script_and_the_row_stays(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        gone = await self.transcripts.delete_session_transcript(
            GUILD, self.campaign.id, self.session, START + 10_000_000, START + 10_000_001
        )
        self.assertGreater(gone, 0)
        self.assertEqual(await self.count(), 0)

    async def test_it_is_never_in_a_backup(self) -> None:
        self.assertIn("theatre_lines", tables.NOT_IN_BACKUP)
