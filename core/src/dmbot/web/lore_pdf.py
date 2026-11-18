"""A lore PDF sent from the website, staged (#4762, step 1; Supervisor's ruling on the issue):
checked, put in the staging bucket under a random key, and noted in `lore_pdf_staged` (migration
0545) for the bot to count its pages and pictures and, after the DM's yes, read (the next steps).

A DM of the campaign only: the doors answer NULL to anyone else, and the check comes before any of
the body is read. One staged file for each campaign; a new one replaces the old one and the old
object is deleted at once. The file is a PDF by its own first bytes, at most `MAX_BYTES` (the
website's own cap, under the core's `name_documents.MAX_PDF_BYTES`: the bot checks its own limits
and the 500 pages again before anything is read). The staging bucket is the one the map uploads use
(its 1-day lifecycle rule removes what nobody picked up), so the website never holds a write key
to kept files. Nothing of the file's words is kept here or logged: the row holds ids and numbers.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any, Protocol

from dmbot.db import Database
from dmbot.pictures.bucket import BucketError
from dmbot.web import lore, relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

log = logging.getLogger(__name__)

# The website's own cap: the core's limit is higher, and the page says this number.
MAX_BYTES = 50 * 1024 * 1024  # (a test holds it under `MAX_PDF_BYTES`)


class Bucket(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def delete(self, key: str) -> None: ...


def sniff(data: bytes) -> bool:
    """A PDF by the file's own first bytes (what the sender or the browser called it does not
    count)."""
    return data.startswith(b"%PDF-")


async def ensure_dm(db: Database, session: Session, ref: str) -> None:
    """Refuse anyone who is not a DM of the campaign (`not_allowed`) before any of their upload is
    read. It is `read`'s one round trip (the same check as the staging door's own, which asks
    again, so a change in between never lets one by)."""
    await read(db, session, ref)


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The staged file in numbers and words (`state` and, once the bot has counted, `pages`,
    `pictures` and `estimateUsd`), or `{state: "none"}`. `not_allowed` for anyone who is not a DM
    of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_lore_pdf_read(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got.get("status") != "ok":
        return {"state": "none"}
    return {
        "ref": hashlib.sha256(str(got["id"]).encode()).hexdigest()[:32],  # (never the id itself)
        "state": str(got["state"]),
        "sizeBytes": int(got["sizeBytes"]),
        "pages": got["pages"],
        "pictures": got["pictures"],
        "estimateUsd": None if got["estimateUsd"] is None else float(got["estimateUsd"]),
        "createdAt": int(got["createdAt"]),
        "updatedAt": int(got["updatedAt"]),
        "serverNow": int(got["now"]),
    }


async def estimate(db: Database, session: Session, ref: str) -> str:
    """Queue `lore-pdf-estimate` for the staged file and give the answer id the page polls
    (`.../web/answers/{id}`, with the usual stage, counts and times). `not_found` when nothing is
    staged, `not_allowed` for anyone who is not a DM. The bot counts only: no AI is called before
    the DM's yes."""
    staged = await read(db, session, ref)
    if staged["state"] == "none":
        raise RecapError("not_found")
    return await relationships._queue(
        db, session, ref, entity=None, action="lore-pdf-estimate", target="", text=staged["ref"],
        flag=False,
    )  # fmt: skip


async def stage(
    db: Database, session: Session, ref: str, bucket: Bucket | None, data: bytes, now: int
) -> dict[str, Any]:
    """Put the PDF in the staging bucket and note it, replacing the campaign's earlier one (its
    object is deleted). Refusals are `RecapError` codes: `uploads_off` (no bucket), `not_allowed`,
    `bad_file` (empty, too big or not a PDF) and `failed` (the bucket did not take it)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if bucket is None:
        raise RecapError("uploads_off")
    await ensure_dm(db, session, ref)
    if not data or len(data) > MAX_BYTES or not sniff(data):
        raise RecapError("bad_file")
    token = secrets.token_hex(16)  # 128 random bits
    key = f"p/{token}.pdf"
    try:
        await bucket.put(key, data)
    except BucketError as exc:
        log.warning("A sent lore PDF could not be staged (%s)", exc)
        raise RecapError("failed") from None
    try:
        async with db.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, guild_id)
            cur = await conn.execute(
                "SELECT dmbot_web_lore_pdf_stage(%s, %s, %s, %s, %s, %s) AS r",
                (guild_id, campaign_id, token, hashlib.sha256(data).hexdigest(), len(data), now),
            )
            found = await cur.fetchone()
    except BaseException:
        # (a commit that went through but whose answer was lost leaves a row for a missing object:
        # the bot's count, step 2, says "gone" for a missing object, and the 1-day sweep clears it)
        await _forget(bucket, key)
        raise
    got = found["r"] if found else None
    if got is None or got.get("status") != "ok":
        await _forget(bucket, key)
        raise RecapError("not_allowed" if got is None else "bad_file")
    if got.get("replaced"):
        await _forget(bucket, str(got["replaced"]))
    return await read(db, session, ref)


async def drop(db: Database, session: Session, ref: str, bucket: Bucket | None) -> None:
    """The DM's "Never mind": the row goes and its object is deleted."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_lore_pdf_drop(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if bucket is not None and got.get("replaced"):
        await _forget(bucket, str(got["replaced"]))


async def _forget(bucket: Bucket, key: str) -> None:
    """Best effort: an object left behind goes with the bucket's 1-day rule."""
    try:
        await bucket.delete(key)
    except BucketError as exc:
        log.warning("A staged lore PDF could not be deleted (%s)", exc)


_GO_REFUSALS = {
    "not_found",
    "not_counted",
    "needs_confirm",
    "busy",
    "too_soon",
    "bad_text",
    "bad_speaker",
}


async def go(db: Database, session: Session, ref: str, body: object) -> dict[str, Any]:
    """The DM's yes: `{"ref": <the staged file's ref>, "choice": "world"|"spoilers"|"smart",
    "speaker": <character id or "">, "rights": true}` queues the read of the counted file through
    the website's lore queue and gives that upload's progress (the page polls the usual lore read).
    The door marks the file as being read in the same statement, so a second press finds it taken.
    Refusals are `RecapError` codes: `needs_confirm` (rights not ticked), `not_found` (no file, or
    another one than the DM saw), `not_counted` (not counted yet), `busy`, `too_soon`, `bad_text`,
    `bad_speaker`, `not_allowed`."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not isinstance(body, dict):
        raise RecapError("bad_text")
    pdf_ref, choice = body.get("ref"), body.get("choice")
    who, rights = body.get("speaker", ""), body.get("rights", False)
    if not isinstance(pdf_ref, str) or not isinstance(choice, str) or not isinstance(who, str):
        raise RecapError("bad_text")
    if not isinstance(rights, bool):
        raise RecapError("bad_text")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_lore_pdf_go(%s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, pdf_ref, who, choice, rights),
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]) if got["status"] in _GO_REFUSALS else "bad_text")
    return await lore.read(db, session, ref, str(got["id"]))
