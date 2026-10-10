import asyncio
from unittest import mock

from dmbot.consent import TERMS_VERSION, ConsentStore
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
        self.assertTrue(await self.store.revoke(1, 2))  # a saved consent was removed
        self.assertFalse(await self.store.revoke(1, 2))  # nothing left to remove
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

    async def test_granted_at_is_per_server_and_cleared_by_revoke(self) -> None:
        self.assertIsNone(await self.store.granted_at(1, 2))
        await self.store.grant(1, 2)
        when = await self.store.granted_at(1, 2)
        self.assertIsNotNone(when)
        self.assertIsNone(await self.store.granted_at(9, 2))  # another server
        await self.store.revoke(1, 2)
        self.assertIsNone(await self.store.granted_at(1, 2))

    async def test_granted_at_is_none_while_a_revoke_is_unsaved(self) -> None:
        await self.store.grant(1, 2)
        self.store.stop_now(1, 2)
        self.assertIsNone(await self.store.granted_at(1, 2))

    async def test_a_stop_during_a_grant_wins(self) -> None:
        lock = self.store._lock(1)
        await lock.acquire()  # the grant's database work is under way
        grant = asyncio.create_task(self.store.grant(1, 2))
        await asyncio.sleep(0)
        self.store.stop_now(1, 2)  # "No thanks" pressed meanwhile
        lock.release()
        await grant
        self.assertFalse(self.store.has_consent(1, 2))
        await self.store.revoke(1, 2)
        self.assertFalse(self.store.has_consent(1, 2))
        self.assertEqual(await ConsentStore(self.db).consenting(1), frozenset())

    async def test_with_an_outside_company_only_yeses_given_knowing_that_count(self) -> None:
        await self.store.grant(1, 2)  # under local Whisper
        await self.store.grant(1, 3, outside_to="deepgram")  # the request named Deepgram
        switched = ConsentStore(self.db, outside="deepgram")
        self.assertEqual(await switched.consenting(1), frozenset({3}))
        self.assertIsNone(await switched.granted_at(1, 2))
        self.assertIsNotNone(await switched.granted_at(1, 3))
        await switched.grant(1, 2, outside_to="deepgram")  # agreed again to the new wording
        self.assertEqual(await switched.consenting(1), frozenset({2, 3}))
        # Back on local Whisper, every yes counts.
        self.assertEqual(await ConsentStore(self.db).consenting(1), frozenset({2, 3}))
        # A yes to Deepgram doesn't cover a different company.
        other = ConsentStore(self.db, outside="cloud")
        self.assertEqual(await other.consenting(1), frozenset())

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


class TermsVersionTests(DatabaseTest):
    """#35: only a yes under the current wording counts."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = ConsentStore(self.db)

    async def test_a_yes_under_older_wording_does_not_count(self) -> None:
        await self.store.grant(1, 2)
        await self.store.grant(1, 3)
        async with self.db.guild(1) as conn:
            await conn.execute(
                "UPDATE consent SET terms_version = %s WHERE user_id = 2", (TERMS_VERSION - 1,)
            )
        fresh = ConsentStore(self.db)
        self.assertEqual(await fresh.consenting(1), frozenset({3}))
        self.assertEqual(set(await fresh.granted_times(1, [2, 3])), {3})
        self.assertEqual(await fresh.outdated(1, [2, 3, 4]), {2})
        await fresh.grant(1, 2, method="consent_command")  # asked again, said yes
        self.assertEqual(await fresh.consenting(1), frozenset({2, 3}))
        self.assertEqual(await fresh.outdated(1, [2]), set())

    async def test_a_yes_from_before_the_minimum_age_wording_is_asked_again(self) -> None:
        # #1018: version 4 is "16 or older". A yes given under version 3 (no age) is not
        # recorded until they press the new button.
        await self.store.grant(1, 2)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE consent SET terms_version = 3 WHERE user_id = 2")
        fresh = ConsentStore(self.db)
        self.assertEqual(TERMS_VERSION, 4)
        self.assertEqual(await fresh.consenting(1), frozenset())
        self.assertEqual(await fresh.outdated(1, [2]), {2})  # the join asks again
        await fresh.grant(1, 2)  # presses "I'm 16 or older, record me"
        self.assertEqual(await fresh.consenting(1), frozenset({2}))

    async def test_stopping_removes_an_older_yes_too(self) -> None:
        await self.store.grant(1, 2)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE consent SET terms_version = 1 WHERE user_id = 2")
        fresh = ConsentStore(self.db)
        self.assertTrue(await fresh.revoke(1, 2))  # it was recorded under the old wording
        self.assertEqual(await fresh.outdated(1, [2]), set())

    async def test_a_held_back_stop_is_never_asked_as_a_renewal(self) -> None:
        await self.store.grant(1, 2)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE consent SET terms_version = 1 WHERE user_id = 2")
        fresh = ConsentStore(self.db)
        fresh.stop_now(1, 2)  # pressed Stop; the save hasn't happened
        self.assertEqual(await fresh.outdated(1, [2]), set())

    async def test_unknown_method_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            await self.store.grant(1, 2, method="typed")  # type: ignore[arg-type]
