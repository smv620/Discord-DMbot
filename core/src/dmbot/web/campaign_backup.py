"""A copy of a campaign, downloaded from the website (#2554, part 1b).

The website's role cannot read the tables a backup holds, so it asks the bot: `request` queues a
copy through one database door (`dmbot_web_backup_add`), the bot builds it with the same sections
and packing as Discord's Download (`campaigns/web_backup.py`), `status` reports where it stands in
the shared progress shape (`dmbot.progress`, #2564), and `fetch` hands over the file once and
removes it (it is kept 15 minutes at most). The owner may ask for a copy in any server, even one
DMbot cannot reach; anyone else may in a server on the session's list. Copies are for a paid
plan (none for Try It): the bot judges that when it builds the copy and the status says so. Only
the person who asked can read or fetch it. A copy is a set, as Discord's is (#2772): the main file
and its transcripts files, each fetched once (`fetch`, `fetch_part`). A copy holds the campaign's
secrets, so it is never logged, and it is a download for the person, not a link to share.

A campaign is named by `<server>.<campaign>`, as for the other pages.
"""

from __future__ import annotations

import re
import time
from typing import Any

from dmbot import progress
from dmbot.campaigns.parts import RESTORE_BOXES
from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _scoped
from dmbot.web.sessions import Session

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
KEEP_FOR_S = 900  # what the database keeps a request and its file for
# What a failed copy says, as the code the page turns into words.
REASONS = {"not_found", "not_allowed", "plan", "too_big", "too_many_files", "failed"}


async def request(db: Database, session: Session, ref: str) -> str:
    """Ask for a copy and return its id (the same id again while one is on the way). Raises
    `RecapError("not_found")` for a campaign this person has no right to a copy of, and
    `"too_soon"` past five requests in an hour."""
    guild_id, campaign_id = _parse(ref)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_backup_add(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


async def status(
    db: Database, session: Session, ref: str, backup_id: str, *, now: int | None = None
) -> dict[str, Any]:
    """Where the person's own copy stands, in the shared progress shape with the file's name and
    size once it is ready and when it will be removed."""
    guild_id, campaign_id = _parse(ref)
    if not ID_PATTERN.match(backup_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_backup_read(%s, %s, %s) AS r", (guild_id, campaign_id, backup_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    moment = int(time.time()) if now is None else now
    reason = got["error"] or None
    shape = progress.shape(
        got["stage"],
        started_at=int(got["createdAt"]),
        updated_at=int(got["updatedAt"]),
        now=moment,
        reason=reason if reason in REASONS else ("failed" if reason else None),
    )
    return {
        **shape,
        "ready": got["state"] == "done",
        "fileName": got["fileName"] or None,
        "size": int(got["size"]) if got["state"] == "done" else None,
        # Whether the main file is still there to fetch (it leaves the row as it is taken, so a
        # half-finished download can carry on with the parts).
        "fileWaiting": bool(got["fileWaiting"]),
        # The transcripts files still to fetch (number and size), and how many there were.
        "parts": [{"n": int(p["n"]), "size": int(p["size"])} for p in got["parts"]],
        "partsTotal": int(got["partsTotal"]),
        "expiresAt": int(got["createdAt"]) + KEEP_FOR_S,
    }


async def fetch(db: Database, session: Session, ref: str, backup_id: str) -> tuple[bytes, str]:
    """The file and its name, once: it is deleted as it is read. Raises `RecapError("not_found")`
    when it is not this person's, not ready, or already taken."""
    guild_id, campaign_id = _parse(ref)
    if not ID_PATTERN.match(backup_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        name_cur = await conn.execute(
            "SELECT dmbot_web_backup_read(%s, %s, %s) AS r", (guild_id, campaign_id, backup_id)
        )
        info = (await name_cur.fetchone() or {}).get("r")
        cur = await conn.execute(
            "SELECT dmbot_web_backup_take(%s, %s, %s) AS f", (guild_id, campaign_id, backup_id)
        )
        found = await cur.fetchone()
    raw = found["f"] if found else None
    if raw is None or info is None:
        raise RecapError("not_found")
    # Held whole: the file is at most a restore's limit, and the database cannot stream it.
    return raw, str(info["fileName"]) or "campaign.dmbot.json.gz"


async def fetch_part(
    db: Database, session: Session, ref: str, backup_id: str, n: int
) -> tuple[bytes, str]:
    """One transcripts file of the copy and its name, once: it is deleted as it is read. Raises
    `RecapError("not_found")` when it is not this person's, not ready, or already taken."""
    guild_id, campaign_id = _parse(ref)
    if not ID_PATTERN.match(backup_id) or not 1 <= n <= RESTORE_BOXES:
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        name_cur = await conn.execute(
            "SELECT dmbot_web_backup_read(%s, %s, %s) AS r", (guild_id, campaign_id, backup_id)
        )
        info = (await name_cur.fetchone() or {}).get("r")
        cur = await conn.execute(
            "SELECT dmbot_web_backup_take_part(%s, %s, %s, %s) AS f",
            (guild_id, campaign_id, backup_id, n),
        )
        found = await cur.fetchone()
    raw = found["f"] if found else None
    if raw is None or info is None:
        raise RecapError("not_found")
    total = int(info["partsTotal"])
    return raw, part_filename(str(info["fileName"]), n, total)


def part_filename(main: str, n: int, total: int) -> str:
    """The name Discord's transcripts file has (`logic.part_filename`), made from the main file's
    own name and date."""
    base = main.removesuffix(".dmbot.json.gz") or "campaign"
    return f"{base}.transcripts-{n}-of-{total}.dmbot.json.gz"
