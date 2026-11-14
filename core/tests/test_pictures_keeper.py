"""Keeping a posted picture (#3274): random keys, one upload per file, files cleaned when the row
is refused or older pictures are removed. A fake bucket stands in for R2."""

from __future__ import annotations

import io
import unittest

from PIL import Image

from dmbot.campaigns import CampaignStore
from dmbot.pictures.bucket import BucketError
from dmbot.pictures.keeper import Context, PictureKeeper
from dmbot.pictures.store import MB, PictureStore
from tests.pg import DatabaseTest

GUILD = 1
DM, ME = 7, 8


class FakeBucket:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fail_put_after = 10**9
        self.puts = 0

    async def put(self, key: str, data: bytes) -> None:
        self.puts += 1
        if self.puts > self.fail_put_after:
            raise BucketError("status 500")
        self.files[key] = data

    async def delete(self, key: str) -> None:
        self.files.pop(key, None)


def picture(color: int = 1, size: tuple[int, int] = (64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (color, 2, 3)).save(out, "PNG")
    return out.getvalue()


class Keeping(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = PictureStore(self.db, clock=lambda: 1000.0)
        self.bucket = FakeBucket()
        self.campaign = await self.campaigns.create(GUILD, "One", DM)

    def keeper(self, storage_mb: int = 5) -> PictureKeeper:
        return PictureKeeper(self.store, self.bucket, storage_mb)

    def ctx(self, message_id: int = 1) -> Context:
        return Context(GUILD, self.campaign.id, ME, "", None, message_id, "a red door", 7, 900)

    async def test_a_picture_is_uploaded_with_a_preview_under_random_keys(self) -> None:
        got = await self.keeper().keep(self.ctx(), picture())
        assert got.picture is not None
        self.assertTrue(got.new)
        self.assertEqual(len(self.bucket.files), 2)
        key, preview = got.picture.object_key, got.picture.preview_key
        self.assertRegex(key, r"^p/[0-9a-f]{32}[.]png$")
        self.assertEqual(preview, key.rsplit(".", 1)[0] + "-preview.webp")
        for secret in (self.campaign.id, got.picture.sha256, got.picture.sha256[:12]):
            self.assertNotIn(secret, key + preview)

    async def test_the_same_file_twice_uploads_once(self) -> None:
        keeper = self.keeper()
        first = await keeper.keep(self.ctx(1), picture())
        again = await keeper.keep(self.ctx(2), picture())
        assert first.picture is not None and again.picture is not None
        self.assertFalse(again.new)
        self.assertEqual(first.picture.code, again.picture.code)
        self.assertEqual(self.bucket.puts, 2)

    async def test_a_picture_that_will_not_decode_is_not_kept_and_nothing_is_uploaded(self) -> None:
        got = await self.keeper().keep(self.ctx(), b"not a picture")
        self.assertEqual((got.picture, got.why_not), (None, "refused"))
        self.assertEqual(self.bucket.puts, 0)

    async def test_a_failed_upload_leaves_no_file_and_no_row(self) -> None:
        self.bucket.fail_put_after = 1  # the picture goes up, its preview fails
        got = await self.keeper().keep(self.ctx(), picture())
        self.assertEqual((got.picture, got.why_not), (None, "failed"))
        self.assertEqual(self.bucket.files, {})
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])

    async def test_one_that_does_not_fit_leaves_its_files_out(self) -> None:
        got = await self.keeper(storage_mb=1).keep(self.ctx(), self.big_picture())
        self.assertEqual((got.picture, got.why_not), (None, "full"))
        self.assertEqual(self.bucket.files, {})

    def big_picture(self) -> bytes:
        import os

        out = io.BytesIO()
        Image.frombytes("RGB", (800, 800), os.urandom(800 * 800 * 3)).save(out, "BMP")
        data = out.getvalue()
        assert len(data) > MB
        return data

    async def test_older_pictures_removed_for_room_lose_their_files(self) -> None:
        keeper = self.keeper(storage_mb=1)
        # three pictures of about 400 KB each fit; a fourth pushes the oldest out
        sizes = [self.noisy(n) for n in range(4)]
        kept = [await keeper.keep(self.ctx(n + 1), data) for n, data in enumerate(sizes)]
        removed = sum(k.removed for k in kept)
        self.assertGreaterEqual(removed, 1)
        live = {p.object_key for p in await self.store.list(GUILD, self.campaign.id)}
        on_disk = {k for k in self.bucket.files if not k.endswith("-preview.webp")}
        self.assertEqual(on_disk, live)

    def noisy(self, n: int) -> bytes:
        import os

        out = io.BytesIO()
        Image.frombytes("RGB", (360, 360), os.urandom(360 * 360 * 3)).save(out, "PNG")
        return out.getvalue() + bytes([n])  # distinct bytes: a different file

    async def test_a_picture_taken_back_loses_its_row_content_and_both_files(self) -> None:
        keeper = self.keeper()
        got = await keeper.keep(self.ctx(), picture())
        assert got.picture is not None
        await keeper.discard(GUILD, self.campaign.id, got.picture)
        self.assertEqual(self.bucket.files, {})
        gone = await self.store.get(GUILD, self.campaign.id, got.picture.id)
        assert gone is not None
        self.assertTrue(gone.gone)
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])

    async def test_deleting_the_message_takes_the_kept_picture_and_its_files(self) -> None:
        keeper = self.keeper()
        mine = await keeper.keep(self.ctx(message_id=11), picture(1))
        other = await keeper.keep(self.ctx(message_id=12), picture(2))
        assert mine.picture is not None and other.picture is not None
        await self.store.set_permanent(GUILD, self.campaign.id, mine.picture.id, True, 5)
        self.assertEqual(await keeper.forget_message(GUILD, 11), 1)  # permanent goes too
        self.assertEqual(await keeper.forget_message(GUILD, 11), 0)
        self.assertEqual(
            [p.id for p in await self.store.list(GUILD, self.campaign.id)], [other.picture.id]
        )
        self.assertEqual(
            sorted(self.bucket.files), sorted([other.picture.object_key, other.picture.preview_key])
        )
        self.assertEqual(await keeper.forget_message(GUILD, 0), 0)

    async def test_each_campaign_keeps_its_own_copy_of_the_same_file(self) -> None:
        other = await self.campaigns.create(GUILD, "Two", DM)
        a = await self.keeper().keep(self.ctx(), picture())
        b = await self.keeper().keep(
            Context(GUILD, other.id, ME, "", None, 1, "", 7, 900), picture()
        )
        assert a.picture is not None and b.picture is not None
        self.assertNotEqual(a.picture.object_key, b.picture.object_key)
        self.assertEqual(len(self.bucket.files), 4)


if __name__ == "__main__":
    unittest.main()
