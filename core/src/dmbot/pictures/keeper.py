"""Keeping a posted picture (#3274): prepare it, put it in the bucket, write its row.

The one place the pieces meet: `prepare` (no metadata, a preview, size limits), the bucket (random
keys `p/<128 random bits>.<ext>` and `p/<same>-preview.webp`, so the public link's redirect shows
nothing about the server, campaign or file), and `PictureStore` (the row, the rolling storage, the
permanent limit). Nothing here decides *whether* a picture may be kept (consent, the switch, the
plan are the caller's); it only does it safely and says what happened, so the caller can still
describe a picture that was not kept. Errors carry a short reason, never a key or a word of the
picture. A file posted twice in one campaign is kept once, and nothing is uploaded for the second.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot.pictures.bucket import BucketError
from dmbot.pictures.prepare import Prepared, Refused, prepare_async
from dmbot.pictures.store import Picture, PictureStore

log = logging.getLogger(__name__)

PREFIX = "p/"


class BucketLike(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def delete(self, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Context:
    """Where a picture came from and what the poster agreed to."""

    guild_id: int
    campaign_id: str
    poster_id: int
    session_id: str
    line_id: int | None
    message_id: int
    description: str
    consent_version: int
    right_granted_at: int


@dataclass(frozen=True, slots=True)
class Result:
    picture: Picture | None  # None: not kept
    new: bool = False  # False when the same file was already kept
    removed: int = 0  # older pictures removed to make room (the DMs are told once)
    why_not: str = ""  # "refused", "full" or "failed" when not kept


def new_keys(ext: str) -> tuple[str, str]:
    token = secrets.token_hex(16)  # 128 random bits
    return f"{PREFIX}{token}.{ext}", f"{PREFIX}{token}-preview.webp"


class PictureKeeper:
    def __init__(
        self,
        store: PictureStore,
        bucket: BucketLike,
        storage_mb: int,
        prepare: Callable[[bytes], Awaitable[Prepared]] = prepare_async,
    ) -> None:
        self._store = store
        self._bucket = bucket
        self._storage_mb = storage_mb
        self._prepare = prepare

    async def _delete(self, *keys: str) -> None:
        """Best effort: a file left behind is found by the sweep. Only the error class is logged."""
        for key in keys:
            if not key:
                continue
            try:
                await self._bucket.delete(key)
            except BucketError as exc:
                log.warning("Pictures: couldn't delete a file (%s)", exc)

    async def discard(
        self, guild_id: int, campaign_id: str, picture: Picture, *, unless_map: bool = False
    ) -> bool:
        """Take back a picture just kept (its poster stopped, or deleted the message, while it was
        being stored): the row keeps its link so the link says it was removed, the files go. True
        when it went. `unless_map`: leave it when it has become a map meanwhile (#4344)."""
        gone = await self._store.delete(guild_id, campaign_id, picture.id, unless_map=unless_map)
        if gone is not None:
            await self._delete(gone.object_key, gone.preview_key)
        return gone is not None

    async def forget_message(self, guild_id: int, message_id: int) -> int:
        """The poster deleted the Discord message a picture was in: the kept picture goes too,
        permanent or not (owner / Supervisor, 2026-11-02). How many were taken back."""
        taken = 0
        for picture in await self._store.by_message(guild_id, message_id):
            await self.discard(guild_id, picture.campaign_id, picture)
            taken += 1
        return taken

    async def keep(self, ctx: Context, data: bytes) -> Result:
        try:
            ready = await self._prepare(data)
        except Refused as exc:
            log.info("Pictures: not kept (%s)", exc)
            return Result(None, why_not="refused")
        known = await self._store.find(ctx.guild_id, ctx.campaign_id, ready.sha256)
        if known is not None:
            return Result(known)
        key, preview_key = new_keys(ready.ext)
        try:
            await self._bucket.put(key, ready.data)
            await self._bucket.put(preview_key, ready.preview)
        except BucketError as exc:
            log.warning("Pictures: couldn't upload (%s)", exc)
            await self._delete(key, preview_key)
            return Result(None, why_not="failed")
        added = await self._store.add(
            ctx.guild_id,
            ctx.campaign_id,
            poster_id=ctx.poster_id,
            session_id=ctx.session_id,
            line_id=ctx.line_id,
            message_id=ctx.message_id,
            description=ctx.description,
            consent_version=ctx.consent_version,
            right_granted_at=ctx.right_granted_at,
            sha256=ready.sha256,
            object_key=key,
            preview_key=preview_key,
            size_bytes=len(ready.data),
            preview_bytes=len(ready.preview),
            storage_mb=self._storage_mb,
        )
        if added.picture is None or not added.new:
            # It did not fit, or the same file was kept at the same moment: the new files are not
            # needed (the row that won has its own).
            await self._delete(key, preview_key)
            if added.picture is None:
                return Result(None, why_not="full")
            return Result(added.picture)
        for old in added.removed:  # removed for room: their files go too
            await self._delete(old.object_key, old.preview_key)
        return Result(added.picture, True, len(added.removed))
