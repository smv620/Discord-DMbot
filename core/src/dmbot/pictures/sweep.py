"""The daily sweep of picture files no row points to (#3274).

A file is left behind when a delete failed (the bucket was down, the process stopped between the
upload and the row). Every key is random and lives under `p/`, so the sweep lists that folder and
deletes the files that no live row names. It refuses to act when it cannot trust what it read: a
failed or empty read of the rows while the bucket holds files, or a listing that errors, means
nothing is deleted. Only counts are logged, never a key. A file younger than `GRACE_S` is left
alone, because an upload puts its files before it writes its row.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Protocol

from dmbot.db import Database
from dmbot.pictures.bucket import BucketError
from dmbot.pictures.keeper import PREFIX

log = logging.getLogger(__name__)

GRACE_S = 3600  # a file younger than this is left alone: an upload puts its files before its row
MAX_DELETES = 500  # in one run: a sweep that finds more than this stops and says so


class SweepBucket(Protocol):
    async def objects(self, prefix: str) -> list[tuple[str, dt.datetime]]: ...
    async def delete(self, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Swept:
    in_bucket: int
    in_rows: int
    deleted: int
    skipped: str = ""  # why nothing was deleted, if so


async def live_keys(db: Database) -> set[str]:
    async with db.unscoped() as conn:  # no server is set: the function reads across them
        cur = await conn.execute("SELECT object_key, preview_key FROM dmbot_picture_keys()")
        rows = await cur.fetchall()
    keys: set[str] = set()
    for row in rows:
        keys.add(str(row["object_key"]))
        if row["preview_key"]:
            keys.add(str(row["preview_key"]))
    return keys


async def sweep(db: Database, bucket: SweepBucket, now: dt.datetime | None = None) -> Swept:
    now = now or dt.datetime.now(dt.UTC)
    try:
        found = await bucket.objects(PREFIX)
    except BucketError as exc:
        log.warning("Pictures sweep: couldn't list the bucket (%s)", exc)
        return Swept(0, 0, 0, "listing failed")
    in_bucket = [key for key, _ in found]
    try:
        wanted = await live_keys(db)
    except Exception as exc:  # the rows could not be read: delete nothing
        log.warning("Pictures sweep: couldn't read the rows (%s)", type(exc).__name__)
        return Swept(len(in_bucket), 0, 0, "rows unreadable")
    if in_bucket and not wanted:
        # Every file would look like a leftover: more likely the read is wrong than the bucket.
        log.warning("Pictures sweep: %d files and no rows, so nothing is deleted", len(in_bucket))
        return Swept(len(in_bucket), 0, 0, "no rows")
    cutoff = now - dt.timedelta(seconds=GRACE_S)
    stray = [key for key, written in found if key not in wanted and written < cutoff]
    if len(stray) > MAX_DELETES:
        log.warning("Pictures sweep: %d leftover files is too many to delete at once", len(stray))
        return Swept(len(in_bucket), len(wanted), 0, "too many")
    deleted = 0
    for key in stray:
        try:
            await bucket.delete(key)
            deleted += 1
        except BucketError as exc:
            log.warning("Pictures sweep: couldn't delete a file (%s)", exc)
    log.info(
        "Pictures sweep: %d files, %d in rows, %d leftover deleted",
        len(in_bucket),
        len(wanted),
        deleted,
    )
    return Swept(len(in_bucket), len(wanted), deleted)


# A staged map (the website's Maps page, #4347) waits for the bot only a short while. Its own
# constant, never `GRACE_S`: that one is how long an upload may take between its files and its row,
# this one is how long a staged file may wait for the bot (Supervisor, 2026-11-15). The staging
# bucket's own one-day lifecycle rule does the same job; this is the second pair of eyes for the
# files that rule missed (a rule that was never switched on).
STAGING_GRACE_S = 86400


async def sweep_staging(bucket: SweepBucket, now: dt.datetime | None = None) -> Swept:
    """Delete the staged files older than `STAGING_GRACE_S`. No database is read: a staged file is
    never named by a row (the bot keeps a copy in the pictures bucket and deletes the staged one),
    so age is the only test. A listing that errors deletes nothing. Counts only in the log."""
    now = now or dt.datetime.now(dt.UTC)
    try:
        found = await bucket.objects(PREFIX)
    except BucketError as exc:
        log.warning("Staging sweep: couldn't list the bucket (%s)", exc)
        return Swept(0, 0, 0, "listing failed")
    cutoff = now - dt.timedelta(seconds=STAGING_GRACE_S)
    stale = [key for key, written in found if written < cutoff]
    if len(stale) > MAX_DELETES:
        log.warning("Staging sweep: %d stale files is too many to delete at once", len(stale))
        return Swept(len(found), 0, 0, "too many")
    deleted = 0
    for key in stale:
        try:
            await bucket.delete(key)
            deleted += 1
        except BucketError as exc:
            log.warning("Staging sweep: couldn't delete a file (%s)", exc)
    log.info("Staging sweep: %d files, %d stale deleted", len(found), deleted)
    return Swept(len(found), 0, deleted)
