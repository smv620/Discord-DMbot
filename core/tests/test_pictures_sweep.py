"""The daily sweep of picture files no row points to (#3274): only unreferenced files go, and
nothing is deleted when the rows cannot be trusted. A fake bucket; invented keys."""

from __future__ import annotations

import datetime as dt
import unittest

import psycopg

from dmbot.campaigns import CampaignStore
from dmbot.pictures.bucket import BucketError
from dmbot.pictures.store import MB, PictureStore
from dmbot.pictures.sweep import MAX_DELETES, live_keys, sweep
from tests import pg
from tests.pg import DatabaseTest

DM, POSTER = 7, 8
NOW = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.UTC)
OLD = NOW - dt.timedelta(days=2)


class Bucket:
    def __init__(self, keys: list[str]) -> None:
        self.files = list(keys)
        self.fail_list = False
        self.written: dict[str, dt.datetime] = {}
        self.deleted: list[str] = []

    async def objects(self, prefix: str) -> list[tuple[str, dt.datetime]]:
        if self.fail_list:
            raise BucketError("status 500")
        return [(k, self.written.get(k, OLD)) for k in self.files if k.startswith(prefix)]

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.files.remove(key)


class Sweeping(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = PictureStore(self.db)

    async def keep(self, guild: int, n: int) -> None:
        campaign = await self.campaigns.create(guild, f"C{guild}-{n}", DM)
        added = await self.store.add(
            guild,
            campaign.id,
            poster_id=POSTER,
            session_id="",
            line_id=None,
            message_id=n,
            description="",
            consent_version=7,
            right_granted_at=900,
            sha256=f"{n:064x}",
            object_key=f"p/live{guild}{n}.png",
            preview_key=f"p/live{guild}{n}-preview.webp",
            size_bytes=MB,
            preview_bytes=0,
            storage_mb=5,
        )
        assert added.picture is not None

    async def test_the_keys_of_every_server_are_read_and_only_live_ones(self) -> None:
        await self.keep(1, 1)
        await self.keep(2, 2)
        self.assertEqual(
            await live_keys(self.db),
            {"p/live11.png", "p/live11-preview.webp", "p/live22.png", "p/live22-preview.webp"},
        )

    async def test_a_normal_server_scope_still_cannot_see_other_servers(self) -> None:
        await self.keep(1, 1)
        await self.keep(2, 2)
        self.assertEqual(len(await self.store.list(1, "nope")), 0)
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM campaign_pictures")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM campaign_pictures")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_a_read_only_role_that_sets_the_setting_itself_reads_nothing(self) -> None:
        if not pg.SUPERUSER_URL:
            self.skipTest("needs a superuser to make a role")
        await self.keep(1, 1)
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT current_schema() AS s, current_user AS u")
            row = await cur.fetchone()
        assert row is not None
        schema_name, owner = row["s"], row["u"]
        admin = pg._admin()
        admin.execute(pg.create_role_sql("picture_ro_test", "picture_ro_test"))
        admin.execute(f"GRANT USAGE ON SCHEMA {schema_name} TO picture_ro_test")
        admin.execute(f"GRANT SELECT ON {schema_name}.campaign_pictures TO picture_ro_test")
        admin.execute(f"GRANT picture_ro_test TO {owner}")  # so this connection can act as it
        async with self.db.unscoped() as conn:
            await conn.execute("SET LOCAL ROLE picture_ro_test")
            await conn.execute("SELECT set_config('dmbot.picture_sweep', 'keys', true)")
            await conn.execute("SELECT set_config('dmbot.guild_id', '2', true)")  # another server
            cur = await conn.execute("SELECT count(*) AS n FROM campaign_pictures")
            seen = await cur.fetchone()
        assert seen is not None
        self.assertEqual(seen["n"], 0)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            async with self.db.unscoped() as conn:
                await conn.execute("SET LOCAL ROLE picture_ro_test")
                await conn.execute("SELECT * FROM dmbot_picture_keys()")

    async def test_only_files_no_row_names_are_deleted(self) -> None:
        await self.keep(1, 1)
        bucket = Bucket(["p/live11.png", "p/live11-preview.webp", "p/old1.png", "p/old2.webp"])
        got = await sweep(self.db, bucket, NOW)
        self.assertEqual((got.in_bucket, got.in_rows, got.deleted), (4, 2, 2))
        self.assertEqual(sorted(bucket.files), ["p/live11-preview.webp", "p/live11.png"])

    async def test_a_file_written_a_moment_ago_is_left_alone(self) -> None:
        await self.keep(1, 1)
        bucket = Bucket(["p/live11.png", "p/live11-preview.webp", "p/new.png", "p/old.png"])
        bucket.written["p/new.png"] = NOW - dt.timedelta(minutes=5)  # its row is not written yet
        got = await sweep(self.db, bucket, NOW)
        self.assertEqual(got.deleted, 1)
        self.assertEqual(bucket.deleted, ["p/old.png"])

    async def test_nothing_goes_when_the_bucket_cannot_be_listed(self) -> None:
        await self.keep(1, 1)
        bucket = Bucket(["p/old1.png"])
        bucket.fail_list = True
        got = await sweep(self.db, bucket, NOW)
        self.assertEqual((got.deleted, got.skipped), (0, "listing failed"))
        self.assertEqual(bucket.deleted, [])

    async def test_nothing_goes_when_there_are_files_and_no_rows(self) -> None:
        bucket = Bucket(["p/a.png", "p/b.png"])
        got = await sweep(self.db, bucket, NOW)
        self.assertEqual((got.deleted, got.skipped), (0, "no rows"))
        self.assertEqual(bucket.deleted, [])

    async def test_too_many_leftovers_are_not_deleted_at_once(self) -> None:
        await self.keep(1, 1)
        bucket = Bucket([f"p/x{n}.png" for n in range(MAX_DELETES + 1)] + ["p/live11.png"])
        got = await sweep(self.db, bucket, NOW)
        self.assertEqual((got.deleted, got.skipped), (0, "too many"))

    async def test_a_deleted_picture_is_not_wanted_any_more(self) -> None:
        await self.keep(1, 1)
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT campaign_id, id FROM campaign_pictures")
            row = await cur.fetchone()
        assert row is not None
        await self.store.delete(1, row["campaign_id"], row["id"])
        await self.keep(2, 2)
        bucket = Bucket(["p/live11.png", "p/live22.png", "p/live22-preview.webp"])
        got = await sweep(self.db, bucket, NOW)
        self.assertEqual(got.deleted, 1)
        self.assertEqual(bucket.deleted, ["p/live11.png"])


if __name__ == "__main__":
    unittest.main()
