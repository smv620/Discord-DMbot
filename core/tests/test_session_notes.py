"""Session notes (#3026): each session's beat sheet, kept with the campaign. It is written when a
recap's beats step finishes and replaced by a redone recap; it survives every way transcript lines
go (only the pointers to them are dropped); it goes with the campaign, in the backup and along
with a move. Invented names."""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from dmbot.accounts import delete_person
from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.recaps.inputs import story_so_far
from dmbot.recaps.notes import SessionNotesSection, SessionNotesStore, numbered
from dmbot.recaps.store import RecapStore
from dmbot.schema import MIGRATIONS
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.pg import REQUIRE_DB, TEST_URL, DatabaseTest

GUILD, OTHER_GUILD = 1, 2
DM, ME = 7, 8
NOW = 1_800_000_000


def beat(what: str, lines: list[int]) -> dict[str, Any]:
    return {"what": what, "who": ["Mira"], "lines": lines, "kind": "event", "where": ""}


FIRST = [beat("Mira opened the silver door.", [1, 2]), beat("Tavin kept watch.", [3])]
SECOND = [beat("The party crossed the bridge.", [1])]


def line(seconds: int, user: int, text: str) -> Line:
    return Line(seconds * 1000, user, text, text)


class NotesTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.recaps = RecapStore(self.db)
        self.notes = SessionNotesStore(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.camp = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.other = await self.campaigns.create(GUILD, "Sunken City", DM)
        self.far = await self.campaigns.create(OTHER_GUILD, "Far Away", DM)

    async def beats_step(self, campaign: Any, session_at: int, sheet: list[Any]) -> None:
        """What the pipeline does when the beats step finishes."""
        if await self.recaps.get(GUILD, campaign.id, session_at) is None:
            await self.recaps.enqueue(GUILD, campaign.id, session_at, dm_user_id=DM)
        await self.recaps.update(GUILD, campaign.id, session_at, state="checked", beats=sheet)

    async def session(self, campaign: Any, started: int) -> str:
        sid = await self.transcripts.open_session(GUILD, campaign.id, started)
        await self.transcripts.add_lines(
            GUILD, sid, [line(started + 1, ME, "hello there"), line(started + 2, DM, "hi")]
        )
        await self.transcripts.end_session(GUILD, sid, started + 3600)
        return sid

    async def kept(self, campaign: Any = None, session_at: int = 1000) -> Any:
        return await self.notes.get(GUILD, (campaign or self.camp).id, session_at)

    def pointers(self, found: Any) -> list[list[int]]:
        return [b["lines"] for b in found.beats]


class Writing(NotesTest):
    async def test_the_beats_step_keeps_the_sheet_as_the_sessions_notes(self) -> None:
        await self.beats_step(self.camp, 1000, FIRST)
        found = await self.kept()
        self.assertEqual([b["what"] for b in found.beats], [b["what"] for b in FIRST])
        self.assertGreater(found.written_at, 0)
        self.assertGreater(found.job_created_at, 0)

    async def test_a_redone_recap_replaces_the_notes(self) -> None:
        await self.beats_step(self.camp, 1000, FIRST)
        await self.beats_step(self.camp, 1000, SECOND)
        found = await self.kept()
        self.assertEqual([b["what"] for b in found.beats], ["The party crossed the bridge."])
        self.assertEqual(len(await self.notes.all(GUILD, self.camp.id)), 1)

    async def test_a_recap_started_over_keeps_the_notes_it_had(self) -> None:
        await self.beats_step(self.camp, 1000, FIRST)
        await self.recaps.update(GUILD, self.camp.id, 1000, state="queued", beats=[])
        self.assertEqual(len((await self.kept()).beats), 2)

    async def test_a_late_write_to_a_job_that_moved_on_changes_nothing(self) -> None:
        await self.beats_step(self.camp, 1000, FIRST)
        async with self.db.guild(GUILD) as conn:  # the DM approved and posted it: no longer a draft
            await conn.execute(
                "UPDATE recap_jobs SET status = 'posted' WHERE guild_id = %s AND campaign_id = %s",
                (GUILD, self.camp.id),
            )
        await self.recaps.update(GUILD, self.camp.id, 1000, state="checked", beats=SECOND)
        self.assertEqual(len((await self.kept()).beats), 2)

    async def test_a_sheet_still_being_built_is_not_the_notes(self) -> None:
        await self.recaps.enqueue(GUILD, self.camp.id, 1000, dm_user_id=DM)
        await self.recaps.update(GUILD, self.camp.id, 1000, state="beats", beats=FIRST)
        self.assertIsNone(await self.kept())

    async def test_each_campaign_and_server_keeps_its_own(self) -> None:
        await self.beats_step(self.camp, 1000, FIRST)
        self.assertIsNone(await self.kept(self.other))
        self.assertEqual(await self.notes.all(OTHER_GUILD, self.far.id), [])
        self.assertEqual(await self.notes.all(OTHER_GUILD, self.camp.id), [])

    async def test_they_are_read_oldest_first_and_numbered_for_discord(self) -> None:
        await self.beats_step(self.camp, 2000, SECOND)
        await self.beats_step(self.camp, 1000, FIRST)
        every = await self.notes.all(GUILD, self.camp.id)
        self.assertEqual([n.session_at for n in every], [1000, 2000])
        self.assertEqual(
            numbered(every[0]), "1. Mira opened the silver door.\n2. Tavin kept watch."
        )
        before = await self.notes.all(GUILD, self.camp.id, before=2000)
        self.assertEqual([n.session_at for n in before], [1000])


class WhenLinesGo(NotesTest):
    """Delete my transcripts, "Keep transcripts for", Stop while saving: the notes stay and lose
    only the pointers to the lines that went."""

    async def setUp_session(self) -> str:
        started = 1000
        sid = await self.session(self.camp, started)
        await self.beats_step(self.camp, started, FIRST)
        return sid

    def assertTextKept(self, found: Any) -> None:
        self.assertEqual(
            [b["what"] for b in found.beats], [b["what"] for b in FIRST]
        )  # (every beat's text stays)

    async def test_delete_my_transcripts_leaves_the_notes_and_drops_the_pointers(self) -> None:
        await self.setUp_session()
        deleted = await self.transcripts.delete_person(GUILD, ME)
        self.assertGreater(deleted.lines, 0)
        found = await self.kept()
        self.assertTextKept(found)
        self.assertEqual(self.pointers(found), [[], []])

    async def test_retention_deletes_the_transcript_but_not_the_notes(self) -> None:
        sid = await self.setUp_session()
        gone = await self.transcripts.delete_session_transcript(GUILD, self.camp.id, sid, NOW, NOW)
        self.assertGreater(gone, 0)
        found = await self.kept()
        self.assertTextKept(found)
        self.assertEqual(self.pointers(found), [[], []])

    async def test_stop_while_saving_takes_lines_out_and_the_pointers_with_them(self) -> None:
        sid = await self.setUp_session()
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s", (sid,)
            )
            ids = [int(r["id"]) for r in await cur.fetchall()]
        await self.transcripts.remove_lines(GUILD, sid, ids[:1])
        found = await self.kept()
        self.assertTextKept(found)
        self.assertEqual(self.pointers(found), [[], []])

    async def test_another_sessions_notes_keep_their_pointers(self) -> None:
        sid = await self.setUp_session()
        await self.beats_step(self.camp, 5000, SECOND)
        await self.transcripts.delete_session_transcript(GUILD, self.camp.id, sid, NOW, NOW)
        self.assertEqual(self.pointers(await self.kept(session_at=5000)), [[1]])

    async def test_nothing_changes_for_another_campaign_with_the_same_start(self) -> None:
        sid = await self.setUp_session()
        await self.beats_step(self.other, 1000, FIRST)
        await self.transcripts.delete_session_transcript(GUILD, self.camp.id, sid, NOW, NOW)
        self.assertEqual(self.pointers(await self.kept(self.other)), [[1, 2], [3]])

    async def test_deleting_an_account_leaves_the_notes(self) -> None:
        await self.setUp_session()
        await delete_person(self.db, DM, now=NOW)
        self.assertEqual(len((await self.kept()).beats), 2)


class WithTheCampaign(NotesTest):
    async def test_campaign_delete_removes_them(self) -> None:
        await self.beats_step(self.camp, 1000, FIRST)
        await self.beats_step(self.other, 1000, FIRST)
        await self.campaigns.delete(GUILD, self.camp.id)
        self.assertEqual(await self.notes.all(GUILD, self.camp.id), [])
        self.assertIsNotNone(await self.kept(self.other))

    async def test_a_backup_holds_every_sessions_notes_and_a_restore_puts_them_back(self) -> None:
        self.campaigns.register_section(SessionNotesSection())
        await self.beats_step(self.camp, 1000, FIRST)
        await self.beats_step(self.camp, 2000, SECOND)
        data = await self.campaigns.export(GUILD, self.camp.id)
        self.assertEqual(len(data["sections"]["session_notes"]), 2)
        restored = await self.campaigns.import_backup(GUILD, data, DM)
        again = await self.notes.all(GUILD, restored.id)
        self.assertEqual([n.session_at for n in again], [1000, 2000])
        self.assertEqual(
            [b["what"] for b in again[0].beats], [b["what"] for b in FIRST]
        )  # (the same words; the file's sheet is read back into its canonical form)

    async def test_a_backup_row_keeps_only_the_beats_own_fields(self) -> None:
        section = SessionNotesSection()
        row = {
            "session_at": 1000,
            "beats": [{**FIRST[0], "junk": "x" * 50}],
            "written_at": 1,
            "job_created_at": 0,
        }
        [checked] = section.check([row])
        self.assertNotIn("junk", checked.beats[0])

    async def test_an_empty_sheet_or_the_same_session_twice_is_refused(self) -> None:
        section = SessionNotesSection()
        base = {"session_at": 1000, "beats": FIRST, "written_at": 1, "job_created_at": 0}
        with self.assertRaises(CampaignError):
            section.check([{**base, "beats": []}])
        with self.assertRaises(CampaignError):
            section.check([base, base])

    async def test_a_damaged_backup_is_refused(self) -> None:
        self.campaigns.register_section(SessionNotesSection())
        await self.beats_step(self.camp, 1000, FIRST)
        data = await self.campaigns.export(GUILD, self.camp.id)
        data["sections"]["session_notes"][0]["beats"] = [{"what": 5}]
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD, data, DM)

    async def test_a_move_to_another_server_carries_them_along(self) -> None:
        await self.beats_step(self.camp, 1000, FIRST)
        async with self.db.guild(GUILD) as conn:  # the one statement a move makes (#2550)
            await conn.execute(
                "SELECT set_config('dmbot.moving_campaign', %s, true)", (self.camp.id,)
            )
            await conn.execute(
                "SELECT set_config('dmbot.moving_to', %s, true)", (str(OTHER_GUILD),)
            )
            await conn.execute(
                "UPDATE campaigns SET guild_id = %s WHERE guild_id = %s AND id = %s",
                (OTHER_GUILD, GUILD, self.camp.id),
            )
        moved = await self.notes.get(OTHER_GUILD, self.camp.id, 1000)
        assert moved is not None
        self.assertEqual(len(moved.beats), 2)
        self.assertIsNone(await self.notes.get(GUILD, self.camp.id, 1000))


class TheStorySoFar(NotesTest):
    async def test_a_session_without_a_recap_is_told_by_its_notes(self) -> None:
        from dmbot.recaps.accepted import AcceptedStore

        await self.beats_step(self.camp, 1000, FIRST)
        told = await story_so_far(AcceptedStore(self.db), GUILD, self.camp.id, 5000, self.notes)
        self.assertEqual(len(told), 1)
        self.assertIn("Mira opened the silver door.", told[0])
        self.assertEqual(await story_so_far(AcceptedStore(self.db), GUILD, self.camp.id, 5000), ())

    async def test_a_beats_private_note_never_reaches_the_story(self) -> None:
        from dmbot.recaps.accepted import AcceptedStore

        sheet = [{**beat("Mira opened the door.", [1]), "note": "the SECRET reason", "keep": True}]
        await self.beats_step(self.camp, 1000, sheet)
        told = await story_so_far(AcceptedStore(self.db), GUILD, self.camp.id, 5000, self.notes)
        self.assertNotIn("SECRET", " ".join(told))
        self.assertIn("Mira opened the door.", told[0])

    async def test_an_accepted_recap_is_told_instead_of_the_notes(self) -> None:
        from dmbot.recaps.accepted import AcceptedStore

        await self.beats_step(self.camp, 1000, FIRST)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO recap_accepted (guild_id, campaign_id, session_at, version,"
                " text_hash, title, summary, accepted_by, accepted_at)"
                " VALUES (%s, %s, 1000, 1, %s, 'T', 'The accepted telling.', %s, 1)",
                (GUILD, self.camp.id, "a" * 64, DM),
            )
        told = await story_so_far(AcceptedStore(self.db), GUILD, self.camp.id, 5000, self.notes)
        self.assertEqual(told, ("The accepted telling.",))


class TheStoryBudget(NotesTest):
    async def test_a_long_notes_entry_is_cut_to_its_share_at_a_beat_and_recaps_survive(
        self,
    ) -> None:
        from dmbot.recaps.accepted import AcceptedStore
        from dmbot.recaps.inputs import NOTES_ENTRY_CHARS

        async with self.db.guild(GUILD) as conn:
            for at, text in ((1000, "The first recap. " * 100), (2000, "The second recap. " * 100)):
                await conn.execute(
                    "INSERT INTO recap_accepted (guild_id, campaign_id, session_at, version,"
                    " text_hash, title, summary, accepted_by, accepted_at)"
                    " VALUES (%s, %s, %s, 1, %s, 'T', %s, %s, 1)",
                    (GUILD, self.camp.id, at, "a" * 64, text.strip(), DM),
                )
        long = [beat(f"Beat number {n} " + "x" * 90, [n]) for n in range(1, 160)]  # about 15,000
        await self.beats_step(self.camp, 3000, long)
        told = await story_so_far(AcceptedStore(self.db), GUILD, self.camp.id, 9000, self.notes)
        self.assertEqual(len(told), 3)
        self.assertTrue(told[0].startswith("The first recap."))
        self.assertTrue(told[1].startswith("The second recap."))
        self.assertLessEqual(len(told[2]), NOTES_ENTRY_CHARS)
        self.assertTrue(told[2].endswith("x" * 90))  # cut after a whole beat, never in the middle

    async def test_many_notes_only_sessions_keep_within_their_total_and_both_recaps_whole(
        self,
    ) -> None:
        from dmbot.recaps.accepted import AcceptedStore
        from dmbot.recaps.inputs import NOTES_TOTAL_CHARS

        recaps = {100: "The first recap. " * 100, 200: "The second recap. " * 100}
        async with self.db.guild(GUILD) as conn:
            for at, text in recaps.items():
                await conn.execute(
                    "INSERT INTO recap_accepted (guild_id, campaign_id, session_at, version,"
                    " text_hash, title, summary, accepted_by, accepted_at)"
                    " VALUES (%s, %s, %s, 1, %s, 'T', %s, %s, 1)",
                    (GUILD, self.camp.id, at, "a" * 64, text.strip(), DM),
                )
        long = [beat(f"Beat number {n} " + "x" * 90, [n]) for n in range(1, 160)]
        for at in range(1000, 1000 + 22):  # 22 more sessions, each with a full-size sheet
            await self.beats_step(self.camp, at, long)
        told = await story_so_far(AcceptedStore(self.db), GUILD, self.camp.id, 9000, self.notes)
        self.assertEqual(told[0], recaps[100].strip())
        self.assertEqual(told[1], recaps[200].strip())
        notes_told = told[2:]
        self.assertTrue(notes_told)
        self.assertLess(len(notes_told), 22)  # the older ones did not fit
        self.assertLessEqual(sum(len(t) for t in notes_told), NOTES_TOTAL_CHARS)

    async def test_a_session_whose_transcript_still_exists_is_not_told_by_its_notes(self) -> None:
        from dmbot.recaps.accepted import AcceptedStore

        started = 1000
        sid = await self.transcripts.open_session(GUILD, self.camp.id, started)
        await self.transcripts.add_lines(GUILD, sid, [line(started + 1, ME, "hello there")])
        await self.transcripts.end_session(GUILD, sid, started + 3600)
        await self.beats_step(self.camp, started, FIRST)
        told = await story_so_far(AcceptedStore(self.db), GUILD, self.camp.id, 5000, self.notes)
        self.assertEqual(told, ())
        await self.transcripts.delete_session_transcript(
            GUILD, self.camp.id, sid, NOW, NOW
        )  # (retention took the transcript: now the notes tell it)
        again = await story_so_far(AcceptedStore(self.db), GUILD, self.camp.id, 5000, self.notes)
        self.assertEqual(len(again), 1)


class TheMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.name = "0293_session_notes"
        self.db = await Database.open(
            TEST_URL,
            schema=self.schema,
            max_size=2,
            migrations=tuple(m for m in MIGRATIONS if m[0] != self.name),
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def test_the_checked_beats_recap_jobs_already_hold_become_the_notes(self) -> None:
        camp = await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM)
        other = await CampaignStore(self.db).create(GUILD, "Sunken City", DM)
        recaps = RecapStore(self.db)
        # (campaign, session, beats, the job's state): only a checked sheet is the session's notes
        jobs: tuple[tuple[Any, int, list[Any], str], ...] = (
            (camp, 1000, FIRST, "checked"),
            (camp, 2000, [], "checked"),  # no beats
            (camp, 3000, SECOND, "beats"),  # a sheet still being built
            (camp, 4000, SECOND, "failed"),  # a job that failed
            (camp, 5000, SECOND, "ready"),
            (other, 1000, SECOND, "prose"),
        )
        for campaign, at, sheet, state in jobs:
            await recaps.enqueue(GUILD, campaign.id, at, dm_user_id=DM)
            await recaps.update(GUILD, campaign.id, at, state="beats", beats=sheet)
            async with self.db.guild(GUILD) as conn:  # (the state, without the notes' own hook)
                await conn.execute(
                    "UPDATE recap_jobs SET state = %s WHERE guild_id = %s AND campaign_id = %s"
                    " AND session_at = %s",
                    (state, GUILD, campaign.id, at),
                )
        await self.db.migrate(MIGRATIONS)
        notes = SessionNotesStore(self.db)
        mine = await notes.all(GUILD, camp.id)
        self.assertEqual([n.session_at for n in mine], [1000, 5000])
        self.assertEqual(mine[0].beats, FIRST)
        theirs = await notes.all(GUILD, other.id)
        self.assertEqual(theirs[0].beats, SECOND)
