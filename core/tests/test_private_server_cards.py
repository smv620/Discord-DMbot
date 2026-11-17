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

    async def test_an_answer_for_a_campaign_that_is_gone_says_it_failed_and_saves_nothing(
        self,
    ) -> None:
        bot = self.bot()
        bot.answer_waiting_question = AsyncMock()
        it = interaction(guild_of=None)
        it.client = bot
        bot.campaigns.guild_of = AsyncMock(return_value=None)
        view = heard_review.HeardReview(CAMPAIGN)
        view.shown = SimpleNamespace(id=5)  # type: ignore[assignment]
        tell = AsyncMock()
        with patch.object(heard_review, "_tell", tell):
            await view.answer(it, "1")
        bot.answer_waiting_question.assert_not_awaited()
        tell.assert_awaited_once()


class SaidAsReview(unittest.IsolatedAsyncioTestCase):
    async def test_a_press_for_a_campaign_that_is_gone_reads_nothing(self) -> None:
        memory = SimpleNamespace(said_as_suggestions=AsyncMock(return_value=[]))
        it = interaction(guild_of=None)
        with patch.object(said_as_review, "_memory", MagicMock(return_value=memory)):
            self.assertIsNone(await said_as_review.SaidAsReview(CAMPAIGN).current_text(it))
        memory.said_as_suggestions.assert_not_awaited()

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


class TheNameButtonsInAPrivateChat(unittest.IsolatedAsyncioTestCase):
    """(#3874 E1) the name-check Undo and "Check new names" are sent privately now: pressed with
    no server they find the campaign's own server, and still check the DM on every press"""

    def bot(self, *, dms: tuple[int, ...] = (DM,)) -> Any:
        campaign = SimpleNamespace(id=CAMPAIGN, dm_user_ids=dms)
        return SimpleNamespace(
            campaigns=SimpleNamespace(
                guild_of=AsyncMock(return_value=GUILD), get=AsyncMock(return_value=campaign)
            ),
            transcripts=object(),
            memory=object(),
        )

    async def test_the_name_check_undo_works_with_no_server_for_a_dm(self) -> None:
        from dmbot.ui import name_pass_undo

        it = interaction()
        it.client = self.bot()
        it.message = None
        with (
            patch.object(name_pass_undo, "undo_fix", AsyncMock(return_value=2)) as undo,
            patch.object(name_pass_undo, "_tell", AsyncMock()) as tell,
        ):
            await name_pass_undo.UndoNamePassButton(CAMPAIGN, 5).callback(it)
        self.assertEqual(undo.await_args_list[-1].args[2], GUILD)  # (the campaign's own server)
        self.assertNotEqual(tell.await_args_list[-1].args[1], name_pass_undo.GONE)

    async def test_the_name_check_undo_still_refuses_someone_who_is_not_a_dm(self) -> None:
        from dmbot.ui import name_pass_undo

        it = interaction(user=99)
        it.client = self.bot()
        with (
            patch.object(name_pass_undo, "undo_fix", AsyncMock()) as undo,
            patch.object(name_pass_undo, "_tell", AsyncMock()) as tell,
        ):
            await name_pass_undo.UndoNamePassButton(CAMPAIGN, 5).callback(it)
        undo.assert_not_awaited()
        self.assertEqual(tell.await_args_list[-1].args[1], name_pass_undo.NOT_YOURS)

    async def test_check_new_names_opens_for_a_dm_with_no_server(self) -> None:
        from dmbot.ui import names

        it = interaction()
        it.client = self.bot()
        with patch.object(names, "start_review", AsyncMock()) as start:
            await names.ReviewButton(CAMPAIGN).callback(it)
        start.assert_awaited_once()

    async def test_check_new_names_still_refuses_someone_who_is_not_a_dm(self) -> None:
        from dmbot.ui import names

        it = interaction(user=99)
        it.client = self.bot()
        with (
            patch.object(names, "start_review", AsyncMock()) as start,
            patch.object(names, "_tell", AsyncMock()) as tell,
        ):
            await names.ReviewButton(CAMPAIGN).callback(it)
        start.assert_not_awaited()
        tell.assert_awaited_once()
