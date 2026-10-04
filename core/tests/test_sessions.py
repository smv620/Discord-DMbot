"""Starting and stopping campaign sessions (`/dmbot start` · `stop` · Status)."""

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import discord

from dmbot.bot import EARS_DOWN, DMBot
from dmbot.campaigns import CampaignStore
from dmbot.channel_access import SAME_CHANNEL
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.ui.logic import NO_CAMPAIGN_ACCESS

GUILD, VOICE, SCREEN, OTHER_TEXT = 1, 2, 3, 4
DM, PLAYER = 7, 8
CAN_POST = discord.Permissions(view_channel=True, send_messages=True, connect=True)


class FakeEarsConnection:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


def member(user_id: int, *, manager: bool = False) -> Any:
    m = MagicMock(spec=discord.Member)
    m.id = user_id
    m.guild_permissions = discord.Permissions(manage_guild=manager)
    return m


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.consent = ConsentStore(":memory:")
        self.campaigns = CampaignStore(":memory:")
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"), self.consent, campaigns=self.campaigns
        )
        self.ears = FakeEarsConnection()
        self.bot.ears._active = self.ears  # type: ignore[assignment]

        self.voice_perms = CAN_POST  # what DMbot may do in the voice channel
        self.user_voice_perms = CAN_POST  # what the person pressing Start may do there
        bot_member = member(999)
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.id = VOICE
        voice.mention = f"<#{VOICE}>"
        voice.permissions_for = lambda who: (
            self.voice_perms if who is bot_member else self.user_voice_perms
        )
        self.voice = voice

        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.me = bot_member
        guild.get_channel = lambda cid: voice if cid == VOICE else None
        self.guild = guild

        other_text = MagicMock(spec=discord.TextChannel)
        other_text.id = OTHER_TEXT
        other_text.permissions_for = lambda _me: CAN_POST
        channels: dict[int, Any] = {SCREEN: object(), OTHER_TEXT: other_text, VOICE: voice}
        self.bot.get_channel = channels.get  # type: ignore[method-assign]

        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)

    async def asyncTearDown(self) -> None:
        self.consent.close()
        self.campaigns.close()

    def interaction(self, user: Any, channel_id: int = SCREEN) -> Any:
        return SimpleNamespace(
            guild=self.guild, user=user, channel_id=channel_id, app_permissions=CAN_POST
        )

    async def start(self, user: Any | None = None, **kw: Any) -> tuple[bool, str]:
        return await self.bot.start_campaign_session(
            self.interaction(user or member(DM), **kw), self.campaign.id, VOICE
        )

    async def test_dm_starts_a_session(self) -> None:
        ok, message = await self.start()
        self.assertTrue(ok, message)
        self.assertIn("Listening to **Frostmaiden**", message)
        self.assertIn("players can peek", message)
        table = self.bot.tables[GUILD]
        self.assertEqual(
            (table.campaign_id, table.voice_channel_id, table.screen_channel_id),
            (self.campaign.id, VOICE, SCREEN),
        )
        self.assertTrue(table.is_dm(DM))
        self.assertTrue(any('"join"' in m for m in self.ears.sent))
        saved = await self.campaigns.get(GUILD, self.campaign.id)
        assert saved is not None
        self.assertEqual((saved.last_voice_channel_id, saved.dm_screen_channel_id), (VOICE, SCREEN))
        self.assertIsNotNone(saved.last_played_at)
        self.assertEqual(self.bot.active_campaign_id(GUILD), self.campaign.id)

    async def test_saved_dm_screen_is_reused(self) -> None:
        await self.campaigns.set_dm_screen(GUILD, self.campaign.id, OTHER_TEXT)
        ok, message = await self.start()
        self.assertTrue(ok, message)
        self.assertEqual(self.bot.tables[GUILD].screen_channel_id, OTHER_TEXT)

    async def test_player_cannot_start_someone_elses_campaign(self) -> None:
        ok, message = await self.start(member(PLAYER))
        self.assertFalse(ok)
        self.assertEqual(message, NO_CAMPAIGN_ACCESS)
        self.assertNotIn(GUILD, self.bot.tables)

    async def test_server_manager_can_start(self) -> None:
        ok, message = await self.start(member(PLAYER, manager=True))
        self.assertTrue(ok, message)

    async def test_needs_connect_permission(self) -> None:
        self.voice_perms = discord.Permissions(view_channel=True, send_messages=True)
        ok, message = await self.start()
        self.assertFalse(ok)
        self.assertIn("**Connect**", message)

    async def test_dm_must_be_able_to_join_the_channel(self) -> None:
        self.user_voice_perms = discord.Permissions(view_channel=True)  # no Connect
        ok, message = await self.start()
        self.assertFalse(ok)
        self.assertIn("can't join", message)
        self.assertIn("yourself", message)
        self.assertNotIn(GUILD, self.bot.tables)

    async def test_two_starts_at_once_start_only_one_session(self) -> None:
        import asyncio

        other = await self.campaigns.create(GUILD, "Second", DM)
        results = await asyncio.gather(
            self.bot.start_campaign_session(self.interaction(member(DM)), self.campaign.id, VOICE),
            self.bot.start_campaign_session(self.interaction(member(DM)), other.id, VOICE),
        )
        self.assertEqual(sorted(ok for ok, _ in results), [False, True])
        joins = [m for m in self.ears.sent if '"join"' in m]
        self.assertEqual(len(joins), 1)

    async def test_refuses_the_voice_channels_own_chat(self) -> None:
        ok, message = await self.start(channel_id=VOICE)
        self.assertFalse(ok)
        self.assertEqual(message, SAME_CHANNEL)

    async def test_only_one_session_per_server(self) -> None:
        await self.start()
        other = await self.campaigns.create(GUILD, "Second", DM)
        ok, message = await self.bot.start_campaign_session(
            self.interaction(member(DM)), other.id, VOICE
        )
        self.assertFalse(ok)
        self.assertIn("already running **Frostmaiden**", message)

    async def test_voice_service_down(self) -> None:
        self.bot.ears._active = None
        self.assertEqual(self.bot.start_blocker(GUILD), EARS_DOWN)

    async def test_stop_permissions(self) -> None:
        await self.start()
        refused = await self.bot.stop_session(GUILD, PLAYER, False)
        self.assertIn("Only the DM", refused)
        self.assertIn("/consent revoke", refused)
        self.assertIn(GUILD, self.bot.tables)
        done = await self.bot.stop_session(GUILD, DM, False)
        self.assertIn("Stopped listening to **Frostmaiden**", done)
        self.assertNotIn(GUILD, self.bot.tables)
        self.assertIn("isn't listening", await self.bot.stop_session(GUILD, DM, False))

    async def test_co_dm_can_stop(self) -> None:
        await self.campaigns.add_dm(GUILD, self.campaign.id, PLAYER)
        await self.start()
        self.assertIn("Stopped", await self.bot.stop_session(GUILD, PLAYER, False))

    async def test_status_lines_are_plain(self) -> None:
        idle = "\n".join(await self.bot.status_lines(GUILD))
        self.assertIn("/dmbot start", idle)
        self.assertIn("Nobody has said yes", idle)
        await self.start()
        busy = "\n".join(await self.bot.status_lines(GUILD))
        self.assertIn("**Frostmaiden**", busy)
        self.assertIn(f"<#{SCREEN}>", busy)
        self.assertIn("Keeping up: yes", busy)
        for jargon in ("backlog", "frame", "pipeline", "ears", "service"):
            self.assertNotIn(jargon, busy.lower())
