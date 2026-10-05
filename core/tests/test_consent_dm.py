"""Consent by private message: who is asked, what they see, and what the buttons do."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot import consent_dm as c
from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table, consent_give
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.ears.protocol import Status
from dmbot.sessions import SessionStore
from dmbot.transcription.config import TranscriptionSettings
from tests.pg import DatabaseTest

GUILD, VOICE, SCREEN, ELSEWHERE = 1, 2, 3, 12345
DM, PLAYER, LATECOMER, OTHER_BOT = 7, 8, 9, 10
FORBIDDEN = discord.Forbidden(MagicMock(status=403), "Cannot send messages to this user")
TOO_FAST = discord.HTTPException(MagicMock(status=400), "Opening DMs too fast")


class FakeEars:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


def test_request_asks_first_and_says_dmbot_never_decides() -> None:
    text = c.request_text("Dragon Club", voice="table", dm="Sam", cloud=False)
    assert text.startswith("🎙️ **Can DMbot record you for your D&D game on Dragon Club?**")
    assert "**Sam** turned on DMbot in the **table** voice channel" in text
    assert "never decides anything" in text
    assert "ignores your voice" in text and "still play as normal" in text
    assert "If you say no, DMbot asks again next session" in text  # no false promise
    assert c.CLOUD_NOTE not in text
    assert c.CLOUD_NOTE in c.request_text("Dragon Club", voice=None, dm=None, cloud=True)


def test_request_without_a_session_leaves_out_who_and_where() -> None:
    text = c.request_text("Dragon Club", voice=None, dm=None, cloud=False)
    assert "turned on DMbot" not in text


def test_names_from_discord_cannot_fake_formatting_or_ping() -> None:
    text = c.request_text("**Free** @everyone", voice="t", dm="_x_", cloud=False)
    assert "**Free**" not in text and "@everyone" not in text


def test_reminder_is_short_and_shows_the_date() -> None:
    text = c.reminder_text("Dragon Club", "table", 1_760_000_000)
    assert "<t:1760000000:D>" in text  # each reader sees their own time zone
    assert "🛑" in text and text.count("\n") == 0


def test_unreachable_note_tells_dms_off_from_other_failures() -> None:
    assert c.unreachable_text([], []) is None
    off = c.unreachable_text(["Aria", "Bram"], [])
    assert off is not None and "Not recording: Aria, Bram" in off and "DMs" in off
    busy = c.unreachable_text([], ["Cael"])
    assert busy is not None and "just now" in busy and "turn on DMs" not in busy


def custom_ids(view: discord.ui.View) -> list[str]:
    return [str(getattr(item, "custom_id", "")) for item in view.children]


def test_buttons_carry_the_server() -> None:
    assert custom_ids(c.request_view(GUILD)) == ["dmbot:consent:yes:1", "dmbot:consent:no:1"]
    assert custom_ids(c.stop_view(GUILD)) == ["dmbot:consent:stop:1"]
    assert custom_ids(c.consent_view(GUILD)) == ["dmbot:consent:yes:1"]


class ConsentDMTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.consent = ConsentStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            self.consent,
            CampaignStore(self.db),
            SessionStore(self.db),
        )
        self.ears = FakeEars()
        self.bot.ears._active = self.ears  # type: ignore[assignment]
        self.posts: list[tuple[int, str]] = []

        async def fake_post(channel_id: int, text: str, view: object = None) -> bool:
            self.posts.append((channel_id, text))
            return True

        self.bot.post = fake_post  # type: ignore[method-assign]

        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.name = "Dragon Club"
        guild.fetch_member = AsyncMock()  # everyone pressing buttons is a member
        self.guild = guild
        self.voice = MagicMock(spec=discord.VoiceChannel)
        self.voice.id = VOICE
        self.voice.name = "table"
        self.dm, self.player = self.member(DM), self.member(PLAYER)
        self.voice.members = [self.dm, self.player, self.member(OTHER_BOT, bot=True)]
        guild.get_member = {DM: self.dm}.get
        channels = {VOICE: self.voice}
        self.bot.get_channel = channels.get  # type: ignore[method-assign]
        self.bot.get_guild = lambda gid: guild if gid == GUILD else None  # type: ignore[method-assign]
        self.table = Table(GUILD, VOICE, SCREEN, dm_user_id=DM, segmenter=Segmenter(GUILD))
        self.bot.tables[GUILD] = self.table
        await self.consent.consenting(GUILD)  # loaded when a session starts

    def member(self, user_id: int, *, bot: bool = False) -> Any:
        m = MagicMock(spec=discord.Member)
        m.id = user_id
        m.bot = bot
        m.display_name = f"user{user_id}"
        m.guild = self.guild
        m.send = AsyncMock()
        return m

    async def settle(self) -> None:
        """Let background private-message rounds finish."""
        await asyncio.gather(*self.bot._asking)

    async def joined(self) -> None:
        await self.bot._on_status(self.table, Status("joined", guild_id=GUILD))
        await self.settle()

    @staticmethod
    def sent_text(member: Any) -> str:
        member.send.assert_awaited_once()
        return str(member.send.await_args.args[0])

    # ---- who is asked, and when -------------------------------------------

    async def test_everyone_in_voice_is_asked_when_the_session_starts(self) -> None:
        await self.joined()
        assert "Can DMbot record you" in self.sent_text(self.dm)  # the DM too
        text = self.sent_text(self.player)
        assert "Can DMbot record you" in text and f"**user{DM}** turned on DMbot" in text
        self.voice.members[2].send.assert_not_called()  # never bots

    async def test_asking_does_not_hold_up_the_voice_link(self) -> None:
        release = asyncio.Event()

        async def slow_send(*_: Any, **__: Any) -> None:
            await release.wait()

        self.player.send.side_effect = slow_send
        await asyncio.wait_for(self.bot._on_status(self.table, Status("joined", guild_id=GUILD)), 1)
        assert self.bot._asking  # still sending, but the status handler returned
        release.set()
        await self.settle()

    async def test_someone_who_already_agreed_gets_a_reminder(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.joined()
        assert "You said yes on <t:" in self.sent_text(self.player)
        assert custom_ids(self.player.send.await_args.kwargs["view"]) == ["dmbot:consent:stop:1"]

    async def test_people_are_asked_once_per_session(self) -> None:
        await self.joined()
        self.table.listening = False  # the voice connection dropped and came back
        await self.joined()
        self.player.send.assert_awaited_once()

    async def test_a_new_session_asks_again(self) -> None:
        await self.joined()
        self.table = Table(GUILD, VOICE, SCREEN, dm_user_id=DM, segmenter=Segmenter(GUILD))
        self.bot.tables[GUILD] = self.table
        await self.joined()
        assert self.player.send.await_count == 2

    async def test_nobody_is_asked_again_after_a_restart(self) -> None:
        self.table.resumed = True
        await self.joined()
        self.player.send.assert_not_called()

    async def test_a_session_stopped_partway_sends_nothing_more(self) -> None:
        async def stop_session(*_: Any, **__: Any) -> None:
            await self.bot.stop_table(GUILD, "test")

        self.dm.send.side_effect = stop_session
        self.player.send.side_effect = FORBIDDEN
        await self.joined()
        self.player.send.assert_not_called()
        assert not [t for cid, t in self.posts if cid == SCREEN and "📭" in t]

    async def voice_update(self, member: Any, before: Any, after: Any) -> None:
        old: Any = SimpleNamespace(channel=before)
        new: Any = SimpleNamespace(channel=after)
        await self.bot.on_voice_state_update(member, old, new)
        await self.settle()

    async def test_someone_joining_later_is_asked_once(self) -> None:
        await self.joined()
        late = self.member(LATECOMER)
        await self.voice_update(late, None, self.voice)
        assert "Can DMbot record you" in self.sent_text(late)
        await self.voice_update(late, self.voice, None)  # dropped out
        await self.voice_update(late, None, self.voice)  # and back
        late.send.assert_awaited_once()

    async def test_moving_in_from_another_channel_counts_as_joining(self) -> None:
        late = self.member(LATECOMER)
        await self.voice_update(late, SimpleNamespace(id=99), self.voice)
        late.send.assert_awaited_once()

    async def test_joining_another_channel_or_muting_asks_nobody(self) -> None:
        late = self.member(LATECOMER)
        await self.voice_update(late, None, SimpleNamespace(id=99))
        await self.voice_update(late, self.voice, self.voice)  # muted
        late.send.assert_not_called()

    async def test_nobody_is_asked_without_a_session(self) -> None:
        del self.bot.tables[GUILD]
        late = self.member(LATECOMER)
        await self.voice_update(late, None, self.voice)
        late.send.assert_not_called()

    async def test_a_revoke_during_the_round_gets_the_question_not_a_reminder(self) -> None:
        await self.consent.grant(GUILD, PLAYER)

        async def revoke_player(*_: Any, **__: Any) -> None:
            self.bot.stop_recording(GUILD, PLAYER)

        self.dm.send.side_effect = revoke_player  # the DM goes first
        await self.joined()
        assert "Can DMbot record you" in self.sent_text(self.player)

    async def test_a_failed_lookup_asks_them_when_they_rejoin(self) -> None:
        self.consent.granted_times = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", "ERROR"):
            await self.joined()
        self.player.send.assert_not_called()
        del self.consent.granted_times  # the database is back
        await self.voice_update(self.player, None, self.voice)
        self.player.send.assert_awaited_once()

    async def test_cloud_transcription_is_mentioned_in_the_message(self) -> None:
        self.bot.settings = Settings(
            discord_token="t",
            ears_secret="s",
            transcription=TranscriptionSettings(engine="cloud"),
        )
        await self.joined()
        assert c.CLOUD_NOTE in self.sent_text(self.player)

    # ---- people DMbot can't reach -------------------------------------------

    def screen_notes(self) -> list[str]:
        return [t for cid, t in self.posts if cid == SCREEN and "📭" in t]

    async def test_the_dm_hears_about_people_with_private_messages_off(self) -> None:
        self.player.send.side_effect = FORBIDDEN
        await self.joined()
        notes = self.screen_notes()
        assert len(notes) == 1 and f"Not recording: user{PLAYER}" in notes[0]
        assert f"user{DM}" not in notes[0]

    async def test_no_note_when_everyone_was_reached(self) -> None:
        await self.joined()
        assert self.screen_notes() == []

    async def test_someone_unreachable_is_asked_again_when_they_rejoin(self) -> None:
        self.player.send.side_effect = [FORBIDDEN, None]  # then they turned DMs on
        await self.joined()
        await self.voice_update(self.player, None, self.voice)
        assert self.player.send.await_count == 2

    async def test_a_discord_hiccup_is_not_blamed_on_their_settings(self) -> None:
        self.player.send.side_effect = TOO_FAST
        with self.assertLogs("dmbot.consent_dm", "WARNING"):
            await self.joined()
        (note,) = self.screen_notes()
        assert "just now" in note and "turn on DMs" not in note

    # ---- buttons -------------------------------------------------------------

    def button_press(self, user_id: int) -> Any:
        return SimpleNamespace(
            client=self.bot,
            user=SimpleNamespace(id=user_id),
            response=SimpleNamespace(
                defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    def allowlists(self) -> list[list[str]]:
        return [json.loads(m)["userIds"] for m in self.ears.sent if '"allowlist"' in m]

    async def test_consent_button_saves_and_starts_capture_at_once(self) -> None:
        press = self.button_press(PLAYER)
        await c.ConsentButton(GUILD).callback(press)
        assert self.consent.has_consent(GUILD, PLAYER)
        assert str(PLAYER) in self.allowlists()[-1]
        edit = press.edit_original_response.await_args.kwargs
        assert "You said yes on <t:" in edit["content"]
        assert custom_ids(edit["view"]) == ["dmbot:consent:stop:1"]

    async def test_consent_sticks_for_the_next_session(self) -> None:
        await c.ConsentButton(GUILD).callback(self.button_press(PLAYER))
        restarted = ConsentStore(self.db)
        assert PLAYER in await restarted.consenting(GUILD)
        assert await restarted.granted_at(GUILD, PLAYER) is not None

    async def test_a_failed_save_says_they_are_not_recorded(self) -> None:
        self.consent.grant = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        press = self.button_press(PLAYER)
        with self.assertLogs("dmbot.consent_dm", "ERROR"):
            await c.ConsentButton(GUILD).callback(press)
        press.followup.send.assert_awaited_once_with(c.GRANT_FAILED, ephemeral=True)
        assert not self.consent.has_consent(GUILD, PLAYER)

    async def test_a_saved_consent_is_confirmed_even_if_the_date_cant_be_read(self) -> None:
        self.consent.granted_at = AsyncMock(side_effect=RuntimeError("db blip"))  # type: ignore[method-assign]
        press = self.button_press(PLAYER)
        await c.ConsentButton(GUILD).callback(press)
        press.followup.send.assert_not_called()  # no "not recording you"
        assert "You said yes" in press.edit_original_response.await_args.kwargs["content"]

    async def test_someone_no_longer_in_the_server_cant_consent(self) -> None:
        self.guild.fetch_member = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Member")
        )
        press = self.button_press(PLAYER)
        await c.ConsentButton(GUILD).callback(press)
        press.followup.send.assert_awaited_once_with(c.NOT_A_MEMBER, ephemeral=True)
        assert not self.consent.has_consent(GUILD, PLAYER)

    async def test_stop_button_stops_capture_and_offers_to_agree_again(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        press = self.button_press(PLAYER)
        await c.StopButton(GUILD).callback(press)
        assert not self.consent.has_consent(GUILD, PLAYER)
        assert PLAYER not in await ConsentStore(self.db).consenting(GUILD)  # saved
        assert str(PLAYER) not in self.allowlists()[-1]
        edit = press.edit_original_response.await_args.kwargs
        assert "Stopped" in edit["content"]
        assert custom_ids(edit["view"]) == ["dmbot:consent:yes:1"]

    async def test_stop_works_before_anything_slow(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        press = self.button_press(PLAYER)

        async def check_already_stopped(*_: Any, **__: Any) -> None:
            assert not self.consent.has_consent(GUILD, PLAYER)

        press.response.defer = AsyncMock(side_effect=check_already_stopped)
        await c.StopButton(GUILD).callback(press)
        press.response.defer.assert_awaited_once()

    async def test_a_stop_that_cant_be_saved_still_stops_and_says_so(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        self.consent.revoke = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        press = self.button_press(PLAYER)
        with self.assertLogs("dmbot.consent_dm", "ERROR"):
            await c.StopButton(GUILD).callback(press)
        press.followup.send.assert_awaited_once_with(c.REVOKE_NOT_SAVED, ephemeral=True)
        assert not self.consent.has_consent(GUILD, PLAYER)

    async def test_no_thanks_on_an_old_message_stops_a_later_yes(self) -> None:
        await self.consent.grant(GUILD, PLAYER)  # said yes some other way
        press = self.button_press(PLAYER)
        await c.DeclineButton(GUILD).callback(press)
        assert not self.consent.has_consent(GUILD, PLAYER)
        assert PLAYER not in await ConsentStore(self.db).consenting(GUILD)
        edit = press.edit_original_response.await_args.kwargs
        assert "won't record you" in edit["content"]
        assert custom_ids(edit["view"]) == ["dmbot:consent:yes:1"]

    async def test_yes_then_quick_no_leaves_ears_without_them(self) -> None:
        lock = self.consent._lock(GUILD)
        await lock.acquire()  # "I consent" is still saving...
        yes = asyncio.create_task(self.bot.give_consent(GUILD, PLAYER))
        await asyncio.sleep(0)
        no = asyncio.create_task(self.bot.withdraw_consent(GUILD, PLAYER))  # ...No thanks
        await asyncio.sleep(0)
        lock.release()
        await asyncio.gather(yes, no)
        assert not self.consent.has_consent(GUILD, PLAYER)
        assert str(PLAYER) not in self.allowlists()[-1]  # ears' latest list

    async def test_voice_join_rounds_stop_when_dmbot_closes(self) -> None:
        self.bot._closing = True
        late = self.member(LATECOMER)
        await self.voice_update(late, None, self.voice)
        late.send.assert_not_called()

    async def test_buttons_for_a_server_not_served_here_change_nothing(self) -> None:
        # DMbot left it, or another process serves it: never claim success.
        await self.consent.grant(ELSEWHERE, PLAYER)
        for button in (c.ConsentButton, c.DeclineButton, c.StopButton):
            press = self.button_press(PLAYER)
            await button(ELSEWHERE).callback(press)
            press.response.send_message.assert_awaited_once_with(c.NOT_HERE, ephemeral=True)
        assert await self.consent.granted_at(ELSEWHERE, PLAYER) is not None

    async def test_button_ids_are_parsed_back_to_the_server(self) -> None:
        for button in (c.ConsentButton, c.DeclineButton, c.StopButton):
            custom_id = str(button(123).custom_id)
            match = button.__discord_ui_compiled_template__.fullmatch(custom_id)
            assert match is not None
            assert (await button.from_custom_id(MagicMock(), MagicMock(), match)).guild_id == 123
            bad = custom_id.replace("123", "abc")
            assert button.__discord_ui_compiled_template__.fullmatch(bad) is None

    # ---- /consent give --------------------------------------------------------

    def slash(self, user_id: int) -> Any:
        return SimpleNamespace(
            client=self.bot,
            guild=self.guild,
            user=SimpleNamespace(id=user_id),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_consent_give_shows_the_same_request_and_saves_nothing_yet(self) -> None:
        call = self.slash(PLAYER)
        await consent_give.callback(call)  # type: ignore[call-arg]
        args = call.followup.send.await_args
        assert "Can DMbot record you" in args.args[0]
        assert custom_ids(args.kwargs["view"]) == ["dmbot:consent:yes:1", "dmbot:consent:no:1"]
        assert not self.consent.has_consent(GUILD, PLAYER)

    async def test_consent_give_after_a_yes_shows_when_and_how_to_stop(self) -> None:
        await ConsentStore(self.db).grant(GUILD, PLAYER)  # given before a restart
        self.bot.consent = ConsentStore(self.db)  # fresh process: cache not loaded
        call = self.slash(PLAYER)
        await consent_give.callback(call)  # type: ignore[call-arg]
        args = call.followup.send.await_args
        assert "You said yes on <t:" in args.args[0]
        assert custom_ids(args.kwargs["view"]) == ["dmbot:consent:stop:1"]
