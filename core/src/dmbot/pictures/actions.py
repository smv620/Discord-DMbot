"""Applying what a poster or a DM chose on the website's Pictures page (#3274).

The website queues a choice (`picture_actions`); the bot applies it here through `PictureStore`, so
the per-campaign lock, the 80% permanent limit and the file deletion stay in one place and the web
side never needs a write right on the bucket or the picture table. Who may do what is checked
again here, from the database and not from the row: the poster or a campaign's DM may make a
picture private or public or delete it; only a DM may mark it permanent (on the website only). The
answer is plain words the page shows. Nothing here logs a key or a picture's words.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.pictures.bucket import BucketError
from dmbot.pictures.keeper import BucketLike
from dmbot.pictures.store import PictureStore

log = logging.getLogger(__name__)

GONE = "That picture isn't kept any more."
NOT_YOURS = "Only the person who posted it or a DM can do that."
DMS_ONLY = "Only a DM can do that."
DONE = {
    "public": "Anyone with the link can see it now.",
    "private": "It is private to the campaign now.",
    "permanent": "It is permanent now.",
    "unpermanent": "It is no longer permanent.",
    "delete": "It is deleted.",
}
EXPIRES_S = 3600  # a queued choice not applied within an hour is dropped
RECLAIM_S = 300  # a choice a worker took and never finished is taken again after this


@dataclass(frozen=True, slots=True)
class Outcome:
    state: str  # done, refused or failed
    note: str


class PictureActions:
    def __init__(
        self,
        db: Database,
        store: PictureStore,
        bucket: BucketLike,
        storage_mb: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._store = store
        self._bucket = bucket
        self._storage_mb = storage_mb
        self._clock = clock

    async def _is_dm(self, guild_id: int, campaign_id: str, user_id: int) -> bool:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s",
                (guild_id, campaign_id, user_id),
            )
            return await cur.fetchone() is not None

    async def apply(
        self, guild_id: int, campaign_id: str, user_id: int, picture_id: int, action: str
    ) -> Outcome:
        picture = await self._store.get(guild_id, campaign_id, picture_id)
        if picture is None or picture.gone:
            return Outcome("refused", GONE)
        dm = await self._is_dm(guild_id, campaign_id, user_id)
        poster = picture.poster_id == user_id
        if action in ("permanent", "unpermanent"):
            if not dm:
                return Outcome("refused", DMS_ONLY)
        elif not (dm or poster):
            return Outcome("refused", NOT_YOURS)
        if action in ("public", "private"):
            ok = await self._store.set_public(guild_id, campaign_id, picture_id, action == "public")
            return Outcome("done", DONE[action]) if ok else Outcome("refused", GONE)
        if action in ("permanent", "unpermanent"):
            said = await self._store.set_permanent(
                guild_id, campaign_id, picture_id, action == "permanent", self._storage_mb
            )
            return Outcome("refused", said) if said else Outcome("done", DONE[action])
        if action == "delete":
            gone = await self._store.delete(guild_id, campaign_id, picture_id)
            if gone is None:
                return Outcome("refused", GONE)
            for key in (gone.object_key, gone.preview_key):
                try:
                    if key:
                        await self._bucket.delete(key)
                except BucketError as exc:  # the daily sweep finds a file left behind
                    log.warning("Pictures: couldn't delete a file (%s)", exc)
            return Outcome("done", DONE[action])
        return Outcome("failed", "DMbot didn't understand that.")

    async def take(self, guild_id: int) -> dict[str, Any] | None:
        """Claim this server's oldest waiting choice, after dropping the ones past an hour."""
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM picture_actions WHERE guild_id = %s AND created_at < %s",
                (guild_id, now - EXPIRES_S),
            )
            cur = await conn.execute(
                "UPDATE picture_actions SET state = 'working', claimed_at = %s"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM picture_actions"
                " WHERE guild_id = %s AND (state = 'waiting'"
                " OR (state = 'working' AND claimed_at < %s)) ORDER BY seq LIMIT 1"
                " FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id, picture_id, action",
                (now, guild_id, now - RECLAIM_S),
            )
            row = await cur.fetchone()
        return None if row is None else dict(row)

    async def finish(self, guild_id: int, action_id: str, outcome: Outcome) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE picture_actions SET state = %s, note = %s WHERE guild_id = %s AND id = %s",
                (outcome.state, outcome.note[:300], guild_id, action_id),
            )

    async def run_once(self, guild_id: int) -> int:
        """Apply this server's waiting choices, oldest first. How many were handled."""
        handled = 0
        while (row := await self.take(guild_id)) is not None:
            try:
                outcome = await self.apply(
                    guild_id,
                    str(row["campaign_id"]),
                    int(row["user_id"]),
                    int(row["picture_id"]),
                    str(row["action"]),
                )
            except Exception as exc:  # one bad row must not stop the rest
                log.warning("Pictures: couldn't apply a choice (%s)", type(exc).__name__)
                outcome = Outcome("failed", "DMbot couldn't do that this time.")
            await self.finish(guild_id, str(row["id"]), outcome)
            handled += 1
        return handled


__all__ = ["Outcome", "PictureActions"]
