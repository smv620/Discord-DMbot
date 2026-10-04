"""Starting and stopping campaign sessions (`/dmbot start` · `stop` · Status)."""

import asyncio
import dataclasses
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.bot import EARS_DOWN, DMBot
from dmbot.campaigns import CampaignStore
from dmbot.channel_access import SAME_CHANNEL
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.dm_screen import DMScreenError
from dmbot.sessions import SessionStore
from dmbot.ui.logic import NO_CAMPAIGN_ACCESS
from tests.pg import DatabaseTest

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


async def screen_from_invoking_channel(bot: DMBot, interaction: Any, campaign: Any) -> int:
    """Stand-in for the real DM-screen hook (tested in test_dm_screen.py), so these tests
    can focus on sessions: the saved screen if it still exists, else the invoking channel."""
    saved = campaign.dm_screen_channel_id
    if saved is not None and bot.get_channel(saved) is not None:
        return int(saved)
    return int(interaction.channel_id)


class SessionTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        hook = patch("dmbot.bot.ensure_dm_screen", screen_from_invoking_channel)
        hook.start()
        self.addCleanup(hook.stop)
        self.consent = ConsentStore(self.db)
        self.campaigns = CampaignStore(self.db)
        self.sessions = SessionStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            self.consent,
            self.campaigns,
            self.sessions,
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

    async def test_dm_screen_problem_is_shown_to_the_dm_and_nothing_starts(self) -> None:
        async def failing_hook(bot: Any, interaction: Any, campaign: Any) -> int:
            raise DMScreenError("I need **Manage Roles** to set up the DM screen.")

        with patch("dmbot.bot.ensure_dm_screen", failing_hook):
            ok, message = await self.start()
        self.assertFalse(ok)
        self.assertIn("Manage Roles", message)
        self.assertNotIn(GUILD, self.bot.tables)

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
        self.assertIn("Writing things down: keeping up", busy)
        for jargon in ("backlog", "frame", "pipeline", "ears", "service"):
            self.assertNotIn(jargon, busy.lower())

    async def test_status_says_writing_is_off_without_transcription(self) -> None:
        # #39: TRANSCRIBER=none must not read as "keeping up".
        self.bot.settings = dataclasses.replace(
            self.bot.settings,
            transcription=dataclasses.replace(self.bot.settings.transcription, engine="none"),
        )
        await self.start()
        busy = "\n".join(await self.bot.status_lines(GUILD))
        self.assertIn("Writing things down: off", busy)
        self.assertNotIn("keeping up", busy)

    async def test_ears_events_do_not_leave_log_tags_behind(self) -> None:
        # The ears connection task lives on, so tags must be scoped per event.
        from dmbot.ears.protocol import Status
        from dmbot.logs import _campaign, _guild

        await self.start()
        self.bot.post = AsyncMock(return_value=True)  # type: ignore[method-assign]
        await self.bot._on_ears_message(Status("joined", guild_id=GUILD))
        await self.bot._on_ears_link_change(False)
        self.assertIsNone(_guild.get())
        self.assertIsNone(_campaign.get())


class ShardSetup(DatabaseTest):
    async def test_bot_serves_the_configured_shards(self) -> None:
        from dmbot.sharding import ShardSettings

        settings = Settings(discord_token="t", ears_secret="s", shards=ShardSettings(4, (1, 3)))
        bot = DMBot(settings, ConsentStore(self.db), CampaignStore(self.db), SessionStore(self.db))
        self.assertEqual((bot.shard_count, bot.shard_ids), (4, [1, 3]))


class SaveAndResume(SessionTests):
    """Sessions are saved so a restarted DMbot picks up where it left off (#70)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.posts: list[tuple[int, str]] = []

        async def fake_post(channel_id: int, text: str, view: object = None) -> bool:
            self.posts.append((channel_id, text))
            return True

        self.bot.post = fake_post  # type: ignore[method-assign]
        self.guild.unavailable = False
        self.bot.get_guild = lambda gid: self.guild if gid == GUILD else None  # type: ignore[method-assign]
        screen = MagicMock(spec=discord.TextChannel)
        screen.id = SCREEN
        screen.permissions_for = lambda _me: CAN_POST
        other_text = MagicMock(spec=discord.TextChannel)
        other_text.id = OTHER_TEXT
        other_text.permissions_for = lambda _me: CAN_POST
        self.channels: dict[int, Any] = {SCREEN: screen, VOICE: self.voice, OTHER_TEXT: other_text}
        self.bot.get_channel = self.channels.get  # type: ignore[method-assign]

    async def restart(self) -> DMBot:
        """A new process with the same database: in-memory state is gone."""
        bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            CampaignStore(self.db),
            SessionStore(self.db),
        )
        self.ears = FakeEarsConnection()
        bot.ears._active = self.ears  # type: ignore[assignment]
        bot.post = self.bot.post  # type: ignore[method-assign]
        bot.get_guild = self.bot.get_guild  # type: ignore[method-assign]
        bot.get_channel = self.bot.get_channel  # type: ignore[method-assign]
        return bot

    def screen_posts(self) -> list[str]:
        return [t for cid, t in self.posts if cid == SCREEN]

    async def test_start_saves_and_stop_forgets(self) -> None:
        await self.start()
        saved = await self.sessions.get(GUILD)
        assert saved is not None
        self.assertEqual(
            (saved.campaign_id, saved.voice_channel_id, saved.screen_channel_id, saved.started_by),
            (self.campaign.id, VOICE, SCREEN, DM),
        )
        await self.bot.stop_session(GUILD, DM, False)
        self.assertIsNone(await self.sessions.get(GUILD))

    async def test_a_failed_save_does_not_start(self) -> None:
        self.sessions.save = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", "ERROR"):
            ok, message = await self.start()
        self.assertFalse(ok)
        self.assertIn("couldn't start", message)
        self.assertNotIn(GUILD, self.bot.tables)
        self.assertFalse(any('"join"' in m for m in self.ears.sent))

    async def test_a_failed_clear_keeps_listening(self) -> None:
        await self.start()
        self.sessions.clear = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", "ERROR"):
            message = await self.bot.stop_session(GUILD, DM, False)
        self.assertIn("still listening", message)
        self.assertIn(GUILD, self.bot.tables)

    async def test_restart_resumes_and_tells_the_dm(self) -> None:
        from dmbot.ears.protocol import Status

        await self.start()
        bot = await self.restart()
        self.assertEqual(await bot.resume_sessions(), set())
        table = bot.tables[GUILD]
        self.assertEqual(
            (table.campaign_id, table.voice_channel_id, table.screen_channel_id, table.dm_user_id),
            (self.campaign.id, VOICE, SCREEN, DM),
        )
        kinds = [json.loads(m)["type"] for m in self.ears.sent]
        self.assertEqual(kinds, ["allowlist", "join"])  # consent list first, then voice
        self.posts.clear()
        await bot._on_status(table, Status("joined", guild_id=GUILD))
        self.assertTrue(any("listening again" in t for t in self.screen_posts()))
        self.posts.clear()
        await bot._on_status(table, Status("joined", guild_id=GUILD))  # a repeat
        self.assertEqual(self.posts, [])

    async def test_quick_repeat_restarts_stay_quiet(self) -> None:
        from dmbot.ears.protocol import Status

        await self.start()
        for _ in range(2):
            bot = await self.restart()
            await bot.resume_sessions()
            self.posts.clear()
            await bot._on_status(bot.tables[GUILD], Status("joined", guild_id=GUILD))
        self.assertFalse(any("listening again" in t for t in self.screen_posts()))

    async def test_a_crash_loop_gives_up(self) -> None:
        import dmbot.bot as bot_module

        await self.start()
        for _ in range(bot_module.MAX_RESUMES_IN_A_ROW):
            await (await self.restart()).resume_sessions()
        bot = await self.restart()
        with self.assertLogs("dmbot.bot", "ERROR"):
            await bot.resume_sessions()
        self.assertNotIn(GUILD, bot.tables)
        self.assertIsNone(await self.sessions.get(GUILD))
        self.assertTrue(any("kept restarting" in t for t in self.screen_posts()))

    async def test_notice_is_not_repeated_after_a_restart(self) -> None:
        from dmbot.ears.protocol import Status

        await self.start()
        await self.bot._on_status(self.bot.tables[GUILD], Status("joined", guild_id=GUILD))
        self.assertTrue(any(cid == VOICE for cid, _ in self.posts))
        bot = await self.restart()
        await bot.resume_sessions()
        self.posts.clear()
        await bot._on_status(bot.tables[GUILD], Status("joined", guild_id=GUILD))
        self.assertFalse(any(cid == VOICE for cid, _ in self.posts))

    async def test_no_resume_when_the_voice_channel_is_gone(self) -> None:
        await self.start()
        self.guild.get_channel = lambda cid: None
        bot = await self.restart()
        await bot.resume_sessions()
        self.assertNotIn(GUILD, bot.tables)
        self.assertIsNone(await self.sessions.get(GUILD))
        self.assertEqual(await self.sessions.guilds_to_resume(bot.settings.shards), [])
        self.assertTrue(any("not listening" in t for t in self.screen_posts()))

    async def test_no_resume_without_a_dm_screen(self) -> None:
        await self.start()
        del self.channels[SCREEN]
        bot = await self.restart()
        await bot.resume_sessions()
        self.assertNotIn(GUILD, bot.tables)
        self.assertIsNone(await self.sessions.get(GUILD))

    async def test_no_resume_for_an_old_session(self) -> None:
        import dmbot.bot as bot_module

        await self.start()
        saved = await self.sessions.get(GUILD)
        assert saved is not None
        old = saved.started_at - bot_module.MAX_RESUME_AGE_S - 60
        await self.sessions.save(dataclasses.replace(saved, started_at=old))
        bot = await self.restart()
        await bot.resume_sessions()
        self.assertNotIn(GUILD, bot.tables)
        self.assertTrue(any("too long ago" in t for t in self.screen_posts()))

    async def test_no_resume_when_dmbot_left_the_server(self) -> None:
        await self.start()
        bot = await self.restart()
        bot.get_guild = lambda gid: None  # type: ignore[method-assign]
        await bot.resume_sessions()
        self.assertIsNone(await self.sessions.get(GUILD))

    async def test_an_unavailable_server_waits_for_discord(self) -> None:
        await self.start()
        self.guild.unavailable = True
        bot = await self.restart()
        await bot.resume_sessions()
        self.assertNotIn(GUILD, bot.tables)
        self.assertIsNotNone(await self.sessions.get(GUILD))  # kept, not thrown away
        self.guild.unavailable = False
        await bot.on_guild_available(self.guild)
        self.assertIn(GUILD, bot.tables)

    async def test_stop_during_a_restart_ends_the_saved_session(self) -> None:
        await self.start()
        bot = await self.restart()  # not resumed yet
        refused = await bot.stop_session(GUILD, PLAYER, False)
        self.assertIn("Only the DM", refused)
        self.assertIsNotNone(await self.sessions.get(GUILD))
        message = await bot.stop_session(GUILD, DM, False)
        self.assertIn("won't rejoin", message)
        self.assertIsNone(await self.sessions.get(GUILD))
        self.assertTrue(any('"leave"' in m for m in self.ears.sent))
        await bot.resume_sessions()
        self.assertNotIn(GUILD, bot.tables)

    async def test_resume_retries_when_the_database_is_not_ready(self) -> None:
        import dmbot.bot as bot_module

        await self.start()
        bot = await self.restart()
        real = bot.sessions.guilds_to_resume
        calls = 0

        async def flaky(shards: Any) -> list[int]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("database starting up")
            return await real(shards)

        bot.sessions.guilds_to_resume = flaky  # type: ignore[method-assign]
        with (
            patch.object(bot_module, "RESUME_RETRY_DELAYS_S", (0, 0)),
            self.assertLogs("dmbot.bot", "ERROR"),
        ):
            await bot._resume_with_retries()
        self.assertIn(GUILD, bot.tables)

    async def test_a_consent_error_during_resume_leaves_nothing_half_started(self) -> None:
        import dmbot.bot as bot_module

        await self.start()
        bot = await self.restart()
        real = bot.consent.consenting
        calls = 0

        async def flaky(guild_id: int) -> frozenset[int]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("database hiccup")
            return await real(guild_id)

        bot.consent.consenting = flaky  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", "ERROR"):
            failed = await bot.resume_sessions()
        self.assertEqual(failed, {GUILD})
        self.assertNotIn(GUILD, bot.tables)  # not stuck "joining…"
        with patch.object(bot_module, "RESUME_RETRY_DELAYS_S", ()):
            self.assertEqual(await bot.resume_sessions(only=failed), set())
        self.assertIn(GUILD, bot.tables)
        self.assertTrue(any('"join"' in m for m in self.ears.sent))

    async def test_a_failed_start_is_not_resumed_later(self) -> None:
        self.consent.consenting = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", "ERROR"):
            ok, _ = await self.start()
        self.assertFalse(ok)
        self.assertNotIn(GUILD, self.bot.tables)
        self.assertIsNone(await self.sessions.get(GUILD))

    async def test_revoke_reaches_ears_even_without_a_session_here(self) -> None:
        from dmbot.bot import consent_revoke

        await self.consent.grant(GUILD, PLAYER)
        bot = await self.restart()  # ears may still be in voice from before
        interaction = SimpleNamespace(
            client=bot,
            guild=self.guild,
            user=member(PLAYER),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await consent_revoke.callback(interaction)  # type: ignore[arg-type,call-arg]
        lists = [json.loads(m) for m in self.ears.sent if '"allowlist"' in m]
        self.assertTrue(lists)
        self.assertNotIn(str(PLAYER), lists[0]["userIds"])

    async def test_resume_starts_once(self) -> None:
        bot = await self.restart()
        bot._resume_with_retries = AsyncMock()  # type: ignore[method-assign]
        await bot.on_ready()
        await bot.on_ready()  # Discord reconnected: must not start again
        await asyncio.gather(*bot._background)
        bot._resume_with_retries.assert_awaited_once()

    async def test_a_saved_session_counts_as_playing(self) -> None:
        await self.start()
        bot = await self.restart()
        self.assertTrue(await bot.is_campaign_playing(GUILD, self.campaign.id))
        self.assertFalse(await bot.is_campaign_playing(GUILD, "other"))
