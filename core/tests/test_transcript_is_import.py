"""Is a session one loaded from a file? `TranscriptStore.is_import` (#3091): true only for a
session with its session_imports row, false for a session recorded live, and false for another
server's session. Invented ids only."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

GUILD, OTHER_GUILD, DM = 58, 59, 5


class TheImportMark(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        campaigns = CampaignStore(self.db)
        self.campaign = await campaigns.create(GUILD, "One", DM)
        self.other = await campaigns.create(OTHER_GUILD, "Two", DM)
        self.transcripts = TranscriptStore(self.db)
        self.imported = await self.transcripts.open_session(GUILD, self.campaign.id, 1_700_000_000)
        self.live = await self.transcripts.open_session(GUILD, self.campaign.id, 1_700_100_000)
        self.theirs = await self.transcripts.open_session(OTHER_GUILD, self.other.id, 1_700_200_000)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id,"
                " file_name, imported_by, imported_at) VALUES (%s, %s, %s, %s, 'a.txt', %s, 1)",
                (GUILD, self.campaign.id, self.imported, "b" * 32, DM),
            )
        async with self.db.guild(OTHER_GUILD) as conn:
            await conn.execute(
                "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id,"
                " file_name, imported_by, imported_at) VALUES (%s, %s, %s, %s, 'b.txt', %s, 1)",
                (OTHER_GUILD, self.other.id, self.theirs, "c" * 32, DM),
            )

    async def test_a_session_loaded_from_a_file_is_an_import(self) -> None:
        self.assertTrue(await self.transcripts.is_import(GUILD, self.imported))

    async def test_a_session_recorded_live_is_not_an_import(self) -> None:
        self.assertFalse(await self.transcripts.is_import(GUILD, self.live))

    async def test_another_servers_import_is_not_an_import_here(self) -> None:
        self.assertFalse(await self.transcripts.is_import(GUILD, self.theirs))
        self.assertTrue(await self.transcripts.is_import(OTHER_GUILD, self.theirs))
