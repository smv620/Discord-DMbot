"""What a poster or DM chose on the website's Pictures page (#3274): who may do what, checked from
the database, the queue's claim order, and the files removed on delete. Invented ids only."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from dmbot.campaigns import CampaignStore
from dmbot.pictures.actions import DMS_ONLY, GONE, NOT_YOURS, Outcome, PictureActions
from dmbot.pictures.bucket import BucketError
from dmbot.pictures.store import MB, PictureStore
from tests.pg import DatabaseTest

GUILD, OTHER = 1, 2
DM, POSTER, STRANGER = 7, 8, 9


class Bucket:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.fail = False

    async def put(self, key: str, data: bytes) -> None: ...

    async def delete(self, key: str) -> None:
        if self.fail:
            raise BucketError("status 500")
        self.deleted.append(key)


class Applying(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = PictureStore(self.db, clock=lambda: 1000.0)
        self.bucket = Bucket()
        self.actions = PictureActions(self.db, self.store, self.bucket, 5, clock=lambda: 5000.0)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        added = await self.store.add(
            GUILD,
            self.campaign.id,
            poster_id=POSTER,
            session_id="",
            line_id=None,
            message_id=1,
            description="a red door",
            consent_version=7,
            right_granted_at=900,
            sha256="a" * 64,
            object_key="p/one.png",
            preview_key="p/one-preview.webp",
            size_bytes=MB,
            preview_bytes=0,
            storage_mb=5,
        )
        assert added.picture is not None
        self.picture = added.picture

    async def test_a_kept_picture_carries_the_build_that_described_it(self) -> None:
        """Lineage (#3739): the description is DMbot's AI read of the image, so the row is
        stamped when it is made; the DM's later choices (private, permanent) keep the stamp."""
        commit, later = "e" * 40, "f" * 40
        with mock.patch.dict(os.environ, {"GIT_COMMIT": commit}):
            added = await self.store.add(
                GUILD, self.campaign.id, poster_id=POSTER, session_id="", line_id=None,
                message_id=2, description="a blue door", consent_version=7, right_granted_at=900,
                sha256="b" * 64, object_key="p/two.png", preview_key="p/two-preview.webp",
                size_bytes=MB, preview_bytes=0, storage_mb=5,
            )  # fmt: skip
        assert added.picture is not None

        async def stamp() -> str | None:
            async with self.db.guild(GUILD) as conn:
                cur = await conn.execute(
                    "SELECT made_by_commit AS c FROM campaign_pictures WHERE id = %s",
                    (added.picture.id,),  # type: ignore[union-attr]
                )
                return (await cur.fetchone())["c"]  # type: ignore[index,no-any-return]

        self.assertEqual(await stamp(), commit)
        with mock.patch.dict(os.environ, {"GIT_COMMIT": later}):
            await self.actions.apply(GUILD, self.campaign.id, DM, added.picture.id, "private")
        self.assertEqual(await stamp(), commit)

    async def do(self, user: int, action: str) -> Outcome:
        return await self.actions.apply(GUILD, self.campaign.id, user, self.picture.id, action)

    async def test_the_poster_and_a_dm_may_make_it_private_and_public_again(self) -> None:
        for user in (POSTER, DM):
            self.assertEqual((await self.do(user, "private")).state, "done")
            got = await self.store.get(GUILD, self.campaign.id, self.picture.id)
            assert got is not None
            self.assertFalse(got.public)
            self.assertEqual((await self.do(user, "public")).state, "done")

    async def test_a_stranger_may_do_nothing(self) -> None:
        for action in ("private", "public", "delete"):
            out = await self.do(STRANGER, action)
            self.assertEqual((out.state, out.note), ("refused", NOT_YOURS))
        self.assertEqual((await self.do(STRANGER, "permanent")).note, DMS_ONLY)

    async def test_only_a_dm_marks_it_permanent_and_the_poster_may_not(self) -> None:
        self.assertEqual((await self.do(POSTER, "permanent")).note, DMS_ONLY)
        self.assertEqual((await self.do(DM, "permanent")).state, "done")
        got = await self.store.get(GUILD, self.campaign.id, self.picture.id)
        assert got is not None
        self.assertTrue(got.permanent)
        self.assertEqual((await self.do(DM, "unpermanent")).state, "done")

    async def test_marking_past_the_limit_says_so_in_plain_words(self) -> None:
        for n in range(2, 6):
            added = await self.store.add(
                GUILD,
                self.campaign.id,
                poster_id=POSTER,
                session_id="",
                line_id=None,
                message_id=n,
                description="",
                consent_version=7,
                right_granted_at=900,
                sha256=f"{n:064x}",
                object_key=f"p/{n}.png",
                preview_key="",
                size_bytes=MB,
                preview_bytes=0,
                storage_mb=5,
            )
            assert added.picture is not None
            await self.actions.apply(GUILD, self.campaign.id, DM, added.picture.id, "permanent")
        out = await self.do(DM, "permanent")
        self.assertEqual(out.state, "refused")
        self.assertIn("Permanent pictures are full", out.note)

    async def test_delete_removes_both_files_and_a_failed_file_delete_still_deletes(self) -> None:
        self.bucket.fail = True
        self.assertEqual((await self.do(POSTER, "delete")).state, "done")
        self.assertEqual(self.bucket.deleted, [])  # the sweep will find them
        self.assertEqual((await self.do(POSTER, "delete")).note, GONE)

    async def test_delete_takes_the_files_out_of_the_bucket(self) -> None:
        await self.do(DM, "delete")
        self.assertEqual(self.bucket.deleted, ["p/one.png", "p/one-preview.webp"])

    async def test_another_servers_picture_is_not_found(self) -> None:
        other = await self.campaigns.create(OTHER, "Far", DM)
        out = await self.actions.apply(OTHER, other.id, DM, self.picture.id, "delete")
        self.assertEqual(out.note, GONE)

    async def queue(self, user: int, action: str, name: str, created: int = 5000) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO picture_actions (guild_id, campaign_id, id, user_id, picture_id,"
                " action, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (GUILD, self.campaign.id, name * 32, user, self.picture.id, action, created),
            )

    async def test_the_queue_is_applied_in_order_and_answers_are_kept(self) -> None:
        await self.queue(POSTER, "private", "a")
        await self.queue(STRANGER, "delete", "b")
        await self.queue(DM, "permanent", "c")
        self.assertEqual(await self.actions.run_once(GUILD), 3)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT id, state, note FROM picture_actions ORDER BY seq")
            rows = [(r["id"][0], r["state"], r["note"]) for r in await cur.fetchall()]
        self.assertEqual(
            rows,
            [
                ("a", "done", "It is private to the campaign now."),
                ("b", "refused", NOT_YOURS),
                ("c", "done", "It is permanent now."),
            ],
        )
        self.assertEqual(await self.actions.run_once(GUILD), 0)

    async def test_a_choice_taken_and_never_finished_is_taken_again(self) -> None:
        await self.queue(DM, "private", "e")
        async with self.db.guild(GUILD) as conn:  # a worker took it 10 minutes ago and died
            await conn.execute(
                "UPDATE picture_actions SET state = 'working', claimed_at = %s", (5000 - 600,)
            )
        self.assertEqual(await self.actions.run_once(GUILD), 1)
        got = await self.store.get(GUILD, self.campaign.id, self.picture.id)
        assert got is not None
        self.assertFalse(got.public)

    async def test_a_choice_a_worker_has_just_taken_is_left_to_it(self) -> None:
        await self.queue(DM, "private", "f")
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE picture_actions SET state = 'working', claimed_at = %s", (5000 - 10,)
            )
        self.assertEqual(await self.actions.run_once(GUILD), 0)

    async def test_a_choice_older_than_an_hour_is_dropped_unapplied(self) -> None:
        await self.queue(DM, "delete", "d", created=5000 - 4000)
        self.assertEqual(await self.actions.run_once(GUILD), 0)
        self.assertIsNotNone(await self.store.get(GUILD, self.campaign.id, self.picture.id))


if __name__ == "__main__":
    unittest.main()
