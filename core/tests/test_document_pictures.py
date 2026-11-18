"""The pictures of a DM's uploaded file, kept for the campaign's DMs alone (#4003 slice c;
Supervisor's ruling on #4003): never public, never at the cost of the table's own pictures, tied to
the upload's notice so its Undo takes them back, and only a DM may do anything with one. The
website's link and gallery are tested in `test_web_pictures.TheDmOnlyPictures`. Invented ids and
pictures only."""

from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from dmbot.campaigns import CampaignStore
from dmbot.memory.pdf_pictures import PictureFile
from dmbot.memory.picture_read import PictureText
from dmbot.memory.picture_vision import Seen
from dmbot.pictures.actions import DM_ONLY_STAYS, DMS_ONLY, PictureActions
from dmbot.pictures.keeper import Context, PictureKeeper, Result
from dmbot.pictures.store import MB, Added, PictureStore
from dmbot.ui import name_background
from dmbot.ui.name_lists import Upload
from dmbot.ui.upload_notice import _take_back_pictures
from tests.pg import DatabaseTest
from tests.test_pictures_keeper import FakeBucket

GUILD = 1
DM, PLAYER = 7, 8
NOTICE, OTHER_NOTICE = "a" * 32, "b" * 32


def drawn(color: int, size: tuple[int, int] = (64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (color, 20, 30)).save(out, "PNG")
    return out.getvalue()


class Kept(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = PictureStore(self.db, clock=lambda: 1000.0)
        self.bucket = FakeBucket()
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def keeper(self, storage_mb: int = 5) -> PictureKeeper:
        return PictureKeeper(self.store, self.bucket, storage_mb)

    def bot(self, storage_mb: int = 5) -> SimpleNamespace:
        return SimpleNamespace(picture_keeper=self.keeper(storage_mb), picture_store=self.store)

    async def from_file(self, data: bytes, storage_mb: int = 5) -> int:
        got = await self.keeper(storage_mb).keep(
            Context(GUILD, self.campaign.id, DM, "", None, 0, "a map", dm_only=True), data
        )
        assert got.picture is not None
        return got.picture.id

    async def from_chat(self, data: bytes, storage_mb: int = 5) -> int:
        got = await self.keeper(storage_mb).keep(
            Context(GUILD, self.campaign.id, PLAYER, "", None, 5, "a door", 7, 900), data
        )
        assert got.picture is not None
        return got.picture.id

    def seen(self, *pictures: tuple[int, bytes, str]) -> Seen:
        texts: list[PictureText] = []
        files: list[PictureFile] = []
        for page, data, where in pictures:
            path = Path(self.folder.name) / f"{len(files) + 1:04d}-p{page:03d}.png"
            path.write_bytes(data)
            texts.append(PictureText(page, "", "Gullhaven", where, f"A {where}."))
            files.append(PictureFile(page, path))
        return Seen(pictures=tuple(texts), files=tuple(files))


class TheStore(Kept):
    async def test_a_dm_only_picture_is_kept_private_and_can_never_be_made_public(self) -> None:
        pid = await self.from_file(drawn(1))
        got = await self.store.get(GUILD, self.campaign.id, pid)
        assert got is not None
        self.assertEqual((got.dm_only, got.public, got.message_id), (True, False, 0))
        self.assertFalse(await self.store.set_public(GUILD, self.campaign.id, pid, True))
        self.assertTrue(await self.store.set_public(GUILD, self.campaign.id, pid, False))
        chat = await self.from_chat(drawn(2))
        found = await self.store.get(GUILD, self.campaign.id, chat)
        assert found is not None
        self.assertEqual((found.dm_only, found.public), (False, True))  # (as before)

    async def test_it_never_pushes_out_the_tables_own_pictures(self) -> None:
        async def add(n: int, *, dm_only: bool) -> object:
            return await self.store.add(
                GUILD, self.campaign.id, poster_id=DM, session_id="", line_id=None,
                message_id=0, description="", consent_version=None,
                right_granted_at=None, sha256=f"{n:064x}", object_key=f"p/{n}",
                preview_key=f"p/{n}-p", size_bytes=3 * MB, preview_bytes=0, storage_mb=5,
                dm_only=dm_only, make_room=not dm_only,
            )  # fmt: skip

        chat = await add(1, dm_only=False)
        refused = await add(2, dm_only=True)
        self.assertEqual(refused, Added(None, False, ()))
        self.assertEqual(
            [p.id for p in await self.store.list(GUILD, self.campaign.id)],
            [chat.picture.id],  # type: ignore[attr-defined]
        )

    async def test_only_dm_only_pictures_with_no_notice_are_tied_to_one(self) -> None:
        mine = await self.from_file(drawn(1))
        tied = await self.from_file(drawn(2))
        chat = await self.from_chat(drawn(3))
        self.assertEqual(
            await self.store.tag_notice(GUILD, self.campaign.id, [tied], OTHER_NOTICE), 1
        )
        n = await self.store.tag_notice(GUILD, self.campaign.id, [mine, tied, chat], NOTICE)
        self.assertEqual(n, 1)
        listed = await self.store.for_notice(GUILD, self.campaign.id, NOTICE)
        self.assertEqual([p.id for p in listed], [mine])
        self.assertEqual(await self.store.for_notice(GUILD, "elsewhere", NOTICE), [])

    async def test_a_notice_id_must_be_one(self) -> None:
        pid = await self.from_file(drawn(1))
        with self.assertRaises(Exception):  # noqa: B017 (the check constraint, any error)
            await self.store.tag_notice(GUILD, self.campaign.id, [pid], "not-an-id")


class TheActions(Kept):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = PictureActions(self.db, self.store, self.bucket, 5, clock=lambda: 5000.0)

    async def test_only_a_dm_may_touch_it_and_never_to_make_it_public(self) -> None:
        pid = await self.from_file(drawn(1))
        apply = self.actions.apply
        out = await apply(GUILD, self.campaign.id, DM, pid, "public")
        self.assertEqual((out.state, out.note), ("refused", DM_ONLY_STAYS))
        for action in ("private", "public", "delete"):
            out = await apply(GUILD, self.campaign.id, PLAYER, pid, action)
            self.assertEqual((out.state, out.note), ("refused", DMS_ONLY))
        self.assertEqual((await apply(GUILD, self.campaign.id, DM, pid, "delete")).state, "done")
        got = await self.store.get(GUILD, self.campaign.id, pid)
        assert got is not None
        self.assertTrue(got.gone)

    async def test_an_uploader_who_is_no_longer_a_dm_may_not_either(self) -> None:
        pid = await self.from_file(drawn(1))  # (posted by DM)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "DELETE FROM campaign_dms WHERE campaign_id = %s AND user_id = %s",
                (self.campaign.id, DM),
            )
        out = await self.actions.apply(GUILD, self.campaign.id, DM, pid, "delete")
        self.assertEqual(out.note, DMS_ONLY)


class KeepingAFilesPictures(Kept):
    def upload(self, **fields: object) -> Upload:
        return Upload("text", "file.pdf", document=True, uploader_id=DM, **fields)  # type: ignore[arg-type]

    async def test_its_maps_tables_and_handouts_are_kept_for_the_dms(self) -> None:
        seen = self.seen((2, drawn(1), "map"), (3, drawn(2), "table"))
        steps: list[tuple[str, int, int]] = []
        new, missed = await name_background._keep_pictures(
            self.bot(), self.campaign, self.upload(), seen, lambda *a: steps.append(a)
        )
        self.assertEqual((len(new), missed), (2, 0))
        kept = [await self.store.get(GUILD, self.campaign.id, pid) for pid in new]
        for picture in kept:
            assert picture is not None
            self.assertEqual(
                (picture.dm_only, picture.public, picture.poster_id, picture.message_id),
                (True, False, DM, 0),
            )
        self.assertEqual([p.description for p in kept if p], ["A map.", "A table."])
        self.assertEqual(steps[-1], ("keeping", 2, 2))  # (the wait shows the step, counts only)

    async def test_a_file_the_campaign_keeps_already_is_left_never_taken_over(self) -> None:
        chat = await self.from_chat(drawn(1))
        seen = self.seen((2, drawn(1), "map"))
        new, missed = await name_background._keep_pictures(
            self.bot(), self.campaign, self.upload(), seen
        )
        self.assertEqual((new, missed), ((), 0))  # (not the upload's: its Undo leaves it)
        found = await self.store.get(GUILD, self.campaign.id, chat)
        assert found is not None
        self.assertFalse(found.dm_only)

    async def test_without_an_uploader_or_a_keeper_nothing_is_kept(self) -> None:
        seen = self.seen((2, drawn(1), "map"))
        nobody = Upload("text", "file.pdf", document=True)
        self.assertEqual(
            await name_background._keep_pictures(self.bot(), self.campaign, nobody, seen), ((), 0)
        )
        off = SimpleNamespace(picture_keeper=None, picture_store=self.store)
        self.assertEqual(
            await name_background._keep_pictures(off, self.campaign, self.upload(), seen), ((), 0)
        )
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])

    async def test_one_that_does_not_fit_is_counted_and_the_rest_are_kept(self) -> None:
        real = self.keeper()
        calls: list[Context] = []

        class FirstIsFull:
            async def keep(self, ctx: Context, data: bytes) -> Result:
                calls.append(ctx)
                if len(calls) == 1:
                    return Result(None, why_not="full")
                return await real.keep(ctx, data)

        seen = self.seen((2, drawn(1), "map"), (3, drawn(2), "handout"))
        bot = SimpleNamespace(picture_keeper=FirstIsFull(), picture_store=self.store)
        new, missed = await name_background._keep_pictures(bot, self.campaign, self.upload(), seen)
        self.assertEqual((len(new), missed), (1, 1))
        self.assertTrue(all(c.dm_only for c in calls))

    async def test_stopped_midway_it_takes_back_what_it_kept(self) -> None:
        real = self.keeper()
        calls = 0

        class StopsOnSecond:
            async def keep(self, ctx: Context, data: bytes) -> Result:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise asyncio.CancelledError
                return await real.keep(ctx, data)

            async def discard(self, *args: object, **kw: object) -> bool:
                return await real.discard(*args, **kw)  # type: ignore[arg-type]

        seen = self.seen((2, drawn(1), "map"), (3, drawn(2), "handout"))
        bot = SimpleNamespace(picture_keeper=StopsOnSecond(), picture_store=self.store)
        with self.assertRaises(asyncio.CancelledError):
            await name_background._keep_pictures(bot, self.campaign, self.upload(), seen)
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])

    async def test_the_same_file_posted_at_the_table_becomes_the_tables(self) -> None:
        pid = await self.from_file(drawn(1))
        await self.store.tag_notice(GUILD, self.campaign.id, [pid], NOTICE)
        again = await self.from_chat(drawn(1))
        self.assertEqual(again, pid)  # (one row per file)
        got = await self.store.get(GUILD, self.campaign.id, pid)
        assert got is not None
        self.assertFalse(got.dm_only)
        self.assertEqual(await self.store.for_notice(GUILD, self.campaign.id, NOTICE), [])

    async def test_a_dm_files_pictures_make_room_before_the_tables_own(self) -> None:
        async def add(n: int, *, dm_only: bool) -> int:
            got = await self.store.add(
                GUILD, self.campaign.id, poster_id=DM, session_id="", line_id=None,
                message_id=0, description="", consent_version=None, right_granted_at=None,
                sha256=f"{n:064x}", object_key=f"p/{n}", preview_key=f"p/{n}-p",
                size_bytes=2 * MB, preview_bytes=0, storage_mb=5,
                dm_only=dm_only, make_room=not dm_only,
            )  # fmt: skip
            assert got.picture is not None
            return got.picture.id

        table = await add(1, dm_only=False)
        from_file = await add(2, dm_only=True)
        await add(3, dm_only=False)  # (no room: the file's picture goes, not the older table one)
        a = await self.store.get(GUILD, self.campaign.id, table)
        b = await self.store.get(GUILD, self.campaign.id, from_file)
        assert a is not None and b is not None
        self.assertEqual((a.gone, b.gone), (False, True))


class TheUploadsUndo(Kept):
    async def test_undo_takes_back_its_pictures_and_their_files_and_nothing_else(self) -> None:
        mine = await self.from_file(drawn(1))
        other = await self.from_file(drawn(2))
        chat = await self.from_chat(drawn(3))
        await self.store.tag_notice(GUILD, self.campaign.id, [mine], NOTICE)
        await self.store.tag_notice(GUILD, self.campaign.id, [other], OTHER_NOTICE)
        files_before = set(self.bucket.files)
        await _take_back_pictures(self.bot(), GUILD, self.campaign.id, NOTICE)
        gone = await self.store.get(GUILD, self.campaign.id, mine)
        assert gone is not None
        self.assertTrue(gone.gone)
        self.assertEqual(len(files_before - set(self.bucket.files)), 2)  # (picture and preview)
        for left in (other, chat):
            found = await self.store.get(GUILD, self.campaign.id, left)
            assert found is not None
            self.assertFalse(found.gone)

    async def test_a_store_failure_never_stops_the_undo(self) -> None:
        class Broken:
            async def for_notice(self, *_: object) -> list[object]:
                raise RuntimeError("down")

        bot = SimpleNamespace(picture_keeper=self.keeper(), picture_store=Broken())
        await _take_back_pictures(bot, GUILD, self.campaign.id, NOTICE)  # (no raise)


class TheLine(unittest.TestCase):
    def test_it_says_where_they_are_how_they_go_and_what_couldnt_be_kept(self) -> None:
        line = name_background.pictures_kept_line
        self.assertEqual(line(0), "")
        self.assertEqual(
            line(1, undo=True),
            "\n🖼️ Kept 1 picture from this file for the DMs only. See Pictures on the website."
            " ↩️ Undo this upload removes it too.",
        )
        self.assertIn("Delete any you don't want there.", line(3))
        self.assertIn("Kept 3 pictures", line(3))
        self.assertIn("removes them too", line(3, undo=True))
        self.assertEqual(
            line(0, 2),
            "\n🖼️ 2 pictures couldn't be kept: no room left for pictures, or a storage problem."
            " Delete some under Pictures on the website to make room.",
        )
        self.assertIn("1 more picture couldn't be kept", line(2, 1))

    def test_the_offer_says_they_are_kept_before_the_yes_and_only_then(self) -> None:
        plain = Upload("text", "file.pdf", document=True, pictures_found=3)
        keeps = Upload("text", "file.pdf", document=True, pictures_found=3, keeps_pictures=True)
        none = Upload("text", "file.pdf", document=True, keeps_pictures=True)
        kept_words = name_background.PICTURES_KEPT_NOTE.strip()
        self.assertIn(kept_words, name_background.offer_text(keeps, False))
        self.assertNotIn(kept_words, name_background.offer_text(plain, False))
        self.assertNotIn(kept_words, name_background.offer_text(none, False))
