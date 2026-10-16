"""A budget for the campaign store's database round trips (#77): every `Database.guild()`
transaction costs a pool checkout, BEGIN, `set_config` and COMMIT, so a change that reads the row
before and after its UPDATE, or a session start spread over three transactions, would be paid for
on every command once the database is not on localhost. The statements and transactions of a
few common store calls are counted here, so a regression fails CI. A deliberate increase
needs the number updated here, with the reason. A real Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.store import NOT_HERE
from dmbot.db import Database
from tests.pg import DatabaseTest

GUILD, DM, CO = 1, 7, 8


class _Counting:
    """Stands in for a connection and counts the statements run on it."""

    def __init__(self, conn: Any, tally: list[int]) -> None:
        self._conn, self._tally = conn, tally

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self._tally[-1] += 1
        return await self._conn.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class RoundTrips(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(GUILD, "Frostmaiden", DM)
        await self.store.add_dm(GUILD, self.campaign.id, CO)

    @asynccontextmanager
    async def counted(self) -> AsyncIterator[list[int]]:
        """Statements per `Database.guild()` transaction, counted after its `set_config`."""
        tally: list[int] = []
        original = Database.guild

        @asynccontextmanager
        async def guild(self_: Database, guild_id: int, **kw: Any) -> AsyncIterator[Any]:
            tally.append(0)
            async with original(self_, guild_id, **kw) as conn:
                yield _Counting(conn, tally)

        with patch.object(Database, "guild", guild):
            yield tally

    # (Each transaction also runs one `set_config` first; it is not counted here.)

    async def test_a_setting_is_one_transaction_and_two_statements(self) -> None:
        async with self.counted() as tally:
            changed = await self.store.set_dm_screen(GUILD, self.campaign.id, 555)
        self.assertEqual(changed.dm_screen_channel_id, 555)
        self.assertEqual(tally, [2])  # UPDATE ... RETURNING, then the DMs

    async def test_a_session_start_is_one_transaction(self) -> None:
        async with self.counted() as tally:
            got = await self.store.record_start(GUILD, self.campaign.id, 555, 666)
        self.assertEqual(
            (got.dm_screen_channel_id, got.last_voice_channel_id), (555, 666)
        )  # (it was three writes in three transactions)
        self.assertIsNotNone(got.last_played_at)
        self.assertEqual(tally, [2])

    async def test_rename_and_rulesets_and_channel_number_cost_the_same(self) -> None:
        async with self.counted() as tally:
            await self.store.rename(GUILD, self.campaign.id, "Icewind")
            await self.store.set_rulesets(GUILD, self.campaign.id, "2014", "2024")
            await self.store.set_channel_number(GUILD, self.campaign.id, 3)
        self.assertEqual(tally, [2, 2, 2])

    async def test_adding_and_removing_a_dm_read_once_with_the_row_locked(self) -> None:
        async with self.counted() as tally:
            added = await self.store.add_dm(GUILD, self.campaign.id, 99)
            removed = await self.store.remove_dm(GUILD, self.campaign.id, 99)
        self.assertEqual(added.dm_user_ids, frozenset({DM, CO, 99}))
        self.assertEqual(removed.dm_user_ids, frozenset({DM, CO}))
        self.assertEqual(tally, [3, 3])  # the locked read, the DMs, the write

    async def test_the_results_match_what_is_stored(self) -> None:
        """The shortcuts return the same campaign a fresh read would."""
        a = await self.store.set_rulesets(GUILD, self.campaign.id, "2014", "2024")
        self.assertEqual(a, await self.store.get(GUILD, self.campaign.id))
        b = await self.store.add_dm(GUILD, self.campaign.id, 99)
        self.assertEqual(b, await self.store.get(GUILD, self.campaign.id))
        c = await self.store.remove_dm(GUILD, self.campaign.id, 99)
        self.assertEqual(c, await self.store.get(GUILD, self.campaign.id))
        d = await self.store.record_start(GUILD, self.campaign.id, 5, 6)
        self.assertEqual(d, await self.store.get(GUILD, self.campaign.id))

    async def test_a_missing_campaign_is_still_not_here(self) -> None:
        for call in (
            self.store.set_dm_screen(GUILD, "f" * 32, 1),
            self.store.record_start(GUILD, "f" * 32, 1, 2),
            self.store.rename(GUILD, "f" * 32, "x"),
            self.store.set_rulesets(GUILD, "f" * 32, "2024", "2014"),
            self.store.add_dm(GUILD, "f" * 32, 9),
            self.store.remove_dm(GUILD, "f" * 32, 9),
            self.store.set_channel_number(GUILD, "f" * 32, 1),
        ):
            with self.assertRaises(CampaignError) as raised:
                await call
            self.assertEqual(str(raised.exception), NOT_HERE)

    async def test_another_server_cannot_change_it(self) -> None:
        with self.assertRaises(CampaignError):
            await self.store.record_start(2, self.campaign.id, 1, 2)
        got = await self.store.get(GUILD, self.campaign.id)
        assert got is not None
        self.assertIsNone(got.dm_screen_channel_id)

    async def test_a_channel_number_already_chosen_is_kept(self) -> None:
        first = await self.store.set_channel_number(GUILD, self.campaign.id, 2)
        second = await self.store.set_channel_number(GUILD, self.campaign.id, 5)
        self.assertEqual((first.channel_number, second.channel_number), (2, 2))
