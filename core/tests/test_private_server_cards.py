"""The "Did they mean…?" and "say it this way" cards work in a private chat (#4630).

A private message has no server, so these two cards (which read `interaction.guild`) returned
quietly and the DM's press did nothing. They now take the campaign's server from the campaign
(`ui/private_server.guild_id_for`), and every press still checks the DM. Stand-in bots; no
database.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.ui import heard_review, said_as_review
from dmbot.ui.private_server import guild_id_for

GUILD = 4242
CAMPAIGN = "c" * 32
DM = 7


def interaction(*, in_server: bool = False, user: int = DM, guild_of: Any = GUILD) -> Any:
    it = MagicMock(spec=discord.Interaction)
    it.guild = SimpleNamespace(id=GUILD) if in_server else None
    it.user = SimpleNamespace(id=user)
    it.client = SimpleNamespace(
        campaigns=SimpleNamespace(guild_of=AsyncMock(return_value=guild_of))
    )
    return it


class TheServerOfACampaign(unittest.IsolatedAsyncioTestCase):
    async def test_a_press_in_a_server_uses_that_server_and_asks_nobody(self) -> None:
        it = interaction(in_server=True)
        self.assertEqual(await guild_id_for(it, CAMPAIGN), GUILD)
        it.client.campaigns.guild_of.assert_not_awaited()

    async def test_a_private_press_asks_the_campaign(self) -> None:
        it = interaction()
        self.assertEqual(await guild_id_for(it, CAMPAIGN), GUILD)
        it.client.campaigns.guild_of.assert_awaited_once_with(CAMPAIGN)

    async def test_a_campaign_that_is_gone_has_no_server(self) -> None:
        self.assertIsNone(await guild_id_for(interaction(guild_of=None), CAMPAIGN))


class HeardReview(unittest.IsolatedAsyncioTestCase):
    def bot(self, *, dms: tuple[int, ...] = (DM,)) -> Any:
        campaign = SimpleNamespace(dm_user_ids=dms)
        store = SimpleNamespace(waiting=AsyncMock(return_value=[]))
        return SimpleNamespace(
            campaigns=SimpleNamespace(
                guild_of=AsyncMock(return_value=GUILD), get=AsyncMock(return_value=campaign)
            ),
            heard_questions=store,
            lookup=SimpleNamespace(get=AsyncMock()),
            consent=SimpleNamespace(has_consent=lambda *_: True),
        )

    async def test_a_dm_in_a_private_chat_reaches_the_stores_question_list(self) -> None:
        bot = self.bot()
        it = interaction()
        it.client = bot
        view = heard_review.HeardReview(CAMPAIGN)
        self.assertIsNone(await view.current_text(it))  # (none waiting: the list was read)
        bot.heard_questions.waiting.assert_awaited_once_with(GUILD, CAMPAIGN)
        bot.campaigns.get.assert_awaited_once_with(GUILD, CAMPAIGN)

    async def test_someone_who_is_not_the_dm_reads_nothing(self) -> None:
        bot = self.bot(dms=(99,))
        it = interaction()
        it.client = bot
        self.assertIsNone(await heard_review.HeardReview(CAMPAIGN).current_text(it))
        bot.heard_questions.waiting.assert_not_awaited()

    async def test_an_answer_in_a_private_chat_is_saved_for_the_campaigns_server(self) -> None:
        bot = self.bot()
        bot.answer_waiting_question = AsyncMock(return_value=("Saved.", False))
        it = interaction()
        it.client = bot
        view = heard_review.HeardReview(CAMPAIGN)
        view.shown = SimpleNamespace(id=5)  # type: ignore[assignment]
        with patch.object(heard_review, "_tell", AsyncMock()):
            await view.answer(it, "1")
        bot.answer_waiting_question.assert_awaited_once_with(GUILD, 5, "1", DM, None)


class SaidAsReview(unittest.IsolatedAsyncioTestCase):
    async def test_a_dm_in_a_private_chat_reaches_the_offer_list(self) -> None:
        memory = SimpleNamespace(said_as_suggestions=AsyncMock(return_value=[]))
        it = interaction()
        with patch.object(said_as_review, "_memory", MagicMock(return_value=memory)):
            self.assertIsNone(await said_as_review.SaidAsReview(CAMPAIGN).current_text(it))
        memory.said_as_suggestions.assert_awaited_once_with(GUILD, CAMPAIGN)

    async def test_a_save_in_a_private_chat_goes_to_the_campaigns_server_for_a_dm_only(
        self,
    ) -> None:
        memory = SimpleNamespace(answer_said_as=AsyncMock())
        suggestion = SimpleNamespace(entity_id="e1", name="Zed")
        view = said_as_review.SaidAsReview(CAMPAIGN)
        for user, saved in ((DM, 1), (99, 0)):
            it = interaction(user=user)
            memory.answer_said_as.reset_mock()
            campaign = SimpleNamespace(guild_id=GUILD, dm_user_ids=(DM,))
            with (
                patch.object(said_as_review, "_memory", MagicMock(return_value=memory)),
                patch.object(said_as_review, "_campaign_for", AsyncMock(return_value=campaign)),
                patch.object(said_as_review, "sees_secrets", lambda c, u: u in c.dm_user_ids),
                patch.object(said_as_review, "_spoken_recaps", AsyncMock(return_value=True)),
                patch.object(said_as_review, "_tell", AsyncMock()),
                patch.object(said_as_review, "changed", MagicMock()),
                patch.object(view, "_next", AsyncMock()),
            ):
                await view._save(it, suggestion, "zed", "Saved.")  # type: ignore[arg-type]
            self.assertEqual(memory.answer_said_as.await_count, saved, user)
            if saved:
                memory.answer_said_as.assert_awaited_once_with(GUILD, CAMPAIGN, "e1", "zed")
