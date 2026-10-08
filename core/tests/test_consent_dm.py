"""Consent by private message: who is asked, what they see, and what the buttons do."""

import asyncio
import json
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot import consent_dm as c
from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table, consent_give
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import TERMS_VERSION, ConsentStore
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
    assert (  # PLAN.md: the whole server, and it stays after a stop
        "Anyone in this server can read and download that text. "
        "It stays there even if you stop later." in text
    )
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
    assert "To stop being recorded, press ⚙️ Menu below." in text and text.count("\n") == 0
    assert "🛑" not in text
    # Also reaches people who said yes before the whole server could read it (#35).
    assert "Anyone in this server can read the text" in text
    assert c.OUTSIDE_NOTE not in text  # local Whisper keeps voices on the server
    outside = c.reminder_text("Dragon Club", None, 1_760_000_000, cloud=True, company="Deepgram")
    assert "Your voice and Discord name go to Deepgram" in outside
    assert outside.count("\n") == 0 and "in **Dragon Club**" in outside  # no voice: still reads


def test_the_sheet_note_only_when_sheets_are_on() -> None:
    # The menu hides 📜 when sheets are off, so the note mustn't point at it then.
    assert c.SHEET_NOTE in c.reminder_text("D", None, 1, sheets=True)
    assert c.SHEET_NOTE not in c.reminder_text("D", None, 1, sheets=False)
    assert c.SHEET_NOTE in c.confirmed_text("D", 1, sheets=True)
    assert c.SHEET_NOTE not in c.confirmed_text("D", 1, sheets=False)


def test_the_outside_note_names_the_company_when_known() -> None:
    known = c.request_text("Dragon Club", voice=None, dm=None, cloud=True, company="Deepgram")
    assert "this server uses Deepgram to turn speech into text" in known
    assert "Discord name" in known
    unknown = c.request_text("Dragon Club", voice=None, dm=None, cloud=True)
    assert c.CLOUD_NOTE in unknown and "another company" in unknown


def test_a_request_with_the_outside_note_marks_its_consent_button() -> None:
    marked = custom_ids(c.request_view(GUILD, outside="deepgram"))[0]
    assert marked == "dmbot:consent:yes:1:v3:out:deepgram"
    assert custom_ids(c.request_view(GUILD))[0] == "dmbot:consent:yes:1:v3"
    # After Stop or No thanks no note is shown, so that button is never marked.
    assert custom_ids(c.consent_view(GUILD)) == ["dmbot:consent:yes:1:v3"]


def test_unreachable_note_tells_dms_off_from_other_failures() -> None:
    assert c.unreachable_text([], []) is None
    off = c.unreachable_text(["Aria", "Bram"], [])
    assert off is not None and "Not recording: Aria, Bram" in off and "DMs" in off
    busy = c.unreachable_text([], ["Cael"])
    assert busy is not None and "just now" in busy and "turn on DMs" not in busy


def custom_ids(view: discord.ui.View) -> list[str]:
    return [str(getattr(item, "custom_id", "")) for item in view.children]


def test_buttons_carry_the_server() -> None:
    assert custom_ids(c.request_view(GUILD)) == ["dmbot:consent:yes:1:v3", "dmbot:consent:no:1"]
    assert custom_ids(c.menu_view(GUILD)) == ["dmbot:consent:menu:1:-"]
    assert custom_ids(c.menu_view(GUILD, "ab" * 16)) == [f"dmbot:consent:menu:1:{'ab' * 16}"]
    assert custom_ids(c.consent_view(GUILD)) == ["dmbot:consent:yes:1:v3"]
    assert custom_ids(c.warning_view(GUILD)) == [
        "dmbot:consent:stopyes:1",
        "dmbot:consent:keep:1:-",
    ]


def test_the_menu_shows_only_what_applies() -> None:
    def ids(**kw: bool) -> list[str]:
        return custom_ids(c.options_view(GUILD, None, **kw))

    sheet, stop, close = "dmbot:sheet:1:-", "dmbot:consent:stop:1:-", "dmbot:consent:close:1:-"
    assert ids(recording=True, sheets=True) == [sheet, stop, close]
    assert ids(recording=True, sheets=False) == [stop, close]
    start = "dmbot:consent:start:1"  # shows the request: never saves a yes by itself
    assert ids(recording=False, sheets=True) == [sheet, start, close]
    assert ids(recording=False, sheets=False) == [start, close]


def button(item: Any) -> Any:
    """The button itself, for a DynamicItem or a plain one."""
    return getattr(item, "item", item)


def test_stop_in_the_menu_is_grey_and_only_yes_is_red() -> None:
    menu = c.options_view(GUILD, None, recording=True, sheets=True)
    assert discord.ButtonStyle.danger not in [button(i).style for i in menu.children]
    warning = c.warning_view(GUILD).children
    assert [button(i).style for i in warning] == [
        discord.ButtonStyle.danger,
        discord.ButtonStyle.secondary,
    ]
    assert button(c.menu_view(GUILD).children[0]).style == discord.ButtonStyle.secondary


def test_every_label_fits() -> None:
    views = [
        c.menu_view(GUILD),
        c.options_view(GUILD, None, recording=True, sheets=True),
        c.options_view(GUILD, None, recording=False, sheets=True),
        c.warning_view(GUILD),
    ]
    for view in views:
        for item in view.children:
            label = str(button(item).label or "")
            assert 0 < len(label) <= 25, label


def test_the_warning_is_plain_and_names_the_server() -> None:
    text = c.warning_text("**Dragon** @everyone")
    assert text.startswith("Stop recording you in **")
    assert "@everyone" not in text and "**Dragon**" not in text
    assert "plot holes can appear" in text and "You can start again any time." in text
    kept = c.kept_text("Dragon Club")
    assert kept.startswith("OK, DMbot keeps recording you in **Dragon Club**.")
    assert "/consent revoke" in kept  # how to stop later, with no menu left on it
    for text in (c.no_longer_recorded_text("X"), c.nothing_to_stop_text("X")):
        assert "/consent give" in text and "Menu" not in text  # nothing to press there
    assert "⚙️ Menu below" in c.not_recorded_menu_text("X")


def test_no_message_carries_a_stop_directly() -> None:
    for view in (c.menu_view(GUILD), c.menu_view(GUILD, "ab" * 16)):
        assert not [i for i in custom_ids(view) if ":stop" in i]


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
        assert "you said yes on <t:" in self.sent_text(self.player)
        (menu,) = custom_ids(self.player.send.await_args.kwargs["view"])
        assert re.fullmatch(r"dmbot:consent:menu:1:([0-9a-f]{32}|-)", menu)  # this campaign

    async def age_consent(self, user_id: int) -> None:
        """Make a saved yes look as if it was given under older wording (#35)."""
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE consent SET terms_version = %s WHERE guild_id = %s AND user_id = %s",
                (TERMS_VERSION - 1, GUILD, user_id),
            )
        self.bot.consent = self.consent = ConsentStore(self.db)  # fresh cache

    async def test_a_yes_under_older_wording_is_asked_again(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.age_consent(PLAYER)
        assert not await self.consent.consenting(GUILD)  # not recorded meanwhile
        await self.joined()
        text = self.sent_text(self.player)
        assert "Can DMbot record you" in text and c.RENEWED in text
        assert "You said yes" not in text
        assert c.RENEWED not in self.sent_text(self.dm)  # never asked before: no note
        # The DM hears why someone who said yes before isn't recorded now.
        notes = [t for cid, t in self.posts if cid == SCREEN and "Asked again" in t]
        assert len(notes) == 1 and f"user{PLAYER}" in notes[0] and f"user{DM}" not in notes[0]

    async def test_after_a_restart_only_older_yeses_are_asked_again(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.age_consent(PLAYER)
        self.table.resumed = True
        await self.joined()
        assert c.RENEWED in self.sent_text(self.player)
        self.dm.send.assert_not_called()  # asked before the restart; not asked twice
        await self.bot.ask_for_consent(self.table, [self.dm])  # still askable on rejoin
        self.dm.send.assert_awaited_once()

    async def test_saying_yes_again_counts_under_the_new_wording(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.age_consent(PLAYER)
        await c.ConsentButton(GUILD).callback(self.button_press(PLAYER))
        assert PLAYER in await ConsentStore(self.db).consenting(GUILD)
        assert not await self.consent.outdated(GUILD, [PLAYER])

    async def test_how_consent_was_given_is_recorded(self) -> None:
        await c.ConsentButton(GUILD).callback(self.button_press(PLAYER))
        await c.ConsentButton(GUILD).callback(self.button_press(DM, guild=self.guild))
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT user_id, method, terms_version FROM consent ORDER BY user_id"
            )
            rows = {r["user_id"]: (r["method"], r["terms_version"]) for r in await cur.fetchall()}
        assert rows[PLAYER] == ("private_message", TERMS_VERSION)
        assert rows[DM] == ("consent_command", TERMS_VERSION)

    async def test_consent_give_after_older_wording_explains_why(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.age_consent(PLAYER)
        call = self.slash(PLAYER)
        await consent_give.callback(call)  # type: ignore[call-arg]
        text = call.followup.send.await_args.args[0]
        assert c.RENEWED in text and "Can DMbot record you" in text

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

    async def test_after_a_restart_onto_deepgram_earlier_yeses_are_asked_again(self) -> None:
        await self.consent.grant(GUILD, PLAYER)  # current wording, local Whisper
        await self.consent.grant(GUILD, DM, outside_to="deepgram")
        self.use_deepgram()
        self.table.resumed = True  # the switch needs a restart; the session resumes
        await self.joined()
        assert "this server uses Deepgram" in self.sent_text(self.player)
        assert "What's new" not in self.sent_text(self.player)  # not a wording change
        assert self.sent_text(self.player).startswith(c.REASK_INTRO)  # says why
        self.dm.send.assert_not_called()  # their yes already names Deepgram

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
        self.consent.status = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", "ERROR"):
            await self.joined()
        self.player.send.assert_not_called()
        del self.consent.status  # the database is back
        await self.voice_update(self.player, None, self.voice)
        self.player.send.assert_awaited_once()

    def use_deepgram(self) -> None:
        self.bot.settings = Settings(
            discord_token="t",
            ears_secret="s",
            transcription=TranscriptionSettings(engine="deepgram", deepgram_api_key="k"),
        )
        self.consent.outside = "deepgram"  # as DMBot sets it from the same settings

    async def test_deepgram_is_named_in_the_message_and_reminder(self) -> None:
        self.use_deepgram()
        await self.consent.grant(GUILD, PLAYER, outside_to="deepgram")
        await self.joined()
        question = self.sent_text(self.dm)
        assert "this server uses Deepgram" in question
        view = self.dm.send.await_args.kwargs["view"]
        assert custom_ids(view)[0] == "dmbot:consent:yes:1:v3:out:deepgram"
        assert "go to Deepgram" in self.sent_text(self.player)  # the reminder

    async def test_name_hints_leave_out_people_dmbot_cant_look_up(self) -> None:
        await self.consent.grant(GUILD, DM)
        await self.consent.grant(GUILD, PLAYER)  # not in the member cache in this test
        from dmbot.audio.segmenter import Utterance

        said = Utterance(GUILD, DM, 0, 0, b"")
        self.assertEqual(await self.bot._name_hints(said), [f"user{DM}"])

    async def test_a_yes_from_before_the_switch_is_asked_again_and_not_recorded(self) -> None:
        await self.consent.grant(GUILD, PLAYER)  # agreed under local Whisper
        self.use_deepgram()
        assert PLAYER not in await self.consent.consenting(GUILD)
        assert not self.consent.has_consent(GUILD, PLAYER)  # ears and core both skip them
        await self.joined()
        assert "this server uses Deepgram" in self.sent_text(self.player)  # the question

    async def test_an_old_consent_button_after_the_switch_asks_again(self) -> None:
        self.use_deepgram()
        press = self.button_press(PLAYER)
        await c.ConsentButton(GUILD).callback(press)  # from a message without the note
        assert await self.consent.granted_at(GUILD, PLAYER) is None  # nothing saved
        edit = press.edit_original_response.await_args.kwargs
        assert edit["content"].startswith(c.REASK_INTRO)
        assert "this server uses Deepgram" in edit["content"]
        assert custom_ids(edit["view"])[0] == "dmbot:consent:yes:1:v3:out:deepgram"

    async def test_a_marked_consent_button_counts_after_the_switch(self) -> None:
        self.use_deepgram()
        await c.ConsentButton(GUILD, outside="deepgram").callback(self.button_press(PLAYER))
        assert self.consent.has_consent(GUILD, PLAYER)
        assert PLAYER in await self.consent.consenting(GUILD)

    async def test_a_button_marked_for_another_company_asks_again(self) -> None:
        self.use_deepgram()
        press = self.button_press(PLAYER)
        await c.ConsentButton(GUILD, outside="cloud").callback(press)
        assert await self.consent.granted_at(GUILD, PLAYER) is None
        assert press.edit_original_response.await_args.kwargs["content"].startswith(c.REASK_INTRO)

    async def test_a_marked_yes_under_whisper_is_saved_and_an_unmarked_one_downgrades(
        self,
    ) -> None:
        await c.ConsentButton(GUILD, outside="deepgram").callback(self.button_press(PLAYER))
        assert PLAYER in await ConsentStore(self.db, outside="deepgram").consenting(GUILD)
        await c.ConsentButton(GUILD).callback(self.button_press(PLAYER))  # no note shown
        assert PLAYER in await self.consent.consenting(GUILD)  # still counts under Whisper
        assert PLAYER not in await ConsentStore(self.db, outside="deepgram").consenting(GUILD)

    async def test_consent_give_while_outside_names_the_company_and_marks_the_button(
        self,
    ) -> None:
        from dmbot.bot import consent_give

        self.use_deepgram()
        call: Any = SimpleNamespace(
            client=self.bot,
            guild=self.guild,
            user=SimpleNamespace(id=PLAYER),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await consent_give.callback(call)  # type: ignore[call-arg]
        args = call.followup.send.await_args
        assert "this server uses Deepgram" in args.args[0]
        assert custom_ids(args.kwargs["view"])[0] == "dmbot:consent:yes:1:v3:out:deepgram"

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

    def button_press(self, user_id: int, guild: Any = None) -> Any:
        """A button press: in a private message, or (with `guild`) in the server."""
        return SimpleNamespace(
            client=self.bot,
            guild=guild,
            guild_id=None if guild is None else guild.id,
            user=SimpleNamespace(id=user_id),
            response=SimpleNamespace(
                defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
            delete_original_response=AsyncMock(),
            message=None,  # an old message in DMs; a menu sets `menu_message()`
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
        assert custom_ids(edit["view"]) == ["dmbot:consent:menu:1:-"]

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

    async def test_yes_stops_capture_and_offers_to_agree_again(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        press = self.button_press(PLAYER)
        await c.StopYesButton(GUILD).callback(press)
        assert not self.consent.has_consent(GUILD, PLAYER)
        assert PLAYER not in await ConsentStore(self.db).consenting(GUILD)  # saved
        assert str(PLAYER) not in self.allowlists()[-1]
        edit = press.edit_original_response.await_args.kwargs
        assert "Stopped" in edit["content"]
        assert c.ALREADY_RECORDED in edit["content"]  # past lines stay readable
        assert custom_ids(edit["view"]) == ["dmbot:consent:yes:1:v3"]

    async def test_stop_works_before_anything_slow(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        press = self.button_press(PLAYER)

        async def check_already_stopped(*_: Any, **__: Any) -> None:
            assert not self.consent.has_consent(GUILD, PLAYER)

        press.response.defer = AsyncMock(side_effect=check_already_stopped)
        await c.StopYesButton(GUILD).callback(press)
        press.response.defer.assert_awaited_once()

    async def test_a_stop_that_cant_be_saved_still_stops_and_says_so(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        self.consent.revoke = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        press = self.button_press(PLAYER)
        with self.assertLogs("dmbot.consent_dm", "ERROR"):
            await c.StopYesButton(GUILD).callback(press)
        press.followup.send.assert_awaited_once_with(
            c.revoke_not_saved(c.STOP_YES_LABEL), ephemeral=True
        )
        assert not self.consent.has_consent(GUILD, PLAYER)

    def menu_message(self, press: Any) -> Any:
        """On an only-you menu (from the /consent give answer)."""
        press.message = SimpleNamespace(flags=SimpleNamespace(ephemeral=True))
        return press

    REMINDER = "🎙️ DMbot is recording you in **Dragon Club** (you said yes on <t:1:D>)."

    def lasting(self, press: Any, content: str = REMINDER) -> Any:
        """On a private message that stays: the session reminder, "you said yes"."""
        press.message = SimpleNamespace(flags=SimpleNamespace(ephemeral=False), content=content)
        return press

    # ---- on a lasting message, everything happens in place (#807) ----------------

    async def test_on_the_reminder_the_menu_takes_the_buttons_place(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        campaign = "ab" * 16
        press = self.lasting(self.button_press(PLAYER))
        await c.MenuButton(GUILD, campaign).callback(press)
        press.response.send_message.assert_not_called()
        edit = press.response.edit_message.await_args.kwargs
        assert set(edit) == {"view"}  # the reminder's text stays
        assert custom_ids(edit["view"]) == [
            f"dmbot:consent:stop:1:{campaign}",
            f"dmbot:consent:close:1:{campaign}",
        ]

    async def test_on_the_reminder_the_warning_comes_first_in_its_text(self) -> None:
        # Not an embed: people who hide embeds would see Yes with no warning.
        await self.consent.grant(GUILD, PLAYER)
        stop = self.lasting(self.button_press(PLAYER))
        await c.StopButton(GUILD, "ab" * 16).callback(stop)
        edit = stop.response.edit_message.await_args.kwargs
        assert edit["content"] == f"{c.warning_text(self.guild.name)}\n\n{self.REMINDER}"
        assert "embed" not in edit and "embeds" not in edit

    async def test_on_the_reminder_yes_leaves_it_reading_stopped(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        warned = c.with_warning(self.guild.name, self.REMINDER)
        yes = self.lasting(self.button_press(PLAYER), warned)
        await c.StopYesButton(GUILD).callback(yes)
        assert not self.consent.has_consent(GUILD, PLAYER)
        edit = yes.edit_original_response.await_args.kwargs  # the reminder's own message
        assert edit["content"] == c.stopped_text(self.guild.name)
        assert custom_ids(edit["view"]) == ["dmbot:consent:yes:1:v3"]

    async def test_on_the_reminder_keep_and_close_give_it_back_unchanged(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        campaign = "ab" * 16
        menu = [f"dmbot:consent:menu:1:{campaign}"]
        warned = c.with_warning(self.guild.name, self.REMINDER)
        keep = self.lasting(self.button_press(PLAYER), warned)
        await c.KeepButton(GUILD, campaign).callback(keep)
        edit = keep.response.edit_message.await_args.kwargs
        assert edit["content"] == self.REMINDER  # byte for byte
        assert custom_ids(edit["view"]) == menu
        close = self.lasting(self.button_press(PLAYER))
        await c.CloseButton(GUILD, campaign).callback(close)
        edit = close.response.edit_message.await_args.kwargs
        assert edit["content"] == self.REMINDER and custom_ids(edit["view"]) == menu
        close.delete_original_response.assert_not_called()  # never deletes the reminder
        assert self.consent.has_consent(GUILD, PLAYER)

    async def test_on_the_reminder_a_stale_keep_corrects_its_text(self) -> None:
        press = self.lasting(self.button_press(PLAYER))  # they stopped another way
        await c.KeepButton(GUILD).callback(press)
        edit = press.response.edit_message.await_args.kwargs
        assert edit["content"] == c.not_recorded_menu_text(self.guild.name)  # not "recording"
        assert custom_ids(edit["view"]) == ["dmbot:consent:menu:1:-"]

    async def test_stop_recording_me_warns_and_records_nothing(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        press = self.menu_message(self.button_press(PLAYER))
        sent = len(self.ears.sent)
        await c.StopButton(GUILD).callback(press)
        assert self.consent.has_consent(GUILD, PLAYER)  # nothing stopped yet
        assert len(self.ears.sent) == sent
        edit = press.response.edit_message.await_args.kwargs  # the menu becomes the warning
        assert edit["content"] == c.warning_text(self.guild.name)
        assert custom_ids(edit["view"]) == ["dmbot:consent:stopyes:1", "dmbot:consent:keep:1:-"]

    async def test_an_old_red_button_warns_and_never_stops_at_once(self) -> None:
        # A 🛑 on a reminder sent before the menu (#807): its id still works, and warns.
        await self.consent.grant(GUILD, PLAYER)
        match = c.StopButton.__discord_ui_compiled_template__.fullmatch("dmbot:consent:stop:1")
        assert match is not None
        old = await c.StopButton.from_custom_id(MagicMock(), MagicMock(), match)
        before = f"🎙️ DMbot is recording you in **Dragon Club**. {c.OLD_STOP_HINT}"
        press = self.lasting(self.button_press(PLAYER), before)  # on the old reminder
        await old.callback(press)
        assert self.consent.has_consent(GUILD, PLAYER)
        edit = press.response.edit_message.await_args.kwargs
        assert edit["content"].startswith(c.warning_text(self.guild.name))
        assert custom_ids(edit["view"]) == ["dmbot:consent:stopyes:1", "dmbot:consent:keep:1:-"]
        keep = self.lasting(self.button_press(PLAYER), edit["content"])
        await c.KeepButton(GUILD).callback(keep)
        back = keep.response.edit_message.await_args.kwargs["content"]  # the menu's words now
        assert back == f"🎙️ DMbot is recording you in **Dragon Club**. {c.MENU_HINT}"

    async def test_a_slow_database_never_keeps_someone_recorded_from_stop(self) -> None:
        # The Supervisor's case: Discord gives 3 s. The cache answers at once for whoever
        # is captured; an empty cache with a slow or broken database still offers Stop.
        await self.consent.grant(GUILD, PLAYER)

        async def hang(*_: Any, **__: Any) -> Any:
            await asyncio.sleep(60)

        self.consent.status = AsyncMock(side_effect=hang)  # type: ignore[method-assign]
        press = self.button_press(PLAYER)
        await asyncio.wait_for(c.MenuButton(GUILD).callback(press), 1)  # cached: no wait
        assert "dmbot:consent:stop:1:-" in custom_ids(
            press.response.send_message.await_args.kwargs["view"]
        )
        self.consent.stop_now(GUILD, PLAYER)  # as if a fresh process: not in the cache
        with patch("dmbot.bot.RECORDED_CHECK_S", 0.05), self.assertLogs("dmbot.bot", "WARNING"):
            assert await asyncio.wait_for(self.bot.recorded(GUILD, PLAYER), 1)  # unsure: yes
        self.consent.status = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", "WARNING"):
            assert await self.bot.recorded(GUILD, PLAYER)

    async def test_setup_registers_the_consent_buttons(self) -> None:
        # So old 🛑 ids, and every menu button, keep working after a restart.
        added: list[Any] = []
        self.bot.add_dynamic_items = lambda *items: added.extend(items)  # type: ignore[method-assign]
        self.bot.tree.sync = AsyncMock()  # type: ignore[method-assign]
        self.bot.ears.start = AsyncMock(side_effect=asyncio.CancelledError)  # type: ignore[method-assign]
        with (
            patch("dmbot.bot.install.configure"),
            patch("dmbot.bot.install.install_link"),
            self.assertRaises(asyncio.CancelledError),
        ):
            await self.bot.setup_hook()
        assert set(c.CONSENT_BUTTONS) <= set(added)

    async def test_keep_recording_changes_nothing(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        press = self.menu_message(self.button_press(PLAYER))
        sent = len(self.ears.sent)
        await c.KeepButton(GUILD).callback(press)
        assert self.consent.has_consent(GUILD, PLAYER)
        assert len(self.ears.sent) == sent
        press.response.edit_message.assert_awaited_once_with(
            content=c.kept_text(self.guild.name), view=None
        )

    async def test_keep_on_a_stale_warning_never_claims_they_are_recorded(self) -> None:
        press = self.menu_message(self.button_press(PLAYER))  # they stopped another way
        await c.KeepButton(GUILD).callback(press)
        press.response.edit_message.assert_awaited_once_with(
            content=c.no_longer_recorded_text(self.guild.name), view=None
        )
        assert not self.consent.has_consent(GUILD, PLAYER)

    async def test_the_menu_opens_privately_with_what_applies(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        press = self.button_press(PLAYER)
        await c.MenuButton(GUILD).callback(press)
        args = press.response.send_message.await_args
        assert args.args[0] == c.menu_text(self.guild.name) and args.kwargs["ephemeral"]
        assert custom_ids(args.kwargs["view"]) == [
            "dmbot:consent:stop:1:-",
            "dmbot:consent:close:1:-",
        ]
        self.bot.sheets = MagicMock()  # sheets on: 📜 too, for the menu's campaign
        await self.consent.revoke(GUILD, PLAYER)  # and someone who stopped
        campaign = "cd" * 16
        press = self.button_press(PLAYER)
        await c.MenuButton(GUILD, campaign).callback(press)
        assert custom_ids(press.response.send_message.await_args.kwargs["view"]) == [
            f"dmbot:sheet:1:{campaign}",
            "dmbot:consent:start:1",
            f"dmbot:consent:close:1:{campaign}",
        ]

    async def test_i_consent_in_the_menu_shows_the_current_request_first(self) -> None:
        # The reviewer's case: a yes under older wording counts as not recorded, and the
        # menu shows no terms, so its I consent must show them before anything is saved.
        await self.consent.grant(GUILD, PLAYER)
        await self.age_consent(PLAYER)
        self.consent = ConsentStore(self.db)
        self.bot.consent = self.consent
        await self.consent.consenting(GUILD)
        press = self.button_press(PLAYER)
        await c.MenuButton(GUILD).callback(press)
        assert "dmbot:consent:start:1" in custom_ids(
            press.response.send_message.await_args.kwargs["view"]
        )
        press = self.menu_message(self.button_press(PLAYER))
        await c.StartButton(GUILD).callback(press)
        edit = press.response.edit_message.await_args.kwargs
        assert "Can DMbot record you" in edit["content"] and c.AI_NOTE in edit["content"]
        assert custom_ids(edit["view"]) == ["dmbot:consent:yes:1:v3", "dmbot:consent:no:1"]
        assert await self.consent.granted_at(GUILD, PLAYER) is None  # nothing saved yet

    async def test_close_deletes_the_menu(self) -> None:
        press = self.menu_message(self.button_press(PLAYER))
        await c.CloseButton(GUILD).callback(press)
        press.delete_original_response.assert_awaited_once()
        press.delete_original_response.side_effect = discord.HTTPException(
            MagicMock(status=500), "nope"
        )
        await c.CloseButton(GUILD).callback(press)  # couldn't delete: collapsed instead
        press.edit_original_response.assert_awaited_once_with(content=c.MENU_CLOSED, view=None)

    async def test_no_thanks_on_an_old_message_stops_a_later_yes(self) -> None:
        await self.consent.grant(GUILD, PLAYER)  # said yes some other way
        press = self.button_press(PLAYER)
        await c.DeclineButton(GUILD).callback(press)
        assert not self.consent.has_consent(GUILD, PLAYER)
        assert PLAYER not in await ConsentStore(self.db).consenting(GUILD)
        edit = press.edit_original_response.await_args.kwargs
        assert "Stopped" in edit["content"]  # their yes was removed: say so
        assert c.ALREADY_RECORDED in edit["content"]
        assert custom_ids(edit["view"]) == ["dmbot:consent:yes:1:v3"]

    async def test_a_plain_no_thanks_says_nothing_about_past_recordings(self) -> None:
        press = self.button_press(PLAYER)
        await c.DeclineButton(GUILD).callback(press)
        content = press.edit_original_response.await_args.kwargs["content"]
        assert "won't record you" in content
        assert c.ALREADY_RECORDED not in content

    async def test_yes_then_quick_no_leaves_ears_without_them(self) -> None:
        lock = self.consent._lock(GUILD)
        await lock.acquire()  # "I consent" is still saving...
        yes = asyncio.create_task(
            self.bot.give_consent(GUILD, PLAYER, "private_message", outside_to=None)
        )
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
        # (Keep recording changes nothing anywhere, so it isn't here.)
        buttons = (
            c.ConsentButton,
            c.DeclineButton,
            c.StopButton,
            c.StopYesButton,
            c.MenuButton,
            c.StartButton,
        )
        for button in buttons:
            press = self.button_press(PLAYER)
            await button(ELSEWHERE).callback(press)
            press.response.send_message.assert_awaited_once_with(c.NOT_HERE, ephemeral=True)
        assert await self.consent.granted_at(ELSEWHERE, PLAYER) is not None

    async def test_a_button_from_before_versions_shows_the_current_question(self) -> None:
        # An unanswered request sent before #172: its ID has no version, so it's version 1.
        match = c.ConsentButton.__discord_ui_compiled_template__.fullmatch("dmbot:consent:yes:1")
        assert match is not None
        old = await c.ConsentButton.from_custom_id(MagicMock(), MagicMock(), match)
        assert old.version == 1
        press = self.button_press(PLAYER)
        await old.callback(press)
        assert await self.consent.granted_at(GUILD, PLAYER) is None  # nothing saved
        edit = press.edit_original_response.await_args.kwargs
        assert edit["content"].startswith(c.STALE_INTRO)
        assert c.CLOUD_NOTE not in edit["content"]  # local Whisper: no outside note
        assert custom_ids(edit["view"])[0] == "dmbot:consent:yes:1:v3"

    async def test_a_button_from_the_last_version_asks_again_and_saves_nothing(self) -> None:
        # #52: an "I consent" sent under the previous wording (before the AI note).
        old_id = f"dmbot:consent:yes:1:v{TERMS_VERSION - 1}"
        match = c.ConsentButton.__discord_ui_compiled_template__.fullmatch(old_id)
        assert match is not None
        old = await c.ConsentButton.from_custom_id(MagicMock(), MagicMock(), match)
        press = self.button_press(PLAYER)
        await old.callback(press)
        assert await self.consent.granted_at(GUILD, PLAYER) is None  # nothing saved
        edit = press.edit_original_response.await_args.kwargs
        assert edit["content"].startswith(c.STALE_INTRO)
        assert c.AI_NOTE in edit["content"]  # the current wording, with the AI note
        assert custom_ids(edit["view"])[0] == f"dmbot:consent:yes:1:v{TERMS_VERSION}"

    async def test_a_marked_button_id_is_parsed_back_with_its_engine(self) -> None:
        custom_id = str(c.ConsentButton(123, outside="deepgram").custom_id)
        match = c.ConsentButton.__discord_ui_compiled_template__.fullmatch(custom_id)
        assert match is not None
        button = await c.ConsentButton.from_custom_id(MagicMock(), MagicMock(), match)
        assert (button.guild_id, button.outside) == (123, "deepgram")

    async def test_button_ids_are_parsed_back_to_the_server(self) -> None:
        buttons = (
            c.ConsentButton,
            c.DeclineButton,
            c.StopButton,
            c.StopYesButton,
            c.KeepButton,
            c.MenuButton,
            c.StartButton,
        )
        for button in buttons:
            custom_id = str(button(123).custom_id)
            match = button.__discord_ui_compiled_template__.fullmatch(custom_id)
            assert match is not None
            assert (await button.from_custom_id(MagicMock(), MagicMock(), match)).guild_id == 123
            bad = custom_id.replace("123", "abc")
            assert button.__discord_ui_compiled_template__.fullmatch(bad) is None

    async def test_the_menu_and_close_ids_are_parsed_back(self) -> None:
        campaign = "ef" * 16
        template = c.MenuButton.__discord_ui_compiled_template__
        match = template.fullmatch(str(c.MenuButton(123, campaign).custom_id))
        assert match is not None
        menu = await c.MenuButton.from_custom_id(MagicMock(), MagicMock(), match)
        assert (menu.guild_id, menu.campaign_id) == (123, campaign)
        assert template.fullmatch(f"dmbot:consent:menu:123:{'x' * 32}") is None
        for button in (c.CloseButton, c.KeepButton, c.StopButton):
            template = button.__discord_ui_compiled_template__
            match = template.fullmatch(str(button(123, campaign).custom_id))
            assert match is not None
            parsed = await button.from_custom_id(MagicMock(), MagicMock(), match)
            assert (parsed.guild_id, parsed.campaign_id) == (123, campaign)

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
        assert custom_ids(args.kwargs["view"]) == ["dmbot:consent:yes:1:v3", "dmbot:consent:no:1"]
        assert not self.consent.has_consent(GUILD, PLAYER)

    async def test_consent_give_after_a_yes_shows_when_and_how_to_stop(self) -> None:
        await ConsentStore(self.db).grant(GUILD, PLAYER)  # given before a restart
        self.bot.consent = ConsentStore(self.db)  # fresh process: cache not loaded
        call = self.slash(PLAYER)
        await consent_give.callback(call)  # type: ignore[call-arg]
        args = call.followup.send.await_args
        assert "You said yes on <t:" in args.args[0]
        assert custom_ids(args.kwargs["view"]) == ["dmbot:consent:menu:1:-"]
