"""Kept pictures in backups (#3274): what the section writes and refuses, that a restored picture
comes back waiting for its file with its link kept only when free, that the parts hold the permanent
and newest pictures first, and that putting a file back reads it again, respects the storage and
never removes anything. A fake bucket and a fake `prepare` stand in for R2 and Pillow."""

from __future__ import annotations

import hashlib
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.pictures import backup, parts
from dmbot.pictures.bucket import BucketError
from dmbot.pictures.parts import Part, PartItem
from dmbot.pictures.prepare import Prepared, Refused
from dmbot.pictures.store import MB, PictureStore
from tests.pg import DatabaseTest

GUILD, OTHER = 1, 2
DM, POSTER = 7, 8


def sha(n: int) -> str:
    return hashlib.sha256(str(n).encode()).hexdigest()


class Bucket:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fail_get: set[str] = set()

    async def put(self, key: str, data: bytes) -> None:
        self.files[key] = data

    async def get(self, key: str) -> bytes:
        if key in self.fail_get:
            raise BucketError("status 500")
        return self.files[key]

    async def delete(self, key: str) -> None:
        self.files.pop(key, None)


async def fake_prepare(data: bytes) -> Prepared:
    if data == b"not a picture":
        raise Refused("unreadable")
    return Prepared(data, "png", b"preview-of-" + data, sha(len(data)), 10, 10)


class Backups(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = PictureStore(self.db, clock=lambda: 1000.0)
        self.bucket = Bucket()
        self.section = backup.PicturesSection()
        self.one = await self.campaigns.create(GUILD, "One", DM)

    async def keep(self, n: int, campaign: str | None = None, mb: int = 1, **kw: Any) -> None:
        cid = campaign or self.one.id
        key = f"p/{n}.png"
        self.bucket.files[key] = f"file-{n}".encode()
        added = await self.store.add(
            kw.get("guild", GUILD),
            cid,
            poster_id=POSTER,
            session_id="s",
            line_id=None,
            message_id=n,
            description=f"picture {n}",
            consent_version=7,
            right_granted_at=900,
            sha256=sha(n),
            object_key=key,
            preview_key=f"p/{n}-preview.webp",
            size_bytes=mb * MB,
            preview_bytes=0,
            storage_mb=kw.get("cap", 50),
        )
        assert added.picture is not None
        if kw.get("permanent"):
            await self.store.set_permanent(GUILD, cid, added.picture.id, True, 50)

    async def dump(self, campaign: str | None = None) -> list[Any]:
        async with self.db.guild(GUILD, snapshot=True) as conn:
            return await self.section.dump(conn, GUILD, campaign or self.one.id)

    async def load(self, rows: list[Any], campaign: str, guild: int = GUILD) -> None:
        async with self.db.guild(guild) as conn:
            await self.section.load(conn, guild, campaign, self.section.check(rows))

    # ---- the rows ---------------------------------------------------------------------------

    async def test_the_rows_are_permanent_first_then_newest_and_carry_no_local_ids(self) -> None:
        for n in (1, 2, 3, 4):
            await self.keep(n, permanent=(n == 2))
        rows = await self.dump()
        self.assertEqual([r["sha256"] for r in rows], [sha(2), sha(4), sha(3), sha(1)])
        flat = repr(rows)
        for local in ("p/1.png", "preview", "message", "session"):
            self.assertNotIn(local, flat)  # (no bucket key, message or session number)
        self.assertEqual(rows[0]["poster"], str(POSTER))
        self.assertTrue(all(r["file"] for r in rows))

    async def test_a_deleted_picture_is_not_in_the_backup(self) -> None:
        await self.keep(1)
        await self.keep(2)
        first = await self.store.find(GUILD, self.one.id, sha(1))
        assert first is not None
        await self.store.delete(GUILD, self.one.id, first.id)
        self.assertEqual([r["sha256"] for r in await self.dump()], [sha(2)])

    # ---- coming back ------------------------------------------------------------------------

    async def test_a_restore_next_to_the_original_gets_new_links_and_waits_for_files(self) -> None:
        await self.keep(1, permanent=True)
        await self.keep(2)
        rows = await self.dump()
        copy = await self.campaigns.create(GUILD, "Copy", DM)
        await self.load(rows, copy.id)
        waiting = await self.store.waiting(GUILD, copy.id)
        self.assertEqual(len(waiting), 2)
        original = {p.code for p in await self.store.list(GUILD, self.one.id)}
        self.assertTrue(original.isdisjoint({p.code for p in waiting}))  # never the same link
        self.assertTrue(all(p.object_key == "" and p.session_id == "" for p in waiting))
        self.assertEqual({p.permanent for p in waiting}, {True, False})
        self.assertEqual((await self.store.usage(GUILD, copy.id, 50)).used_bytes, 0)

    async def test_the_link_is_kept_when_nothing_else_has_it(self) -> None:
        await self.keep(1)
        rows = await self.dump()
        async with self.db.guild(GUILD) as conn:  # (the original is gone: its link is free)
            await conn.execute("DELETE FROM campaigns WHERE guild_id = %s", (GUILD,))
        elsewhere = await self.campaigns.create(OTHER, "Else", DM)
        await self.load(rows, elsewhere.id, guild=OTHER)
        got = await self.store.waiting(OTHER, elsewhere.id)
        self.assertEqual([p.code for p in got], [rows[0]["code"]])

    async def test_replacing_a_campaign_clears_its_pictures_first(self) -> None:
        await self.keep(1)
        async with self.db.guild(GUILD) as conn:
            await self.section.clear(conn, GUILD, self.one.id)
        self.assertEqual(await self.store.list(GUILD, self.one.id), [])

    def test_a_forged_row_is_refused(self) -> None:
        good = {
            "sha256": sha(1),
            "poster": "8",
            "description": "x",
            "code": "c" * 43,
            "public": True,
            "permanent": False,
            "consent_version": 7,
            "right_granted_at": 900,
            "made_at": 1,
            "size": 10,
            "file": True,
        }
        self.assertEqual(len(self.section.check([good])), 1)
        for change in (
            {"sha256": "z" * 64},
            {"poster": "-1"},
            {"poster": 8},
            {"description": "x" * 2501},
            {"code": "short"},
            {"public": 1},
            {"consent_version": 6},
            {"size": 0},
            {"size": parts.PICTURE_READ_BYTES + 1},
            {"made_at": True},
        ):
            with self.assertRaises(CampaignError, msg=str(change)):
                self.section.check([{**good, **change}])
        with self.assertRaises(CampaignError):
            self.section.check([good, good])  # (the same file twice)
        with self.assertRaises(CampaignError):
            self.section.check(["not a row"])

    # ---- the parts --------------------------------------------------------------------------

    async def test_parts_hold_the_files_in_the_rows_order_and_close_at_the_size(self) -> None:
        for n in (1, 2, 3):
            await self.keep(n, mb=1)
        rows = await self.dump()  # newest first: 3, 2, 1
        plan = backup.plan(rows, 2 * MB)
        self.assertEqual([[r["sha256"] for r in g] for g in plan], [[sha(3), sha(2)], [sha(1)]])
        made = [
            raw
            async for raw in backup.make_parts(
                self.store, self.bucket, GUILD, self.one.id, rows, "b" * 32, 2 * MB
            )
        ]
        got = [parts.decode_part(raw) for raw in made]
        self.assertEqual([p.number for p in got], [1, 2])
        self.assertEqual(got[0].items[0], PartItem(sha(3), b"file-3"))

    async def test_a_file_missing_from_the_bucket_is_left_out_of_its_part(self) -> None:
        await self.keep(1)
        await self.keep(2)
        self.bucket.fail_get.add("p/2.png")
        rows = await self.dump()
        [raw] = [
            r
            async for r in backup.make_parts(
                self.store, self.bucket, GUILD, self.one.id, rows, "b" * 32, 20 * MB
            )
        ]
        self.assertEqual([i.ref for i in parts.decode_part(raw).items], [sha(1)])

    # ---- putting files back -----------------------------------------------------------------

    async def restored_copy(self, *, cap: int = 50) -> tuple[str, backup.PictureRestorer]:
        for n in (1, 2, 3):
            await self.keep(n)
        rows = await self.dump()
        copy = await self.campaigns.create(GUILD, "Copy", DM)
        await self.load(rows, copy.id)
        return copy.id, backup.PictureRestorer(self.store, self.bucket, cap, prepare=fake_prepare)

    async def test_a_file_is_read_again_uploaded_under_new_keys_and_fills_its_row(self) -> None:
        cid, restorer = await self.restored_copy()
        part = Part("b" * 32, 1, [PartItem(sha(1), b"file-1"), PartItem(sha(2), b"file-2")])
        done = await restorer.apply(GUILD, cid, part)
        self.assertEqual((done.restored, done.refused, done.full, done.unknown), (2, 0, 0, 0))
        ready = [p for p in await self.store.list(GUILD, cid) if not p.waiting]
        self.assertEqual(len(ready), 2)
        self.assertTrue(all(p.object_key.startswith("p/") for p in ready))
        self.assertEqual(
            len(await self.store.waiting(GUILD, cid)), 1
        )  # (the third has no file yet)
        self.assertEqual(self.bucket.files[ready[0].object_key][:5], b"file-")

    async def test_a_file_that_is_not_a_picture_leaves_its_row_waiting(self) -> None:
        cid, restorer = await self.restored_copy()
        done = await restorer.apply(
            GUILD, cid, Part("b" * 32, 1, [PartItem(sha(1), b"not a picture")])
        )
        self.assertEqual((done.restored, done.refused), (0, 1))
        self.assertEqual(len(await self.store.waiting(GUILD, cid)), 3)

    async def test_a_file_for_no_waiting_picture_of_this_campaign_is_ignored(self) -> None:
        cid, restorer = await self.restored_copy()
        done = await restorer.apply(GUILD, cid, Part("b" * 32, 1, [PartItem(sha(99), b"file-99")]))
        self.assertEqual(done.unknown, 1)
        # (and a picture is filled once: the same file twice does nothing more)
        part = Part("b" * 32, 1, [PartItem(sha(1), b"file-1")])
        await restorer.apply(GUILD, cid, part)
        again = await restorer.apply(GUILD, cid, part)
        self.assertEqual((again.restored, again.unknown), (0, 1))

    async def test_nothing_is_removed_to_make_room_and_the_extra_upload_is_deleted(self) -> None:
        cid, restorer = await self.restored_copy(cap=1)  # one MB; each file stands for ~1 byte
        # Fill the copy's storage with a real 1 MB picture first.
        await self.keep(50, campaign=cid, mb=1, cap=1)
        before = set(self.bucket.files)
        done = await restorer.apply(GUILD, cid, Part("b" * 32, 1, [PartItem(sha(1), b"file-1")]))
        self.assertEqual((done.restored, done.full), (0, 1))
        self.assertEqual(set(self.bucket.files), before)  # (what it uploaded is gone again)
        self.assertEqual(len(await self.store.waiting(GUILD, cid)), 3)
        kept = await self.store.find(GUILD, cid, sha(50))
        assert kept is not None
        self.assertIsNone(kept.deleted_at)
