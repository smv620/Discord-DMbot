import unittest

from dmbot.consent import ConsentStore


class ConsentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = ConsentStore(":memory:")

    async def asyncTearDown(self) -> None:
        self.store.close()

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

    async def test_grant_is_idempotent(self) -> None:
        await self.store.grant(1, 2)
        self.assertEqual(await self.store.grant(1, 2), frozenset({2}))
