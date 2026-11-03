"""Kept pictures in a campaign's backup (#3274).

Two halves. **The section** (`PicturesSection`) puts the picture *rows* in the main file: who
posted each, its link, its words, public or private, permanent or not, and the poster's own consent
record. Nothing that means something only here goes in: no bucket keys, no Discord message or
session numbers. On restore each row comes back as *waiting for its file*, so its link, poster and
permanent mark survive even when the file cannot come back at once.

**The files** travel in numbered parts (`pictures/parts.py`), the permanent pictures first and then
the newest, so a restore that can take only some files brings back what matters most. A restore
(`PictureRestorer`) reads each picture again through `prepare` (no byte of a backup is trusted),
uploads it under new random keys and fills in the waiting row it belongs to. What does not fit the
storage is left waiting, never removed to make room.

The link code is kept when it is free and a new one made when it is not (a restore next to the
original, or in another server, must never share a link with the campaign it was copied from).
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg import errors as pg_errors

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.pictures.bucket import BucketError
from dmbot.pictures.keeper import BucketLike, new_keys
from dmbot.pictures.parts import (
    PICTURE_READ_BYTES,
    Part,
    PartItem,
    encode_part,
    groups,
)
from dmbot.pictures.prepare import Prepared, Refused, prepare_async
from dmbot.pictures.store import PictureStore, new_code

log = logging.getLogger(__name__)

DAMAGED = "This backup file is damaged (bad picture entry)."
ROWS_MAX = 20000
INT64_MAX = 2**63 - 1
_SHA = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[A-Za-z0-9_-]{43,64}")


class BucketReader(BucketLike, Protocol):
    async def get(self, key: str) -> bytes: ...


# ---- the section: rows in the main file -------------------------------------------------------


class PicturesSection:
    name = "pictures"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        """Permanent first, then newest first: the order the files are put in the parts, and so
        the order a restore that cannot take them all brings them back."""
        cur = await conn.execute(
            "SELECT sha256, poster_id, description, code, public, permanent, consent_version,"
            " right_granted_at, made_at, size_bytes, restore_state FROM campaign_pictures"
            " WHERE guild_id = %s AND campaign_id = %s AND deleted_at IS NULL"
            " ORDER BY permanent DESC, id DESC",
            (guild_id, campaign_id),
        )
        return [
            {
                "sha256": r["sha256"],
                "poster": str(r["poster_id"]),
                "description": r["description"],
                "code": r["code"],
                "public": bool(r["public"]),
                "permanent": bool(r["permanent"]),
                "consent_version": int(r["consent_version"]),
                "right_granted_at": int(r["right_granted_at"]),
                "made_at": int(r["made_at"]),
                "size": int(r["size_bytes"]),
                "file": r["restore_state"] == "ready",  # (false: its file was not back yet)
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        """Every field from the untrusted file, checked (in a worker thread). Pure."""
        if len(rows) > ROWS_MAX:
            raise CampaignError(DAMAGED)
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for row in rows:
            got = row if isinstance(row, dict) else {}
            sha, poster, text, code = (
                got.get(k) for k in ("sha256", "poster", "description", "code")
            )
            size, made = got.get("size"), got.get("made_at")
            version, granted = got.get("consent_version"), got.get("right_granted_at")
            flags = [got.get(k) for k in ("public", "permanent", "file")]
            if not (
                isinstance(sha, str)
                and _SHA.fullmatch(sha)
                and sha not in seen
                and isinstance(poster, str)
                and poster.isascii()
                and poster.isdigit()
                and 0 < int(poster) <= INT64_MAX
                and isinstance(text, str)
                and len(text) <= 2500
                and isinstance(code, str)
                and _CODE.fullmatch(code)
                and all(type(f) is bool for f in flags)
                and type(size) is int
                and type(made) is int
                and type(version) is int
                and type(granted) is int
                and 0 < size <= PICTURE_READ_BYTES
                and 0 <= made <= INT64_MAX
                and 7 <= version <= 1000
                and 0 < granted <= INT64_MAX
            ):
                raise CampaignError(DAMAGED)
            seen.add(sha)
            out.append(
                {
                    "sha256": sha,
                    "poster": int(poster),
                    "description": text,
                    "code": code,
                    "public": flags[0],
                    "permanent": flags[1],
                    "consent_version": version,
                    "right_granted_at": granted,
                    "made_at": made,
                    "size": size,
                }
            )
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        """Every row comes back waiting for its file. `rows` as `check` returned them, or straight
        from a file."""
        checked = (
            rows if all("poster" in r and isinstance(r["poster"], int) for r in rows) else None
        )
        for row in checked if checked is not None else self.check(rows):
            await self._insert(conn, guild_id, campaign_id, row)

    async def _insert(
        self, conn: Conn, guild_id: int, campaign_id: str, row: dict[str, Any]
    ) -> None:
        code = row["code"]
        for attempt in range(4):
            try:
                async with (
                    conn.transaction()
                ):  # (a savepoint: a taken link must not end the restore)
                    await conn.execute(
                        "INSERT INTO campaign_pictures (guild_id, campaign_id, poster_id,"
                        " description, code, public, permanent, consent_version, right_granted_at,"
                        " sha256, object_key, size_bytes, made_at, restore_state)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', %s, %s, 'waiting')",
                        (
                            guild_id,
                            campaign_id,
                            row["poster"],
                            row["description"],
                            code,
                            row["public"],
                            row["permanent"],
                            row["consent_version"],
                            row["right_granted_at"],
                            row["sha256"],
                            row["size"],
                            row["made_at"],
                        ),
                    )
                return
            except pg_errors.UniqueViolation as exc:
                # The link is used by another campaign (the original, usually): this one gets its
                # own. The same file twice in a campaign cannot happen (`check` refuses it).
                if "code" not in str(exc) or attempt == 3:
                    raise CampaignError(DAMAGED) from exc
                code = new_code()

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        # The files are left for the daily sweep (no row names them any more, so they go after
        # its hour of grace): a restore that rolls back must never have deleted a file whose row
        # is still there.
        await conn.execute(
            "DELETE FROM campaign_pictures WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


# ---- making the parts -------------------------------------------------------------------------


def plan(rows: Sequence[dict[str, Any]], part_bytes: int) -> list[list[dict[str, Any]]]:
    """Which listed pictures go in which part: only those with a file, in the list's order, never
    splitting a picture. Pure, so the main file can say how many parts there are before any file
    is read."""
    with_file = [r for r in rows if r.get("file")]
    return [
        [with_file[i] for i in group]
        for group in groups([r["size"] for r in with_file], part_bytes)
    ]


async def make_parts(
    store: PictureStore,
    bucket: BucketReader,
    guild_id: int,
    campaign_id: str,
    rows: Sequence[dict[str, Any]],
    backup_id: str,
    part_bytes: int,
) -> AsyncIterator[bytes]:
    """The part files, one at a time (so only one part's pictures are held at once). A picture
    whose file is gone from the bucket is left out of its part; the main file still lists it, and a
    restore brings it back waiting."""
    for number, group in enumerate(plan(rows, part_bytes), 1):
        items: list[PartItem] = []
        for row in group:
            picture = await store.find(guild_id, campaign_id, row["sha256"])
            if picture is None or picture.gone or picture.waiting:
                continue
            try:
                items.append(PartItem(row["sha256"], await bucket.get(picture.object_key)))
            except BucketError as exc:
                log.warning("Pictures: a file could not be read for a backup (%s)", exc)
        yield encode_part(backup_id, number, items)


# ---- putting the files back -------------------------------------------------------------------


@dataclass(slots=True)
class Restored:
    """Counts only: what a restore did with the pictures in the parts it was given."""

    restored: int = 0
    refused: int = 0  # a file that was not a picture DMbot would keep
    full: int = 0  # would pass the storage: left waiting
    unknown: int = 0  # not a picture of this campaign still waiting for its file

    def add(self, other: Restored) -> None:
        self.restored += other.restored
        self.refused += other.refused
        self.full += other.full
        self.unknown += other.unknown


class PictureRestorer:
    def __init__(
        self,
        store: PictureStore,
        bucket: BucketLike,
        storage_mb: int,
        prepare: Any = prepare_async,
    ) -> None:
        self._store = store
        self._bucket = bucket
        self._storage_mb = storage_mb
        self._prepare = prepare

    async def apply(self, guild_id: int, campaign_id: str, part: Part) -> Restored:
        done = Restored()
        for item in part.items:
            picture = await self._store.find(guild_id, campaign_id, item.ref)
            if picture is None or picture.gone or not picture.waiting:
                done.unknown += 1
                continue
            try:
                ready: Prepared = await self._prepare(item.data)
            except Refused:
                done.refused += 1
                continue
            key, preview_key = new_keys(ready.ext)
            try:
                await self._bucket.put(key, ready.data)
                await self._bucket.put(preview_key, ready.preview)
                outcome = await self._store.fill(
                    guild_id,
                    campaign_id,
                    picture.id,
                    object_key=key,
                    preview_key=preview_key,
                    size_bytes=len(ready.data),
                    preview_bytes=len(ready.preview),
                    storage_mb=self._storage_mb,
                )
            except BucketError as exc:
                log.warning("Pictures: a restore could not upload a file (%s)", exc)
                outcome = "failed"
            if outcome == "filled":
                done.restored += 1
                continue
            for stray in (key, preview_key):  # not needed: the sweep would find them, but be tidy
                with contextlib.suppress(BucketError):
                    await self._bucket.delete(stray)
            if outcome == "full":
                done.full += 1
            elif outcome == "gone":
                done.unknown += 1
            else:
                done.refused += 1
        return done
