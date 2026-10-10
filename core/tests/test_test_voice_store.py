"""The second yes for test recordings (#1019) against a real Postgres (CI)."""

from dmbot.test_recording.store import TestVoiceStore
from tests.pg import DatabaseTest

GUILD, OTHER_GUILD, ALEX, SAM = 1, 2, 11, 12


class StoreTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = TestVoiceStore(self.db)

    async def test_a_yes_is_remembered_after_a_restart(self) -> None:
        await self.store.grant(GUILD, ALEX)
        self.assertTrue(self.store.has(GUILD, ALEX))
        fresh = TestVoiceStore(self.db)
        self.assertFalse(fresh.has(GUILD, ALEX))  # nothing loaded yet: nothing is saved
        await fresh.load(GUILD)
        self.assertTrue(fresh.has(GUILD, ALEX))
        self.assertFalse(fresh.has(GUILD, SAM))

    async def test_saying_yes_twice_is_harmless(self) -> None:
        first = await self.store.grant(GUILD, ALEX)
        second = await self.store.grant(GUILD, ALEX)
        self.assertGreaterEqual(second, first)

    async def test_a_yes_in_one_server_is_not_a_yes_in_another(self) -> None:
        await self.store.grant(GUILD, ALEX)
        self.assertFalse(self.store.has(OTHER_GUILD, ALEX))
        fresh = TestVoiceStore(self.db)
        await fresh.load(OTHER_GUILD)
        self.assertFalse(fresh.has(OTHER_GUILD, ALEX))

    async def test_stopping_is_at_once_and_stays_after_a_restart(self) -> None:
        await self.store.grant(GUILD, ALEX)
        self.store.stop_now(GUILD, ALEX)
        self.assertFalse(self.store.has(GUILD, ALEX))  # before any database work
        await self.store.revoke(GUILD, ALEX)
        fresh = TestVoiceStore(self.db)
        await fresh.load(GUILD)
        self.assertFalse(fresh.has(GUILD, ALEX))
