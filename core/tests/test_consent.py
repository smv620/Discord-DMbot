import asyncio
from unittest import mock

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

    async def test_reload_cannot_bring_back_a_revoked_player(self) -> None:
        await self.store.grant(1, 2)
        # Player 2 revokes; the save hasn't happened when player 3's grant reloads
        # from the database, which still lists player 2.
        self.store.stop_now(1, 2)
        self.assertFalse(self.store.has_consent(1, 2))
        self.assertEqual(await self.store.grant(1, 3), frozenset({3}))
        self.assertFalse(self.store.has_consent(1, 2))
        await self.store.revoke(1, 2)
        self.assertEqual(await self.store.consenting(1), frozenset({3}))

    async def test_grant_and_revoke_at_the_same_time(self) -> None:
        for user in range(10):
            await self.store.grant(1, user)
        await asyncio.gather(
            *(self.store.grant(1, 100 + u) for u in range(10)),
            *(self.store.revoke(1, u) for u in range(10)),
        )
        expected = frozenset(range(100, 110))
        self.assertEqual(await self.store.consenting(1), expected)
        self.assertEqual(await ConsentStore(self.db).consenting(1), expected)

    async def test_failed_revoke_still_stops_recording(self) -> None:
        await self.store.grant(1, 2)
        with (
            mock.patch.object(self.db, "guild", side_effect=RuntimeError("database down")),
            self.assertRaises(RuntimeError),
        ):
            await self.store.revoke(1, 2)
        self.assertFalse(self.store.has_consent(1, 2))
        await self.store.grant(1, 3)  # reloads; the database still lists player 2
        self.assertEqual(await self.store.consenting(1), frozenset({3}))
        # Giving consent again is a clear choice and lifts the hold.
        await self.store.grant(1, 2)
        self.assertTrue(self.store.has_consent(1, 2))
