"""A map sent from the Maps page (#4347, slice B): checked, staged, queued for the bot.

The website's database role cannot write pictures or maps, and the bot's stores keep each change
as one Undo, so this module only does what the bot should not wait for: it checks the person and
the file, puts the two files in the bucket under random keys (`p/<token>.<ext>` and
`p/<token>-preview.webp`, as the picture keeper does) and queues one `maps-add` action
(migration 0498). The bot re-checks the sender and the files, writes the rows, and deletes the
files if it refuses; a file nobody picked up is swept by the picture sweep (about an hour).
Nothing is stored for anyone outside the campaign's people, for a picture the person did not say they may use, or for a
file that is not a PNG, JPEG or WebP picture by its own bytes (an SVG is never one). The picture
is re-encoded first, so no location data or hidden text rides along. Words, ids and sizes only
are queued, never a word of the picture.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Protocol

from dmbot.db import Database
from dmbot.pictures.bucket import BucketError
from dmbot.pictures.keeper import new_keys
from dmbot.pictures.prepare import Refused, prepare_async
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session
from dmbot.web.transcripts import reads_campaign

log = logging.getLogger(__name__)

# The most a sent picture may weigh, before it is re-encoded: the page says the same number.
MAX_BYTES = 10 * 1024 * 1024
TITLE_MAX = 120
# The door's text allows five digits for a side; a longer one could pass `prepare` (40 megapixels).
SIDE_MAX = 99_999
# Re-encoding a picture is heavy: at most this many at once in this process.
_BUSY = asyncio.Semaphore(2)

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"


class Bucket(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def delete(self, key: str) -> None: ...


def sniff(data: bytes) -> bool:
    """A PNG, a JPEG or a WebP by the file's own first bytes. What the browser or the sender
    called it does not count, and an SVG, a GIF or anything else is not one of these."""
    return (
        data.startswith(_PNG)
        or data.startswith(_JPEG)
        or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def clean_title(raw: object) -> str:
    """The map's name: one line of 1 to 120 characters, no control characters."""
    if not isinstance(raw, str):
        raise RecapError("bad_name")
    title = re.sub(r"\s+", " ", raw).strip()
    if not title or len(title) > TITLE_MAX or any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
        raise RecapError("bad_name")
    return title


async def ensure_person(db: Database, session: Session, ref: str) -> None:
    """Refuse a stranger before any of their upload is read (`not_allowed`): only the campaign's
    people may send a map. The queue door asks again, so a change in between never lets one by."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        if not await reads_campaign(conn, guild_id, campaign_id):
            raise RecapError("not_allowed")


async def add(
    db: Database,
    session: Session,
    ref: str,
    bucket: Bucket | None,
    data: bytes,
    title: object,
    *,
    rights: bool,
) -> str:
    """Stage the picture and queue `maps-add`; the answer id the page polls. Refusals are
    `RecapError` codes: `needs_rights`, `bad_name`, `bad_file`, `uploads_off`, `not_allowed`,
    and `failed` (the bucket did not take it)."""
    guild_id, _campaign = _parse(ref)
    _guild_of(session, guild_id)
    if not rights:
        raise RecapError("needs_rights")  # (nothing is stored without the person's own yes)
    name = clean_title(title)
    if bucket is None:
        raise RecapError("uploads_off")
    await ensure_person(db, session, ref)
    if not data or len(data) > MAX_BYTES or not sniff(data):
        raise RecapError("bad_file")
    try:
        async with _BUSY:
            ready = await prepare_async(data)
    except Refused:
        raise RecapError("bad_file") from None
    if ready.ext not in ("png", "jpg", "webp") or max(ready.width, ready.height) > SIDE_MAX:
        raise RecapError("bad_file")
    key, preview_key = new_keys(ready.ext)
    token = key.removeprefix("p/").split(".")[0]
    try:
        await bucket.put(key, ready.data)
        await bucket.put(preview_key, ready.preview)
    except BucketError as exc:
        log.warning("A sent map could not be staged (%s)", exc)
        await _forget(bucket, key, preview_key)
        raise RecapError("failed") from None
    # `ready.sha256` is the hash of the bytes sent, not of the re-encoded file that is staged, so
    # the bot must not compare it with the staged object: it only names the picture (the same file
    # sent twice is one picture). The sizes are of the staged files.
    text = (
        f"{token} {ready.ext} {ready.sha256} {len(ready.data)} {len(ready.preview)} "
        f"{ready.width} {ready.height} {name}"
    )
    try:
        return await relationships._queue(
            db, session, ref, entity=None, action="maps-add", target="", text=text, flag=False
        )
    except BaseException:
        await _forget(bucket, key, preview_key)  # (queued nothing: the files go at once)
        raise


async def _forget(bucket: Bucket, *keys: str) -> None:
    """Best effort: a file left behind is found by the sweep."""
    for key in keys:
        try:
            await bucket.delete(key)
        except BucketError as exc:
            log.warning("A staged map file could not be deleted (%s)", exc)
