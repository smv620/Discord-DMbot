"""The theatrical transcript's storage (#3244, slice 2) against a real Postgres: a row exists only
while its sources do, it goes with a person and with a session's retention, it is a DM-only
working copy that is never in a backup. Invented names and numbers."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import tables
from dmbot.transcript.models import SIDEBAR_ANSWER, Line
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.theatre_store import MAX_SOURCES, Row, TheatreStore
from tests.test_memory_names import GUILD, NamesTest

TAVIN, DM_USER, MIRA = 8, 9, 10
START = 1_700_000_000
MS = START * 1000


class ScriptCase(NamesTest):
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


class TheScript(ScriptCase):
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
        self.assertEqual(await self.count(), 1)  # (and nothing of Mira's went with it)

    async def test_a_sessions_retention_removes_its_script_and_the_row_stays(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        gone = await self.transcripts.delete_session_transcript(
            GUILD, self.campaign.id, self.session, START + 10_000_000, START + 10_000_001
        )
        self.assertGreater(gone, 0)
        self.assertEqual(await self.count(), 0)

    async def test_it_is_never_in_a_backup(self) -> None:
        self.assertIn("theatre_lines", tables.NOT_IN_BACKUP)
        carried = {t for names in tables.SECTION_TABLES.values() for t in names}
        self.assertNotIn("theatre_lines", carried)

    async def test_saved_lines_taken_back_take_the_script_made_of_them(self) -> None:
        # someone pressed Stop while their lines were being saved (remove_lines)
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.transcripts.remove_lines(GUILD, self.session, [self.ids[2]])
        got = await self.theatre.read(GUILD, self.session)
        self.assertEqual(
            [r.text for r in got], ["Yes, you're [Kell] right.", "How many charges?", "I nod."]
        )

    async def test_a_rewrite_of_the_cleaned_words_makes_the_script_out_of_date(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.transcripts.relabel_line(GUILD, self.session, TAVIN, MS + 1000, "Yes, indeed.")
        self.assertEqual(await self.count(), 0)

    async def test_a_change_of_who_said_it_makes_the_script_out_of_date(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.transcripts.set_entity(GUILD, self.session, TAVIN, MS + 1000, "Kell")
        self.assertEqual(await self.count(), 0)

    async def test_a_change_to_a_line_that_is_not_there_changes_nothing(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.transcripts.relabel_line(GUILD, self.session, TAVIN, MS + 99, "x")
        self.assertEqual(await self.count(), 4)

    async def test_deleting_the_session_takes_its_script(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM transcript_sessions WHERE id = %s", (self.session,))
        self.assertEqual(await self.count(), 0)

    async def test_a_dm_sidebar_line_cannot_be_a_source(self) -> None:
        await self.transcripts.add_lines(
            GUILD, self.session, [Line(MS + 5000, DM_USER, "q", "q", sidebar=SIDEBAR_ANSWER)]
        )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s AND sidebar IS NOT NULL",
                (self.session,),
            )
            side = int((await cur.fetchone())["id"])  # type: ignore[index]
        done = await self.theatre.replace(GUILD, self.session, [Row(DM_USER, "Hi.", (side,))])
        self.assertEqual((done.kept, done.dropped), (0, 1))

    async def test_a_row_citing_too_much_is_counted_apart_and_not_lost_silently(self) -> None:
        many = tuple(range(1, MAX_SOURCES + 2))
        done = await self.theatre.replace(
            GUILD, self.session, [Row(TAVIN, "A very long thought.", many), *self.rows()]
        )
        self.assertEqual((done.kept, done.too_big), (4, 1))

    async def test_an_empty_or_unusable_run_does_not_wipe_the_script_already_kept(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.theatre.replace(GUILD, self.session, [])
        await self.theatre.replace(GUILD, self.session, [Row(MIRA, "Hi.", (self.ids[0],))])
        self.assertEqual(await self.count(), 4)

    async def test_a_nul_character_in_the_text_is_removed_not_a_crash(self) -> None:
        done = await self.theatre.replace(
            GUILD, self.session, [Row(TAVIN, "Hi\x00 there.", (self.ids[0],))]
        )
        self.assertEqual(done.kept, 1)
        self.assertEqual((await self.theatre.read(GUILD, self.session))[0].text, "Hi there.")

    async def test_another_servers_script_is_not_seen_or_erased(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        self.assertEqual(await self.theatre.read(GUILD + 1, self.session), [])
        async with self.db.guild(GUILD + 1) as conn:
            from dmbot.transcript import theatre_store

            await theatre_store.forget_person(conn, GUILD + 1, TAVIN)
        self.assertEqual(await self.count(), 4)


class TheTriggers(ScriptCase):
    """Erasing is structural: plain SQL on transcript_lines, no store method involved."""

    async def sql(self, query: str, args: tuple[Any, ...]) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(query, args)

    async def test_deleting_a_line_removes_the_rows_that_cite_it_and_only_those(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.sql("DELETE FROM transcript_lines WHERE id = %s", (self.ids[1],))
        got = await self.theatre.read(GUILD, self.session)
        self.assertEqual(
            [r.source_ids for r in got], [(self.ids[0],), (self.ids[2],), (self.ids[3],)]
        )

    async def test_changing_what_a_line_says_removes_the_sessions_script(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.sql(
            "UPDATE transcript_lines SET text = 'Changed.' WHERE id = %s", (self.ids[0],)
        )
        self.assertEqual(await self.count(), 0)

    async def test_changing_who_said_it_or_whose_it_is_removes_the_sessions_script(self) -> None:
        for column, value in (("entity", "Kell"), ("user_id", 99), ("heard", "different")):
            with self.subTest(column):
                await self.theatre.replace(GUILD, self.session, self.rows())
                self.assertGreater(await self.count(), 0)
                await self.sql(
                    f"UPDATE transcript_lines SET {column} = %s WHERE id = %s",
                    (value, self.ids[0]),
                )
                self.assertEqual(await self.count(), 0)

    async def test_an_update_that_changes_nothing_removes_nothing(self) -> None:
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.sql("UPDATE transcript_lines SET text = text WHERE id = %s", (self.ids[0],))
        await self.sql("UPDATE transcript_lines SET topic = topic WHERE id = %s", (self.ids[0],))
        self.assertEqual(await self.count(), 4)

    async def test_another_sessions_script_is_untouched(self) -> None:
        other = await self.transcripts.open_session(GUILD, self.campaign.id, START + 5000)
        await self.transcripts.add_lines(GUILD, other, [Line(MS + 9_000_000, TAVIN, "x", "x")])
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s", (other,)
            )
            theirs = int((await cur.fetchone())["id"])  # type: ignore[index]
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.theatre.replace(GUILD, other, [Row(TAVIN, "Hi.", (theirs,))])
        await self.sql(
            "UPDATE transcript_lines SET text = 'Changed.' WHERE id = %s", (self.ids[0],)
        )
        self.assertEqual(await self.theatre.read(GUILD, self.session), [])
        self.assertEqual(len(await self.theatre.read(GUILD, other)), 1)

    async def test_another_servers_script_is_untouched(self) -> None:
        elsewhere = await self.campaigns.create(GUILD + 1, "Elsewhere", DM_USER)
        theirs = await self.transcripts.open_session(GUILD + 1, elsewhere.id, START)
        await self.transcripts.add_lines(GUILD + 1, theirs, [Line(MS + 1000, TAVIN, "hi", "hi")])
        async with self.db.guild(GUILD + 1) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s", (theirs,)
            )
            line = int((await cur.fetchone())["id"])  # type: ignore[index]
        await self.theatre.replace(GUILD + 1, theirs, [Row(TAVIN, "Hi.", (line,))])
        await self.theatre.replace(GUILD, self.session, self.rows())
        await self.sql(
            "UPDATE transcript_lines SET text = 'Changed.' WHERE id = %s", (self.ids[0],)
        )
        await self.sql("DELETE FROM transcript_lines WHERE id = %s", (self.ids[1],))
        self.assertEqual(len(await self.theatre.read(GUILD + 1, theirs)), 1)
