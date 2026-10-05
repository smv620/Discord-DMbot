"""Consent by private message: who is asked, what they see, and what the buttons do."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot import consent_dm as c
from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.ears.protocol import Status
from dmbot.sessions import SessionStore
from tests.pg import DatabaseTest

GUILD, VOICE, SCREEN = 1, 2, 3
DM, PLAYER, LATECOMER, OTHER_BOT = 7, 8, 9, 10
FORBIDDEN = discord.Forbidden(MagicMock(status=403), "Cannot send messages to this user")


class FakeEars:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


def test_request_says_what_happens_and_that_the_answer_is_remembered() -> None:
    text = c.request_text("Dragon Club", "#table", cloud=False)
    assert "#table" in text and "Dragon Club" in text
    assert "records what you say" in text
    assert "ignores your voice" in text
    assert "only need to answer once" in text
    assert "outside speech-to-text" not in text
    assert "outside speech-to-text" in c.request_text("Dragon Club", "#table", cloud=True)


def test_reminder_shows_when_they_agreed_and_how_to_stop() -> None:
    text = c.reminder_text("Dragon Club", "#table", 1_760_000_000)
    assert "<t:1760000000:f>" in text  # each reader sees their own time zone
    assert c.STOP_LABEL in text and "/consent revoke" in text


def test_unreachable_note_names_people_and_says_what_to_do() -> None:
    text = c.unreachable_text(["Aria", "Bram"])
    assert "Aria, Bram" in text and "/consent give" in text


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
        self.guild = guild
        self.voice = MagicMock(spec=discord.VoiceChannel)
        self.voice.id = VOICE
        self.voice.name = "table"
        self.dm, self.player = self.member(DM), self.member(PLAYER)
        self.voice.members = [self.dm, self.player, self.member(OTHER_BOT, bot=True)]
        channels = {VOICE: self.voice}
        self.bot.get_channel = channels.get  # type: ignore[method-assign]
        self.bot.get_guild = lambda gid: guild if gid == GUILD else None  # type: ignore[method-assign]
        self.table = Table(GUILD, VOICE, SCREEN, dm_user_id=DM, segmenter=Segmenter(GUILD))
        self.bot.tables[GUILD] = self.table

    def member(self, user_id: int, *, bot: bool = False) -> Any:
        m = MagicMock(spec=discord.Member)
        m.id = user_id
        m.bot = bot
        m.display_name = f"user{user_id}"
        m.guild = self.guild
        m.send = AsyncMock()
        return m

    async def joined(self) -> None:
        await self.bot._on_status(self.table, Status("joined", guild_id=GUILD))

    @staticmethod
    def sent_text(member: Any) -> str:
        member.send.assert_awaited_once()
        return str(member.send.await_args.args[0])

    async def test_everyone_in_voice_is_asked_when_the_session_starts(self) -> None:
        await self.joined()
        assert "records what you say" in self.sent_text(self.dm)  # the DM too
        assert "records what you say" in self.sent_text(self.player)
        bot_member = self.voice.members[2]
        bot_member.send.assert_not_called()  # never bots

    async def test_someone_who_already_agreed_gets_a_reminder(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.joined()
        text = self.sent_text(self.player)
        assert "You agreed to be recorded on <t:" in text
        view = self.player.send.await_args.kwargs["view"]
        assert custom_ids(view) == ["dmbot:consent:stop:1"]

    async def test_people_are_asked_once_per_session(self) -> None:
        await self.joined()
        self.table.listening = False  # the voice connection dropped and came back
        await self.joined()
        self.player.send.assert_awaited_once()

    async def test_nobody_is_asked_again_after_a_restart(self) -> None:
        self.table.resumed = True
        await self.joined()
        self.player.send.assert_not_called()

    async def voice_update(self, member: Any, before: Any, after: Any) -> None:
        old: Any = SimpleNamespace(channel=before)
        new: Any = SimpleNamespace(channel=after)
        await self.bot.on_voice_state_update(member, old, new)

    async def test_someone_joining_later_is_asked_once(self) -> None:
        await self.joined()
        late = self.member(LATECOMER)
        await self.voice_update(late, None, self.voice)
        assert "records what you say" in self.sent_text(late)
        await self.voice_update(late, self.voice, None)  # dropped out
        await self.voice_update(late, None, self.voice)  # and back
        late.send.assert_awaited_once()

    async def test_joining_another_channel_or_muting_asks_nobody(self) -> None:
        late = self.member(LATECOMER)
        other = SimpleNamespace(id=99)
        await self.voice_update(late, None, other)
        await self.voice_update(late, self.voice, self.voice)  # muted
        late.send.assert_not_called()

    async def test_nobody_is_asked_without_a_session(self) -> None:
        del self.bot.tables[GUILD]
        late = self.member(LATECOMER)
        await self.voice_update(late, None, self.voice)
        late.send.assert_not_called()

    async def test_the_dm_hears_about_people_with_private_messages_off(self) -> None:
        self.player.send.side_effect = FORBIDDEN
        await self.joined()
        notes = [t for cid, t in self.posts if cid == SCREEN and "📭" in t]
        assert len(notes) == 1 and f"user{PLAYER}" in notes[0]
        assert f"user{DM}" not in notes[0]

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
        assert "You agreed on <t:" in edit["content"]
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
        press.followup.send.assert_awaited_once_with(c.GRANT_FAILED)
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

    async def test_no_thanks_records_nothing_and_keeps_the_consent_button(self) -> None:
        press = self.button_press(PLAYER)
        await c.DeclineButton(GUILD).callback(press)
        assert not self.consent.has_consent(GUILD, PLAYER)
        edit = press.response.edit_message.await_args.kwargs
        assert "ignore your voice" in edit["content"]
        assert custom_ids(edit["view"]) == ["dmbot:consent:yes:1"]

    async def test_consent_for_a_server_dmbot_left_changes_nothing(self) -> None:
        press = self.button_press(PLAYER)
        await c.ConsentButton(12345).callback(press)
        press.response.send_message.assert_awaited_once_with(c.SERVER_GONE)
        assert not self.consent.has_consent(12345, PLAYER)
