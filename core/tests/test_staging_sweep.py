"""The sweep of staged maps nobody picked up (#4347): a file older than a day goes, a younger one
stays, and a bucket that cannot be listed loses nothing. A fake bucket; invented keys."""

from __future__ import annotations

import datetime as dt
import unittest

from dmbot.pictures.bucket import BucketError
from dmbot.pictures.sweep import GRACE_S, MAX_DELETES, STAGING_GRACE_S, sweep_staging

NOW = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.UTC)


class Bucket:
    def __init__(self, written: dict[str, dt.datetime]) -> None:
        self.written = dict(written)
        self.deleted: list[str] = []
        self.fail_list = False
        self.fail_delete: set[str] = set()

    async def objects(self, prefix: str) -> list[tuple[str, dt.datetime]]:
        if self.fail_list:
            raise BucketError("status 500")
        return [(k, w) for k, w in self.written.items() if k.startswith(prefix)]

    async def delete(self, key: str) -> None:
        if key in self.fail_delete:
            raise BucketError("status 500")
        self.deleted.append(key)
        del self.written[key]


class TheStagingSweep(unittest.IsolatedAsyncioTestCase):
    def test_the_grace_is_its_own_day_and_not_the_pictures_hour(self) -> None:
        self.assertEqual(STAGING_GRACE_S, 24 * 3600)
        self.assertNotEqual(STAGING_GRACE_S, GRACE_S)

    async def test_only_a_file_older_than_a_day_goes(self) -> None:
        bucket = Bucket(
            {
                "p/old.png": NOW - dt.timedelta(hours=25),
                "p/old-preview.webp": NOW - dt.timedelta(hours=25),
                "p/fresh.png": NOW - dt.timedelta(hours=23),
                "p/just-now.png": NOW,
            }
        )
        got = await sweep_staging(bucket, NOW)
        self.assertEqual((got.in_bucket, got.deleted), (4, 2))
        self.assertEqual(sorted(bucket.written), ["p/fresh.png", "p/just-now.png"])

    async def test_a_listing_that_fails_deletes_nothing(self) -> None:
        bucket = Bucket({"p/old.png": NOW - dt.timedelta(days=3)})
        bucket.fail_list = True
        got = await sweep_staging(bucket, NOW)
        self.assertEqual((got.deleted, got.skipped), (0, "listing failed"))
        self.assertEqual(bucket.deleted, [])

    async def test_a_delete_that_fails_is_skipped_and_the_rest_go(self) -> None:
        old = NOW - dt.timedelta(days=2)
        bucket = Bucket({"p/a.png": old, "p/b.png": old})
        bucket.fail_delete = {"p/a.png"}
        got = await sweep_staging(bucket, NOW)
        self.assertEqual(got.deleted, 1)
        self.assertEqual(bucket.deleted, ["p/b.png"])

    async def test_too_many_stale_files_at_once_is_left_alone(self) -> None:
        old = NOW - dt.timedelta(days=2)
        bucket = Bucket({f"p/{n}.png": old for n in range(MAX_DELETES + 1)})
        got = await sweep_staging(bucket, NOW)
        self.assertEqual((got.deleted, got.skipped), (0, "too many"))
