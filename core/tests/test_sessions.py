"""Starting and stopping campaign sessions (`/dmbot start` · `stop` · Status)."""

import asyncio
import dataclasses
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.bot import EARS_DOWN, DMBot
from dmbot.campaigns import CampaignStore
from dmbot.channel_access import SAME_CHANNEL
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.consent_dm import ALREADY_RECORDED
from dmbot.dm_screen import DMScreenError
from dmbot.sessions import SessionStore
from dmbot.ui.logic import NO_CAMPAIGN_ACCESS
from tests.pg import DatabaseTest

GUILD, VOICE, SCREEN, OTHER_TEXT, TRANSCRIPT = 1, 2, 3, 4, 5
DM, PLAYER, OTHER_PERSON = 7, 8, 9
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


async def transcript_channel(*_: Any, **__: Any) -> Any:
    """Stand-in for the real transcript-channel setup (tested in test_transcript_channel)."""
    return SimpleNamespace(id=TRANSCRIPT)


class SessionTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        hook = patch("dmbot.bot.ensure_dm_screen", screen_from_invoking_channel)
        hook.start()
        self.addCleanup(hook.stop)
        transcript_hook = patch("dmbot.bot.setup_transcript_channel", transcript_channel)
        transcript_hook.start()
        self.addCleanup(transcript_hook.stop)
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

        async def fake_post_message(channel_id: int, text: str, view: object = None) -> Any:
            self.posts.append((channel_id, text))
            return MagicMock(edit=AsyncMock())

        self.bot.post = fake_post  # type: ignore[method-assign]
        self.bot.post_message = fake_post_message  # type: ignore[method-assign]
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
        bot.post_message = self.bot.post_message  # type: ignore[method-assign]
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
        reply = interaction.followup.send.await_args
        self.assertIn(ALREADY_RECORDED, reply.args[0])  # same words as the Stop button
        self.assertTrue(reply.kwargs["ephemeral"])

    async def test_a_revoke_that_cant_be_saved_still_stops_and_says_so(self) -> None:
        from dmbot.bot import consent_revoke

        await self.consent.grant(GUILD, PLAYER)
        self.consent.revoke = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        interaction = self._consent_interaction(PLAYER)
        with self.assertLogs("dmbot.bot", "ERROR"):
            await consent_revoke.callback(interaction)  # type: ignore[call-arg]
        text = interaction.followup.send.await_args.args[0]
        self.assertIn("couldn't save this yet", text)
        self.assertNotIn(ALREADY_RECORDED, text)
        self.assertFalse(self.consent.has_consent(GUILD, PLAYER))

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

    # ---- session event logs (#37): IDs and numbers only, never names ----------

    async def test_session_events_are_logged_without_names(self) -> None:
        from dmbot.ears.protocol import Status

        self.bot.post = AsyncMock(return_value=True)  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await self.start()
            await self.bot._on_ears_message(Status("joined", guild_id=GUILD))
            await self.bot._on_ears_message(Status("joined", guild_id=GUILD))  # a repeat
            await self.bot._on_ears_message(
                Status("error", guild_id=GUILD, detail="Lost the voice connection.")
            )
            await self.bot.stop_session(GUILD, DM, False)
        text = "\n".join(logs.output)
        for expected in (
            f"Session started: voice channel {VOICE}, DM screen {SCREEN}",
            "In the voice channel; 0 of 0 there opted in",
            "Recording notice posted",
            "WARNING:dmbot.bot:Voice error: Lost the voice connection.",
            f"Session ended: /dmbot stop by user {DM}",
        ):
            self.assertIn(expected, text)
        self.assertEqual(text.count("In the voice channel"), 1)
        self.assertNotIn("Frostmaiden", text)  # campaign names stay out of logs

    async def test_failed_recording_notice_is_a_warning(self) -> None:
        from dmbot.ears.protocol import Status

        await self.start()
        self.bot.post = AsyncMock(return_value=False)  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", level="WARNING") as logs:
            await self.bot._on_ears_message(Status("joined", guild_id=GUILD))
        self.assertIn("Recording notice NOT posted", "\n".join(logs.output))

    async def test_resume_and_saved_stop_are_logged(self) -> None:
        await self.start()
        bot = await self.restart()
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await bot.resume_sessions()
        self.assertIn("Session started again after a restart", "\n".join(logs.output))

        bot = await self.restart()  # saved again, not running in this process
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await bot.stop_session(GUILD, DM, False)
        self.assertIn(
            f"Saved session ended before it resumed: /dmbot stop by user {DM}",
            "\n".join(logs.output),
        )

    def _consent_interaction(self, user_id: int) -> Any:
        return SimpleNamespace(
            client=self.bot,
            guild=self.guild,
            user=member(user_id),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_consent_is_logged_by_id(self) -> None:
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await self.bot.give_consent(GUILD, PLAYER, "private_message", outside_to=None)
            await self.bot.withdraw_consent(GUILD, PLAYER)
        text = "\n".join(logs.output)
        self.assertIn(f"Consent given: user {PLAYER}", text)
        self.assertIn(f"Consent withdrawn: user {PLAYER}", text)

    def at_the_table(self, *people: int) -> None:
        """These people are in the voice channel, with display names."""
        names = {PLAYER: "Mia", OTHER_PERSON: "Dee", DM: "Sam"}
        self.voice.members = [member(uid) for uid in people]
        for m in self.voice.members:
            m.bot = False
            m.display_name = names[m.id]
            m.guild = self.guild
        self.guild.get_member = lambda uid: next(
            (m for m in self.voice.members if m.id == uid), None
        )
        self.bot.get_guild = lambda gid: self.guild  # type: ignore[method-assign]

    async def test_the_dm_screen_shows_each_yes_and_stop_during_a_session(self) -> None:
        # #107: the DM screen used to say "0 player(s) have opted in" and never update.
        self.at_the_table(PLAYER)
        await self.start()
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        await self.bot.give_consent(GUILD, PLAYER, "private_message", outside_to=None)
        await self.bot.give_consent(GUILD, PLAYER, "consent_command", outside_to=None)  # again
        await self.bot.withdraw_consent(GUILD, PLAYER)
        await self.bot.withdraw_consent(GUILD, PLAYER)  # already stopped: nothing new
        await self.bot.give_consent(GUILD, OTHER_PERSON, "private_message", outside_to=None)
        await asyncio.gather(*self.bot._asking)
        notes = [c.args[1] for c in posted.await_args_list if c.args[0] == SCREEN]
        self.assertEqual(len(notes), 2, notes)  # nothing for someone not at the table
        self.assertIn("**Mia** said yes: DMbot is recording them now", notes[0])
        self.assertIn("**Mia** said stop. DMbot no longer records them.", notes[1])

    async def test_a_yes_that_doesnt_count_is_never_shown_as_recording(self) -> None:
        # A yes to an older request naming another speech-to-text company isn't saved
        # as consent here, so the DM screen mustn't say they're being recorded.
        self.at_the_table(PLAYER)
        self.consent.outside = "deepgram"
        await self.start()
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        await self.bot.give_consent(GUILD, PLAYER, "private_message", outside_to="cloud")
        await asyncio.gather(*self.bot._asking)
        self.assertFalse(self.consent.has_consent(GUILD, PLAYER))
        self.assertEqual([c for c in posted.await_args_list if c.args[0] == SCREEN], [])

    async def test_the_listening_message_names_who_is_recorded(self) -> None:
        from dmbot.ears.protocol import Status

        await self.consent.grant(GUILD, PLAYER)
        self.at_the_table(PLAYER, OTHER_PERSON)
        await self.start()
        self.bot.post = AsyncMock(return_value=True)  # type: ignore[method-assign]
        listening = MagicMock(edit=AsyncMock())
        posted = AsyncMock(return_value=listening)
        self.bot.post_message = posted  # type: ignore[method-assign]
        await self.bot._on_ears_message(Status("joined", guild_id=GUILD))
        call = next(c for c in posted.await_args_list if "Listening in" in c.args[1])
        text = call.args[1]
        self.assertIn("🎙 Recording: Mia.", text)
        self.assertIn("✉️ Not recorded yet: Dee. DMbot is asking privately", text)
        self.assertNotIn("0 player", text)
        # #108: the DM stops with one press; the button comes off at the end.
        (button,) = call.args[2].children
        self.assertEqual(button.custom_id, f"dmbot:stop:{self.campaign.id}")
        await self.bot.stop_table(GUILD, "test")
        await asyncio.gather(*self.bot._finishing)
        listening.edit.assert_awaited_with(view=None)

    def press_stop(self, user: Any) -> Any:
        return SimpleNamespace(
            client=self.bot,
            guild=self.guild,
            user=user,
            message=MagicMock(edit=AsyncMock()),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_the_stop_button_stops_only_for_the_dm(self) -> None:
        from dmbot.dm_screen import StopListeningButton
        from dmbot.dm_screen import messages as m

        await self.start()
        button = StopListeningButton(self.campaign.id)
        player = self.press_stop(member(PLAYER))
        await button.callback(player)  # type: ignore[arg-type]
        self.assertIn("Only the DM can stop", player.followup.send.await_args.args[0])
        self.assertIn(GUILD, self.bot.tables)  # still listening
        dm = self.press_stop(member(DM))
        await button.callback(dm)  # type: ignore[arg-type]
        self.assertIn("Stopped listening", dm.followup.send.await_args.args[0])
        self.assertNotIn(GUILD, self.bot.tables)
        again = self.press_stop(member(DM))  # an old message, pressed later
        await button.callback(again)  # type: ignore[arg-type]
        again.response.send_message.assert_awaited_once()
        self.assertEqual(again.response.send_message.await_args.args[0], m.NOT_LISTENING_NOW)
        again.message.edit.assert_awaited_with(view=None)

    async def test_a_server_manager_can_press_stop_but_not_for_another_campaign(self) -> None:
        from dmbot.dm_screen import StopListeningButton

        await self.start()
        other = await self.campaigns.create(GUILD, "Strahd", DM)
        wrong = self.press_stop(member(OTHER_PERSON, manager=True))
        await StopListeningButton(other.id).callback(wrong)  # type: ignore[arg-type]
        self.assertIn(GUILD, self.bot.tables)  # an old button never stops a newer session
        manager = self.press_stop(member(OTHER_PERSON, manager=True))
        await StopListeningButton(self.campaign.id).callback(manager)  # type: ignore[arg-type]
        self.assertIn("Stopped listening", manager.followup.send.await_args.args[0])
        self.assertNotIn(GUILD, self.bot.tables)

    async def test_people_joining_mid_session_are_shown(self) -> None:
        from dmbot.ears.protocol import Status

        await self.consent.grant(GUILD, PLAYER)
        self.at_the_table()
        await self.start()
        await self.bot._on_ears_message(Status("joined", guild_id=GUILD))
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        self.bot.start_asking = MagicMock()  # type: ignore[method-assign]
        self.at_the_table(PLAYER, OTHER_PERSON)
        after = SimpleNamespace(channel=self.voice)
        before = SimpleNamespace(channel=None)
        for m in self.voice.members:
            await self.bot.on_voice_state_update(m, before, after)  # type: ignore[arg-type]
        await asyncio.gather(*self.bot._asking)
        notes = [c.args[1] for c in posted.await_args_list if c.args[0] == SCREEN]
        self.assertIn("🎙 **Mia** joined and is recorded (they said yes before).", notes)
        self.assertIn("✉️ **Dee** joined. Not recorded unless they say yes.", notes)

    async def test_consent_not_logged_as_given_when_the_save_fails(self) -> None:
        self.consent.grant = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            logging.getLogger("dmbot.bot").info("start")  # assertLogs needs a line
            with self.assertRaises(RuntimeError):
                await self.bot.give_consent(GUILD, PLAYER, "private_message", outside_to=None)
        self.assertNotIn("Consent given", "\n".join(logs.output))

    async def test_capture_check_is_logged_and_only_gaps_reach_the_dm(self) -> None:
        from dmbot.audio.segmenter import Utterance

        await self.start()
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        table = self.bot.tables[GUILD]
        table.capture_log.add_utterance(Utterance(GUILD, PLAYER, 0, 0, bytes(32000)))
        table.capture_log.add_health(PLAYER, 50, 50)
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await self.bot.post_summary(table)
        self.assertIn(
            f"Capture check: 1 speaker(s); user {PLAYER}: 1 x speech, 1.0 s, audio 100%",
            "\n".join(logs.output),
        )
        posted.assert_not_awaited()  # all fine: nothing in the DM screen (#134)
        table.capture_log.add_utterance(Utterance(GUILD, PLAYER, 0, 0, bytes(32000)))
        table.capture_log.add_health(PLAYER, 30, 50)
        await self.bot.post_summary(table)
        posted.assert_awaited_once()
        call = posted.await_args
        assert call is not None
        self.assertEqual(call.args[0], SCREEN)
        self.assertIn("voice is cutting out for DMbot", call.args[1])

    # ---- the live transcript channel (#124) ------------------------------------

    async def test_start_sets_up_the_transcript_channel(self) -> None:
        ok, message = await self.start()
        self.assertTrue(ok, message)
        self.assertEqual(self.bot.tables[GUILD].transcript_channel_id, TRANSCRIPT)
        self.assertIn(f"<#{TRANSCRIPT}>", message)

    async def test_a_transcript_channel_problem_never_stops_the_session(self) -> None:
        from dmbot.dm_screen.transcript_channel import TranscriptChannelError

        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        failing = AsyncMock(side_effect=TranscriptChannelError("Discord said: full."))
        with patch("dmbot.bot.setup_transcript_channel", failing):
            ok, message = await self.start()
        self.assertTrue(ok, message)
        self.assertIn("No live transcript this time", message)
        self.assertIsNone(self.bot.tables[GUILD].transcript_channel_id)
        notes = [c.args[1] for c in posted.await_args_list if c.args[0] == SCREEN]
        self.assertTrue(any("No live transcript this session" in n and "full" in n for n in notes))

    async def joined_with_transcript(self) -> tuple[Any, list[str]]:
        """A started, joined session whose transcript posts are collected."""
        from dmbot.ears.protocol import Status

        await self.start()
        table = self.bot.tables[GUILD]
        self.bot.post = AsyncMock(return_value=True)  # type: ignore[method-assign]
        await self.bot._on_status(table, Status("joined", guild_id=GUILD))
        sent: list[str] = []

        async def fake_post(channel_id: int, text: str) -> str:
            self.assertEqual(channel_id, TRANSCRIPT)
            sent.append(text)
            return "posted"

        self.bot._post_transcript = fake_post  # type: ignore[method-assign]
        return table, sent

    def said(self, table: Any, text: str, user: int = PLAYER) -> None:
        from dmbot.audio.segmenter import Utterance

        utterance = Utterance(GUILD, user, 0, 0, bytes(32000), table.segmenter.session)
        self.bot._deliver_transcript(utterance, text)

    async def test_what_is_said_goes_to_the_transcript_channel_not_the_dm_screen(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table, sent = await self.joined_with_transcript()
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        self.said(table, "I cast Shield")
        await self.bot.flush_transcript(table)
        await self.bot.post_summary(table)
        posted.assert_not_awaited()  # nothing for the DM screen
        text = "\n".join(sent)
        self.assertIn("Session started", text)
        self.assertIn("I cast Shield", text)

    async def test_someone_who_stops_loses_their_unposted_words(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table, sent = await self.joined_with_transcript()
        self.said(table, "secret plan")
        self.bot.stop_recording(GUILD, PLAYER)
        await self.bot.flush_transcript(table)
        self.assertNotIn("secret plan", "\n".join(sent))

    async def test_speech_from_a_stopped_session_never_reaches_the_next(self) -> None:
        from dmbot.audio.segmenter import Utterance

        await self.consent.grant(GUILD, PLAYER)
        table, sent = await self.joined_with_transcript()
        old = Utterance(GUILD, PLAYER, 0, 0, bytes(32000), table.segmenter.session - 1)
        self.bot._deliver_transcript(old, "from the last session")
        await self.bot.flush_transcript(table)
        self.assertNotIn("from the last session", "\n".join(sent))

    async def test_a_failed_post_is_tried_again(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table, sent = await self.joined_with_transcript()
        results = iter(["retry", "posted", "posted"])

        async def flaky(channel_id: int, text: str) -> str:
            result = next(results)
            if result == "posted":
                sent.append(text)
            return result

        self.bot._post_transcript = flaky  # type: ignore[method-assign]
        self.said(table, "hello")
        await self.bot.flush_transcript(table)
        self.assertEqual(sent, [])
        await self.bot.flush_transcript(table)
        self.assertIn("hello", "\n".join(sent))

    async def test_transcript_lines_never_make_a_pop_up(self) -> None:
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        self.bot.get_channel = MagicMock(return_value=channel)  # type: ignore[method-assign]
        self.assertEqual(await self.bot._post_transcript(5, "**Mia:** hi"), "posted")
        call = channel.send.await_args
        assert call is not None
        self.assertTrue(call.kwargs["silent"])
        self.assertTrue(call.kwargs["suppress_embeds"])  # still no link previews

    async def test_a_lost_channel_stops_the_transcript_and_tells_the_dm_once(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table, _ = await self.joined_with_transcript()
        gone = AsyncMock(return_value="gone")
        self.bot._post_transcript = gone  # type: ignore[method-assign]
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        self.said(table, "hello")
        await self.bot.flush_transcript(table)
        self.said(table, "again")
        await self.bot.flush_transcript(table)
        self.assertIsNone(table.transcript_channel_id)
        gone.assert_awaited_once()
        posted.assert_awaited_once()
        call = posted.await_args
        assert call is not None
        self.assertIn("live transcript stopped", call.args[1])

    async def test_speech_still_being_written_down_at_stop_is_kept(self) -> None:
        # #109: the last words used to vanish when the DM pressed stop.
        await self.consent.grant(GUILD, PLAYER)
        table, sent = await self.joined_with_transcript()
        release = await self.slow_worker("Last words before we stop.")
        self.queue_speech(table)
        await self.bot.stop_table(GUILD, "test")
        release.set()
        await asyncio.gather(*self.bot._finishing)
        text = "\n".join(sent)
        self.assertIn("Last words before we stop.", text)
        self.assertLess(text.index("Last words"), text.index("Session ended"))
        posted: Any = self.bot.post
        summaries = [c.args[1] for c in posted.await_args_list if "Session ended:" in c.args[1]]
        self.assertEqual(len(summaries), 1)
        self.assertIn("under a minute", summaries[0])
        self.assertIn("Everything DMbot heard was written down", summaries[0])
        self.assertEqual(self.bot._ending, {})

    async def slow_worker(self, text: str) -> asyncio.Event:
        """A running transcription worker that holds each clip until released."""
        release = asyncio.Event()

        async def slow(utterance: Any, hints: list[str]) -> str:
            await release.wait()
            return text.format(session=utterance.session)

        self.bot.pipeline.transcriber.transcribe = slow  # type: ignore[method-assign]
        # Names show as mentions (SaveAndResume's fake server returns mock members).
        self.bot.get_guild = lambda gid: None  # type: ignore[method-assign]
        worker = asyncio.create_task(self.bot.pipeline.run())
        self.addCleanup(worker.cancel)
        await asyncio.sleep(0)
        return release

    def queue_speech(self, table: Any) -> None:
        from dmbot.audio.segmenter import Utterance
        from dmbot.bot import _now_ms

        start_ms = _now_ms() - 1000  # said just before the stop
        self.bot.pipeline.enqueue(
            Utterance(GUILD, PLAYER, start_ms, start_ms, bytes(32000), table.segmenter.session)
        )

    async def test_stop_recording_while_the_session_finishes_drops_their_words(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table, sent = await self.joined_with_transcript()
        release = await self.slow_worker("Do not keep this.")
        self.queue_speech(table)
        await self.bot.stop_table(GUILD, "test")
        self.bot.stop_recording(GUILD, PLAYER)  # while the last words are being written
        release.set()
        await asyncio.gather(*self.bot._finishing)
        self.assertNotIn("Do not keep this.", "\n".join(sent))

    async def test_a_new_session_never_gets_the_old_ones_last_words(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        old, sent = await self.joined_with_transcript()
        release = await self.slow_worker("said in session {session}")
        self.queue_speech(old)
        await self.bot.stop_table(GUILD, "test")
        await self.start()  # the DM starts again right away
        new = self.bot.tables[GUILD]
        self.assertIsNot(new, old)
        release.set()
        await asyncio.gather(*self.bot._finishing)
        self.assertIn(f"said in session {old.segmenter.session}", "\n".join(sent))
        self.assertEqual(len(new.transcript), 0)  # nothing of the old session's
        self.assertEqual(new.heard, [])

    async def test_a_shutdown_right_after_stop_still_finishes_quickly(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table, _ = await self.joined_with_transcript()
        await self.slow_worker("never written")  # stuck: never released
        self.queue_speech(table)
        finished = AsyncMock(return_value=0)
        self.bot.finish_transcript = finished  # type: ignore[method-assign]
        await self.bot.stop_table(GUILD, "test")
        await asyncio.sleep(0.05)
        self.bot.pipeline.stop_waiting()  # what close() does first
        await asyncio.wait_for(asyncio.gather(*self.bot._finishing), 5)
        finished.assert_awaited_once()
        self.assertEqual(self.bot._ending, {})

    async def test_the_session_end_is_marked_in_the_transcript(self) -> None:
        _, sent = await self.joined_with_transcript()
        await self.bot.stop_table(GUILD, "test")  # answers at once; posts in the background
        await asyncio.gather(*self.bot._finishing)
        self.assertIn("Session ended", sent[-1])
