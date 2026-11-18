"""Maps inside an uploaded document (#4259, MapBot layers slice 5; Supervisor's ruling on #4259,
2026-11-18): a DM's own writing carries no right-to-use yes for a map, so each map picture the read
found asks the map question once, privately, at most 10 a file. A yes makes a DM-only map that
points at the upload's notice (`from_notice`); "↩ Undo this upload" removes it through
`remove_map` (§7.5); the notice's own clean-up never deletes it; and it is never shown to the table
while its picture is the DMs' alone. Real stores on a test database, Discord a stand-in, invented
ids. Needs Postgres."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import CampaignStore
from dmbot.maps import words
from dmbot.maps.store import DMS_FILE, NOT_KEPT, MapError, MapStore, Viewer, may_use_map
from dmbot.memory.store import MemoryStore
from dmbot.pictures.keeper import PictureKeeper
from dmbot.pictures.store import MB, PictureStore
from dmbot.ui import map_question, name_background
from dmbot.ui.upload_notice import _take_back_pictures
from dmbot.upload_notices import UploadNotices
from tests import test_map_question as mq
from tests.pg import DatabaseTest
from tests.test_map_question import DM, GUILD, Bucket


class _AFile(DatabaseTest):
    """The map question's own setup (`test_map_question.Asking`), without running its tests."""

    def press(self, user: int) -> Any:
        return mq.Asking.press(self, user)  # type: ignore[arg-type]

    @staticmethod
    def call(it: Any) -> Any:
        return mq.Asking.call(it)

    def shown(self, it: Any) -> str:
        return str(self.call(it.edit_original_response).kwargs["content"])

    async def kept_maps(self) -> list[Any]:
        return list(await self.maps.maps(GUILD, self.campaign.id, Viewer(dm=True)))

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.memory = MemoryStore(self.db)
        self.maps = MapStore(self.db, self.memory, clock=lambda: 2000.0)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        self.bot = SimpleNamespace(
            picture_store=self.pictures,
            picture_keeper=PictureKeeper(self.pictures, Bucket(), 5),
            map_store=self.maps,
            memory=self.memory,
            campaigns=self.campaigns,
            settings=SimpleNamespace(picture_storage_mb=5),
            # (a DM's file needs no yes to the table's pictures: theirs is "no" here)
            consent=SimpleNamespace(has_image_consent=lambda guild, user: False),
        )
        self.notices = UploadNotices(self.db)
        self.notice = await self.notices.add(GUILD, self.campaign.id, "atlas.pdf", {}, [])

    async def from_file(self, n: int = 1, *, tied: bool = True) -> int:
        added = await self.pictures.add(
            GUILD, self.campaign.id, poster_id=DM, session_id="", line_id=None, message_id=0,
            description="A map.", consent_version=None, right_granted_at=None,
            sha256=f"{n:064x}", object_key=f"p/{n}.png", preview_key="", size_bytes=MB,
            preview_bytes=0, storage_mb=50, dm_only=True, make_room=False,
        )  # fmt: skip
        assert added.picture is not None
        if tied:
            await self.pictures.tag_notice(
                GUILD, self.campaign.id, [added.picture.id], self.notice.id
            )
        return added.picture.id

    async def from_notice(self, map_id: str) -> str | None:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT from_notice FROM campaign_maps WHERE id = %s", (map_id,)
            )
            row = await cur.fetchone()
        assert row is not None
        return None if row["from_notice"] is None else str(row["from_notice"])

    async def yes(self, picture_id: int) -> Any:
        it = self.press(DM)
        await map_question.YesButton(GUILD, picture_id).callback(it)
        return it


class FromAFile(_AFile):
    async def test_the_question_says_only_the_dms_see_it(self) -> None:
        picture = await self.pictures.by_id(GUILD, await self.from_file())
        sent = AsyncMock()
        self.bot.fetch_user = AsyncMock(return_value=SimpleNamespace(send=sent))
        await map_question.ask(self.bot, GUILD, self.campaign, picture)
        text = self.call(sent).args[0]
        self.assertIn(words.QUESTION, text)
        self.assertIn(words.SEEN_BY_DMS, text)

    async def test_a_yes_makes_a_dm_only_map_of_the_document_tied_to_its_upload(self) -> None:
        it = await self.yes(await self.from_file())
        [kept] = await self.kept_maps()
        self.assertEqual(
            (kept.audience, kept.came_from, kept.right_by, kept.right_at),
            ("dm", "document", DM, 2000),  # (the DM's own yes, now: their writing asked none)
        )
        self.assertTrue(may_use_map(kept))
        self.assertEqual(self.shown(it), words.said_yes(True, False))
        self.assertEqual(await self.from_notice(kept.id), self.notice.id)
        self.assertEqual(
            await self.maps.for_notice(GUILD, self.campaign.id, self.notice.id), [kept.id]
        )

    async def test_it_is_never_shown_to_the_table(self) -> None:
        await self.yes(await self.from_file())
        [kept] = await self.kept_maps()
        with self.assertRaises(MapError) as said:
            await self.maps.set_audience(GUILD, self.campaign.id, kept.id, "table")
        self.assertEqual(str(said.exception), DMS_FILE)

    async def test_the_uploads_undo_removes_its_maps_and_pictures_and_nothing_else(self) -> None:
        mine = await self.from_file(1)
        await self.yes(mine)
        other_notice = await self.notices.add(GUILD, self.campaign.id, "other.pdf", {}, [])
        other = await self.from_file(2, tied=False)
        await self.pictures.tag_notice(GUILD, self.campaign.id, [other], other_notice.id)
        await self.yes(other)
        await _take_back_pictures(self.bot, GUILD, self.campaign.id, self.notice.id)
        left = await self.kept_maps()
        self.assertEqual([m.picture_id for m in left], [other])
        gone = await self.pictures.get(GUILD, self.campaign.id, mine)
        assert gone is not None
        self.assertTrue(gone.gone)

    async def test_the_notices_clean_up_never_deletes_a_kept_map(self) -> None:
        await self.yes(await self.from_file())
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "DELETE FROM upload_notices WHERE guild_id = %s AND id = %s",
                (GUILD, self.notice.id),
            )
        [kept] = await self.kept_maps()
        self.assertIsNone(await self.from_notice(kept.id))

    async def test_add_map_takes_an_earlier_right_to_use_time(self) -> None:
        pid = await self.from_file()
        got = await self.maps.add_map(
            GUILD, self.campaign.id, picture_id=pid, uploader_id=DM, uploader_is_dm=True,
            right_to_use=True, kind="other", came_from="document", storage_mb=5,
            from_notice=self.notice.id, right_at=1500,
        )  # fmt: skip
        self.assertEqual(got.value.right_at, 1500)


class TheEdges(_AFile):
    async def test_a_no_takes_the_files_picture_back_and_keeps_no_map(self) -> None:
        pid = await self.from_file()
        it = self.press(DM)
        await map_question.NoButton(GUILD, pid).callback(it)
        self.assertEqual(await self.kept_maps(), [])
        gone = await self.pictures.get(GUILD, self.campaign.id, pid)
        assert gone is not None
        self.assertTrue(gone.gone)
        self.assertEqual(self.shown(it), words.NO_FILE)

    async def test_a_yes_after_the_notice_was_cleaned_up_keeps_an_untied_map(self) -> None:
        pid = await self.from_file()
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "DELETE FROM upload_notices WHERE guild_id = %s AND id = %s",
                (GUILD, self.notice.id),
            )
        await self.yes(pid)
        [kept] = await self.kept_maps()
        self.assertIsNone(await self.from_notice(kept.id))

    async def test_a_yes_after_the_upload_was_undone_keeps_nothing(self) -> None:
        pid = await self.from_file()
        await self.notices.claim_undo(GUILD, self.notice.id)
        it = await self.yes(pid)
        self.assertEqual(await self.kept_maps(), [])
        self.assertEqual(self.shown(it), NOT_KEPT)


class AskingAboutAFilesMaps(_AFile):
    def asking_bot(self, *, layers: bool = True) -> Any:
        return SimpleNamespace(
            picture_store=self.pictures, map_store=self.maps,
            settings=SimpleNamespace(map_layers_on=layers),
        )  # fmt: skip

    async def test_each_map_is_asked_once_up_to_ten_a_file_by_its_page(self) -> None:
        maps = [(await self.from_file(n), n + 1) for n in range(1, 13)]
        with mock.patch.object(map_question, "ask", AsyncMock(return_value=True)) as ask:
            asked, over, unsent = await name_background._ask_about_maps(
                self.asking_bot(), self.campaign, maps, "atlas.pdf"
            )
        self.assertEqual((asked, over, unsent), (10, 2, False))
        calls = ask.await_args_list
        self.assertEqual([c.args[3].id for c in calls], [m[0] for m in maps[:10]])
        self.assertEqual(calls[0].args[4], "🗺️ From **atlas.pdf**, page 2.")

    async def test_with_the_map_layers_off_none_is_asked(self) -> None:
        maps = [(await self.from_file(), 2)]
        with mock.patch.object(map_question, "ask", AsyncMock(return_value=True)) as ask:
            got = await name_background._ask_about_maps(
                self.asking_bot(layers=False), self.campaign, maps, "atlas.pdf"
            )
        self.assertEqual(got, (0, 0, False))
        ask.assert_not_awaited()

    async def test_closed_private_messages_stop_it_after_one_note_to_the_dms(self) -> None:
        maps = [(await self.from_file(n), n) for n in (1, 2, 3)]
        bot = self.asking_bot()
        bot.fetch_user = AsyncMock(
            return_value=SimpleNamespace(
                display_name="Ann",
                send=AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "closed")),
            )
        )
        with mock.patch("dmbot.dm_screen.private_note.send", AsyncMock()) as note:
            got = await name_background._ask_about_maps(bot, self.campaign, maps, "atlas.pdf")
        self.assertEqual(got, (0, 0, True))
        self.assertEqual(note.await_count, 1)  # (the DMs are told once, never ten times)

    def test_the_line_says_what_was_asked_and_what_was_not(self) -> None:
        line = name_background.maps_asked_line
        self.assertEqual(line(0), "")
        self.assertEqual(
            line(1), "\n🗺️ DMbot asked you about 1 map in it, in your private chat with DMbot."
        )
        self.assertEqual(
            line(10, 2),
            "\n🗺️ DMbot asked you about 10 maps in it, one question each, in your private chat"
            " with DMbot. 2 more maps weren't asked about (DMbot asks about 10 a file). They stay"
            " under Pictures, for the DMs only.",
        )
        self.assertIn("1 more map wasn't asked about", line(10, 1))
        unsent = line(0, 2, True)
        self.assertIn("couldn't send you its map questions", unsent)
        self.assertIn("2 maps weren't asked about", unsent)  # (no "more" when none was asked)
