"""The table's DMbot chat channel (#1187): made like the game-time channel but typeable, kept out
of backups, and never picked as the DM's extra table chat. Needs the test database."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.dm_screen import messages
from dmbot.dm_screen.dmbchat_channel import (
    dmbchat_channel_name,
    is_dmbchat_name,
    setup_dmbchat_channel,
)
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL, DatabaseTest
from tests.test_dm_screen_setup import BOT, BOT_PERMS, GENERAL, text_channel

GUILD = 111
DM = 7
NEW = 680000000000000061  # long: a short id turns up inside timestamps (#1213)


class Setup(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(GUILD, "Rime of the Frostmaiden", DM)
        me = MagicMock(spec=discord.Member)
        me.id = BOT
        me.guild_permissions = BOT_PERMS
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.me = me
        guild.get_role = lambda _id: None
        guild.get_member = lambda _id: None
        self.general = text_channel(GENERAL, "general", guild)
        self.new = text_channel(NEW, "dmb-chat-rmfthfrstmdn", guild)
        guild.fetch_channel = AsyncMock(return_value=self.general)
        guild.create_text_channel = AsyncMock(return_value=self.new)
        guild.get_channel = lambda cid: self.new if cid == NEW else None
        self.guild = guild

    async def test_a_new_channel_is_named_saved_and_open_for_everyone_to_type_in(self) -> None:
        channel = await setup_dmbchat_channel(self.guild, self.campaign.id, self.store)
        assert channel is self.new
        call = self.guild.create_text_channel.call_args
        self.assertEqual(call.args[0], "dmb-chat-rmfthfrstmdn")
        created: dict[Any, discord.PermissionOverwrite] = call.kwargs["overwrites"]
        everyone = next(ow for key, ow in created.items() if key.id == GUILD)
        self.assertTrue(everyone.view_channel and everyone.read_message_history)
        self.assertTrue(everyone.send_messages)  # unlike the transcript and game-time channels
        self.assertEqual(call.kwargs["topic"], messages.DMBCHAT_TOPIC)
        self.assertEqual(
            messages.DMBCHAT_TOPIC, "Ask DMbot a quick rules question. Your DM has the final say."
        )
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.dmbchat_channel_id == NEW
        self.assertIsNone(saved.gametime_channel_id)  # each channel keeps its own id

    async def test_the_name_is_a_dmbchat_one(self) -> None:
        self.assertEqual(dmbchat_channel_name(self.campaign), "dmb-chat-rmfthfrstmdn")
        self.assertTrue(is_dmbchat_name("dmb-chat-x"))
        self.assertFalse(is_dmbchat_name("dmb-gametime-x"))
        self.assertFalse(is_dmbchat_name("general"))

    async def test_a_channel_that_is_not_a_dmbchat_one_is_never_edited(self) -> None:
        await self.store.set_dmbchat_channel(GUILD, self.campaign.id, GENERAL)
        await setup_dmbchat_channel(self.guild, self.campaign.id, self.store)
        self.general.edit.assert_not_awaited()  # it is "general": a new one is made instead
        self.guild.create_text_channel.assert_awaited_once()

    async def test_a_backup_never_holds_the_channel_id(self) -> None:
        await self.store.set_dmbchat_channel(GUILD, self.campaign.id, NEW)
        exported = await self.store.export(GUILD, self.campaign.id)
        self.assertNotIn(str(NEW), repr(exported))
        self.assertNotIn("dmbchat", repr(exported))

    async def test_it_is_never_picked_as_the_dms_extra_table_chat(self) -> None:
        await self.store.set_dmbchat_channel(GUILD, self.campaign.id, NEW)
        self.assertIn(NEW, await self.store.special_channel_ids(GUILD))


class Migration0056(DatabaseTest):
    async def test_rows_that_exist_are_unchanged_and_the_new_id_is_empty(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0056"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = CampaignStore(self.db, clock=lambda: 1)
        made = await campaigns.create(GUILD, "Frozen Wastes", DM)
        await self.db.migrate()
        campaign = await campaigns.get(GUILD, made.id)
        assert campaign is not None
        self.assertEqual(campaign.name, "Frozen Wastes")
        self.assertIsNone(campaign.dmbchat_channel_id)
        await campaigns.set_dmbchat_channel(GUILD, made.id, NEW)
        again = await campaigns.get(GUILD, made.id)
        assert again is not None and again.dmbchat_channel_id == NEW


class Migration0057(DatabaseTest):
    async def test_a_table_question_line_can_be_stored_and_read_back(self) -> None:
        from dmbot.transcript.models import (
            SIDEBAR_ANSWER,
            SIDEBAR_TABLE_QUESTION,
            Line,
            Lineage,
        )
        from dmbot.transcript.store import TranscriptStore

        transcripts = TranscriptStore(self.db)
        campaigns = CampaignStore(self.db, clock=lambda: 1)
        made = await campaigns.create(GUILD, "Frozen Wastes", DM)
        session = await transcripts.open_session(GUILD, made.id, 1_700_000_000)
        question = Line(
            1_700_000_010_000,
            8,
            "does fireball need line of sight",
            "does fireball need line of sight",
            sidebar=SIDEBAR_TABLE_QUESTION,
            lineage=Lineage(ref="d4e5f6", via="chat"),
        )
        answer = Line(
            1_700_000_012_000,
            8,
            "No.",
            "No.",
            sidebar=SIDEBAR_ANSWER,
            lineage=Lineage(reply_to="d4e5f6", model="m", prompt="sidebar-7"),
        )
        await transcripts.add_lines(GUILD, session, [question, answer])
        got = await transcripts.lines(GUILD, session)
        kinds = [(x.sidebar, x.lineage.via, x.lineage.reply_to) for x in got]
        self.assertIn((SIDEBAR_TABLE_QUESTION, "chat", ""), kinds)
