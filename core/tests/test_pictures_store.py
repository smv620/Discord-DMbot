"""Kept pictures (#3274): one row per file per campaign, isolated by server and campaign, the
rolling storage that removes the oldest non-permanent pictures first, and the permanent limit of
80% of the storage. Invented ids only."""

from __future__ import annotations

import asyncio
import unittest

from dmbot.campaigns import CampaignStore
from dmbot.pictures.store import MB, Added, PictureStore, permanent_limit_bytes
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 1, 2
DM, ME = 7, 8


def sha(n: int) -> str:
    return f"{n:064x}"


class Kept(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = PictureStore(self.db, clock=lambda: 1000.0)
        self.one = await self.campaigns.create(GUILD_A, "One", DM)
        self.two = await self.campaigns.create(GUILD_A, "Two", DM)
        self.far = await self.campaigns.create(GUILD_B, "Far", DM)

    async def keep(
        self, campaign: str, n: int, mb: int = 1, *, guild: int = GUILD_A, cap: int = 5
    ) -> Added:
        return await self.store.add(
            guild,
            campaign,
            poster_id=ME,
            session_id="",
            line_id=None,
            message_id=n,
            description=f"picture {n}",
            consent_version=7,
            right_granted_at=900,
            sha256=sha(n),
            object_key=f"{guild}/{campaign}/{sha(n)}.png",
            preview_key=f"{guild}/{campaign}/{sha(n)}.webp",
            size_bytes=mb * MB,
            preview_bytes=0,
            storage_mb=cap,
        )

    async def test_a_picture_is_kept_with_a_long_random_link(self) -> None:
        added = await self.keep(self.one.id, 1)
        assert added.picture is not None
        self.assertTrue(added.new)
        self.assertGreaterEqual(len(added.picture.code), 43)
        self.assertTrue(added.picture.public)
        self.assertFalse(added.picture.permanent)

    async def test_the_same_file_in_one_campaign_is_one_picture(self) -> None:
        first = await self.keep(self.one.id, 1)
        again = await self.keep(self.one.id, 1)
        assert first.picture is not None and again.picture is not None
        self.assertFalse(again.new)
        self.assertEqual(first.picture.code, again.picture.code)
        self.assertEqual((await self.store.usage(GUILD_A, self.one.id, 5)).used_bytes, MB)

    async def test_the_same_file_in_another_campaign_is_its_own_picture(self) -> None:
        a = await self.keep(self.one.id, 1)
        b = await self.keep(self.two.id, 1)
        assert a.picture is not None and b.picture is not None
        self.assertNotEqual(a.picture.code, b.picture.code)
        self.assertEqual(await self.store.list(GUILD_A, self.one.id), [a.picture])

    async def test_another_campaign_or_server_never_sees_it(self) -> None:
        added = await self.keep(self.one.id, 1)
        assert added.picture is not None
        self.assertIsNone(await self.store.get(GUILD_A, self.two.id, added.picture.id))
        self.assertIsNone(await self.store.get(GUILD_B, self.one.id, added.picture.id))
        self.assertEqual(await self.store.list(GUILD_B, self.far.id), [])
        self.assertFalse(await self.store.set_public(GUILD_A, self.two.id, added.picture.id, False))
        self.assertIsNone(await self.store.delete(GUILD_A, self.two.id, added.picture.id))

    async def test_over_the_storage_the_oldest_go_first(self) -> None:
        for n in range(1, 6):
            await self.keep(self.one.id, n)
        added = await self.keep(self.one.id, 6)
        self.assertEqual([p.message_id for p in added.removed], [1])
        kept = await self.store.list(GUILD_A, self.one.id)
        self.assertEqual([p.message_id for p in kept], [6, 5, 4, 3, 2])

    async def test_a_permanent_picture_is_never_removed_for_room(self) -> None:
        first = await self.keep(self.one.id, 1)
        assert first.picture is not None
        self.assertIsNone(
            await self.store.set_permanent(GUILD_A, self.one.id, first.picture.id, True, 5)
        )
        for n in range(2, 6):
            await self.keep(self.one.id, n)
        added = await self.keep(self.one.id, 6)
        self.assertEqual([p.message_id for p in added.removed], [2])
        kept = {p.message_id for p in await self.store.list(GUILD_A, self.one.id)}
        self.assertIn(1, kept)

    async def test_a_removed_picture_keeps_its_link_and_says_why(self) -> None:
        first = await self.keep(self.one.id, 1)
        assert first.picture is not None
        for n in range(2, 7):
            await self.keep(self.one.id, n)
        gone = await self.store.get(GUILD_A, self.one.id, first.picture.id)
        assert gone is not None
        self.assertTrue(gone.gone and gone.removed_for_room)
        self.assertEqual(gone.description, "")

    async def test_nothing_is_kept_when_only_permanent_ones_are_in_the_way(self) -> None:
        cap = 5
        for n in range(1, 5):  # 4 permanent = the 80% limit of 5 MB
            added = await self.keep(self.one.id, n, cap=cap)
            assert added.picture is not None
            self.assertIsNone(
                await self.store.set_permanent(GUILD_A, self.one.id, added.picture.id, True, cap)
            )
        await self.keep(self.one.id, 5, cap=cap)  # fills the last MB
        too_big = await self.keep(self.one.id, 6, mb=2, cap=cap)
        self.assertIsNone(too_big.picture)
        self.assertEqual(too_big.removed, ())
        self.assertEqual(len(await self.store.list(GUILD_A, self.one.id)), 5)

    async def test_a_picture_bigger_than_the_storage_is_not_kept(self) -> None:
        self.assertIsNone((await self.keep(self.one.id, 1, mb=6)).picture)

    async def test_marking_past_eighty_percent_is_refused_and_follows_the_setting(self) -> None:
        pics = []
        for n in range(1, 6):
            added = await self.keep(self.one.id, n)
            assert added.picture is not None
            pics.append(added.picture)
        for pic in pics[:4]:
            self.assertIsNone(await self.store.set_permanent(GUILD_A, self.one.id, pic.id, True, 5))
        refused = await self.store.set_permanent(GUILD_A, self.one.id, pics[4].id, True, 5)
        self.assertEqual(refused, "Permanent pictures are full (4 MB). Unmark one to make room.")
        # a bigger storage setting makes room, with no number written in the code
        self.assertIsNone(
            await self.store.set_permanent(GUILD_A, self.one.id, pics[4].id, True, 10)
        )
        self.assertEqual(permanent_limit_bytes(10), 8 * MB)
        # unmarking always works
        self.assertIsNone(
            await self.store.set_permanent(GUILD_A, self.one.id, pics[0].id, False, 5)
        )

    async def test_two_uploads_at_once_cannot_pass_the_storage(self) -> None:
        for n in range(1, 5):
            await self.keep(self.one.id, n)
        await asyncio.gather(*(self.keep(self.one.id, n, mb=2) for n in (10, 11, 12)))
        used = (await self.store.usage(GUILD_A, self.one.id, 5)).used_bytes
        self.assertLessEqual(used, 5 * MB)

    async def test_delete_keeps_the_link_and_frees_the_room(self) -> None:
        added = await self.keep(self.one.id, 1)
        assert added.picture is not None
        gone = await self.store.delete(GUILD_A, self.one.id, added.picture.id)
        assert gone is not None
        self.assertEqual(gone.object_key, added.picture.object_key)
        self.assertIsNone(await self.store.delete(GUILD_A, self.one.id, added.picture.id))
        self.assertEqual((await self.store.usage(GUILD_A, self.one.id, 5)).used_bytes, 0)
        after = await self.store.get(GUILD_A, self.one.id, added.picture.id)
        assert after is not None
        self.assertTrue(after.gone and not after.removed_for_room)
        # the same file can be kept again, with a new link
        again = await self.keep(self.one.id, 1)
        assert again.picture is not None
        self.assertNotEqual(again.picture.code, added.picture.code)

    async def test_a_refused_picture_leaves_the_removed_one_saying_so(self) -> None:
        first = await self.keep(self.one.id, 1)
        assert first.picture is not None
        await self.store.delete(GUILD_A, self.one.id, first.picture.id)
        for n in range(2, 6):  # 4 permanent MB = the limit, then one more MB kept
            added = await self.keep(self.one.id, n)
            assert added.picture is not None
            await self.store.set_permanent(GUILD_A, self.one.id, added.picture.id, True, 5)
        await self.keep(self.one.id, 6)
        refused = await self.keep(self.one.id, 1, mb=2)  # the deleted file again, too big now
        self.assertIsNone(refused.picture)
        old = await self.store.get(GUILD_A, self.one.id, first.picture.id)
        assert old is not None
        self.assertTrue(old.gone)

    async def test_private_and_public_can_be_changed(self) -> None:
        added = await self.keep(self.one.id, 1)
        assert added.picture is not None
        self.assertTrue(await self.store.set_public(GUILD_A, self.one.id, added.picture.id, False))
        got = await self.store.get(GUILD_A, self.one.id, added.picture.id)
        assert got is not None
        self.assertFalse(got.public)

    async def test_the_campaign_takes_its_pictures_with_it(self) -> None:
        await self.keep(self.one.id, 1)
        await self.campaigns.delete(GUILD_A, self.one.id)
        self.assertEqual(await self.store.list(GUILD_A, self.one.id), [])


if __name__ == "__main__":
    unittest.main()
