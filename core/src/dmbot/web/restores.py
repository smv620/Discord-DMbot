"""Loading a backup from the website (#2745).

The website's role cannot write the campaign tables, so the person's copy is staged through
database doors (`dmbot_web_restore_*`, schema.py) and the bot applies it with the same code as
Discord's `/dmbot restore`, after checking again the plan, the free campaign slot, that the person
is still in the server and (to replace a campaign) still one of its DMs, and that the campaign is
not playing. What this module checks is only what can be seen from the file itself: the limits
Discord's command has (imported from the code that applies them, never a second copy), that it
is a DMbot backup, and how many transcripts files it came with. A copy holds the campaign's
secrets, so it is never logged and its contents never leave in an answer: only its name, which the
person gave us in the file, goes back.

Steps: `stage` (the copy), `add_part` (each transcripts file the copy asked for), `go` (send it,
with all the files or, as in Discord, without any), `status` (the shared progress shape,
dmbot.progress, #2564), `cancel`. The bytes are kept 24 hours at most and are emptied when the bot
has applied or refused them.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from dmbot import progress
from dmbot.campaigns.models import CampaignError
from dmbot.campaigns.parts import PART_READ_BYTES
from dmbot.campaigns.store import GZIP_MAGIC, MAX_BACKUP_BYTES, NOT_A_BACKUP, decode_backup
from dmbot.db import Database
from dmbot.ui import logic
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _scoped
from dmbot.web.sessions import Session

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
# Discord's `/dmbot restore` has one box for the copy and 24 for transcripts files.
MAX_PARTS = 24
# A whole restore's bytes, for the API's body limits: the copy and every transcripts file.
MAX_BODY_BYTES = max(MAX_BACKUP_BYTES, PART_READ_BYTES)
KEEP_FOR_S = 24 * 60 * 60
# What a failed restore says, as the code the page turns into words (the bot writes these).
REASONS = {
    "not_found",  # the campaign to replace is gone
    "not_allowed",  # no longer a DM of it, or no longer in the server
    "plan",  # the plan has no copies, or no free campaign slot
    "playing",  # the campaign to replace is playing right now
    "not_a_backup",
    "too_big",
    "parts",  # a transcripts file is missing, repeated or from another copy
    "failed",
}


def _check(blob: object, limit: int) -> bytes:
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise RecapError("not_a_backup")
    if len(blob) > limit:
        raise RecapError("too_big")
    return bytes(blob)


async def stage(
    db: Database, session: Session, server_id: int, replace_ref: str | None, raw: object
) -> dict[str, Any]:
    """Check the copy and stage it. Returns `{id, name, partsWanted, state}`. Raises
    `RecapError`: `too_big`, `not_a_backup`, `too_many_parts`, `not_found` (no such campaign of
    the person's to replace, or not in that server), `busy` (another restore of theirs is still
    pending; its id is in `.problems[0]['id']`), `too_soon`."""
    blob = _check(raw, MAX_BACKUP_BYTES)
    try:
        # Pure CPU on up to 25 MB: off the event loop, as the bot does (#164).
        data = await asyncio.to_thread(decode_backup, blob)
    except CampaignError as exc:
        raise RecapError("not_a_backup" if str(exc) == NOT_A_BACKUP else "too_big") from None
    name = logic.backup_campaign_name(data)
    if name is None:
        raise RecapError("not_a_backup")
    wanted = logic.backup_parts(data)
    if wanted > MAX_PARTS:
        raise RecapError("too_many_parts")
    replace_id: str | None = None
    if replace_ref is not None:
        guild_id, replace_id = _parse(replace_ref)
        if guild_id != server_id:
            raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, server_id)
        cur = await conn.execute(
            "SELECT dmbot_web_restore_add(%s, %s, %s, %s, %s) AS r",
            (server_id, replace_id, name, wanted, blob),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] == "busy":
        raise RecapError("busy", problems=[{"id": got["id"]}])
    if got["status"] == "bad_target":
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return {"id": got["id"], "name": name, "partsWanted": wanted, "state": got["state"]}


async def add_part(
    db: Database, session: Session, server_id: int, restore_id: str, number: int, raw: object
) -> dict[str, Any]:
    """One transcripts file of a staged copy. Returns `{received, partsWanted}`. Raises
    `RecapError`: `too_big`, `not_a_backup` (not a packed file), `not_found`, `bad_part`,
    `not_receiving`."""
    if not ID_PATTERN.match(restore_id):
        raise RecapError("not_found")
    blob = _check(raw, PART_READ_BYTES)
    if not blob.startswith(GZIP_MAGIC):
        raise RecapError("not_a_backup")  # a transcripts file is always packed
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, server_id)
        cur = await conn.execute(
            "SELECT dmbot_web_restore_part(%s, %s, %s, %s) AS r",
            (server_id, restore_id, number, blob),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return {"received": got["received"]}


async def go(
    db: Database, session: Session, server_id: int, restore_id: str, *, without: bool
) -> None:
    """Send it to the bot. `without`: restore without the transcripts files (Discord allows all or
    none). Raises `RecapError`: `not_found`, `missing` (files still to come), `not_receiving`."""
    if not ID_PATTERN.match(restore_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, server_id)
        cur = await conn.execute(
            "SELECT dmbot_web_restore_go(%s, %s, %s) AS r", (server_id, restore_id, without)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))


async def status(
    db: Database,
    session: Session,
    server_id: int,
    restore_id: str | None,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Where the person's own restore stands: the shared progress shape, plus the copy's name, the
    files it asks for and has, and once ended the campaign it made (`campaign`, a ref) and how many
    lines were left out for people who had deleted theirs. `restore_id` None: the person's latest
    in that server, if any (for a page that was closed); raises `not_found` when there is none."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, server_id)
        if restore_id is None:
            cur = await conn.execute("SELECT dmbot_web_restore_pending(%s) AS r", (server_id,))
            row = await cur.fetchone()
            restore_id = row["r"] if row else None
        if restore_id is None or not ID_PATTERN.match(restore_id):
            raise RecapError("not_found")
        cur = await conn.execute(
            "SELECT dmbot_web_restore_read(%s, %s) AS r", (server_id, restore_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    moment = int(time.time()) if now is None else now
    reason = got["error"] or None
    stage_ = got["stage"]
    shape = progress.shape(
        stage_,
        started_at=int(got["createdAt"]),
        updated_at=int(got["updatedAt"]),
        now=moment,
        done=int(got["doneParts"]) if stage_ == "restore_writing" else None,
        total=int(got["partsWanted"]) if stage_ == "restore_writing" else None,
        unit="parts" if stage_ == "restore_writing" else None,
        reason=reason if reason in REASONS else ("failed" if reason else None),
    )
    ended = got["state"] in ("done", "failed")
    return {
        **shape,
        "id": restore_id,
        "name": got["name"],
        "replacing": got["replaceId"] is not None,
        # The campaign being replaced, so the page can name it after a reload (it finds it in /me).
        "replaceRef": (f"{server_id}.{got['replaceId']}" if got["replaceId"] else None),
        "partsWanted": int(got["partsWanted"]),
        "partsReceived": int(got["partsReceived"]),
        "withoutParts": bool(got["withoutParts"]),
        "campaign": (
            f"{server_id}.{got['resultId']}" if got["state"] == "done" and got["resultId"] else None
        ),
        "leftOut": int(got["leftOut"]) if ended else 0,
        "expiresAt": int(got["createdAt"]) + KEEP_FOR_S,
    }


async def cancel(db: Database, session: Session, server_id: int, restore_id: str) -> None:
    """Take a restore back before the bot starts it (or clear one that has ended). The bytes go
    with the row. Raises `not_found` when there is nothing of theirs to remove."""
    if not ID_PATTERN.match(restore_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, server_id)
        cur = await conn.execute(
            "SELECT dmbot_web_restore_cancel(%s, %s) AS r", (server_id, restore_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got == "working":
        raise RecapError("working")
    if got != "ok":
        raise RecapError("not_found")
