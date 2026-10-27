"""A person's own "Delete my transcripts" on the website (#2769; the parity row of #2554).

Discord's 🗑 button deletes the pressing person's lines in one server through the bot
(`delete_my_lines`), which holds each running session's save lock and drops what they have said and
not yet saved. The website's role cannot touch the transcript tables, so it asks the bot: `counts`
tells the person how many lines in how many sessions are theirs (the warning's numbers), `request`
queues the deletion through one database door (`dmbot_web_my_lines_delete`), and `status` reports
where it stands in the shared progress shape (`dmbot.progress`, #2564). The door speaks only for
the signed-in person, only in a server on their session's list, and only about their own lines;
counts and stages are all that leave, never a word of a transcript.

A server is named by its id, as for `/me/recording/{server}/stop`.
"""

from __future__ import annotations

import re
import time
from typing import Any

from dmbot import progress
from dmbot.db import Database
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
# What a failed deletion says, as the code the page turns into words.
REASONS = {"failed"}


def _server(session: Session, server_id: str) -> int:
    """The server's id, if it is one on the session's list; otherwise `not_found`."""
    if not server_id.isdigit() or len(server_id) > 20:
        raise RecapError("not_found")
    return _guild_of(session, int(server_id))


async def counts(db: Database, session: Session, server_id: str) -> dict[str, Any]:
    """How many lines the person has in the server and in how many sessions, and their deletion
    that is open, if any (`{id, state}`)."""
    guild_id = _server(session, server_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute("SELECT dmbot_web_my_lines(%s) AS r", (guild_id,))
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return {"lines": int(got["lines"]), "sessions": int(got["sessions"]), "open": got["open"]}


async def request(db: Database, session: Session, server_id: str) -> str:
    """Ask the bot to delete the person's own lines in the server and return the request's id (the
    same id again while one is open). `nothing` when they have no lines there, `too_soon` past ten
    requests in an hour."""
    guild_id = _server(session, server_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute("SELECT dmbot_web_my_lines_delete(%s) AS r", (guild_id,))
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


async def status(
    db: Database, session: Session, server_id: str, deletion_id: str, *, now: int | None = None
) -> dict[str, Any]:
    """Where the person's own deletion stands, in the shared progress shape, with the counts it
    removed once it is done. There is no "n of N sessions" while it runs: the erasure is one
    transaction, so the page shows the stage, how long it has run and when it last moved."""
    guild_id = _server(session, server_id)
    if not ID_PATTERN.match(deletion_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_my_lines_read(%s, %s) AS r", (guild_id, deletion_id)
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
    done = got["state"] == "done"
    return {
        **shape,
        "lines": int(got["lines"]) if done else None,
        "sessions": int(got["sessions"]) if done else None,
    }
