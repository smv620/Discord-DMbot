"""The right-to-use question for a map (#4344, slice 3b-1): only the picture's poster may answer;
a yes keeps it as a map (a DM's also usable, a player's waiting for a DM); a no takes the picture
back; a second press, a gone picture and a closed private chat each say so in words. The real
stores on a test database; Discord is a stand-in. Invented ids. Needs Postgres."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.campaigns import CampaignStore
from dmbot.maps import words
from dmbot.maps.store import ALREADY, MapStore, Viewer, may_use_map
from dmbot.memory.store import MemoryStore
from dmbot.pictures.keeper import PictureKeeper
from dmbot.pictures.store import MB, PictureStore
from dmbot.ui import map_question
from tests.pg import DatabaseTest

GUILD = 1
DM, PLAYER, STRANGER = 7, 8, 9


class Bucket:
    async def put(self, key: str, data: bytes) -> None: ...
    async def delete(self, key: str) -> None: ...


class Asking(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.memory = MemoryStore(self.db)
        self.maps = MapStore(self.db, self.memory, clock=lambda: 2000.0)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        self.consenting = True
        self.bot = SimpleNamespace(
            picture_store=self.pictures,
            picture_keeper=PictureKeeper(self.pictures, Bucket(), 5),
            map_store=self.maps,
            memory=self.memory,
            campaigns=self.campaigns,
            settings=SimpleNamespace(picture_storage_mb=5),
            consent=SimpleNamespace(has_image_consent=lambda guild, user: self.consenting),
        )

    async def picture(self, poster: int, n: int = 1, message_id: int = 11) -> int:
        added = await self.pictures.add(
            GUILD, self.campaign.id, poster_id=poster, session_id="", line_id=None,
            message_id=message_id, description="", consent_version=7, right_granted_at=900,
            sha256=f"{n:064x}", object_key=f"p/{n}.png", preview_key="", size_bytes=MB,
            preview_bytes=0, storage_mb=5,
        )  # fmt: skip
        assert added.picture is not None
        return added.picture.id

    def press(self, user: int) -> Any:
        return SimpleNamespace(
            user=SimpleNamespace(id=user),
            client=self.bot,
            type=discord.InteractionType.component,
            response=SimpleNamespace(is_done=lambda: True, defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    @staticmethod
    def call(mock: Any) -> Any:
        """The last call of an awaited mock, so the checks below have no `None` to doubt."""
        last = mock.await_args
        assert last is not None
        return last

    def shown(self, it: Any) -> str:
        return str(self.call(it.edit_original_response).kwargs["content"])

    async def kept_maps(self) -> list[Any]:
        return await self.maps.maps(GUILD, self.campaign.id, Viewer(dm=True))

    async def test_a_players_yes_keeps_the_map_and_waits_for_a_dm(self) -> None:
        pic = await self.picture(PLAYER)
        it = self.press(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(it)
        [kept] = await self.kept_maps()
        self.assertEqual((kept.right_by, kept.waiting, kept.audience), (PLAYER, True, "table"))
        self.assertFalse(may_use_map(kept))
        self.assertEqual(self.shown(it), words.said_yes(False, True))
        [button] = self.call(it.edit_original_response).kwargs["view"].children
        self.assertTrue(button.item.custom_id.startswith("dmbot:mapqt:"))  # (Take it back)

    async def test_a_dms_yes_keeps_it_and_lets_dmbot_use_it(self) -> None:
        pic = await self.picture(DM)
        it = self.press(DM)
        await map_question.YesButton(GUILD, pic).callback(it)
        [kept] = await self.kept_maps()
        self.assertTrue(may_use_map(kept))
        self.assertEqual(self.shown(it), words.said_yes(True, True))

    async def test_a_no_takes_the_picture_back(self) -> None:
        pic = await self.picture(PLAYER)
        it = self.press(PLAYER)
        await map_question.NoButton(GUILD, pic).callback(it)
        self.assertEqual(await self.kept_maps(), [])
        gone = await self.pictures.by_id(GUILD, pic)
        assert gone is not None
        self.assertTrue(gone.gone)
        self.assertEqual(self.shown(it), words.said_no(True))

    async def test_only_the_poster_may_answer(self) -> None:
        pic = await self.picture(PLAYER)
        for user in (STRANGER, DM):  # (not even a DM: it is the poster's yes)
            it = self.press(user)
            await map_question.YesButton(GUILD, pic).callback(it)
            it.followup.send.assert_awaited_once()
            self.assertEqual(self.call(it.followup.send).args[0], words.NOT_YOURS)
        self.assertEqual(await self.kept_maps(), [])
        kept = await self.pictures.by_id(GUILD, pic)
        assert kept is not None
        self.assertFalse(kept.gone)

    async def test_a_second_press_and_a_gone_picture_say_so(self) -> None:
        pic = await self.picture(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(self.press(PLAYER))
        again = self.press(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(again)
        self.assertEqual(self.shown(again), ALREADY)  # (the store's own words)
        self.assertEqual(len(await self.kept_maps()), 1)
        nothing = self.press(PLAYER)
        await map_question.YesButton(GUILD, 999).callback(nothing)
        self.assertEqual(self.shown(nothing), words.GONE)

    async def test_the_question_goes_to_the_poster_in_private_with_two_buttons(self) -> None:
        pic = await self.picture(PLAYER)
        picture = await self.pictures.by_id(GUILD, pic)
        sent = AsyncMock()
        self.bot.fetch_user = AsyncMock(return_value=SimpleNamespace(send=sent))
        await map_question.ask(self.bot, GUILD, self.campaign, picture)
        text = self.call(sent).args[0]
        self.assertIn(words.QUESTION, text)
        self.assertIn("Everyone at the table has seen it", text)
        labels = [c.item.label for c in self.call(sent).kwargs["view"].children]
        self.assertEqual(labels, [words.YES_LABEL, words.NO_LABEL])

    async def test_closed_private_messages_tell_the_dms_and_use_no_map(self) -> None:
        pic = await self.picture(PLAYER)
        picture = await self.pictures.by_id(GUILD, pic)
        closed = SimpleNamespace(
            display_name="Mia",
            send=AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "closed")),
        )
        self.bot.fetch_user = AsyncMock(return_value=closed)
        with patch("dmbot.dm_screen.private_note.send", new=AsyncMock()) as note:
            await map_question.ask(self.bot, GUILD, self.campaign, picture)
        self.assertEqual(self.call(note).args[2], words.dms_could_not_ask("Mia"))
        self.assertEqual(await self.kept_maps(), [])

    async def test_a_poster_who_stopped_since_the_question_cannot_keep_it_as_a_map(self) -> None:
        pic = await self.picture(PLAYER)
        self.consenting = False  # (they pressed Stop recording me, or withdrew their yes)
        it = self.press(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(it)
        self.assertEqual(self.shown(it), words.NO_CONSENT)
        self.assertEqual(await self.kept_maps(), [])

    async def test_a_no_after_a_yes_leaves_the_map_the_yes_made(self) -> None:
        pic = await self.picture(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(self.press(PLAYER))
        late = self.press(PLAYER)  # (both buttons were still on the card)
        await map_question.NoButton(GUILD, pic).callback(late)
        self.assertEqual(self.shown(late), words.ANSWERED)
        self.assertEqual(len(await self.kept_maps()), 1)
        kept = await self.pictures.by_id(GUILD, pic)
        assert kept is not None
        self.assertFalse(kept.gone)

    async def test_the_poster_takes_it_back_and_only_the_map_goes(self) -> None:
        pic = await self.picture(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(self.press(PLAYER))
        back = self.press(PLAYER)
        await map_question.TakeBackButton(GUILD, pic).callback(back)
        self.assertEqual(await self.kept_maps(), [])
        kept = await self.pictures.by_id(GUILD, pic)
        assert kept is not None
        self.assertFalse(kept.gone)
        self.assertEqual(self.shown(back), words.TOOK_BACK)
        self.assertEqual(
            len(self.call(back.edit_original_response).kwargs["view"].children), 1
        )  # (the Undo)

    async def test_only_the_poster_may_take_it_back(self) -> None:
        pic = await self.picture(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(self.press(PLAYER))
        for user in (STRANGER, DM):
            it = self.press(user)
            await map_question.TakeBackButton(GUILD, pic).callback(it)
            self.assertEqual(self.call(it.followup.send).args[0], words.NOT_YOURS)
        self.assertEqual(len(await self.kept_maps()), 1)

    async def undo_press(self, user: int, batch: int) -> Any:
        it = self.press(user)
        await map_question.PosterUndoButton(GUILD, self.campaign.id, batch).callback(it)
        return it

    @staticmethod
    def batch_in(it: Any) -> int:
        view = it.edit_original_response.await_args.kwargs["view"]
        return int(view.children[0].item.custom_id.rsplit(":", 1)[1])

    async def test_the_posters_undo_brings_the_map_back_and_is_theirs_alone(self) -> None:
        pic = await self.picture(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(self.press(PLAYER))
        back = self.press(PLAYER)
        await map_question.TakeBackButton(GUILD, pic).callback(back)
        batch = self.batch_in(back)
        other = await self.undo_press(STRANGER, batch)  # (a forwarded card)
        self.assertEqual(self.call(other.followup.send).args[0], words.NOT_YOUR_CHANGE)
        self.assertEqual(await self.kept_maps(), [])
        mine = await self.undo_press(PLAYER, batch)
        self.assertEqual(self.shown(mine), words.UNDONE)
        self.assertEqual(len(await self.kept_maps()), 1)
        made_up = await self.undo_press(PLAYER, 999_999)
        self.assertEqual(self.call(made_up.followup.send).args[0], words.NOT_YOUR_CHANGE)

    async def test_a_dms_removal_is_not_the_posters_to_undo(self) -> None:
        pic = await self.picture(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(self.press(PLAYER))
        [kept] = await self.kept_maps()
        gone = await self.maps.remove_map(GUILD, self.campaign.id, kept.id)  # (a DM's Remove)
        assert gone.batch is not None
        it = await self.undo_press(PLAYER, gone.batch)  # (their own map, but not their change)
        self.assertEqual(self.call(it.followup.send).args[0], words.NOT_YOUR_CHANGE)
        self.assertEqual(await self.kept_maps(), [])

    async def test_asking_marks_the_picture_and_the_reminder_goes_once(self) -> None:
        pic = await self.picture(PLAYER)
        picture = await self.pictures.by_id(GUILD, pic)
        sent = AsyncMock()
        self.bot.fetch_user = AsyncMock(return_value=SimpleNamespace(send=sent))
        await map_question.ask(self.bot, GUILD, self.campaign, picture)
        self.assertEqual(
            len(await self.pictures.unanswered_map_questions(GUILD, self.campaign.id)), 1
        )
        sent.reset_mock()
        await map_question.remind(self.bot, GUILD, self.campaign)
        self.assertIn(words.REMINDER, self.call(sent).args[0])
        sent.reset_mock()
        await map_question.remind(self.bot, GUILD, self.campaign)  # (never again)
        sent.assert_not_awaited()

    async def test_no_reminder_for_an_answered_picture_or_a_poster_who_stopped(self) -> None:
        answered, stopped = await self.picture(PLAYER, 1), await self.picture(PLAYER, 2, 12)
        for pic in (answered, stopped):
            await self.pictures.mark_map_asked(GUILD, self.campaign.id, pic)
        await map_question.YesButton(GUILD, answered).callback(self.press(PLAYER))
        sent = AsyncMock()
        self.bot.fetch_user = AsyncMock(return_value=SimpleNamespace(send=sent))
        self.consenting = False  # (they stopped recording pictures)
        await map_question.remind(self.bot, GUILD, self.campaign)
        sent.assert_not_awaited()
        self.consenting = True
        await map_question.remind(self.bot, GUILD, self.campaign)
        sent.assert_not_awaited()  # (marked reminded the first time: not nagged later)
