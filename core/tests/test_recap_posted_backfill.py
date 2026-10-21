"""Migration 0149 on a database that already holds recaps (#2183): a recap posted while its audio
was still queued is marked failed, so the audio step, which now takes posted recaps, does not
narrate it after the deploy with nobody asking. A draft with queued audio is left alone. The
rows are planted before the migration runs, the way a deploy meets them. Invented values."""

from __future__ import annotations

import unittest
import uuid

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.recaps.store import RecapStore
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL

GUILD = 1
DM = 7


class PostedQueuedBackfill(unittest.IsolatedAsyncioTestCase):
    async def test_a_posted_recap_with_queued_audio_is_marked_failed_and_a_draft_is_not(
        self,
    ) -> None:
        if not TEST_URL:
            self.skipTest("needs the test database")
        schema = f"t_{uuid.uuid4().hex[:16]}"
        names = [n for n, _ in MIGRATIONS]
        before = MIGRATIONS[: names.index("0149_audio_again_posted")]
        db = await Database.open(TEST_URL, schema=schema, max_size=2, migrations=before)
        try:
            campaigns = CampaignStore(db)
            store = RecapStore(db)
            posted = await campaigns.create(GUILD, "Posted", DM)
            draft = await campaigns.create(GUILD, "Draft", DM)
            for campaign in (posted, draft):
                await store.enqueue(GUILD, campaign.id, 100, dm_user_id=DM)
                await store.update(GUILD, campaign.id, 100, state="ready", title="T", summary="S")
                await store.set_audio(GUILD, campaign.id, 100, audio="queued")
            async with db.guild(GUILD) as conn:
                await conn.execute(
                    "UPDATE recap_jobs SET status = 'posted' WHERE campaign_id = %s", (posted.id,)
                )
            await db.migrate(MIGRATIONS)
            got_posted = await store.get(GUILD, posted.id, 100)
            got_draft = await store.get(GUILD, draft.id, 100)
            assert got_posted is not None and got_draft is not None
            self.assertEqual((got_posted.status, got_posted.audio), ("posted", "failed"))
            self.assertEqual(
                got_posted.audio_error,
                "The audio wasn't made before this recap was posted. "
                "You can make it now for this page.",
            )
            self.assertEqual((got_draft.status, got_draft.audio), ("draft", "queued"))
            self.assertEqual(await store.audio_due(GUILD), [(draft.id, 100)])
        finally:
            await db.close()
            await drop_schema(TEST_URL, schema)
