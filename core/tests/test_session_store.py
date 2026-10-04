"""Saved sessions in Postgres: per-server isolation, routing by shard, cleanup."""

import psycopg

from dmbot.campaigns import CampaignStore
from dmbot.sessions import SavedSession, SessionStore
from dmbot.sharding import ShardSettings
from tests.pg import DatabaseTest

# Server IDs chosen so (id >> 22) % 2 puts A on shard 0 and B on shard 1.
GUILD_A = 2 << 22
GUILD_B = 3 << 22


def saved(guild_id: int, campaign_id: str, **kw: object) -> SavedSession:
    base: dict[str, object] = {
        "guild_id": guild_id,
        "campaign_id": campaign_id,
        "voice_channel_id": 10,
        "screen_channel_id": 11,
        "started_by": 7,
        "started_at": 1_700_000_000,
        "notice_posted": False,
    }
    base.update(kw)
    return SavedSession(**base)  # type: ignore[arg-type]


class SessionStoreTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = SessionStore(self.db)
        self.campaigns = CampaignStore(self.db)
        self.a = await self.campaigns.create(GUILD_A, "A", 7)
        self.b = await self.campaigns.create(GUILD_B, "B", 8)

    async def test_save_get_replace_clear(self) -> None:
        await self.store.save(saved(GUILD_A, self.a.id))
        self.assertEqual(await self.store.get(GUILD_A), saved(GUILD_A, self.a.id))
        await self.store.save(saved(GUILD_A, self.a.id, voice_channel_id=99))
        got = await self.store.get(GUILD_A)
        assert got is not None
        self.assertEqual(got.voice_channel_id, 99)
        await self.store.clear(GUILD_A)
        self.assertIsNone(await self.store.get(GUILD_A))
        self.assertEqual(await self.store.guilds_to_resume(ShardSettings(1, (0,))), [])

    async def test_other_servers_cannot_see_it(self) -> None:
        await self.store.save(saved(GUILD_A, self.a.id))
        self.assertIsNone(await self.store.get(GUILD_B))
        await self.store.clear(GUILD_B)  # clearing another server's does nothing
        self.assertIsNotNone(await self.store.get(GUILD_A))

    async def test_cannot_point_at_another_servers_campaign(self) -> None:
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            await self.store.save(saved(GUILD_A, self.b.id))

    async def test_resume_list_is_split_by_shard(self) -> None:
        await self.store.save(saved(GUILD_A, self.a.id))
        await self.store.save(saved(GUILD_B, self.b.id))
        self.assertEqual(
            await self.store.guilds_to_resume(ShardSettings(1, (0,))), [GUILD_A, GUILD_B]
        )
        self.assertEqual(await self.store.guilds_to_resume(ShardSettings(2, (0,))), [GUILD_A])
        self.assertEqual(await self.store.guilds_to_resume(ShardSettings(2, (1,))), [GUILD_B])

    async def test_notice_flag(self) -> None:
        await self.store.save(saved(GUILD_A, self.a.id))
        await self.store.mark_notice_posted(GUILD_A)
        got = await self.store.get(GUILD_A)
        assert got is not None
        self.assertTrue(got.notice_posted)

    async def test_deleting_the_campaign_removes_the_session(self) -> None:
        await self.store.save(saved(GUILD_A, self.a.id))
        await self.campaigns.delete(GUILD_A, self.a.id)
        self.assertIsNone(await self.store.get(GUILD_A))
