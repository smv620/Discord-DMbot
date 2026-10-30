"""The party rule after a live session and after an import (#3036): the step runs after the stored
transcript, only people who still agree are linked, and nothing runs without memory, a campaign or
a stored session. Invented ids only; the rule itself is tested in test_party.py."""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.bot import DMBot
from dmbot.memory.party import PartyLinked

GUILD, CAMPAIGN, SESSION = 111, "c" * 32, "s" * 32


def fake_bot(*, memory: bool = True) -> Any:
    # The real link methods run on a stand-in bot: their collaborators are mocks.
    bot = AsyncMock()
    bot.memory = AsyncMock() if memory else None
    if memory:
        bot.memory.link_party = AsyncMock(return_value=PartyLinked(2, 1, 2, True))
    bot.consent.consenting = AsyncMock(return_value=frozenset({1, 3}))
    bot.transcripts = SimpleNamespace(db=MagicMock())
    bot._link_the_party = partial(DMBot._link_the_party, bot)
    return bot


class TheLiveStep(IsolatedAsyncioTestCase):
    async def test_the_party_runs_after_the_stored_transcript(self) -> None:
        # The real step list, every step a stand-in that records its name.
        bot = AsyncMock()
        table = MagicMock()
        table.transcript_channel_id = None
        await DMBot._after_session(bot, table, 0, True)
        called = [c[0] for c in bot.mock_calls if c[0] in ("finish_transcript", "link_the_party")]
        self.assertEqual(called, ["finish_transcript", "link_the_party"])

    async def test_only_speakers_who_still_agree_are_linked(self) -> None:
        bot = fake_bot()
        table: Any = SimpleNamespace(
            guild_id=GUILD, campaign_id=CAMPAIGN, transcript_session_id=SESSION
        )
        with patch("dmbot.bot.session_speakers", AsyncMock(return_value={1, 2, 3})):
            await DMBot.link_the_party(bot, table)

        bot.memory.link_party.assert_awaited_once_with(GUILD, CAMPAIGN, {1, 3})

    async def test_nothing_runs_without_memory(self) -> None:
        bot = fake_bot(memory=False)
        table: Any = SimpleNamespace(
            guild_id=GUILD, campaign_id=CAMPAIGN, transcript_session_id=SESSION
        )
        await DMBot.link_the_party(bot, table)

    async def test_nothing_runs_without_a_campaign_or_a_stored_session(self) -> None:
        for campaign_id, session_id in ((None, SESSION), (CAMPAIGN, None)):
            with self.subTest(campaign=campaign_id, session=session_id):
                bot = fake_bot()
                table: Any = SimpleNamespace(
                    guild_id=GUILD, campaign_id=campaign_id, transcript_session_id=session_id
                )
                await DMBot.link_the_party(bot, table)
                bot.memory.link_party.assert_not_awaited()


class TheImportStep(IsolatedAsyncioTestCase):
    async def test_a_failed_link_after_the_scan_is_logged_and_nothing_else_changes(self) -> None:
        # A link that raises must not reach the scan's failure path (which would count the
        # session as unchecked and drop its new names).
        bot = fake_bot()
        bot.memory.link_party = AsyncMock(side_effect=RuntimeError("the campaign row is gone"))
        ready: Any = SimpleNamespace(guild_id=GUILD, campaign_id=CAMPAIGN, session_id=SESSION)
        with patch("dmbot.bot.session_speakers", AsyncMock(return_value={1, 3})):
            await DMBot._link_import_party(bot, GUILD, ready)  # must not raise

    async def test_an_import_with_no_memory_links_nothing(self) -> None:
        bot = fake_bot(memory=False)
        ready: Any = SimpleNamespace(guild_id=GUILD, campaign_id=CAMPAIGN, session_id=SESSION)
        await DMBot._link_import_party(bot, GUILD, ready)  # no memory: nothing to do
