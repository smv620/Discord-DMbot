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
from dmbot.maps.store import MapStore, Viewer, may_use_map
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
        self.maps = MapStore(self.db, MemoryStore(self.db), clock=lambda: 2000.0)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        self.bot = SimpleNamespace(
            picture_store=self.pictures,
            picture_keeper=PictureKeeper(self.pictures, Bucket(), 5),
            map_store=self.maps,
            campaigns=self.campaigns,
            settings=SimpleNamespace(picture_storage_mb=5),
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
    def shown(it: Any) -> str:
        return str(it.edit_original_response.await_args.kwargs["content"])

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
        self.assertIsNone(it.edit_original_response.await_args.kwargs["view"])

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
            self.assertEqual(it.followup.send.await_args.args[0], words.NOT_YOURS)
        self.assertEqual(await self.kept_maps(), [])
        kept = await self.pictures.by_id(GUILD, pic)
        assert kept is not None
        self.assertFalse(kept.gone)

    async def test_a_second_press_and_a_gone_picture_say_so(self) -> None:
        pic = await self.picture(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(self.press(PLAYER))
        again = self.press(PLAYER)
        await map_question.YesButton(GUILD, pic).callback(again)
        self.assertEqual(self.shown(again), words.ANSWERED)
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
        text = sent.await_args.args[0]
        self.assertIn(words.QUESTION, text)
        self.assertIn("Everyone at the table has seen it", text)
        labels = [c.item.label for c in sent.await_args.kwargs["view"].children]
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
        self.assertEqual(note.await_args.args[2], words.dms_could_not_ask("Mia"))
        self.assertEqual(await self.kept_maps(), [])
