"""What one lore upload or import added, for the website's notice (#3879): the counts, the upload's
label, until when Undo works. One database door (`dmbot_web_upload_notice`, 0404) answers it for a
DM of the campaign; anyone else gets "not found". Counts and times only: never a name, never the
change numbers. Built to the page's shape (`UploadNotice` in web/src/account/api.ts)."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

KINDS = ("lore", "import")
_SOURCE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")  # an id: letters, digits and a few joiners
# Counts that are not a kind of name: they ride beside the kinds in the stored tally.
_NOT_KINDS = ("connections", "other_names")


async def read(
    db: Database, session: Session, ref: str, kind: str, source_id: str
) -> dict[str, Any]:
    """The notice for one upload (`kind` lore or import, and its id), in the page's shape. `none`
    when there is no notice for it (the bot is still adding, it added nothing, or it was written
    before notices knew their source). Refusals: `not_found` for a stranger, a bad kind or id."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if kind not in KINDS or not _SOURCE.match(source_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_upload_notice(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, kind, source_id),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    now = int(got["now"])
    if got["state"] != "done":
        return {
            "state": "none",
            "label": "",
            "counts": {"names": 0, "byKind": {}, "connections": 0},
            "undoUntil": None,
            "undone": None,
            "questionsLeft": 0,
            "serverNow": now,
        }
    stored = {str(k): int(v) for k, v in (got.get("counts") or {}).items() if isinstance(v, int)}
    by_kind = {k: v for k, v in stored.items() if k not in _NOT_KINDS and v > 0}
    return {
        "state": "done",
        "label": str(got.get("label") or ""),
        "counts": {
            "names": sum(by_kind.values()),
            "byKind": by_kind,
            "connections": stored.get("connections", 0),
        },
        "undoUntil": got.get("undoUntil"),
        # A past Undo says nothing here: how many it took back is not kept, and "took back 0 of 0"
        # would be wrong. The page shows its own answer for a press it made, and the Undo button is
        # gone because `undoUntil` is empty.
        "undone": None,
        "questionsLeft": 0,
        "serverNow": now,
    }
    stored = {str(k): int(v) for k, v in (got.get("counts") or {}).items() if isinstance(v, int)}
    by_kind = {k: v for k, v in stored.items() if k not in _NOT_KINDS and v > 0}
    return {
        "state": "done",
        "label": str(got.get("label") or ""),
        "counts": {
            "names": sum(by_kind.values()),
            "byKind": by_kind,
            "connections": stored.get("connections", 0),
        },
        "undoUntil": got.get("undoUntil"),
        # An Undo already done: how many it took back is not kept, so no numbers (`took` 0 of 0 is
        # the page's "taken back" without a count).
        "undone": {"took": 0, "of": 0} if got.get("undone") else None,
        # The questions an upload left are the bot's to count; not read here yet (#3879).
        "questionsLeft": 0,
        "serverNow": now,
    }
