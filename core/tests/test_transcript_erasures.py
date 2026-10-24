"""A restore does not bring back lines a person erased with "Delete my transcripts" (#2605).
Needs Postgres."""

from __future__ import annotations

from dmbot.campaigns.parts import LeftOut
from dmbot.transcript.store import TranscriptStore
from tests.test_transcript_parts import DM_A, DM_B, GUILD_A, GUILD_B, SPEAKER, _Base

OTHER = 77
LATER = 4_000_000_000  # a session that began long after any erase made while this test runs


class Erasures(_Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.transcripts = TranscriptStore(self.db)

    async def seed(self, started: int = 1000) -> str:
        sid = await self.session(started, 3)
        await self.sql(
            GUILD_A,
            "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard, text)"
            " VALUES (%s, %s, 9000, %s, 'their raw', 'their clean')",
            GUILD_A, sid, OTHER,
        )  # fmt: skip
        return sid

    async def copy(self) -> tuple[dict, list[bytes]]:  # type: ignore[type-arg]
        return await self.campaigns.export_set(GUILD_A, self.cid)

    async def lines_of(self, guild: int, campaign: str) -> list[int]:
        rows = await self.sql(
            guild,
            "SELECT l.user_id FROM transcript_lines l JOIN transcript_sessions s"
            " ON s.id = l.session_id AND s.guild_id = l.guild_id"
            " WHERE s.guild_id = %s AND s.campaign_id = %s ORDER BY l.id",
            guild, campaign,
        )  # fmt: skip
        return [int(r["user_id"]) for r in rows]

    async def test_a_copy_made_before_an_erase_comes_back_without_those_lines(self) -> None:
        await self.seed()
        main, files = await self.copy()
        await self.transcripts.delete_person(GUILD_A, SPEAKER)
        left = LeftOut()
        restored = await self.campaigns.import_backup(
            GUILD_B, main, DM_B, parts=files, left_out=left
        )
        self.assertEqual(await self.lines_of(GUILD_B, restored.id), [OTHER])
        self.assertEqual(left.lines, 3)
        (session,) = await self.sql(
            GUILD_B, "SELECT line_count, speakers FROM transcript_sessions WHERE campaign_id = %s",
            restored.id,
        )  # fmt: skip
        self.assertEqual((session["line_count"], session["speakers"]), (1, [OTHER]))

    async def test_a_restore_over_the_same_server_skips_them_too(self) -> None:
        await self.seed()
        main, files = await self.copy()
        await self.transcripts.delete_person(GUILD_A, SPEAKER)
        left = LeftOut()
        again = await self.campaigns.import_backup(
            GUILD_A, main, DM_A, replace_campaign_id=self.cid, parts=files, left_out=left
        )
        self.assertEqual(await self.lines_of(GUILD_A, again.id), [OTHER])

    async def test_an_erase_in_the_server_restored_into_counts_as_well(self) -> None:
        await self.seed()
        main, files = await self.copy()
        await self.transcripts.delete_person(GUILD_B, SPEAKER)  # (nothing to delete there)
        left = LeftOut()
        restored = await self.campaigns.import_backup(
            GUILD_B, main, DM_B, parts=files, left_out=left
        )
        self.assertEqual(await self.lines_of(GUILD_B, restored.id), [OTHER])
        self.assertEqual(left.lines, 3)

    async def test_a_session_that_began_after_the_erase_stays_whole(self) -> None:
        await self.seed(LATER)
        main, files = await self.copy()
        await self.transcripts.delete_person(GUILD_A, SPEAKER)
        left = LeftOut()
        restored = await self.campaigns.import_backup(
            GUILD_B, main, DM_B, parts=files, left_out=left
        )
        self.assertEqual(sorted(await self.lines_of(GUILD_B, restored.id)), [SPEAKER] * 3 + [OTHER])
        self.assertEqual(left.lines, 0)

    async def test_nobody_erased_means_nothing_is_left_out(self) -> None:
        await self.seed()
        main, files = await self.copy()
        left = LeftOut()
        restored = await self.campaigns.import_backup(
            GUILD_B, main, DM_B, parts=files, left_out=left
        )
        self.assertEqual(len(await self.lines_of(GUILD_B, restored.id)), 4)
        self.assertEqual(left.lines, 0)

    async def test_the_door_answers_for_the_one_server_asked_and_leaves_the_callers_alone(
        self,
    ) -> None:
        await self.transcripts.delete_person(GUILD_A, SPEAKER)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT * FROM dmbot_erased_speakers(%s, %s)", (GUILD_A, [SPEAKER])
            )
            got = await cur.fetchall()
            cur = await conn.execute("SELECT * FROM transcript_erasures")
            seen = await cur.fetchall()
        self.assertEqual([int(r["user_id"]) for r in got], [SPEAKER])
        self.assertEqual(seen, [])  # (this server's own rows only, as before)
