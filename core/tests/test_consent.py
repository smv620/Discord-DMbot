import asyncio

from dmbot.consent import ConsentStore
from tests.pg import DatabaseTest


class ConsentTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = ConsentStore(self.db)

    async def test_default_denies(self) -> None:
        self.assertFalse(self.store.has_consent(1, 2))
        self.assertEqual(await self.store.consenting(1), frozenset())

    async def test_grant_and_revoke(self) -> None:
        self.assertEqual(await self.store.grant(1, 2), frozenset({2}))
        self.assertTrue(self.store.has_consent(1, 2))
        self.assertEqual(await self.store.revoke(1, 2), frozenset())
        self.assertFalse(self.store.has_consent(1, 2))

    async def test_per_guild(self) -> None:
        await self.store.grant(1, 2)
        self.assertFalse(self.store.has_consent(9, 2))
        self.assertEqual(await self.store.consenting(9), frozenset())

    async def test_grant_is_idempotent(self) -> None:
        await self.store.grant(1, 2)
        self.assertEqual(await self.store.grant(1, 2), frozenset({2}))

    async def test_survives_a_restart(self) -> None:
        await self.store.grant(1, 2)
        fresh = ConsentStore(self.db)  # new process: empty cache, same database
        self.assertFalse(fresh.has_consent(1, 2))  # cache not loaded yet: deny
        self.assertEqual(await fresh.consenting(1), frozenset({2}))
        self.assertTrue(fresh.has_consent(1, 2))

    async def test_concurrent_grants(self) -> None:
        await asyncio.gather(*(self.store.grant(1, u) for u in range(20)))
        self.assertEqual(len(await self.store.consenting(1)), 20)
