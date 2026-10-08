"""Starting and stopping campaign sessions (`/dmbot start` · `stop` · Status)."""

import asyncio
import contextlib
import dataclasses
import json
import logging
import time
from dataclasses import replace
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
from dmbot.memory import sheets
from dmbot.memory.sheet_store import CharacterSheet
from dmbot.sessions import SessionStore
from dmbot.transcript import questions as name_questions
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
        self.assertEqual(table.screen_level, "normal")  # how much DMbot says (#504)
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

    async def test_a_task_that_must_keep_running_is_logged_if_it_stops(self) -> None:
        async def returns() -> None:
            return None

        with self.assertLogs("dmbot.bot", "ERROR") as logs:
            await self.bot._watched(returns(), "transcribe")
            await asyncio.sleep(0)  # the callback runs
        self.assertIn("The transcribe task stopped", logs.output[0])

        async def runs() -> None:
            await asyncio.Event().wait()

        forever = self.bot._watched(runs(), "transcribe")
        await asyncio.sleep(0)
        forever.cancel()  # shutting down: nothing logged
        with self.assertNoLogs("dmbot.bot", "ERROR"):
            with contextlib.suppress(asyncio.CancelledError):
                await forever
            await asyncio.sleep(0)

    async def test_the_hint_cache_goes_once_the_last_words_are_written(self) -> None:
        await self.start()
        await self.bot.stop_session(GUILD, DM, False)
        # The stopped session's last clips still ask for hints, which fills it again…
        self.bot._hint_people_cache[GUILD] = (0.0, frozenset(), None, ((), ()))
        await asyncio.gather(*self.bot._finishing)  # …until the wind-down is done
        self.assertNotIn(GUILD, self.bot._hint_people_cache)

    async def test_status_shows_this_servers_own_backlog(self) -> None:
        from dmbot.audio.segmenter import Utterance
        from dmbot.ui.logic import WRITING_BEHIND_BACKLOG

        await self.start()
        other = GUILD + 1  # another server falling behind (#173, #470)
        for at in range(WRITING_BEHIND_BACKLOG + 1):
            self.bot.pipeline.enqueue(Utterance(other, PLAYER, at, at + 1000, bytes(3200), 0))
        mine = "\n".join(await self.bot.status_lines(GUILD))
        self.assertIn("Writing things down: keeping up", mine)  # not "falling behind"
        self.assertEqual(self.bot.pipeline.backlog_of(other), WRITING_BEHIND_BACKLOG + 1)

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
        with self.assertLogs("dmbot.sessions", "INFO") as logs:
            await self.bot.stop_session(GUILD, DM, False)
        self.assertIsNone(await self.sessions.get(GUILD))
        self.assertIn(f"Saved session removed: /dmbot stop by user {DM}", logs.output[-1])

    async def test_reading_sheets_never_delays_the_start(self) -> None:
        # #723: the kept sheets' names at once; the slow read of each link afterwards.
        kept = sheets.clean({"v": 1, "source": "typed", "name": "Testa", "spells": ["Old Spell"]})
        fresh = sheets.clean({"v": 1, "source": "typed", "name": "Testa", "spells": ["New Spell"]})
        sheet = CharacterSheet("0" * 32, "Testa", PLAYER, "u", kept, 1)
        store = MagicMock(sheets=AsyncMock(return_value=[sheet]))
        self.bot.sheets = store
        reading, done = asyncio.Event(), asyncio.Event()
        refreshed: list[bool] = []

        async def slow_refresh(*_: Any, **__: Any) -> list[CharacterSheet]:
            reading.set()
            await done.wait()  # D&D Beyond is slow today
            refreshed.append(True)
            return [dataclasses.replace(sheet, sheet=fresh)]

        with patch("dmbot.bot.refresh_sheets", slow_refresh):
            ok, _ = await asyncio.wait_for(self.start(), 2)
            self.assertTrue(ok)  # started without waiting for the sheets
            await asyncio.wait_for(reading.wait(), 2)
            table = self.bot.tables[GUILD]
            self.assertEqual(table.sheet_hints, ("Old Spell",))
            done.set()
            for _ in range(20):
                await asyncio.sleep(0)
        self.assertEqual((refreshed, table.sheet_hints), ([True], ("New Spell",)))

    async def test_stopping_stops_reading_sheets(self) -> None:
        sheet = CharacterSheet("0" * 32, "Testa", PLAYER, "u", None, None)
        self.bot.sheets = MagicMock(sheets=AsyncMock(return_value=[sheet]))
        reading = asyncio.Event()

        async def endless_refresh(*_: Any, **__: Any) -> list[CharacterSheet]:
            reading.set()
            await asyncio.Event().wait()
            return []

        with patch("dmbot.bot.refresh_sheets", endless_refresh):
            await self.start()
            await asyncio.wait_for(reading.wait(), 2)
            task = self.bot.tables[GUILD].sheet_task
            assert task is not None
            await self.bot.stop_session(GUILD, DM, False)
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        self.assertTrue(task.cancelled())

    async def test_after_a_restart_sheets_are_not_read_again(self) -> None:
        kept = sheets.clean({"v": 1, "source": "typed", "name": "Testa", "spells": ["Old Spell"]})
        sheet = CharacterSheet("0" * 32, "Testa", PLAYER, "u", kept, 1)
        self.bot.sheets = MagicMock(sheets=AsyncMock(return_value=[sheet]))
        await self.start()
        first = self.bot.tables[GUILD].sheet_task  # the first start's own read: let it finish
        assert first is not None
        await asyncio.wait_for(first, 2)
        table = self.bot.tables.pop(GUILD)  # as if picked up again after a restart
        table.resumed, table.sheet_hints = True, ()
        refresh = AsyncMock()
        with patch("dmbot.bot.refresh_sheets", refresh):
            await self.bot.start_table(table)
            task = table.sheet_task
            assert task is not None
            await asyncio.wait_for(task, 2)
        refresh.assert_not_awaited()
        self.assertEqual(table.sheet_hints, ("Old Spell",))

    def typed_sheet(self, entity: str, *spells: str) -> CharacterSheet:
        snapshot = sheets.clean(
            {"v": 1, "source": "typed", "name": "Testa", "spells": list(spells)}
        )
        return CharacterSheet(entity, "Testa", PLAYER, None, snapshot, 1)

    async def test_an_older_hints_load_finishing_last_never_wins(self) -> None:
        await self.start()
        table = self.bot.tables[GUILD]
        first = asyncio.Event()
        calls = 0

        async def sheets_now(*_: Any, **__: Any) -> list[CharacterSheet]:
            nonlocal calls
            calls += 1
            if calls == 1:
                await first.wait()  # the older load is slow
                return [self.typed_sheet("0" * 32, "Old Spell")]
            return [self.typed_sheet("0" * 32, "New Spell")]

        self.bot.sheets = MagicMock(sheets=AsyncMock(side_effect=sheets_now))
        older = asyncio.ensure_future(self.bot._sheet_hints(table, refresh=False))
        await asyncio.sleep(0)
        await self.bot._sheet_hints(table, refresh=False)  # the newer one
        first.set()
        await older
        self.assertEqual(table.sheet_hints, ("New Spell",))

    async def test_a_refresh_overlapped_by_a_change_loads_again(self) -> None:
        await self.start()
        table = self.bot.tables[GUILD]
        state = {"now": [self.typed_sheet("0" * 32, "Kept Spell")]}
        self.bot.sheets = MagicMock(sheets=AsyncMock(side_effect=lambda *a, **k: state["now"]))
        reading, done = asyncio.Event(), asyncio.Event()

        async def slow_refresh(*_: Any, **__: Any) -> list[CharacterSheet]:
            reading.set()
            await done.wait()
            return [self.typed_sheet("0" * 32, "Read Before The Change")]

        with patch("dmbot.bot.refresh_sheets", slow_refresh):
            run = asyncio.ensure_future(self.bot._sheet_hints(table, refresh=True))
            await asyncio.wait_for(reading.wait(), 2)
            state["now"] = [self.typed_sheet("0" * 32, "After The Change")]
            assert table.campaign_id is not None
            self.bot.sheets_changed(GUILD, table.campaign_id)  # e.g. the player typed theirs in
            done.set()
            await run
            for _ in range(20):
                await asyncio.sleep(0)
        self.assertEqual(table.sheet_hints, ("After The Change",))
        # Loaded at the start, by the change, and again after the refresh: three reads.
        self.assertGreaterEqual(self.bot.sheets.sheets.await_count, 3)

    async def test_the_start_log_line_never_holds_typed_names_links_or_secrets(self) -> None:
        await self.start()
        table = self.bot.tables[GUILD]
        read = sheets.clean(
            {"v": 1, "source": "dndbeyond", "name": "Testa", "spells": ["Test Spell", "Hidden One"]}
        )
        linked = CharacterSheet("0" * 32, "Testa", PLAYER, sheets.sheet_url(42), read, 1)
        typed = self.typed_sheet("1" * 32, "Typed By A Player")
        self.bot.sheets = MagicMock(sheets=AsyncMock(return_value=[linked, typed]))
        self.bot._secret_keys = AsyncMock(return_value=frozenset({"hidden one"}))  # type: ignore[method-assign]
        with (
            patch("dmbot.bot.refresh_sheets", AsyncMock(return_value=[linked, typed])),
            self.assertLogs("dmbot.bot", "INFO") as logs,
        ):
            await self.bot._sheet_hints(table, refresh=True)
        (line,) = [o for o in logs.output if "Character sheets:" in o]
        self.assertIn("1 linked", line)
        self.assertIn("Test Spell", line)
        for never in ("Typed By A Player", "Hidden One", "dndbeyond.com", "Testa"):
            self.assertNotIn(never, line)

    async def test_the_start_log_line_says_none_are_linked(self) -> None:
        await self.start()
        table = self.bot.tables[GUILD]
        self.bot.sheets = MagicMock(sheets=AsyncMock(return_value=[]))
        with (
            patch("dmbot.bot.refresh_sheets", AsyncMock(return_value=[])),
            self.assertLogs("dmbot.bot", "INFO") as logs,
        ):
            await self.bot._sheet_hints(table, refresh=True)
        (line,) = [o for o in logs.output if "Character sheets:" in o]
        self.assertIn("0 linked, 0 names in the hints; from D&D Beyond: none", line)

    async def test_a_sheet_store_failure_logs_only_its_kind(self) -> None:
        await self.start()
        table = self.bot.tables[GUILD]
        table.sheet_hints = ()
        broken = RuntimeError("DETAIL: Failing row contains (https://www.dndbeyond.com/...)")
        self.bot.sheets = MagicMock(sheets=AsyncMock(side_effect=broken))
        with self.assertLogs("dmbot.bot", "ERROR") as logs:
            await self.bot._sheet_hints(table, refresh=True)
        self.assertNotIn("dndbeyond", "\n".join(logs.output))
        self.assertIn("RuntimeError", logs.output[0])
        self.assertEqual(table.sheet_hints, ())

    async def test_stopping_a_session_that_wasnt_saved_is_a_warning(self) -> None:
        await self.start()
        await self.sessions.clear(GUILD, "test")  # the row goes missing, as in #147
        with self.assertLogs("dmbot.bot", "WARNING") as logs:
            await self.bot.stop_session(GUILD, DM, False)
        self.assertIn("Stopped a session that wasn't saved", "\n".join(logs.output))
        self.assertNotIn(GUILD, self.bot.tables)  # it still stops

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

    async def test_a_resumed_session_keeps_how_much_dmbot_says_and_alerts_still_show(
        self,
    ) -> None:
        await self.campaigns.set_dm_screen_level(GUILD, self.campaign.id, "quiet")
        await self.start()
        bot = await self.restart()
        await bot.resume_sessions()
        self.assertEqual(bot.tables[GUILD].screen_level, "quiet")  # #504
        self.posts.clear()
        await bot._alert_dm(GUILD, "⚠️ DMbot stopped hearing the table.")
        self.assertIn("⚠️ DMbot stopped hearing the table.", self.screen_posts())  # always

    def capture_views(self, bot: Any) -> list[tuple[str, list[str]]]:
        """Each message posted with buttons: its text and button IDs."""
        views: list[tuple[str, list[str]]] = []

        async def post_message(channel_id: int, text: str, view: Any = None) -> Any:
            self.posts.append((channel_id, text))
            views.append((text, [i.custom_id for i in view.children] if view else []))
            return MagicMock(edit=AsyncMock())

        bot.post_message = post_message
        return views

    async def test_a_level_change_is_noted_in_the_dm_screen_once(self) -> None:
        # #553: a co-DM sees why DMbot went quiet; the same level again says nothing.
        await self.start()
        self.posts.clear()
        await self.bot.set_screen_level(GUILD, self.campaign.id, "quiet", was="normal")
        await self.bot.set_screen_level(GUILD, self.campaign.id, "quiet", was="quiet")
        self.assertEqual(self.screen_posts(), ["🔇 How much DMbot says: Quiet."])
        await self.bot.set_screen_level(GUILD, self.campaign.id, "normal", was="quiet")
        self.assertEqual(self.screen_posts()[-1], "🔔 How much DMbot says: Normal.")

    async def test_a_level_change_while_starting_reaches_the_session(self) -> None:
        # #553: Quiet tapped while /dmbot start is still saving the session.
        saving, release, saved = asyncio.Event(), asyncio.Event(), asyncio.Event()
        save, set_level = self.sessions.save, self.campaigns.set_dm_screen_level

        async def slow_save(*args: Any, **kwargs: Any) -> Any:
            saving.set()
            await release.wait()
            return await save(*args, **kwargs)

        async def set_and_tell(*args: Any, **kwargs: Any) -> Any:
            result = await set_level(*args, **kwargs)
            saved.set()
            return result

        with (
            patch.object(self.sessions, "save", slow_save),
            patch.object(self.campaigns, "set_dm_screen_level", set_and_tell),
        ):
            starting = asyncio.create_task(self.start())
            await saving.wait()  # the start has read the campaign (still Normal)…
            changing = asyncio.create_task(
                self.bot.set_screen_level(GUILD, self.campaign.id, "quiet", was="normal")
            )
            await saved.wait()  # …and Quiet is saved; the tap now waits for the lock
            for _ in range(5):
                await asyncio.sleep(0)
            self.assertFalse(changing.done())  # waiting behind the start, not finished
            release.set()
            ok, message = await starting
            await changing
        self.assertTrue(ok, message)
        self.assertEqual(self.bot.tables[GUILD].screen_level, "quiet")

    async def test_a_session_still_finishing_follows_a_level_change(self) -> None:
        await self.start()
        table = self.bot.tables[GUILD]
        last_words = asyncio.Event()  # still writing down the last speech

        async def drain(*_: Any) -> bool:
            await last_words.wait()
            return True

        with patch.object(self.bot.pipeline, "drain", drain):
            await self.bot.stop_table(GUILD, "test")
            self.assertTrue(any(t is table for t in self.bot._ending.get(GUILD, [])))
            await self.bot.set_screen_level(GUILD, self.campaign.id, "quiet", was="normal")
            self.assertEqual(table.screen_level, "quiet")
            last_words.set()
            await asyncio.gather(*self.bot._finishing)

    async def test_listening_again_after_a_restart_has_settings_then_stop(self) -> None:
        from dmbot.ears.protocol import Status

        await self.start()
        bot = await self.restart()
        views = self.capture_views(bot)
        await bot.resume_sessions()
        await bot._on_status(bot.tables[GUILD], Status("joined", guild_id=GUILD))
        (ids,) = [ids for text, ids in views if "listening again" in text]
        cid = self.campaign.id
        self.assertEqual(ids, [f"dmbot:settings:{cid}", f"dmbot:stop:{cid}"])

    async def test_quick_repeat_restarts_stay_quiet(self) -> None:
        from dmbot.ears.protocol import Status

        await self.start()
        each: list[list[tuple[str, list[str]]]] = []
        for _ in range(2):  # the first restart is announced, the second is quick
            bot = await self.restart()
            each.append(self.capture_views(bot))
            await bot.resume_sessions()
            self.posts.clear()
            await bot._on_status(bot.tables[GUILD], Status("joined", guild_id=GUILD))
        self.assertFalse(any("listening again" in t for t in self.screen_posts()))
        # #553 (a decision): no new ⚙️ Settings message on a quiet resume either; the
        # help card is refreshed at the next /dmbot start. Don't "fix" this into a
        # card re-post on every restart.
        posted = [ids for _, ids in each[-1]]  # the second, quick one (the first is announced)
        self.assertFalse(any(i.startswith("dmbot:settings:") for ids in posted for i in ids))

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
        with self.assertLogs("dmbot.sessions", "INFO") as logs:
            await bot.resume_sessions()
        self.assertNotIn(GUILD, bot.tables)
        self.assertTrue(any("too long ago" in t for t in self.screen_posts()))
        self.assertIn(
            "Saved session removed: not resumed: started over 16 hours ago", logs.output[-1]
        )

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

    async def test_revoke_warns_first_and_yes_reaches_ears_without_a_session_here(self) -> None:
        from dmbot.bot import consent_revoke
        from dmbot.consent_dm import StopYesButton, warning_text

        self.guild.name = "Dragon Club"
        await self.consent.grant(GUILD, PLAYER)
        bot = await self.restart()  # ears may still be in voice from before
        await bot.consent.consenting(GUILD)
        interaction = SimpleNamespace(
            client=bot,
            guild=self.guild,
            guild_id=GUILD,
            user=member(PLAYER),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        await consent_revoke.callback(interaction)  # type: ignore[arg-type,call-arg]
        warned = interaction.response.send_message.await_args
        self.assertEqual(warned.args[0], warning_text(self.guild.name))  # #807: a warning
        self.assertTrue(warned.kwargs["ephemeral"])
        self.assertTrue(bot.consent.has_consent(GUILD, PLAYER))  # nothing until Yes
        self.assertFalse([m for m in self.ears.sent if '"allowlist"' in m])

        await StopYesButton(GUILD).callback(interaction)  # type: ignore[arg-type]
        lists = [json.loads(m) for m in self.ears.sent if '"allowlist"' in m]
        self.assertTrue(lists)
        self.assertNotIn(str(PLAYER), lists[0]["userIds"])
        done = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn(ALREADY_RECORDED, done)  # the same words as from the menu

    async def test_yes_that_cant_be_saved_still_stops_and_says_so(self) -> None:
        from dmbot.consent_dm import STOP_YES_LABEL, StopYesButton, revoke_not_saved

        await self.consent.grant(GUILD, PLAYER)
        self.consent.revoke = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        interaction = self._consent_interaction(PLAYER)
        with self.assertLogs("dmbot.consent_dm", "ERROR"):
            await StopYesButton(GUILD).callback(interaction)
        interaction.followup.send.assert_awaited_once_with(
            revoke_not_saved(STOP_YES_LABEL), ephemeral=True
        )
        self.assertFalse(self.consent.has_consent(GUILD, PLAYER))

    async def test_revoke_from_someone_not_recorded_says_so_without_a_warning(self) -> None:
        from dmbot.bot import consent_revoke
        from dmbot.consent_dm import not_recorded_text

        self.guild.name = "Dragon Club"
        interaction = self._consent_interaction(PLAYER)
        await consent_revoke.callback(interaction)  # type: ignore[call-arg]
        args = interaction.response.send_message.await_args
        self.assertEqual(args.args[0], not_recorded_text(self.guild.name))
        self.assertNotIn("view", args.kwargs)
        self.assertFalse(self.consent.has_consent(GUILD, PLAYER))

    async def test_yes_during_a_live_session_stops_capture_at_once(self) -> None:
        # #69, through the #807 path: ears drops them before anything slow.
        from dmbot.consent_dm import StopYesButton

        await self.consent.grant(GUILD, PLAYER)
        await self.start()
        interaction = self._consent_interaction(PLAYER)

        async def already_stopped(*_: Any, **__: Any) -> None:
            self.assertFalse(self.consent.has_consent(GUILD, PLAYER))  # before any await

        interaction.response.defer = AsyncMock(side_effect=already_stopped)
        await StopYesButton(GUILD).callback(interaction)
        interaction.response.defer.assert_awaited_once()
        await asyncio.sleep(0)  # the allowlist goes to ears in its own task
        last = [json.loads(m) for m in self.ears.sent if '"allowlist"' in m][-1]
        self.assertNotIn(str(PLAYER), last["userIds"])

    async def test_resume_starts_once(self) -> None:
        bot = await self.restart()
        bot._resume_with_retries = AsyncMock()  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", "INFO") as logs:
            await bot.on_ready()
            await bot.on_ready()  # Discord reconnected: must not start again
        await asyncio.gather(*bot._background)
        bot._resume_with_retries.assert_awaited_once()
        # How many servers DMbot is in, once per start, as a count (#426).
        counted = [r.getMessage() for r in logs.records if "server(s)" in r.getMessage()]
        self.assertEqual(counted, [f"Connected to {len(bot.guilds)} server(s)"])

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

    async def test_a_voice_warning_tells_the_dm_once_and_listening_goes_on(self) -> None:
        # #631: ears couldn't hear one person; the DM is told, not too often.
        from dmbot.ears.protocol import Status

        await self.start()
        await self.bot._on_ears_message(Status("joined", guild_id=GUILD))
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        self.bot.name_of = lambda guild_id, user_id: "Ulfgar"  # type: ignore[method-assign]
        warning = Status("warning", guild_id=GUILD, detail="kept failing", user_id=PLAYER)
        with self.assertLogs("dmbot.bot", level="WARNING") as logs:
            await self.bot._on_ears_message(warning)
            await self.bot._on_ears_message(warning)  # again at once: not told twice
        told = [c.args for c in posted.await_args_list if "words just now" in c.args[1]]
        self.assertEqual(len(told), 1)
        self.assertEqual(told[0][0], SCREEN)
        self.assertIn("If this happens again, ask them to leave the voice channel", told[0][1])
        self.assertIn("**Ulfgar**'s words", told[0][1])
        self.assertIn(f"Voice warning for user {PLAYER}", "\n".join(logs.output))
        self.assertTrue(self.bot.tables[GUILD].listening)  # the session goes on
        # Five minutes later it's said again.
        table = self.bot.tables[GUILD]
        table.voice_lost_told[PLAYER] -= 301
        await self.bot._on_ears_message(warning)
        told = [c.args for c in posted.await_args_list if "words just now" in c.args[1]]
        self.assertEqual(len(told), 2)

    async def test_failed_recording_notice_is_a_warning(self) -> None:
        from dmbot.ears.protocol import Status

        await self.start()
        self.bot.post = AsyncMock(return_value=False)  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", level="WARNING") as logs:
            await self.bot._on_ears_message(Status("joined", guild_id=GUILD))
        self.assertIn("Recording notice NOT posted", "\n".join(logs.output))

    async def test_after_a_restart_the_speech_line_says_so(self) -> None:
        # #523: the minutes count from the restart, and `resumed` resets on the join.
        from dmbot.ears.protocol import Status

        await self.start()
        bot = await self.restart()
        await bot.resume_sessions()
        table = bot.tables[GUILD]
        await bot._on_status(table, Status("joined", guild_id=GUILD))
        self.assertFalse(table.resumed)
        with self.assertLogs("dmbot.bot", "INFO") as logs:
            await bot.stop_table(GUILD, "test")
            await asyncio.gather(*bot._finishing)
        self.assertTrue(
            any("of speech (0%) since the restart" in m for m in logs.output), logs.output
        )

    async def test_resume_and_saved_stop_are_logged(self) -> None:
        await self.start()
        bot = await self.restart()
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await bot.resume_sessions()
        self.assertIn("Session started again after a restart", "\n".join(logs.output))

        bot = await self.restart()  # saved again, not running in this process
        with self.assertLogs("dmbot.sessions", level="INFO") as logs:
            await bot.stop_session(GUILD, DM, False)
        self.assertIn(
            f"Saved session removed: /dmbot stop by user {DM} before it resumed",
            "\n".join(logs.output),
        )

    def _consent_interaction(self, user_id: int) -> Any:
        return SimpleNamespace(
            client=self.bot,
            guild=self.guild,
            guild_id=GUILD,
            user=member(user_id),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
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
        settings, button = call.args[2].children  # Settings first (#515)
        self.assertEqual(button.custom_id, f"dmbot:stop:{self.campaign.id}")
        self.assertEqual(settings.custom_id, f"dmbot:settings:{self.campaign.id}")  # #515
        await self.bot.stop_table(GUILD, "test")
        await asyncio.gather(*self.bot._finishing)
        listening.edit.assert_awaited_with(view=None)

    def press_stop(self, user: Any) -> Any:
        return SimpleNamespace(
            client=self.bot,
            guild=self.guild,
            user=user,
            message=MagicMock(edit=AsyncMock()),
            response=SimpleNamespace(
                defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def ask_to_stop(self, user: Any, campaign_id: str | None = None) -> Any:
        """Press ⏹ Stop listening: the private question (#554), and its view."""
        from dmbot.dm_screen import StopListeningButton

        it = self.press_stop(user)
        await StopListeningButton(campaign_id or self.campaign.id).callback(it)
        return it

    @staticmethod
    def became(it: Any) -> str:
        """What the question became after the answer."""
        return str(it.edit_original_response.await_args.kwargs["content"])

    async def answer(self, question: Any, label: str, user: Any) -> Any:
        """Press "Yes, stop" or "Cancel" on the question."""
        view = question.response.send_message.await_args.kwargs["view"]
        (button,) = [b for b in view.children if b.label == label]
        it = self.press_stop(user)
        await button.callback(it)
        return it

    async def test_the_stop_button_asks_first_and_stops_only_for_the_dm(self) -> None:
        from dmbot.dm_screen import messages as m

        await self.start()
        question = await self.ask_to_stop(member(DM))
        self.assertIn(GUILD, self.bot.tables)  # one tap stops nothing
        args, kwargs = question.response.send_message.await_args
        self.assertEqual(args[0], "Stop listening and end the session for **Frostmaiden**?")
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual([b.label for b in kwargs["view"].children], ["Yes, stop", "Cancel"])
        # A player is told at once, with no question to answer.
        theirs = await self.ask_to_stop(member(PLAYER))
        self.assertEqual(theirs.response.send_message.await_args.args[0], m.ONLY_DM_STOPS)
        self.assertIsNone(theirs.response.send_message.await_args.kwargs.get("view"))
        # …and checked again at "Yes, stop" (say a DM lost the role meanwhile).
        from dmbot.dm_screen.buttons import StopConfirm

        session, _ = self.bot.active_session(GUILD) or (0, "")
        late = self.press_stop(member(PLAYER))
        yes = next(b for b in StopConfirm(self.campaign.id, session).children)
        await yes.callback(late)
        self.assertEqual(self.became(late), m.ONLY_DM_STOPS)
        self.assertIn(GUILD, self.bot.tables)  # still listening
        dm = await self.answer(question, "Yes, stop", member(DM))
        dm.response.edit_message.assert_awaited_once_with(content=m.STOPPING, view=None)
        self.assertIn("Stopped listening", self.became(dm))
        self.assertNotIn(GUILD, self.bot.tables)
        again = await self.ask_to_stop(member(DM))  # an old message, pressed later
        again.response.send_message.assert_awaited_once()
        self.assertEqual(again.response.send_message.await_args.args[0], m.NOT_LISTENING_NOW)
        again.message.edit.assert_awaited_with(view=None)

    async def test_cancel_keeps_listening(self) -> None:
        await self.start()
        question = await self.ask_to_stop(member(DM))
        cancel = await self.answer(question, "Cancel", member(DM))
        cancel.response.edit_message.assert_awaited_once_with(content="Still listening.", view=None)
        self.assertIn(GUILD, self.bot.tables)

    async def test_a_yes_from_an_earlier_session_stops_nothing(self) -> None:
        from dmbot.dm_screen import messages as m

        await self.start()
        question = await self.ask_to_stop(member(DM))
        await self.bot.stop_session(GUILD, DM, False)
        ok, message = await self.start()  # a new session of the same campaign
        self.assertTrue(ok, message)
        stale = await self.answer(question, "Yes, stop", member(DM))
        self.assertEqual(stale.edit_original_response.await_args.kwargs["content"], m.STOP_STALE)
        self.assertIn(GUILD, self.bot.tables)  # the new session keeps going
        await self.bot.stop_session(GUILD, DM, False)
        after = await self.answer(question, "Yes, stop", member(DM))  # nothing running now
        self.assertEqual(self.became(after), m.NOT_LISTENING_NOW)

    async def test_an_expired_question_says_so(self) -> None:
        from dmbot.dm_screen import messages as m

        await self.start()
        question = await self.ask_to_stop(member(DM))
        view = question.response.send_message.await_args.kwargs["view"]
        self.assertEqual(view.timeout, m.STOP_CONFIRM_S)
        await view.on_timeout()
        question.edit_original_response.assert_awaited_once_with(content=m.STOP_EXPIRED, view=None)
        self.assertIn(GUILD, self.bot.tables)

    async def test_a_server_manager_can_stop_but_not_for_another_campaign(self) -> None:
        await self.start()
        other = await self.campaigns.create(GUILD, "Strahd", DM)
        wrong = await self.ask_to_stop(member(OTHER_PERSON, manager=True), other.id)
        self.assertIsNone(wrong.response.send_message.await_args.kwargs.get("view"))
        self.assertIn(GUILD, self.bot.tables)  # an old button never stops a newer session
        question = await self.ask_to_stop(member(OTHER_PERSON, manager=True))
        manager = await self.answer(question, "Yes, stop", member(OTHER_PERSON, manager=True))
        self.assertIn("Stopped listening", self.became(manager))
        self.assertNotIn(GUILD, self.bot.tables)

    async def test_settings_change_how_much_dmbot_says_mid_session(self) -> None:
        # #515: ⚙️ Settings → Quiet saves it and the running session follows at once.
        from dmbot.dm_screen.settings import LevelButton

        await self.start()
        player = self.press_stop(member(PLAYER))
        await LevelButton(self.campaign.id, "quiet").callback(player)
        self.assertIn("Only this campaign's DM", player.response.send_message.await_args.args[0])
        self.assertEqual(self.bot.tables[GUILD].screen_level, "normal")
        dm = self.press_stop(member(DM))
        dm.edit_original_response = AsyncMock()
        await LevelButton(self.campaign.id, "quiet").callback(dm)
        self.assertEqual(self.bot.tables[GUILD].screen_level, "quiet")
        saved = await self.campaigns.get(GUILD, self.campaign.id)
        assert saved is not None
        self.assertEqual(saved.dm_screen_level, "quiet")
        content = dm.edit_original_response.await_args.kwargs["content"]
        self.assertIn("**How much DMbot says:** Quiet.", content)
        other = await self.campaigns.create(GUILD, "Strahd", DM)
        await self.bot.set_screen_level(GUILD, other.id, "normal", was="normal")  # another one
        self.assertEqual(self.bot.tables[GUILD].screen_level, "quiet")  # untouched

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

        await self.consent.grant(GUILD, PLAYER)  # only people still recorded are checked
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
        table.capture_log.add_health(PLAYER, 300, 500)  # 4 s lost: the audio rule fires (#671)
        # ...and their lines read garbled (#699): low confidence over enough words.
        table.capture_log.add_line(PLAYER, "I go to the ... the ... north road and", 0.3)
        await self.bot.post_summary(table)
        posted.assert_awaited_once()
        call = posted.await_args
        assert call is not None
        self.assertEqual(call.args[0], SCREEN)
        self.assertIn("voice is cutting out for DMbot", call.args[1])
        self.assertIn(PLAYER, table.totals.flagged)  # so the summary names them too

    async def test_one_tables_failed_check_never_stops_the_others(self) -> None:
        await self.start()
        first = self.bot.tables[GUILD]
        other = replace(first, guild_id=GUILD + 1)
        done: list[int] = []

        async def post_summary(table: Any) -> None:
            if table is first:
                raise RuntimeError("boom")
            done.append(table.guild_id)

        self.bot.post_summary = post_summary  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", level="ERROR") as logs:
            await asyncio.gather(self.bot._summary_one(first), self.bot._summary_one(other))
        self.assertIn("Capture check failed", "\n".join(logs.output))
        self.assertEqual(done, [GUILD + 1])

    async def test_the_audio_check_reads_their_lines_before_warning(self) -> None:
        # #699: the audio rule starts a check of their lines; only garbled lines warn, a
        # stop during the AI's wait names nobody, and a large loss warns without asking.
        from dmbot.ai import Reply
        from dmbot.audio.segmenter import Utterance

        class AI:
            def __init__(self, answer: str, during: Any = None) -> None:
                self.answer, self.during, self.calls = answer, during, 0

            async def complete(self, system: str, text: str, *, max_tokens: int = 8000) -> Reply:
                self.calls += 1
                if self.during is not None:
                    self.during()
                return Reply(self.answer, False, 100, 1)

        await self.consent.grant(GUILD, PLAYER)
        await self.start()
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        table = self.bot.tables[GUILD]

        def patchy(received: int = 300, expected: int = 500) -> None:
            table.capture_log.add_utterance(Utterance(GUILD, PLAYER, 0, 0, bytes(32000)))
            table.capture_log.add_health(PLAYER, received, expected)
            table.capture_log.add_line(PLAYER, "the bridge north", None)  # unknown: ask

        self.bot.topic_ai = AI("no")  # type: ignore[assignment]
        patchy()
        await self.bot.post_summary(table)
        posted.assert_not_awaited()  # reads fine: nothing for the DM

        # A stop that lands after the check is scheduled, before its turn: their lines
        # never reach the AI.
        table.audio_checker.asked_at.clear()
        table.audio_checker.fine_until.clear()
        early = AI("yes")
        self.bot.topic_ai = early  # type: ignore[assignment]
        due = table.capture_log.due

        def due_then_stop(now: float) -> Any:
            asyncio.get_running_loop().call_soon(self.bot.stop_recording, GUILD, PLAYER)
            return due(now)

        patchy()
        with patch.object(table.capture_log, "due", due_then_stop):
            await self.bot.post_summary(table)
        self.assertEqual(early.calls, 0)
        posted.assert_not_awaited()
        await self.consent.grant(GUILD, PLAYER)

        table.audio_checker.asked_at.clear()
        self.bot.topic_ai = AI(  # type: ignore[assignment]
            "yes", during=lambda: self.bot.stop_recording(GUILD, PLAYER)
        )
        patchy()
        await self.bot.post_summary(table)
        posted.assert_not_awaited()  # garbled, but they stopped during the wait
        self.assertNotIn(PLAYER, table.totals.flagged)

        await self.consent.grant(GUILD, PLAYER)
        asked = AI("no")
        self.bot.topic_ai = asked  # type: ignore[assignment]
        patchy(400, 1000)  # 40% got through, 12 s lost
        await self.bot.post_summary(table)
        posted.assert_awaited_once()  # at once, without asking
        self.assertEqual(asked.calls, 0)
        self.assertIn(PLAYER, table.totals.flagged)

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

        async def fake_post(channel_id: int, text: str) -> tuple[str, Any]:
            self.assertEqual(channel_id, TRANSCRIPT)
            sent.append(text)
            return "posted", None

        self.bot._post_transcript = fake_post  # type: ignore[method-assign]
        return table, sent

    def said(self, table: Any, text: str, user: int = PLAYER, at_ms: int = 0) -> None:
        from dmbot.audio.segmenter import Utterance

        utterance = Utterance(GUILD, user, at_ms, at_ms, bytes(32000), table.segmenter.session)
        self.bot._deliver_transcript(utterance, text)

    async def filtered(self, answer: str | Exception, *, hold: asyncio.Event | None = None) -> Any:
        """A session with the off-topic filter on (#52): an AI stand-in, and windows of
        two lines. The table, with the stand-in on `self.ai_calls`."""
        from dmbot.ai import Reply
        from dmbot.transcript.models import TranscriptBuffer

        calls: list[str] = []

        async def complete(system: str, text: str, *, max_tokens: int = 0) -> Reply:
            calls.append(text)
            if hold is not None:
                await hold.wait()  # the AI is slow
            if isinstance(answer, Exception):
                raise answer
            return Reply(answer, cut=False, input_tokens=50, output_tokens=6)

        self.ai_calls = calls
        self.addCleanup(setattr, self.bot, "topic_ai", self.bot.topic_ai)
        self.bot.topic_ai = SimpleNamespace(complete=complete)  # type: ignore[assignment]
        await self.consent.grant(GUILD, PLAYER)
        await self.consent.grant(GUILD, DM)  # the DM speaks in some of these too
        table, _ = await self.joined_with_transcript()
        table.unsaved = TranscriptBuffer()
        self.addCleanup(setattr, self.bot, "transcripts", self.bot.transcripts)
        self.bot.transcripts = object()  # type: ignore[assignment]  # only checked for None
        table.topics.window_lines = 2
        return table

    async def settle(self) -> None:
        for _ in range(10):
            await asyncio.sleep(0)

    async def test_off_topic_lines_never_reach_the_names_scan(self) -> None:
        table = await self.filtered("1 other\n2 game")
        self.said(table, "my boss called again")
        self.assertEqual(table.heard, [])  # held until its window is labelled
        self.said(table, "we sneak past the guards", at_ms=5_000)
        await self.settle()
        self.assertEqual(len(self.ai_calls), 1)  # one call for the window
        self.assertEqual(  # only the words, numbered: never who said them
            self.ai_calls[0], "1. my boss called again\n2. we sneak past the guards"
        )
        self.assertEqual([t for _, t in table.heard], ["we sneak past the guards"])
        topics = {line.text: line.topic for line in table.unsaved._waiting}
        self.assertEqual(topics["my boss called again"], "off_topic")
        self.assertEqual(topics["we sneak past the guards"], "game")
        self.assertEqual((table.topic_calls, table.topic_tokens), (1, [50, 6]))

    async def test_plainly_game_talk_isnt_asked_about(self) -> None:
        table = await self.filtered("1 other")
        self.said(table, "I roll a d20 for initiative")
        await self.settle()
        self.assertEqual(self.ai_calls, [])
        self.assertEqual([t for _, t in table.heard], ["I roll a d20 for initiative"])

    async def test_if_the_filter_fails_every_line_is_kept(self) -> None:
        from dmbot.ai import AIError

        table = await self.filtered(AIError("busy"))
        self.said(table, "my boss called again")
        self.said(table, "pass the chips", at_ms=5_000)
        await self.settle()
        self.assertEqual(len(table.heard), 2)  # when unsure, keep it
        self.assertTrue(all(line.topic == "game" for line in table.unsaved._waiting))

    async def test_someone_who_stops_is_never_labelled_or_scanned(self) -> None:
        table = await self.filtered("1 other\n2 other")
        self.said(table, "my boss called again")
        self.bot.stop_recording(GUILD, PLAYER)
        self.said(table, "pass the chips please", user=DM, at_ms=5_000)
        self.said(table, "and the salsa too", user=DM, at_ms=9_000)  # fills the window
        await self.settle()
        self.assertEqual(self.ai_calls, ["1. pass the chips please\n2. and the salsa too"])
        self.assertTrue(all(text != "my boss called again" for _, text in table.heard))

    async def test_off_topic_lines_in_the_channel_become_markers_one_edit_each(self) -> None:
        table = await self.filtered("1 other\n2 other")
        posted = MagicMock(edit=AsyncMock())

        async def post(channel_id: int, text: str) -> Any:
            return "posted", posted

        self.bot._post_transcript = post  # type: ignore[method-assign]
        self.said(table, "my boss called again")
        self.said(table, "he wants me in on Monday", at_ms=5_000)
        await self.bot.flush_transcript(table)  # both posted, in one message
        await self.settle()
        posted.edit.assert_awaited_once()  # one edit for the message, not one per line
        content = posted.edit.await_args.kwargs["content"]
        self.assertNotIn("boss", content)
        self.assertEqual(content.count("of off-topic chat skipped"), 2)  # brackets escaped

    async def test_table_talk_is_kept_everywhere(self) -> None:
        table = await self.filtered("1 table\n2 game")
        self.said(table, "wait whose turn is it")
        self.said(table, "we sneak past the guards", at_ms=5_000)
        await self.settle()
        self.assertEqual(len(table.heard), 2)
        topics = {line.text: line.topic for line in table.unsaved._waiting}
        self.assertEqual(topics["wait whose turn is it"], "table_talk")

    async def test_a_short_line_isnt_asked_about(self) -> None:
        table = await self.filtered("1 other")
        self.said(table, "ok sure")
        await self.settle()
        self.assertEqual(self.ai_calls, [])
        self.assertEqual([t for _, t in table.heard], ["ok sure"])

    async def test_a_window_that_doesnt_fill_is_asked_about_after_a_while(self) -> None:
        table = await self.filtered("1 other")
        table.topics.window_lines, table.topics.window_s = 6, 0.01
        self.said(table, "my boss called again")
        await asyncio.sleep(0.05)
        await self.settle()
        self.assertEqual(len(self.ai_calls), 1)
        self.assertEqual(table.heard, [])

    async def test_someone_who_stops_while_the_ai_answers_is_left_alone(self) -> None:
        answering = asyncio.Event()
        table = await self.filtered("1 game\n2 game", hold=answering)
        self.said(table, "my boss called again")
        self.said(table, "pass the chips please", at_ms=5_000)
        await self.settle()
        self.bot.stop_recording(GUILD, PLAYER)  # while the AI is answering
        answering.set()
        await self.settle()
        self.assertEqual(table.heard, [])  # nothing of theirs reaches the names scan

    async def test_a_stop_that_reaches_the_cache_from_elsewhere_sends_nothing(self) -> None:
        # Consent is checked right before the call, not only when lines are added.
        table = await self.filtered("1 game")
        table.topics.window_lines = 2
        self.said(table, "my boss called again")
        await self.consent.revoke(GUILD, PLAYER)  # the store, not stop_recording
        self.said(table, "pass the chips please", user=DM, at_ms=5_000)
        await self.settle()
        self.assertEqual(self.ai_calls, ["1. pass the chips please"])

    async def test_a_slow_ai_never_holds_up_the_end_of_a_session(self) -> None:
        import dmbot.bot as bot_module

        never = asyncio.Event()
        table = await self.filtered("1 other", hold=never)
        table.topics.window_lines = 6
        self.said(table, "my boss called again")
        with patch.object(bot_module, "TOPIC_CALL_TIMEOUT_S", 0.01):
            await self.bot.stop_table(GUILD, "test")
            await asyncio.wait_for(asyncio.gather(*self.bot._finishing), 2)
        self.assertEqual([t for _, t in table.heard], ["my boss called again"])  # kept

    async def test_the_last_window_is_labelled_when_the_session_ends(self) -> None:
        table = await self.filtered("1 other")
        table.topics.window_lines = 6  # it won't fill
        self.said(table, "my boss called again")
        await self.bot.stop_table(GUILD, "test")
        await asyncio.gather(*self.bot._finishing)
        self.assertEqual(len(self.ai_calls), 1)
        self.assertEqual(table.heard, [])

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

    async def test_misheard_names_are_fixed_and_what_was_heard_is_kept(self) -> None:
        import time

        from dmbot.memory.lookup import CampaignLookup, LookupData
        from dmbot.memory.models import CONFIRMED, Alias, Entity
        from dmbot.transcript.models import TranscriptBuffer

        await self.consent.grant(GUILD, PLAYER)
        table, sent = await self.joined_with_transcript()
        eid = "b" * 32
        table.name_lookup = CampaignLookup.build(
            LookupData(
                1,
                (Entity(eid, "npc", "Belleros", "", CONFIRMED, None, "dm", 0),),
                (
                    Alias(
                        "a" * 32,
                        eid,
                        "Belleros",
                        "belleros",
                        "full",
                        None,
                        False,
                        CONFIRMED,
                        (),
                        "dm",
                        0,
                    ),
                ),
                (),
                (),
            )
        )
        table.scene.note([eid], DM, time.monotonic())  # Belleros came up a moment ago
        table.unsaved = TranscriptBuffer()
        self.bot.transcripts = object()  # type: ignore[assignment]  # only checked for None
        self.said(table, "I think Beleros has it")
        await self.bot.flush_transcript(table)
        self.assertIn("I think Belleros has it", "\n".join(sent))
        (saved,) = table.unsaved.take(lambda _: True)
        self.assertEqual((saved.heard, saved.text), ("I think Beleros has it",
                                                     "I think Belleros has it"))  # fmt: skip
        # the scan reads the cleaned line, so a name fixed live isn't new (#394)
        self.assertEqual(table.heard[-1], (PLAYER, "I think Belleros has it"))
        self.assertEqual(table.heard_counts[(eid, PLAYER)], 1)
        self.assertTrue(table.vocabulary.is_name("beleros"))
        self.bot.stop_recording(GUILD, PLAYER)
        self.assertFalse(table.vocabulary.is_name("beleros"))  # forgotten with them

    def two_alike(self, *more: Any) -> Any:
        """A campaign where "Marin" sounds like both Maren and Marron (#296); `more`:
        other aliases."""
        from dmbot.memory.lookup import CampaignLookup, LookupData
        from dmbot.memory.models import CONFIRMED, Alias, Entity

        names = (("a" * 32, "Maren"), ("b" * 32, "Marron"))
        return CampaignLookup.build(
            LookupData(
                1,
                tuple(Entity(e, "npc", n, "", CONFIRMED, None, "dm", 0) for e, n in names),
                tuple(
                    Alias(
                        e[:16] + "0" * 16,
                        e,
                        n,
                        n.lower(),
                        "full",
                        None,
                        False,
                        CONFIRMED,
                        (),
                        "dm",
                        0,
                    )
                    for e, n in names
                )
                + more,
                (),
                (),
            )
        )

    async def asked_about_marin(
        self, *, saving: bool = False, level: str = "normal"
    ) -> tuple[Any, Any, Any, Any]:
        """A question asked; the table, its message, the post and the campaign memory.
        `saving`: the line waits to be saved (and `self.bot.transcripts` is a stand-in);
        `level`: how much DMbot says in the DM screen (#504)."""
        from dmbot.transcript.models import TranscriptBuffer

        await self.consent.grant(GUILD, PLAYER)
        table, _ = await self.joined_with_transcript()
        table.name_lookup = self.two_alike()
        if saving:
            table.unsaved = TranscriptBuffer()
            self.addCleanup(setattr, self.bot, "transcripts", self.bot.transcripts)
            self.bot.transcripts = MagicMock(relabel_line=AsyncMock(return_value=1))
        import time

        table.scene.note(["a" * 32], DM, time.monotonic())  # Maren came up: it matters
        message = MagicMock(edit=AsyncMock())
        self.bot.post_message = AsyncMock(return_value=message)  # type: ignore[method-assign]
        memory: Any = MagicMock(add_correction=AsyncMock(return_value=MagicMock(batch=41)))
        self.addCleanup(setattr, self.bot, "memory", self.bot.memory)  # put back after
        self.bot.memory = memory
        table.screen_level = level
        self.said(table, "then Marin speaks")
        for _ in range(5):
            await asyncio.sleep(0)  # the question is posted in the background
        return table, message, self.bot.post_message, memory

    async def test_the_dm_is_asked_did_they_mean(self) -> None:
        table, _, posted, _ = await self.asked_about_marin()
        channel, text, view = posted.await_args.args
        self.assertEqual(channel, SCREEN)  # the DM screen, never the transcript channel
        self.assertIn('say "Marin"** ("…then Marin speaks…")', text)
        labels = [item.item.label for item in view.children]
        self.assertEqual(sorted(labels[:2]), ["Maren", "Marron"])
        self.assertEqual(labels[2:], ["Type it…", 'Keep "Marin"'])  # #503
        # one open question at a time: a new word isn't asked about yet
        self.said(table, "then Marrin agrees")
        await asyncio.sleep(0)
        self.assertEqual(posted.await_count, 1)

    async def test_only_the_dm_answers_and_the_answer_is_saved(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        text, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", PLAYER)
        self.assertFalse(done)
        self.assertIn("Only the DM", text)
        text, done, undo = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertTrue(done)
        entity_id, name = asked.options[0]
        self.assertIn(f"**{name}**", text)
        add = memory.add_correction
        add.assert_awaited_once()
        self.assertEqual(add.await_args.args[2], "Marin")
        self.assertEqual(add.await_args.kwargs["entity_id"], entity_id)
        self.assertEqual(add.await_args.kwargs["source"], "dm")
        self.assertEqual(undo, (table.campaign_id, 41))  # Undo takes this change back
        again, _, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertIn("closed", again)  # answered once only

    def waiting_text(self, table: Any) -> str:
        (line,) = list(table.unsaved._waiting)
        return str(line.text)

    async def test_an_answer_fixes_the_line_it_asked_about_and_undo_puts_it_back(self) -> None:
        table, _, _, _ = await self.asked_about_marin(saving=True)
        asked = table.questions.open
        assert asked is not None
        _, name = asked.options[0]
        text, _, undo = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertIn("in that line and from now on", text)  # #503
        self.assertEqual(self.waiting_text(table), f"then {name} speaks")
        assert undo is not None
        back = await self.bot.answer_undone(GUILD, table.campaign_id, undo[1])
        self.assertIs(back, True)
        self.assertEqual(self.waiting_text(table), "then Marin speaks")  # as heard again
        self.assertIsNone(await self.bot.answer_undone(GUILD, table.campaign_id, undo[1]))

    async def test_an_answer_fixes_the_saved_line(self) -> None:
        table, _, _, _ = await self.asked_about_marin(saving=True)
        table.unsaved.take(lambda _: True)  # already saved
        table.transcript_session_id = "s5"
        asked = table.questions.open
        assert asked is not None
        _, name = asked.options[0]
        await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        saved: Any = self.bot.transcripts
        saved.relabel_line.assert_awaited_once_with(GUILD, "s5", PLAYER, 0, f"then {name} speaks")

    async def test_an_answer_edits_the_channel_message_while_it_is_recent(self) -> None:
        table, _, _, _ = await self.asked_about_marin()
        posted = MagicMock(edit=AsyncMock())

        async def post(channel_id: int, text: str) -> Any:
            return "posted", posted

        self.bot._post_transcript = post  # type: ignore[method-assign]
        await self.bot.flush_transcript(table)
        asked = table.questions.open
        assert asked is not None
        _, name = asked.options[0]
        text, _, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertIn(f"then {name} speaks", posted.edit.await_args.kwargs["content"])
        self.assertIn("in that line", text)  # nothing saved, but the channel shows it

    async def test_undo_of_an_answer_puts_the_saved_line_back(self) -> None:
        table, _, _, _ = await self.asked_about_marin(saving=True)
        table.unsaved.take(lambda _: True)  # already saved
        table.transcript_session_id = "s5"
        asked = table.questions.open
        assert asked is not None
        _, _, undo = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        assert undo is not None
        await self.bot.answer_undone(GUILD, "another campaign", undo[1])  # not this one
        saved: Any = self.bot.transcripts
        self.assertEqual(saved.relabel_line.await_count, 1)
        await self.bot.answer_undone(GUILD, table.campaign_id, undo[1])
        self.assertEqual(saved.relabel_line.await_args.args[4], "then Marin speaks")

    async def test_an_answer_already_saved_still_fixes_the_line(self) -> None:
        table, _, _, memory = await self.asked_about_marin(saving=True)
        memory.add_correction.return_value = MagicMock(batch=None)  # nothing to undo
        asked = table.questions.open
        assert asked is not None
        _, name = asked.options[0]
        text, _, undo = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertIsNone(undo)
        self.assertIn("in that line", text)
        self.assertEqual(self.waiting_text(table), f"then {name} speaks")
        self.assertEqual([a.batch for a in table.fix_notes.answers], [None])  # kept

    async def test_someone_who_stops_while_the_answer_saves_keeps_their_line(self) -> None:
        table, _, _, memory = await self.asked_about_marin(saving=True)
        table.unsaved.take(lambda _: True)
        table.transcript_session_id = "s5"
        asked = table.questions.open
        assert asked is not None

        async def stop_meanwhile(*_: Any, **__: Any) -> Any:
            self.bot.stop_recording(GUILD, PLAYER)
            return MagicMock(batch=7)

        memory.add_correction.side_effect = stop_meanwhile
        text, _, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertNotIn("Marin", text)
        saved: Any = self.bot.transcripts
        saved.relabel_line.assert_not_awaited()  # their line isn't touched

    async def test_an_answer_that_would_write_a_secret_leaves_the_line(self) -> None:
        from dmbot.memory.models import CONFIRMED, Alias

        table, _, _, _ = await self.asked_about_marin(saving=True)
        # Made secret since the question was asked: "Maren Speaks" is a secret title.
        hidden = Alias("b" * 16 + "1" * 16, "b" * 32, "Maren Speaks", "maren speaks",
                       "title", None, True, CONFIRMED, (), "dm", 0)  # fmt: skip
        table.name_lookup = self.two_alike(hidden)
        asked = table.questions.open
        assert asked is not None
        pick = next(str(i) for i, (_, n) in enumerate(asked.options) if n == "Maren")
        text, done, _ = await self.bot.answer_name_question(GUILD, asked.id, pick, DM)
        self.assertTrue(done)  # the rule is saved
        self.assertNotIn("in that line", text)
        self.assertIn("That line stays as heard.", text)
        self.assertEqual(self.waiting_text(table), "then Marin speaks")  # not written

    async def test_a_failure_writing_the_line_still_gives_the_answer_its_undo(self) -> None:
        table, _, _, _ = await self.asked_about_marin(saving=True)
        table.unsaved.take(lambda _: True)  # already saved
        table.transcript_session_id = "s5"
        saved: Any = self.bot.transcripts
        saved.relabel_line.side_effect = RuntimeError("database down")
        asked = table.questions.open
        assert asked is not None
        with self.assertLogs("dmbot.bot", "ERROR"):
            text, done, undo = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertTrue(done)
        self.assertEqual(undo, (table.campaign_id, 41))
        self.assertIn("That line stays as heard.", text)
        self.assertEqual(table.fix_notes.answers, [])  # not brought in by a later rewrite

    async def test_undo_of_an_answer_says_when_its_line_stays(self) -> None:
        # #589: the database can't put the line back: the DM is told (False).
        table, _, _, _ = await self.asked_about_marin(saving=True)
        table.unsaved.take(lambda _: True)  # already saved
        table.transcript_session_id = "s5"
        asked = table.questions.open
        assert asked is not None
        _, _, undo = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        assert undo is not None
        saved: Any = self.bot.transcripts
        saved.relabel_line.side_effect = RuntimeError("database down")
        with self.assertLogs("dmbot.bot", "ERROR"):
            back = await self.bot.answer_undone(GUILD, table.campaign_id, undo[1])
        self.assertIs(back, False)

    async def test_undo_of_an_answer_says_nothing_about_someone_who_stopped(self) -> None:
        table, _, _, _ = await self.asked_about_marin(saving=True)
        asked = table.questions.open
        assert asked is not None
        _, _, undo = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        assert undo is not None
        await table.save_lock.acquire()  # a batch being saved
        undoing = asyncio.create_task(self.bot.answer_undone(GUILD, table.campaign_id, undo[1]))
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertFalse(undoing.done())  # waiting for the lock
        await self.consent.revoke(GUILD, PLAYER)
        table.save_lock.release()
        self.assertIsNone(await undoing)

    async def test_a_failed_save_leaves_the_channel_as_heard_too(self) -> None:
        # All or nothing: "That line stays as heard" must be true in the channel as well.
        table, _, _, _ = await self.asked_about_marin(saving=True)
        posted = MagicMock(edit=AsyncMock())

        async def post(channel_id: int, text: str) -> Any:
            return "posted", posted

        self.bot._post_transcript = post  # type: ignore[method-assign]
        await self.bot.flush_transcript(table)
        table.unsaved.take(lambda _: True)  # already saved
        table.transcript_session_id = "s5"
        saved: Any = self.bot.transcripts
        saved.relabel_line.side_effect = RuntimeError("database down")
        asked = table.questions.open
        assert asked is not None
        with self.assertLogs("dmbot.bot", "ERROR"):
            text, _, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertIn("That line stays as heard.", text)
        posted.edit.assert_not_awaited()

    async def test_a_failure_editing_the_channel_after_the_save_still_says_fixed(self) -> None:
        table, _, _, _ = await self.asked_about_marin(saving=True)
        posted = MagicMock(edit=AsyncMock(side_effect=OSError("connection reset")))

        async def post(channel_id: int, text: str) -> Any:
            return "posted", posted

        self.bot._post_transcript = post  # type: ignore[method-assign]
        await self.bot.flush_transcript(table)
        asked = table.questions.open
        assert asked is not None
        with self.assertLogs("dmbot.bot", "ERROR"):
            text, _, undo = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertIn("in that line", text)  # the saved line is fixed
        self.assertIsNotNone(undo)

    async def test_no_line_to_fix_says_nothing_about_it(self) -> None:
        table, _, _, _ = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        table.questions.open = replace(asked, line="")  # e.g. asked before #503
        text, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertTrue(done)
        self.assertNotIn("That line", text)
        self.assertNotIn("in that line", text)

    async def test_a_typed_name_dmbot_doesnt_know_becomes_a_new_name(self) -> None:
        table, _, _, memory = await self.asked_about_marin(saving=True)
        memory.add_typed_name = AsyncMock(return_value=MagicMock(batch=42))
        asked = table.questions.open
        assert asked is not None
        text, done, undo = await self.bot.answer_name_question(
            GUILD, asked.id, "type", DM, "  Maerin  "
        )
        self.assertTrue(done)
        self.assertIn("**Maerin**", text)
        self.assertIn("is new: it waits in 📝 Check new names", text)
        memory.add_typed_name.assert_awaited_once_with(GUILD, table.campaign_id, "Marin", "Maerin")
        memory.add_correction.assert_not_awaited()
        self.assertEqual(undo, (table.campaign_id, 42))
        self.assertEqual(self.waiting_text(table), "then Maerin speaks")

    async def test_a_typed_name_dmbot_knows_is_that_name(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        text, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "type", DM, "maren")
        self.assertTrue(done)
        self.assertIn("**Maren**", text)  # written its own way
        self.assertEqual(memory.add_correction.await_args.kwargs["entity_id"], "a" * 32)
        self.assertEqual(memory.add_correction.await_args.kwargs["action"], "fix")

    async def test_a_typed_secret_name_or_a_bad_one_leaves_the_question_open(self) -> None:
        from dmbot.memory.models import CONFIRMED, Alias

        table, _, _, memory = await self.asked_about_marin()
        hidden = Alias("a" * 16 + "1" * 16, "a" * 32, "the Veiled One", "the veiled one",
                       "title", None, True, CONFIRMED, (), "dm", 0)  # fmt: skip
        table.name_lookup = self.two_alike(hidden)
        asked = table.questions.open
        assert asked is not None
        for typed, expected in [
            ("The Veiled One", name_questions.TYPED_SECRET),
            ("Maren | NPC", "| sign"),
            ("x" * 61, "longer than 60"),
            ("   ", "No name was typed"),
        ]:
            with self.subTest(typed=typed):
                text, done, _ = await self.bot.answer_name_question(
                    GUILD, asked.id, "type", DM, typed
                )
                self.assertFalse(done)
                self.assertIn(expected, text)
        self.assertTrue(table.questions.is_open(asked.id))
        memory.add_correction.assert_not_awaited()

    async def test_a_typed_name_from_someone_else_or_after_they_stopped(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        memory.add_typed_name = AsyncMock(return_value=MagicMock(batch=42))
        asked = table.questions.open
        assert asked is not None
        who = self.bot.can_type_answer(GUILD, asked.id, PLAYER)
        self.assertEqual(who, (name_questions.ONLY_DM, ""))
        self.assertEqual(self.bot.can_type_answer(GUILD, asked.id, DM), (None, "Marin"))
        self.bot.stop_recording(GUILD, PLAYER)  # while the form was open
        text, _, _ = await self.bot.answer_name_question(GUILD, asked.id, "type", DM, "Maerin")
        self.assertIn("closed before your answer arrived", text)
        self.assertNotIn("Marin", text)  # their words aren't shown again
        memory.add_typed_name.assert_not_awaited()
        self.assertEqual(self.bot.can_type_answer(GUILD, asked.id, DM)[0], name_questions.EXPIRED)

    async def test_a_typed_name_the_store_refuses_leaves_the_question_open(self) -> None:
        from dmbot.memory.models import MemoryRuleError

        table, _, _, memory = await self.asked_about_marin()
        memory.add_typed_name = AsyncMock(side_effect=MemoryRuleError("taken"))
        asked = table.questions.open
        assert asked is not None
        text, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "type", DM, "Vessa")
        self.assertEqual((text, done), (name_questions.TYPED_REFUSED, False))
        self.assertTrue(table.questions.is_open(asked.id))

    async def test_a_typed_name_needs_the_names_checked(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        memory.add_typed_name = AsyncMock()
        table.name_lookup = None  # couldn't be loaded
        asked = table.questions.open
        assert asked is not None
        text, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "type", DM, "Vessa")
        self.assertEqual((text, done), (name_questions.TYPED_CANT_CHECK, False))
        memory.add_typed_name.assert_not_awaited()

    async def test_typed_just_as_heard_keeps_it(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        memory.add_typed_name = AsyncMock()
        asked = table.questions.open
        assert asked is not None
        text, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "type", DM, "marin")
        self.assertTrue(done)
        self.assertEqual(memory.add_correction.await_args.kwargs["action"], "keep")
        memory.add_typed_name.assert_not_awaited()
        self.assertIn("won't change", text)

    async def test_quiet_asks_nothing(self) -> None:
        # #504: at quiet, no "Did they mean…?" (fix notes: with a suggestion, below).
        table, _, posted, _ = await self.asked_about_marin(level="quiet")
        posted.assert_not_awaited()
        self.assertIsNone(table.questions.open)

    async def test_keep_as_heard_is_saved(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        await self.bot.answer_name_question(GUILD, asked.id, "keep", DM)
        self.assertEqual(memory.add_correction.await_args.kwargs["action"], "keep")

    async def test_a_press_while_saving_waits_and_a_failure_reopens(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(*_: Any, **__: Any) -> Any:
            started.set()
            await release.wait()
            raise RuntimeError("database down")

        memory.add_correction.side_effect = slow
        first = asyncio.create_task(self.bot.answer_name_question(GUILD, asked.id, "0", DM))
        await started.wait()
        text, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "1", DM)
        self.assertEqual((text, done), (name_questions.BUSY, False))
        release.set()
        with self.assertRaises(RuntimeError):
            await first
        self.assertTrue(table.questions.is_open(asked.id))  # open again, buttons still work
        self.assertIsNotNone(table.question_message)

    async def test_someone_who_stops_while_the_answer_saves_stays_hidden(self) -> None:
        table, message, _, memory = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None

        async def revoke_meanwhile(*_: Any, **__: Any) -> Any:
            self.bot.stop_recording(GUILD, PLAYER)
            return MagicMock(batch=7)

        memory.add_correction.side_effect = revoke_meanwhile
        text, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertTrue(done)
        self.assertNotIn("Marin", text)  # their words aren't shown again
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertNotIn("Marin", message.edit.await_args.kwargs["content"])

    async def test_a_speaker_who_stops_closes_their_question(self) -> None:
        table, message, _, memory = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        self.bot.stop_recording(GUILD, PLAYER)
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertIsNone(table.questions.open)
        self.assertNotIn("Marin", message.edit.await_args.kwargs["content"])  # words gone
        text, _, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertIn("closed", text)
        memory.add_correction.assert_not_awaited()

    async def test_an_unanswered_question_expires_then_a_cooldown(self) -> None:
        table, message, posted, _ = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        table.questions.open = replace(asked, asked_at=asked.asked_at - 10_000)
        self.said(table, "then Marrin agrees")
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(
            message.edit.await_args.kwargs["content"], '⌛ Not answered: "Marin" stays as heard.'
        )
        self.assertEqual(posted.await_count, 1)  # the cooldown: not straight away
        table.questions.closed_at = -10_000.0  # the cooldown has passed
        self.said(table, "then Marrin agrees")
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(posted.await_count, 2)

    async def test_stopping_the_session_closes_its_question(self) -> None:
        table, message, _, _ = await self.asked_about_marin()
        await self.bot.stop_table(GUILD, "test")
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertIsNone(table.questions.open)
        self.assertIn("Not answered", message.edit.await_args.kwargs["content"])

    async def test_the_session_ending_while_the_answer_saves_still_says_got_it(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None

        async def end_meanwhile(*_: Any, **__: Any) -> Any:
            await self.bot.stop_table(GUILD, "test")
            return MagicMock(batch=8)

        memory.add_correction.side_effect = end_meanwhile
        text, done, undo = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertTrue(done)
        self.assertIn("Got it", text)  # not "stopped being recorded": they didn't
        self.assertEqual(undo, (table.campaign_id, 8))

    async def test_an_answer_saved_before_offers_no_undo(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        memory.add_correction.return_value = MagicMock(batch=None)  # already there
        _, done, undo = await self.bot.answer_name_question(GUILD, asked.id, "keep", DM)
        self.assertTrue(done)
        self.assertIsNone(undo)

    async def fixed_from_a_suggestion(self, level: str = "normal") -> tuple[Any, Any, Any]:
        """A line where "Hrothgarr" is fixed to the suggested (not confirmed) Hrothgar;
        `level`: how much DMbot says in the DM screen."""
        from dmbot.memory.lookup import CampaignLookup, LookupData
        from dmbot.memory.models import PROPOSED, Alias, Entity
        from dmbot.transcript.models import TranscriptBuffer

        await self.consent.grant(GUILD, PLAYER)
        table, _ = await self.joined_with_transcript()
        eid = "d" * 32
        table.name_lookup = CampaignLookup.build(
            LookupData(
                1,
                (Entity(eid, "concept", "Hrothgar", "", PROPOSED, None, "scan", 0),),
                (
                    Alias(
                        eid,
                        eid,
                        "Hrothgar",
                        "hrothgar",
                        "full",
                        None,
                        False,
                        PROPOSED,
                        (),
                        "scan",
                        0,
                    ),
                ),
                (),
                (),
            )
        )
        table.unsaved = TranscriptBuffer()
        self.bot.transcripts = object()  # type: ignore[assignment]  # only checked for None
        message = MagicMock(edit=AsyncMock())
        self.bot.post_message = AsyncMock(return_value=message)  # type: ignore[method-assign]
        memory: Any = MagicMock(add_correction=AsyncMock(return_value=MagicMock(batch=3)))
        self.addCleanup(setattr, self.bot, "memory", self.bot.memory)
        self.bot.memory = memory
        table.screen_level = level
        self.said(table, "then Hrothgarr roars")
        for _ in range(5):
            await asyncio.sleep(0)
        return table, message, memory

    async def test_quiet_leaves_a_suggested_name_as_heard(self) -> None:
        # #504: such a fix is never silent, so with no Undo shown it isn't made.
        table, _, _ = await self.fixed_from_a_suggestion("quiet")
        self.bot.post_message.assert_not_awaited()  # type: ignore[attr-defined]
        (line,) = list(table.unsaved._waiting)
        self.assertEqual(line.text, "then Hrothgarr roars")
        self.assertEqual(table.fix_notes.notes, [])

    async def test_a_session_still_finishing_makes_no_unsure_fixes(self) -> None:
        # Its fix notes can't be shown any more, so such a fix would be silent.
        table, _, _ = await self.fixed_from_a_suggestion()
        self.bot.tables.pop(GUILD)
        self.bot._ending[GUILD] = [table]
        self.addCleanup(self.bot._ending.pop, GUILD, None)
        self.said(table, "and Hrothgarr leaves")
        self.assertEqual(list(table.unsaved._waiting)[-1].text, "and Hrothgarr leaves")

    async def test_the_campaigns_level_is_used_when_a_session_starts(self) -> None:
        await self.campaigns.set_dm_screen_level(GUILD, self.campaign.id, "quiet")
        ok, message = await self.start()
        self.assertTrue(ok, message)
        self.assertEqual(self.bot.tables[GUILD].screen_level, "quiet")

    async def test_a_fix_from_a_suggestion_is_shown_with_undo(self) -> None:
        table, _, _ = await self.fixed_from_a_suggestion()
        channel, text, view = self.bot.post_message.await_args.args  # type: ignore[attr-defined]
        self.assertEqual(channel, SCREEN)  # the DM screen, never the transcript channel
        self.assertIn("1. **Hrothgarr** → **Hrothgar**", text)
        self.assertEqual([b.item.label for b in view.children], ["Undo 1"])
        (line,) = list(table.unsaved._waiting)
        self.assertEqual(line.text, "then Hrothgar roars")  # fixed, with Undo

    async def test_undo_puts_the_heard_words_back_and_keeps_them(self) -> None:
        table, message, memory = await self.fixed_from_a_suggestion()
        (note,) = table.fix_notes.notes
        answer, _ = await self.bot.undo_fix(GUILD, note.id, PLAYER)
        self.assertIn("Only this campaign's DM", answer)
        answer, allow = await self.bot.undo_fix(GUILD, note.id, DM)
        self.assertIn('"Hrothgarr" stays as heard', answer)
        self.assertNotIn("couldn't be put back", answer)  # the waiting line was changed
        self.assertEqual(allow, (table.campaign_id, 3))  # "Allow again" takes it back
        self.assertEqual(memory.add_correction.await_args.kwargs["action"], "keep")
        self.assertEqual(memory.add_correction.await_args.args[2], "Hrothgarr")
        (line,) = list(table.unsaved._waiting)
        self.assertEqual(line.text, "then Hrothgarr roars")  # as heard again
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertIn("↩️ undone", message.edit.await_args.kwargs["content"])
        again, _ = await self.bot.undo_fix(GUILD, note.id, DM)
        self.assertIn("already undone", again)

    async def test_undo_edits_the_channel_message_while_it_is_recent(self) -> None:
        table, _, _ = await self.fixed_from_a_suggestion()
        posted = MagicMock(edit=AsyncMock())

        async def post(channel_id: int, text: str) -> Any:
            return "posted", posted

        self.bot._post_transcript = post  # type: ignore[method-assign]
        await self.bot.flush_transcript(table)
        (note,) = table.fix_notes.notes
        await self.bot.undo_fix(GUILD, note.id, DM)
        self.assertIn("then Hrothgarr roars", posted.edit.await_args.kwargs["content"])

    async def test_someone_who_stops_while_undo_waits_isnt_described(self) -> None:
        # #588: Stop recording me while Undo waits for the save lock: no word about
        # their line (it isn't "still" anything; it's being dropped).
        table, _, memory = await self.fixed_from_a_suggestion()
        (note,) = table.fix_notes.notes
        await table.save_lock.acquire()  # a batch being saved
        undo = asyncio.create_task(self.bot.undo_fix(GUILD, note.id, DM))
        for _ in range(5):
            await asyncio.sleep(0)
        memory.add_correction.assert_awaited_once()  # past the earlier checks…
        self.assertFalse(undo.done())  # …and waiting for the lock
        await self.consent.revoke(GUILD, PLAYER)
        table.save_lock.release()
        answer, _ = await undo
        self.assertNotIn("still says", answer)
        self.assertIn("stopped being recorded", answer)

    async def test_undo_says_when_the_line_couldnt_be_put_back(self) -> None:
        table, _, _ = await self.fixed_from_a_suggestion()
        posted = MagicMock(edit=AsyncMock())

        async def post(channel_id: int, text: str) -> Any:
            return "posted", posted

        self.bot._post_transcript = post  # type: ignore[method-assign]
        await self.bot.flush_transcript(table)
        table.unsaved.take(lambda _: True)  # already saved
        table.transcript_session_id = "s5"
        self.bot.transcripts = MagicMock(
            relabel_line=AsyncMock(side_effect=RuntimeError("database down"))
        )
        (note,) = table.fix_notes.notes
        with self.assertLogs("dmbot.bot", "ERROR"):
            answer, allow = await self.bot.undo_fix(GUILD, note.id, DM)
        self.assertIn("couldn't be put back, so it still says **Hrothgar**", answer)
        posted.edit.assert_not_awaited()  # all or nothing (#567)
        self.assertEqual(allow, (table.campaign_id, 3))

    async def test_someone_who_stops_during_the_undo_isnt_put_back(self) -> None:
        table, _, memory = await self.fixed_from_a_suggestion()
        (note,) = table.fix_notes.notes

        async def stop_meanwhile(*_: Any, **__: Any) -> Any:
            await self.consent.revoke(GUILD, PLAYER)
            return MagicMock(batch=4)

        memory.add_correction.side_effect = stop_meanwhile
        answer, _ = await self.bot.undo_fix(GUILD, note.id, DM)
        self.assertIn("stopped being recorded", answer)

    async def test_no_undo_buttons_after_the_session_ends(self) -> None:
        table, message, _ = await self.fixed_from_a_suggestion()
        await self.bot.stop_table(GUILD, "test")
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertIsNone(message.edit.await_args.kwargs["view"])
        self.assertTrue(table.fix_ended)

    async def test_allow_again_takes_back_the_rule(self) -> None:
        table, _, memory = await self.fixed_from_a_suggestion()
        memory.undo = AsyncMock()
        self.bot.campaigns.get = AsyncMock(  # type: ignore[method-assign]
            return_value=MagicMock(dm_user_ids=frozenset({DM}))
        )
        answer = await self.bot.allow_fix_again(GUILD, table.campaign_id, 3, PLAYER)
        self.assertIn("Only this campaign's DM", answer)
        answer = await self.bot.allow_fix_again(GUILD, table.campaign_id, 3, DM)
        self.assertIn("may fix those words again", answer)
        memory.undo.assert_awaited_once_with(GUILD, table.campaign_id, 3)

    async def test_lower_case_words_count_even_without_the_names(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table, _ = await self.joined_with_transcript()
        table.name_lookup = None  # the names couldn't be loaded
        self.said(table, "careful, a thorn")
        self.assertTrue(table.vocabulary.is_word("Thorn"))  # #295

    async def test_someone_in_voice_who_never_agreed_keeps_their_name(self) -> None:
        import time

        from dmbot.memory.lookup import CampaignLookup, LookupData
        from dmbot.memory.models import CONFIRMED, Alias, Entity

        self.at_the_table(PLAYER, OTHER_PERSON, DM)
        await self.consent.grant(GUILD, PLAYER)
        table, sent = await self.joined_with_transcript()
        other = next(m for m in self.voice.members if m.id == OTHER_PERSON)
        other.display_name = "Marin"  # never agreed, so never recorded, but said aloud
        eid = "c" * 32
        table.name_lookup = CampaignLookup.build(
            LookupData(
                1,
                (Entity(eid, "npc", "Maren", "", CONFIRMED, None, "dm", 0),),
                (Alias(eid, eid, "Maren", "maren", "full", None, False, CONFIRMED, (), "dm", 0),),
                (),
                (),
            )
        )
        table.scene.note([eid], DM, time.monotonic())  # Maren came up a moment ago
        table.people = self.bot._everyone_at_table(table, [])
        self.said(table, "thanks Marin, good call")
        await self.bot.flush_transcript(table)
        self.assertIn("thanks Marin, good call", "\n".join(sent))

    async def test_everyone_at_the_table_is_protected_from_name_fixes(self) -> None:
        self.at_the_table(PLAYER, OTHER_PERSON, DM)
        await self.start()
        table = self.bot.tables[GUILD]
        # Dee never agreed, but her name is said at the table and must never change
        self.assertEqual(set(self.bot._everyone_at_table(table, ["Mia"])), {"Mia", "Dee", "Sam"})
        # the DM out of voice is still found; bots never count
        dm = next(m for m in self.voice.members if m.id == DM)
        robot = member(12345)
        robot.bot, robot.display_name = True, "Beleros"
        self.voice.members = [m for m in self.voice.members if m.id != DM] + [robot]
        self.guild.get_member = lambda uid: dm if uid == DM else None
        self.assertEqual(set(self.bot._everyone_at_table(table, [])), {"Mia", "Dee", "Sam"})

    async def test_a_failed_post_is_tried_again(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table, sent = await self.joined_with_transcript()
        results = iter(["retry", "posted", "posted"])

        async def flaky(channel_id: int, text: str) -> tuple[str, Any]:
            result = next(results)
            if result == "posted":
                sent.append(text)
            return result, None

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
        result = await self.bot._post_transcript(5, "**Mia:** hi")
        self.assertEqual(result, ("posted", channel.send.return_value))  # kept for a late fix
        call = channel.send.await_args
        assert call is not None
        self.assertTrue(call.kwargs["silent"])
        self.assertTrue(call.kwargs["suppress_embeds"])  # still no link previews

    async def test_a_lost_channel_stops_the_transcript_and_tells_the_dm_once(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table, _ = await self.joined_with_transcript()
        gone = AsyncMock(return_value=("gone", None))
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

    async def test_the_log_says_how_much_speech_was_sent(self) -> None:
        # #523: listening time against speech sent, for the prices; numbers only.
        await self.consent.grant(GUILD, PLAYER)
        table, _ = await self.joined_with_transcript()
        release = await self.slow_worker("Words that must stay off the log.")
        self.queue_speech(table)
        release.set()
        table.listening_from = int(time.time()) - 120  # two minutes of listening
        with self.assertLogs("dmbot.bot", "INFO") as logs:
            await self.bot.stop_table(GUILD, "test")
            await asyncio.gather(*self.bot._finishing)
        line = "Listened 2.0 min, sent 0.0 min of speech (1%)"  # one second of speech
        self.assertTrue(any(line in m for m in logs.output), logs.output)
        self.assertNotIn("Words that must", "\n".join(logs.output))
        self.assertNotIn(table.segmenter.session, self.bot.pipeline.sent_s_in)

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
