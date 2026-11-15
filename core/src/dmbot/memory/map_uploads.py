"""Keeping a map sent from the website's Maps page (#4347, `maps-add`): the bot's half.

The website checks the person and the file, re-encodes the picture and stages it in a separate
staging bucket under a random token (`p/<token>.<ext>`, `p/<token>-preview.webp`), then queues one
`maps-add` action whose text is `<token> <ext> <sha256> <size> <preview size> <width> <height>
<title>` (shape checked by the queue door, migration 0498). Nothing there is the person's word of
trust: this module checks the sender again, reads the staged file, and keeps it through the one
picture keeper (which decodes and re-encodes it again, so a staged file that is not a picture is
refused, and its limits and storage rules hold), then writes the map row as one Undo and deletes
the staged files. The sent `sha256` is the hash of the file the person sent, not of the staged
one, so it is never compared with the staged bytes; the sizes are of the staged files and are.
A staged file that is gone (the staging bucket removes it after a day) answers `expired`.
Words, ids and sizes only are ever logged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from dmbot.maps import words
from dmbot.pictures.bucket import BucketError
from dmbot.pictures.keeper import Context as PictureContext

if TYPE_CHECKING:
    from dmbot.maps.store import MapStore
    from dmbot.memory.web_actions import Action, Outcome
    from dmbot.pictures.keeper import PictureKeeper

log = logging.getLogger(__name__)

EXPIRED = "expired"  # the staged file is gone: "That upload expired before DMbot could keep it."
BAD_FILE = "bad_file"  # the staged file is not what was queued, or not a picture
MAP_REFUSED = "map_refused"  # the map store said no; its own words are in `result_other`
MAX_STAGED = 10 * 1024 * 1024  # what the website lets through (`web/maps_upload.py`)

_TEXT = re.compile(
    r"(?P<token>[0-9a-f]{32}) (?P<ext>png|jpg|webp) (?P<sha>[0-9a-f]{64}) (?P<size>[0-9]{1,9})"
    r" (?P<preview>[0-9]{1,9}) (?P<width>[0-9]{1,5}) (?P<height>[0-9]{1,5}) (?P<title>.{1,120})"
)


class StagingBucket(Protocol):
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class MapUploads:
    """What the bot needs to keep a sent map: the staging bucket, the picture keeper, the map store
    and the storage limit. A missing piece makes every `maps-add` fail (and be sent again)."""

    staging: StagingBucket | None
    keeper: PictureKeeper | None
    maps: MapStore | None
    storage_mb: int


def parse(text: str) -> re.Match[str] | None:
    """The queued text's parts, or None for anything that is not the door's own shape."""
    found = _TEXT.fullmatch(text)
    if found is None or any(ord(ch) < 32 or ord(ch) == 127 for ch in found["title"]):
        return None
    return found


async def add(
    uploads: MapUploads | None, guild_id: int, campaign_id: str, *, is_dm: bool, a: Action
) -> Outcome:
    """Keep the staged picture as a map for this person. `is_dm`: they run the campaign (a player
    in it was checked by the caller). A DM's own map is also theirs to use; a player's waits for a
    DM, as on Discord (`MapStore.add_map`)."""
    from dmbot.maps.store import MapError
    from dmbot.memory.web_actions import FAILED, Outcome

    if uploads is None or uploads.staging is None or uploads.keeper is None or uploads.maps is None:
        return Outcome(error=FAILED)
    got = parse(a.text)
    if got is None:
        return Outcome(error=BAD_FILE)
    key = f"p/{got['token']}.{got['ext']}"
    preview_key = f"p/{got['token']}-preview.webp"
    size = int(got["size"])
    picture = None
    new = False
    final = True  # whether the staged files go: not when the same upload may be sent again
    try:
        if size > MAX_STAGED:
            return Outcome(error=BAD_FILE)
        try:
            data = await uploads.staging.get(key)
        except BucketError as exc:
            # a 404 is a file that is gone; anything else may work again, so it is `failed`
            final = False
            return Outcome(error=EXPIRED if str(exc) == "status 404" else FAILED)
        if len(data) != size:
            return Outcome(error=BAD_FILE)
        # The person's own yes ("may I use and save this map here?") was given on the website
        # before anything was staged (`web/maps_upload.py` refuses without it), and only that route
        # holds a key to the staging bucket, under tokens of 128 random bits, so a file here is one
        # that said yes; the map row records it, with who and when.
        # No chat-capture consent is recorded: sending a map is the person's own act (as for
        # `/dmbot map`).
        kept = await uploads.keeper.keep(
            PictureContext(
                guild_id=guild_id, campaign_id=campaign_id, poster_id=a.user_id, session_id="",
                line_id=None, message_id=0, description=got["title"],
                consent_version=None, right_granted_at=None,
            ),
            data,
        )  # fmt: skip
        picture, new = kept.picture, kept.new
        if picture is None:
            if kept.why_not == "refused":
                return Outcome(error=BAD_FILE)
            if kept.why_not == "full":  # (no room: nothing to retry until something is removed)
                return Outcome(error=MAP_REFUSED, result_other=words.BUSY)
            final = False  # (the bucket or database failed: the page sends it again)
            return Outcome(error=FAILED)
        try:
            made = await uploads.maps.add_map(
                guild_id, campaign_id, picture_id=picture.id, uploader_id=a.user_id,
                uploader_is_dm=is_dm, right_to_use=True, kind="other", came_from="website",
                title=got["title"], audience="dm", width=int(got["width"]),
                height=int(got["height"]), storage_mb=uploads.storage_mb,
            )  # fmt: skip
        except MapError as exc:  # (no room, already a map): the store's own words
            if new:
                await uploads.keeper.discard(guild_id, campaign_id, picture, unless_map=True)
            return Outcome(error=MAP_REFUSED, result_other=str(exc))
        except Exception:
            if new:  # (a database error: no stray picture is left behind, and it may be sent again)
                await uploads.keeper.discard(guild_id, campaign_id, picture, unless_map=True)
            final = False
            raise
        log.info("A sent map was kept (%s)", "dm" if is_dm else "player")
        batches = (made.batch,) if made.batch is not None else ()
        return Outcome(result_id=str(made.value.id), batches=batches)
    finally:
        # best effort: a file left behind goes with the staging bucket's own one-day rule
        for staged in (key, preview_key) if final else ():
            try:
                await uploads.staging.delete(staged)
            except BucketError as exc:
                log.warning("A staged map file could not be deleted (%s)", exc)
