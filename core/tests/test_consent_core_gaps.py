"""Core re-checks consent where audio comes in, and where a stored line is made (#1858).

CLAUDE.md, Hard rules, Consent: "never capture, decode, store, or transcribe audio from a user who
has not opted in. Bots are never captured. This is enforced in ears (allowlist) and re-checked in
core, including after every async step." Coverage showed paths where nothing tested the refusal,
found by `pytest --cov` on the consent paths. No Discord, no database, no network.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.audio.segmenter import _MAX_BYTES, Segmenter
from dmbot.bot import DMBot
from dmbot.config import Settings
from dmbot.ears.protocol import AudioFrame
from dmbot.transcript import suggestions
from dmbot.ui import line_fix

GUILD, AGREED, NOT_AGREED = 1, 101, 202


class Agreed:
    """Who said yes, and can say stop: the consent store's two questions about a person."""

    def __init__(self, *people: int) -> None:
        self.people = set(people)

    def has_consent(self, guild_id: int, user_id: int) -> bool:
        return guild_id == GUILD and user_id in self.people


def bot_with(agreed: Agreed) -> tuple[DMBot, MagicMock]:
    bot = DMBot(
        Settings(discord_token="t", ears_secret="s"),
        cast(Any, MagicMock()),
        cast(Any, MagicMock()),
        cast(Any, MagicMock()),
    )
    bot.consent = cast(Any, agreed)
    queue = MagicMock()
    bot.pipeline = cast(Any, SimpleNamespace(enqueue=queue))
    bot.tables = {GUILD: cast(Any, SimpleNamespace(segmenter=Segmenter(GUILD)))}
    return bot, queue


def frame(user: int, size: int = _MAX_BYTES) -> AudioFrame:
    """A frame long enough to fill an utterance, so one that is let in is queued at once."""
    return AudioFrame(guild_id=GUILD, user_id=user, timestamp_ms=1_000, pcm=bytes(size))


class FramesAreCheckedAtTheDoor(unittest.TestCase):
    def test_a_frame_from_someone_who_never_agreed_is_dropped_before_anything_keeps_it(
        self,
    ) -> None:
        """Hard rule, Consent: ears should never send it, but core checks every frame again
        (defence in depth). The audio is not buffered, queued or transcribed."""
        bot, queue = bot_with(Agreed(AGREED))
        bot._on_audio(frame(NOT_AGREED))
        queue.assert_not_called()
        self.assertEqual(bot.tables[GUILD].segmenter.active_speakers, 0)  # not even buffered

    def test_a_frame_from_someone_who_agreed_goes_through(self) -> None:
        bot, queue = bot_with(Agreed(AGREED))
        bot._on_audio(frame(AGREED))
        queue.assert_called_once()
        self.assertEqual(queue.call_args.args[0].user_id, AGREED)

    def test_the_next_frame_after_a_stop_is_dropped(self) -> None:
        """Hard rule, Consent (🗂️ Menu, Stop recording me): the person agreed, then pressed Stop.
        The very next frame, already on its way from ears, is not kept."""
        agreed = Agreed(AGREED)
        bot, queue = bot_with(agreed)
        bot._on_audio(frame(AGREED, size=1_000))  # a short piece: buffered, not yet an utterance
        self.assertEqual(bot.tables[GUILD].segmenter.active_speakers, 1)
        agreed.people.discard(AGREED)
        bot._on_audio(frame(AGREED))  # would have filled the utterance
        queue.assert_not_called()

    def test_a_frame_for_a_server_with_no_table_is_dropped(self) -> None:
        bot, queue = bot_with(Agreed(AGREED))
        bot.tables.clear()
        bot._on_audio(frame(AGREED))
        queue.assert_not_called()


class AFixIsNotKeptForSomeoneWhoStopped(unittest.IsolatedAsyncioTestCase):
    """The player's own "fix my line" (CLAUDE.md, Hard rules, Consent): the words they send are
    stored for the DM to accept. Consent is checked when they send, and again after the waits
    (the campaign is read from the database in between)."""

    def setUp(self) -> None:
        self.agreed = Agreed(AGREED)
        self.stored = AsyncMock(return_value=SimpleNamespace(id="7"))

        async def campaign(_guild: int, _id: str) -> Any:
            self.agreed.people.discard(AGREED)  # they press Stop while the campaign is read
            return SimpleNamespace(dm_screen_channel_id=555)

        self.bot = SimpleNamespace(
            consent=self.agreed,
            suggestions=SimpleNamespace(suggest=self.stored),
            campaigns=SimpleNamespace(get=campaign),
            post_message=AsyncMock(),
        )
        self.told: list[str] = []

        async def send(text: str, **_: Any) -> None:
            self.told.append(text)

        self.interaction = SimpleNamespace(
            client=self.bot,
            user=SimpleNamespace(id=AGREED, display_name="Alice"),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=send),
        )
        self.line = suggestions.Own(started_ms=1_000, heard="hello", text="Hello", user_id=AGREED)

    async def test_a_stop_during_the_wait_keeps_nothing_and_tells_the_player(self) -> None:
        await line_fix.submit(
            cast(Any, self.interaction), GUILD, "c", "5", self.line, "Hello there"
        )
        self.stored.assert_not_called()  # the words were never stored
        self.bot.post_message.assert_not_called()  # and nothing was put on the DM screen
        self.assertEqual(self.told, [line_fix.NOT_RECORDED])

    async def test_without_a_stop_the_same_words_are_kept(self) -> None:
        async def campaign(_guild: int, _id: str) -> Any:
            return SimpleNamespace(dm_screen_channel_id=555)

        self.bot.campaigns = SimpleNamespace(get=campaign)
        await line_fix.submit(
            cast(Any, self.interaction), GUILD, "c", "5", self.line, "Hello there"
        )
        self.stored.assert_called_once()


class CombatSaidAloud(unittest.IsolatedAsyncioTestCase):
    """`_combat_phrase_changes`: "next round" said by the DM moves the fight only while the DM is
    still recorded and the session is still running, after the campaign look-up (an await)."""

    DM = AGREED

    def setup_bot(self, agreed: Agreed) -> tuple[Any, Any, Any]:
        table = SimpleNamespace(guild_id=GUILD, campaign_id="c" * 32, listening=True)
        campaign = SimpleNamespace(
            guild_id=GUILD, id="c" * 32, dm_user_ids=frozenset({self.DM}), name="Alpha"
        )
        fake = SimpleNamespace(
            tables={GUILD: table},
            consent=agreed,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            _split_holds_phrase=AsyncMock(return_value=False),
        )
        return fake, table, campaign

    async def changes(self, fake: Any, table: Any) -> tuple[bool, AsyncMock]:
        from dmbot.timebot import combat_phrases

        press = AsyncMock(side_effect=RuntimeError("stop here: the combat state was reached"))
        with patch("dmbot.bot.clock_screen.press", press):
            changed = await DMBot._combat_phrase_changes(
                fake, table, self.DM, "next round", combat_phrases.CombatSaid("next"), 0.0
            )
        return changed, press

    async def test_a_dm_who_stopped_does_not_move_the_fight(self) -> None:
        fake, table, _ = self.setup_bot(Agreed())  # no one agreed (or the DM pressed Stop)
        changed, press = await self.changes(fake, table)
        self.assertFalse(changed)
        press.assert_not_awaited()

    async def test_a_session_that_ended_does_not_move_the_fight(self) -> None:
        fake, table, _ = self.setup_bot(Agreed(self.DM))
        fake.tables = {}
        changed, press = await self.changes(fake, table)
        self.assertFalse(changed)
        press.assert_not_awaited()

    async def test_a_paused_session_does_not_move_the_fight(self) -> None:
        fake, table, _ = self.setup_bot(Agreed(self.DM))
        table.listening = False
        changed, press = await self.changes(fake, table)
        self.assertFalse(changed)
        press.assert_not_awaited()

    async def test_the_same_phrase_from_a_recorded_dm_reaches_the_clock(self) -> None:
        fake, table, _ = self.setup_bot(Agreed(self.DM))
        with self.assertLogs("dmbot.bot", "ERROR"):
            changed, press = await self.changes(fake, table)
        self.assertFalse(changed)  # (the stand-in press fails: the point is that it was reached)
        press.assert_awaited_once()
