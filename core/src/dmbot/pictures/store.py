"""The rows of kept pictures, and the two storage limits that guard them (#3274).

A picture is kept with its campaign (docs/PLAN.md, "Pictures are kept, and shared by link"). This
store holds the rows only; the files are in the private bucket (`pictures/storage.py`, a later
step) and the caller deletes the keys this store hands back. Two limits are kept here, in one
transaction with a lock per campaign, so two uploads or two marks at once cannot break either:

* **Rolling storage:** a campaign holds at most `storage_mb` of pictures (previews count; a picture
  posted twice is stored once). When a new one would not fit, the oldest pictures that are not
  permanent go first, never a permanent one.
* **Permanent:** at most 80% of `storage_mb` may be permanent. The limit is computed from the
  setting, never written as a number.

Every call is scoped to one server and one campaign. The only lookup by link code, which is how the
public door resolves `/pictures/<code>`, is the web door's own function (WebDev), not this store.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Conn, Database

log = logging.getLogger(__name__)

MB = 1024 * 1024
PERMANENT_SHARE = 0.8  # of the storage, owner decision 2026-11-02
CODE_BYTES = 32  # 256 bits in the link code

COLUMNS = (
    "id, campaign_id, poster_id, session_id, line_id, message_id, description, code, public,"
    " permanent, sha256, object_key, preview_key, size_bytes, preview_bytes, made_at, deleted_at,"
    " removed_for_room"
)


@dataclass(frozen=True, slots=True)
class Picture:
    id: int
    campaign_id: str
    poster_id: int
    session_id: str
    line_id: int | None
    message_id: int
    description: str
    code: str
    public: bool
    permanent: bool
    sha256: str
    object_key: str
    preview_key: str
    size_bytes: int
    preview_bytes: int
    made_at: int
    deleted_at: int | None
    removed_for_room: bool

    @property
    def gone(self) -> bool:
        return self.deleted_at is not None

    @property
    def stored_bytes(self) -> int:
        return self.size_bytes + self.preview_bytes


@dataclass(frozen=True, slots=True)
class Added:
    """What `add` did. `picture` is None when it did not fit even after the room that could be
    made (everything left is permanent, or it is bigger than the storage)."""

    picture: Picture | None
    new: bool  # False when the same file was already kept in this campaign
    removed: tuple[Picture, ...]  # pictures removed for room; the caller deletes their files


@dataclass(frozen=True, slots=True)
class Usage:
    used_bytes: int
    permanent_bytes: int
    limit_bytes: int
    permanent_limit_bytes: int


def permanent_limit_bytes(storage_mb: int) -> int:
    """80% of the storage, from the setting."""
    return int(storage_mb * MB * PERMANENT_SHARE)


def new_code() -> str:
    return secrets.token_urlsafe(CODE_BYTES)


def _picture(row: Any) -> Picture:
    return Picture(
        id=int(row["id"]),
        campaign_id=str(row["campaign_id"]),
        poster_id=int(row["poster_id"]),
        session_id=str(row["session_id"]),
        line_id=None if row["line_id"] is None else int(row["line_id"]),
        message_id=int(row["message_id"]),
        description=str(row["description"]),
        code=str(row["code"]),
        public=bool(row["public"]),
        permanent=bool(row["permanent"]),
        sha256=str(row["sha256"]),
        object_key=str(row["object_key"]),
        preview_key=str(row["preview_key"]),
        size_bytes=int(row["size_bytes"]),
        preview_bytes=int(row["preview_bytes"]),
        made_at=int(row["made_at"]),
        deleted_at=None if row["deleted_at"] is None else int(row["deleted_at"]),
        removed_for_room=bool(row["removed_for_room"]),
    )


async def _lock(conn: Conn, guild_id: int, campaign_id: str) -> None:
    """One change at a time per campaign, so both limits are exact."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"campaign_pictures:{guild_id}:{campaign_id}",),
    )


async def _usage(conn: Conn, guild_id: int, campaign_id: str, storage_mb: int) -> Usage:
    cur = await conn.execute(
        "SELECT COALESCE(SUM(size_bytes + preview_bytes), 0) AS used,"
        " COALESCE(SUM(size_bytes + preview_bytes) FILTER (WHERE permanent), 0) AS perm"
        " FROM campaign_pictures WHERE guild_id = %s AND campaign_id = %s AND deleted_at IS NULL",
        (guild_id, campaign_id),
    )
    row = await cur.fetchone()
    assert row is not None
    return Usage(
        int(row["used"]),
        int(row["perm"]),
        storage_mb * MB,
        permanent_limit_bytes(storage_mb),
    )


class PictureStore:
    """A campaign's kept pictures."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def add(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        poster_id: int,
        session_id: str,
        line_id: int | None,
        message_id: int,
        description: str,
        consent_version: int,
        right_granted_at: int,
        sha256: str,
        object_key: str,
        preview_key: str,
        size_bytes: int,
        preview_bytes: int,
        storage_mb: int,
    ) -> Added:
        """Keep a picture. The same file already kept in this campaign is returned as it is
        (`new` False) and nothing changes; a removed one is kept again with a new link. When it
        would not fit, the oldest pictures that are not permanent are removed first, in the same
        transaction."""
        need = size_bytes + preview_bytes
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            await _lock(conn, guild_id, campaign_id)
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_pictures"
                " WHERE guild_id = %s AND campaign_id = %s AND sha256 = %s",
                (guild_id, campaign_id, sha256),
            )
            same = await cur.fetchone()
            if same is not None and same["deleted_at"] is None:
                return Added(_picture(same), False, ())
            limit = storage_mb * MB
            if need > limit:
                return Added(None, False, ())
            used = (await _usage(conn, guild_id, campaign_id, storage_mb)).used_bytes
            removed: list[Picture] = []
            if used + need > limit:
                cur = await conn.execute(
                    f"SELECT {COLUMNS} FROM campaign_pictures"
                    " WHERE guild_id = %s AND campaign_id = %s AND deleted_at IS NULL"
                    " AND NOT permanent ORDER BY id",
                    (guild_id, campaign_id),
                )
                for row in await cur.fetchall():
                    if used + need <= limit:
                        break
                    old = _picture(row)
                    removed.append(old)
                    used -= old.stored_bytes
                if used + need > limit:  # nothing more may go: keep nothing, change nothing
                    return Added(None, False, ())
                await conn.execute(
                    "UPDATE campaign_pictures SET deleted_at = %s, removed_for_room = TRUE,"
                    " description = '' WHERE guild_id = %s AND campaign_id = %s AND id = ANY(%s)",
                    (now, guild_id, campaign_id, [p.id for p in removed]),
                )
            if same is not None:  # gone before: its row makes way for a new link, but only now
                # that the picture fits, so a refusal leaves the old link saying it was removed
                await conn.execute("DELETE FROM campaign_pictures WHERE id = %s", (same["id"],))
            cur = await conn.execute(
                "INSERT INTO campaign_pictures (guild_id, campaign_id, poster_id, session_id,"
                " line_id, message_id, description, code, consent_version, right_granted_at,"
                " sha256, object_key, preview_key, size_bytes, preview_bytes, made_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                f" RETURNING {COLUMNS}",
                (
                    guild_id,
                    campaign_id,
                    poster_id,
                    session_id,
                    line_id,
                    message_id,
                    description[:2500],
                    new_code(),
                    consent_version,
                    right_granted_at,
                    sha256,
                    object_key,
                    preview_key,
                    size_bytes,
                    preview_bytes,
                    now,
                ),
            )
            made = await cur.fetchone()
            assert made is not None
            return Added(_picture(made), True, tuple(removed))

    async def find(self, guild_id: int, campaign_id: str, sha256: str) -> Picture | None:
        """The kept picture of this exact file in this campaign, if any (not a removed one)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_pictures WHERE guild_id = %s AND campaign_id = %s"
                " AND sha256 = %s AND deleted_at IS NULL",
                (guild_id, campaign_id, sha256),
            )
            row = await cur.fetchone()
        return None if row is None else _picture(row)

    async def by_message(self, guild_id: int, message_id: int) -> list[Picture]:
        """The kept pictures posted in this Discord message (not removed ones). Message id 0 is a
        restored picture's, which has no message."""
        if message_id <= 0:
            return []
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_pictures WHERE guild_id = %s AND message_id = %s"
                " AND deleted_at IS NULL",
                (guild_id, message_id),
            )
            return [_picture(r) for r in await cur.fetchall()]

    async def by_id(self, guild_id: int, picture_id: int) -> Picture | None:
        """One picture of this server by its id (a button carries the id, not the campaign). The
        row-level security of the server still applies, so another server's id finds nothing."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_pictures WHERE guild_id = %s AND id = %s",
                (guild_id, picture_id),
            )
            row = await cur.fetchone()
        return None if row is None else _picture(row)

    async def get(self, guild_id: int, campaign_id: str, picture_id: int) -> Picture | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_pictures"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, picture_id),
            )
            row = await cur.fetchone()
        return None if row is None else _picture(row)

    async def list(
        self, guild_id: int, campaign_id: str, *, before: int = 0, limit: int = 60
    ) -> list[Picture]:
        """Kept pictures, newest first; `before` is the last id seen. Removed ones are left out."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_pictures"
                " WHERE guild_id = %s AND campaign_id = %s AND deleted_at IS NULL"
                " AND (%s = 0 OR id < %s) ORDER BY id DESC LIMIT %s",
                (guild_id, campaign_id, before, before, max(1, min(limit, 200))),
            )
            return [_picture(r) for r in await cur.fetchall()]

    async def usage(self, guild_id: int, campaign_id: str, storage_mb: int) -> Usage:
        async with self._db.guild(guild_id) as conn:
            return await _usage(conn, guild_id, campaign_id, storage_mb)

    async def set_public(
        self, guild_id: int, campaign_id: str, picture_id: int, public: bool
    ) -> bool:
        """Make a picture public (anyone with the link) or private to the campaign. False when it
        is not kept here."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE campaign_pictures SET public = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s AND deleted_at IS NULL",
                (public, guild_id, campaign_id, picture_id),
            )
            return bool(cur.rowcount)

    async def set_permanent(
        self, guild_id: int, campaign_id: str, picture_id: int, permanent: bool, storage_mb: int
    ) -> str | None:
        """Mark or unmark a picture permanent. None when done; otherwise the words to show:
        refused when permanent pictures would pass 80% of the storage. Who may do this (a DM,
        on the website) is the caller's check."""
        async with self._db.guild(guild_id) as conn:
            await _lock(conn, guild_id, campaign_id)
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_pictures"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND deleted_at IS NULL",
                (guild_id, campaign_id, picture_id),
            )
            row = await cur.fetchone()
            if row is None:
                return "That picture isn't kept any more."
            pic = _picture(row)
            if not permanent:  # a map's picture stays kept while it is a map (#4344)
                cur = await conn.execute(
                    "SELECT 1 FROM campaign_maps WHERE guild_id = %s AND campaign_id = %s"
                    " AND picture_id = %s",
                    (guild_id, campaign_id, picture_id),
                )
                if await cur.fetchone() is not None:
                    return "It's a map, so it stays. Remove the map first."
            if permanent and not pic.permanent:
                usage = await _usage(conn, guild_id, campaign_id, storage_mb)
                if usage.permanent_bytes + pic.stored_bytes > usage.permanent_limit_bytes:
                    return (
                        f"Permanent pictures are full ({usage.permanent_limit_bytes // MB} MB). "
                        "Unmark one to make room."
                    )
            await conn.execute(
                "UPDATE campaign_pictures SET permanent = %s WHERE id = %s", (permanent, pic.id)
            )
            return None

    async def mark_map_asked(self, guild_id: int, campaign_id: str, picture_id: int) -> None:
        """The poster was just asked whether this picture is a map they may use (#4344). Set once;
        asking again for the same picture does not move it."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_pictures SET map_asked_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s AND map_asked_at IS NULL",
                (int(self._clock()), guild_id, campaign_id, picture_id),
            )

    async def unanswered_map_questions(self, guild_id: int, campaign_id: str) -> Sequence[Picture]:
        """Kept pictures whose poster was asked about a map, was not reminded yet, and has no map
        for it: the ones the one reminder at the next session start is for (#4344)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"""SELECT {COLUMNS} FROM campaign_pictures p
                    WHERE guild_id = %s AND campaign_id = %s AND deleted_at IS NULL
                      AND map_asked_at IS NOT NULL AND map_reminded_at IS NULL
                      AND NOT EXISTS (SELECT 1 FROM campaign_maps m
                                      WHERE m.guild_id = p.guild_id
                                        AND m.campaign_id = p.campaign_id
                                        AND m.picture_id = p.id)
                    ORDER BY id""",
                (guild_id, campaign_id),
            )
            return [_picture(r) for r in await cur.fetchall()]

    async def mark_map_reminded(self, guild_id: int, campaign_id: str, picture_id: int) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_pictures SET map_reminded_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s AND map_reminded_at IS NULL",
                (int(self._clock()), guild_id, campaign_id, picture_id),
            )

    async def mark_map_taken_back(
        self, guild_id: int, campaign_id: str, picture_id: int, batch: int
    ) -> None:
        """The poster just took their map back, as this change-log batch (#4344): the one batch
        their own Undo accepts (`taken_back_by`). A later take-back replaces it."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_pictures SET map_taken_back_batch = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (batch, guild_id, campaign_id, picture_id),
            )

    async def taken_back_by(
        self, guild_id: int, campaign_id: str, batch: int, user_id: int
    ) -> bool:
        """Whether this batch is a take-back this person made of their own picture's map: the guard
        on the poster's Undo, so a card forwarded to someone else, a made-up batch, or a batch that
        was a DM's Remove, Yes or No undoes nothing."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM campaign_pictures WHERE guild_id = %s AND campaign_id = %s"
                " AND map_taken_back_batch = %s AND poster_id = %s AND deleted_at IS NULL",
                (guild_id, campaign_id, batch, user_id),
            )
            return await cur.fetchone() is not None

    async def delete(
        self, guild_id: int, campaign_id: str, picture_id: int, *, unless_map: bool = False
    ) -> Picture | None:
        """Delete a picture: the row keeps its link (so it can say it was removed), the
        description goes. Returns what was kept so the caller deletes the files; None when it was
        not kept here or already gone."""
        async with self._db.guild(guild_id) as conn:
            await _lock(conn, guild_id, campaign_id)
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_pictures"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND deleted_at IS NULL",
                (guild_id, campaign_id, picture_id),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            if unless_map:  # (a poster's no must not take a map their own yes just made, #4344)
                cur = await conn.execute(
                    "SELECT 1 FROM campaign_maps WHERE guild_id = %s AND campaign_id = %s"
                    " AND picture_id = %s",
                    (guild_id, campaign_id, picture_id),
                )
                if await cur.fetchone() is not None:
                    return None
            # A picture that is gone is no map either (#4344: a poster who deletes their picture
            # takes its map with it, in the same step).
            cur = await conn.execute(
                "DELETE FROM campaign_maps WHERE guild_id = %s AND campaign_id = %s"
                " AND picture_id = %s RETURNING id",
                (guild_id, campaign_id, picture_id),
            )
            gone = [str(r["id"]) for r in await cur.fetchall()]
            if gone:  # its change-log rows hold the uploader's id and its title: nothing is left
                await conn.execute(
                    "DELETE FROM memory_changes WHERE guild_id = %s AND campaign_id = %s"
                    " AND table_name = 'campaign_maps' AND row_id = ANY(%s)",
                    (guild_id, campaign_id, gone),
                )
            await conn.execute(
                "UPDATE campaign_pictures SET deleted_at = %s, permanent = FALSE, description = ''"
                " WHERE id = %s",
                (int(self._clock()), picture_id),
            )
            return _picture(row)
