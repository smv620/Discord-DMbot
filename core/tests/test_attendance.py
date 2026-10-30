"""Who took part in a stored session (#3036): only the people with a line in that session, each
once, and never a session of another server. Only user ids are read, never a line's text."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.memory.models import new_id
from dmbot.transcript.attendance import session_speakers
from dmbot.transcript.models import OFF_RECORD, SIDEBAR_ANSWER
from tests.pg import DatabaseTest

GUILD, OTHER, DM = 111, 222, 9


class TheSpeakers(DatabaseTest):
    async def add_session(
        self, guild: int, campaign_id: str, lines: list[int], started_at: int = 100
    ) -> str:
        session = new_id()
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at,"
                " ended_at, line_count) VALUES (%s, %s, %s, %s, %s, %s)",
                (session, guild, campaign_id, started_at, started_at + 100, len(lines)),
            )
            for user in lines:
                await conn.execute(
                    "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id,"
                    " heard, duration_ms) VALUES (%s, %s, 5, %s, 'we meet', 1000)",
                    (guild, session, user),
                )
        return session

    async def test_each_speaker_once_for_that_session_only(self) -> None:
        campaign = await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM)
        first = await self.add_session(GUILD, campaign.id, [DM, 1001, 1001, 1002])
        await self.add_session(GUILD, campaign.id, [1003], started_at=1000)

        self.assertEqual(await session_speakers(self.db, GUILD, first), {DM, 1001, 1002})

    async def test_a_session_of_another_server_gives_nobody(self) -> None:
        campaign = await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM)
        session = await self.add_session(GUILD, campaign.id, [1001])

        self.assertEqual(await session_speakers(self.db, OTHER, session), set())

    async def test_a_session_with_no_lines_gives_nobody(self) -> None:
        campaign = await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM)
        session = await self.add_session(GUILD, campaign.id, [])

        self.assertEqual(await session_speakers(self.db, GUILD, session), set())

    async def test_only_table_speech_counts(self) -> None:
        # The DM's sidebar question, a typed chat line and an off-the-record marker each have a user
        # id, but none of them is speaking at the table.
        campaign = await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM)
        session = new_id()
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at,"
                " ended_at, line_count) VALUES (%s, %s, %s, 100, 200, 3)",
                (session, GUILD, campaign.id),
            )
            for user, sidebar, typed in (
                (DM, SIDEBAR_ANSWER, False),
                (2001, None, True),
                (2002, OFF_RECORD, False),
                (1001, None, False),
            ):
                await conn.execute(
                    "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id,"
                    " heard, duration_ms, sidebar, typed) VALUES (%s, %s, 5, %s, 'we meet',"
                    " 1000, %s, %s)",
                    (GUILD, session, user, sidebar, typed),
                )

        self.assertEqual(await session_speakers(self.db, GUILD, session), {1001})
