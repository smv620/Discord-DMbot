"""What each lore upload added for the DM, kept so the notice can be shown, edited and undone
(#3878). One row for each upload that added things by itself (`migrations/0396_upload_notices.py`):
its name and date, the counts by kind, the numbers of the changes it made, and the ids of its live
message in the DM Bot channel (so its buttons survive a restart, #3874). Counts and ids only: no
name and no word of the text is kept here."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from dmbot.db import Database

UNDO_DAYS = 7  # as long as the store lets a change be taken back
UNDO_SECONDS = UNDO_DAYS * 86400
KEEP_DAYS = 30  # a row is forgotten a while after its Undo has gone
LABEL_MAX = 200


@dataclass(frozen=True, slots=True)
class Notice:
    guild_id: int
    campaign_id: str
    id: str
    label: str
    created_at: int
    counts: dict[str, int] = field(default_factory=dict)
    changes: tuple[int, ...] = ()
    channel_id: int | None = None
    message_id: int | None = None
    undone_at: int | None = None

    def can_undo(self, now: int) -> bool:
        """Undo is offered for 7 days, once, and only if the upload changed something."""
        return (
            self.undone_at is None and bool(self.changes) and now - self.created_at < UNDO_SECONDS
        )


class UploadNotices:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def add(
        self,
        guild_id: int,
        campaign_id: str,
        label: str,
        counts: dict[str, int],
        changes: list[int],
    ) -> Notice:
        notice_id = secrets.token_hex(16)
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO upload_notices (guild_id, campaign_id, id, label, created_at,"
                " counts, changes) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)",
                (
                    guild_id,
                    campaign_id,
                    notice_id,
                    label[:LABEL_MAX],
                    now,
                    json.dumps(counts),
                    changes,
                ),
            )
            # (the old ones go with the next new one: counts and ids, small and few)
            await conn.execute(
                "DELETE FROM upload_notices WHERE guild_id = %s AND campaign_id = %s"
                " AND created_at < %s",
                (guild_id, campaign_id, now - (UNDO_DAYS + KEEP_DAYS) * 86400),
            )
        return Notice(
            guild_id, campaign_id, notice_id, label[:LABEL_MAX], now, dict(counts), tuple(changes)
        )

    async def attach(self, guild_id: int, notice_id: str, channel_id: int, message_id: int) -> None:
        """Remember where the live message is."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE upload_notices SET channel_id = %s, message_id = %s"
                " WHERE guild_id = %s AND id = %s",
                (channel_id, message_id, guild_id, notice_id),
            )

    async def get(self, guild_id: int, notice_id: str) -> Notice | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM upload_notices WHERE guild_id = %s AND id = %s",
                (guild_id, notice_id),
            )
            row = await cur.fetchone()
        return None if row is None else _notice(row)

    async def claim_undo(self, guild_id: int, notice_id: str) -> Notice | None:
        """Take the right to undo this upload, once: the notice as it was, or None when it was
        already taken back (or is gone). Two presses at once cannot both win."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE upload_notices SET undone_at = %s WHERE guild_id = %s AND id = %s"
                " AND undone_at IS NULL RETURNING *",
                (int(self._now()), guild_id, notice_id),
            )
            row = await cur.fetchone()
        return None if row is None else _notice(row)

    async def give_back_undo(self, guild_id: int, notice_id: str) -> None:
        """An undo that could not start gives the right back, so the DM may press again."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE upload_notices SET undone_at = NULL WHERE guild_id = %s AND id = %s",
                (guild_id, notice_id),
            )


def _notice(row: dict[str, object]) -> Notice:
    raw = row["counts"]
    counts = raw if isinstance(raw, dict) else json.loads(str(raw or "{}"))
    changes = row["changes"]
    return Notice(
        guild_id=int(str(row["guild_id"])),
        campaign_id=str(row["campaign_id"]),
        id=str(row["id"]),
        label=str(row["label"]),
        created_at=int(str(row["created_at"])),
        counts={str(k): int(v) for k, v in counts.items()},
        changes=tuple(int(x) for x in changes) if isinstance(changes, list) else (),
        channel_id=None if row["channel_id"] is None else int(str(row["channel_id"])),
        message_id=None if row["message_id"] is None else int(str(row["message_id"])),
        undone_at=None if row["undone_at"] is None else int(str(row["undone_at"])),
    )
