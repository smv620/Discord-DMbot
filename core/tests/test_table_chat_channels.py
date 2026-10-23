"""The extra table-chat channels of a server's campaigns (#2522), for finding where DMbot may
have answered a picture: only channels a campaign still reads, and only that server's."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from tests.pg import DatabaseTest

GUILD, OTHER, DM = 1, 2, 7


class TableChatChannels(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.one = await self.store.create(GUILD, "One", DM)
        self.two = await self.store.create(GUILD, "Two", DM)

    async def test_none_until_a_campaign_picks_a_channel(self) -> None:
        self.assertEqual(await self.store.table_chat_channel_ids(GUILD), frozenset())

    async def test_every_campaigns_channel_while_its_switch_is_on(self) -> None:
        await self.store.set_table_chat(GUILD, self.one.id, on=True, channel_id=11)
        await self.store.set_table_chat(GUILD, self.two.id, on=True, channel_id=12)
        self.assertEqual(await self.store.table_chat_channel_ids(GUILD), frozenset({11, 12}))

    async def test_a_switched_off_campaign_or_no_channel_gives_none(self) -> None:
        await self.store.set_table_chat(GUILD, self.one.id, on=False, channel_id=11)
        await self.store.set_table_chat(GUILD, self.two.id, on=True, channel_id=None)
        self.assertEqual(await self.store.table_chat_channel_ids(GUILD), frozenset())

    async def test_another_servers_channels_are_never_given(self) -> None:
        elsewhere = await self.store.create(OTHER, "Elsewhere", DM)
        await self.store.set_table_chat(OTHER, elsewhere.id, on=True, channel_id=99)
        await self.store.set_table_chat(GUILD, self.one.id, on=True, channel_id=11)
        self.assertEqual(await self.store.table_chat_channel_ids(GUILD), frozenset({11}))
        self.assertEqual(await self.store.table_chat_channel_ids(OTHER), frozenset({99}))
