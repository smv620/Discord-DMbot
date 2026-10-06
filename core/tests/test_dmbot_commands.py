"""The /dmbot commands' first steps, with fake Discord interactions."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
from dmbot.campaigns.store import MAX_BACKUP_BYTES, encode_backup
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.sessions import SessionStore
from dmbot.ui import dmbot_commands as cmds
from tests.pg import DatabaseTest

GUILD, DM, OTHER_DM = 1, 7, 8


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.deferred = False

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, text: str = "", **kw: Any) -> None:
        self.done = True
        self.sent.append((text, kw))

    async def defer(self, **kw: Any) -> None:
        self.done = True
        self.deferred = True


def fake_interaction(bot: DMBot, user_id: int = DM) -> Any:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.guild_permissions = discord.Permissions.none()
    guild = SimpleNamespace(id=GUILD)
    followup = SimpleNamespace(send=AsyncMock())
    return SimpleNamespace(
        client=bot, guild=guild, user=user, response=FakeResponse(), followup=followup
    )


def attachment(raw: bytes, size: int | None = None) -> Any:
    a = MagicMock(spec=discord.Attachment)
    a.size = len(raw) if size is None else size
    a.read = AsyncMock(return_value=raw)
    return a


class CommandTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.consent = ConsentStore(self.db)
        self.campaigns = CampaignStore(self.db)
        self.sessions = SessionStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            self.consent,
            self.campaigns,
            self.sessions,
        )
        self.bot.ears._active = SimpleNamespace(send=AsyncMock())  # type: ignore[assignment]

    async def test_first_start_shows_the_welcome(self) -> None:
        it = fake_interaction(self.bot)
        await cmds.dmbot_start.callback(it)  # type: ignore[call-arg]
        text, kw = it.response.sent[0]
        self.assertIn("never makes up the story", text)
        self.assertIsInstance(kw["view"], cmds.Welcome)
        self.assertTrue(kw["ephemeral"])

    async def test_returning_dm_sees_the_campaign_picker(self) -> None:
        await self.campaigns.create(GUILD, "Frostmaiden", DM)
        it = fake_interaction(self.bot)
        await cmds.dmbot_start.callback(it)  # type: ignore[call-arg]
        text, kw = it.response.sent[0]
        self.assertIn("**Frostmaiden**", text)
        self.assertIsInstance(kw["view"], cmds.CampaignPicker)

    async def test_restore_refuses_big_files_before_downloading(self) -> None:
        it = fake_interaction(self.bot)
        file = attachment(b"{}", size=MAX_BACKUP_BYTES + 1)
        await cmds.dmbot_restore.callback(it, file)  # type: ignore[call-arg]
        self.assertIn("too big", it.response.sent[0][0])
        file.read.assert_not_called()

    async def test_restore_rejects_files_that_are_not_backups(self) -> None:
        for raw in (b"not json", json.dumps({"hello": 1}).encode()):
            it = fake_interaction(self.bot)
            await cmds.dmbot_restore.callback(it, attachment(raw))  # type: ignore[call-arg]
            message = it.followup.send.call_args.args[0]
            self.assertIn("isn't a DMbot campaign backup", message)

    async def test_restore_offers_only_your_own_campaigns_to_replace(self) -> None:
        mine = await self.campaigns.create(GUILD, "Mine", DM)
        await self.campaigns.create(GUILD, "Theirs", OTHER_DM)
        backup = encode_backup(await self.campaigns.export(GUILD, mine.id))
        it = fake_interaction(self.bot)
        await cmds.dmbot_restore.callback(it, attachment(backup))  # type: ignore[call-arg]
        view = it.followup.send.call_args.kwargs["view"]
        self.assertIsInstance(view, cmds.RestoreChoice)
        self.assertEqual(view.replaceable_ids, [mine.id])

    async def test_backup_needs_a_campaign_you_run(self) -> None:
        await self.campaigns.create(GUILD, "Theirs", OTHER_DM)
        it = fake_interaction(self.bot)
        await cmds.dmbot_backup.callback(it)  # type: ignore[call-arg]
        self.assertIn("not the DM of any campaign", it.response.sent[0][0])

    async def test_a_server_manager_cannot_download_a_campaign_they_dont_run(self) -> None:
        theirs = await self.campaigns.create(GUILD, "Theirs", OTHER_DM)
        it = fake_interaction(self.bot)
        it.user.guild_permissions = discord.Permissions(manage_guild=True)
        await cmds.dmbot_backup.callback(it)  # type: ignore[call-arg]
        self.assertIn("not the DM of any campaign", it.response.sent[0][0])
        it = fake_interaction(self.bot)
        it.user.guild_permissions = discord.Permissions(manage_guild=True)
        await cmds.send_backup(it, theirs.id)  # a pressed button is checked again
        self.assertIn("Only this campaign's DM can download a copy", it.response.sent[0][0])
