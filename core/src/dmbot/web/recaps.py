"""The DM's recap page, as an API (#1382; docs/PLAN.md, "Cinematic recaps"): list, read, edit,
Post, Try again and Don't post.

The recap is a job the bot runs after the session and emails the DM about; this is where the DM
reads it, changes it and decides. Everything is read and written under the website's database
role, one server at a time, the servers in this session's list only. A row-level rule
(`web_dm_recaps`) shows a recap only to a DM of its campaign, so a player, or a DM of another
campaign, gets "not found" for it, as for a recap that does not exist.

Nothing is posted from here. Post only marks the recap `posting`; the bot, which holds the
Discord token, checks again (the plan, who is still recorded, secret names read fresh) and sends
it. Edits are limited in size here; the secret-name check is the bot's, at Post, since the
website's role cannot read the campaign's names.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Conn, Database
from dmbot.recaps.draft import BULLET_MAX, HOUSEKEEPING_BULLETS, SUMMARY_MAX
from dmbot.recaps.notify import job_ref, parse_ref
from dmbot.recaps.store import MAX_TRIES
from dmbot.recaps.text import strip_cues
from dmbot.web.me import MAX_GUILDS
from dmbot.web.sessions import Session

LIST_LIMIT = 20

_COLUMNS = (
    "r.guild_id, r.campaign_id, r.session_at, r.state, r.status, r.title, r.summary,"
    " r.housekeeping, r.music, r.tries, r.last_error, r.game_day, c.name AS campaign"
)
_FROM = (
    "FROM recap_jobs r LEFT JOIN campaigns c ON c.id = r.campaign_id AND c.guild_id = r.guild_id"
)


class RecapError(Exception):
    """Something the page shows in plain words. `code` is for the page's own message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _view(row: dict[str, Any]) -> dict[str, Any]:
    state, status = row["state"], row["status"]
    ready = state == "ready" and status == "draft"
    return {
        "ref": job_ref(row["guild_id"], row["campaign_id"], row["session_at"]),
        "campaign": row["campaign"],
        "sessionAt": row["session_at"],
        "gameDay": row["game_day"],
        "state": state,
        "status": status,
        "working": state not in ("ready", "failed") and status == "draft",
        "title": row["title"],
        "text": strip_cues(row["summary"]),
        "summary": row["summary"],
        "housekeeping": list(row["housekeeping"]),
        "error": row["last_error"],
        "canEdit": ready,
        "canPost": ready,
        "canTryAgain": status == "draft"
        and state in ("ready", "failed")
        and row["tries"] < MAX_TRIES,
        "triesLeft": max(0, MAX_TRIES - row["tries"]),
        "canDrop": status == "draft",
    }


async def _scoped(conn: Conn, guild_id: int) -> None:
    await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(guild_id),))


def _guild_of(session: Session, guild_id: int) -> int:
    if guild_id not in {int(g.id) for g in session.guilds[:MAX_GUILDS]}:
        raise RecapError("not_found")
    return guild_id


async def list_recaps(db: Database, session: Session) -> list[dict[str, Any]]:
    """The signed-in DM's latest recaps, newest first, in the servers of this session's list."""
    out: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        for guild in session.guilds[:MAX_GUILDS]:
            await _scoped(conn, int(guild.id))
            cur = await conn.execute(
                f"SELECT {_COLUMNS} {_FROM} WHERE r.guild_id = %s"
                " ORDER BY r.session_at DESC LIMIT %s",
                (int(guild.id), LIST_LIMIT),
            )
            out += [{**_view(r), "serverName": guild.name} for r in await cur.fetchall()]
    out.sort(key=lambda v: v["sessionAt"], reverse=True)
    return out[:LIST_LIMIT]


async def _one(conn: Conn, ref: str, session: Session) -> tuple[int, str, int, dict[str, Any]]:
    parsed = parse_ref(ref)
    if parsed is None:
        raise RecapError("not_found")
    guild_id, campaign_id, session_at = parsed
    _guild_of(session, guild_id)
    await _scoped(conn, guild_id)
    cur = await conn.execute(
        f"SELECT {_COLUMNS} {_FROM} WHERE r.guild_id = %s AND r.campaign_id = %s"
        " AND r.session_at = %s",
        (guild_id, campaign_id, session_at),
    )
    row = await cur.fetchone()
    if row is None:
        raise RecapError("not_found")
    return guild_id, campaign_id, session_at, row


async def read_recap(db: Database, session: Session, ref: str) -> dict[str, Any]:
    async with db.user(session.user_id, session=session.id_hash) as conn:
        *_, row = await _one(conn, ref, session)
    return _view(row)


async def edit_recap(
    db: Database, session: Session, ref: str, *, summary: str, housekeeping: list[str]
) -> dict[str, Any]:
    """Save the DM's text. Only while the recap is ready and undecided; the size limits are
    checked here, the secret-name check by the bot at Post."""
    text = summary.strip()
    bullets = [" ".join(b.split()) for b in housekeeping if b.strip()]
    if not text or len(text) > SUMMARY_MAX:
        raise RecapError("bad_text")
    if len(bullets) > HOUSEKEEPING_BULLETS or any(len(b) > BULLET_MAX for b in bullets):
        raise RecapError("bad_text")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session)
        cur = await conn.execute(
            "UPDATE recap_jobs SET summary = %s, housekeeping = %s WHERE guild_id = %s"
            " AND campaign_id = %s AND session_at = %s AND state = 'ready' AND status = 'draft'",
            (text, bullets, gid, cid, at),
        )
        if not cur.rowcount:
            raise RecapError("not_editable")
        *_, row = await _one(conn, ref, session)
    return _view(row)


async def decide(db: Database, session: Session, ref: str, action: str) -> dict[str, Any]:
    """`post` (the bot sends it), `drop` (Don't post) or `again` (Try again, at most twice)."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session)
        where = " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
        args: tuple[Any, ...] = (gid, cid, at)
        if action == "post":
            cur = await conn.execute(
                "UPDATE recap_jobs SET status = 'posting' "
                + where
                + " AND state = 'ready' AND status = 'draft'",
                args,
            )
        elif action == "drop":
            cur = await conn.execute(
                "UPDATE recap_jobs SET status = 'dropped' " + where + " AND status = 'draft'", args
            )
        elif action == "again":
            cur = await conn.execute(
                # Only these two: the worker sees "queued" with an email already sent and resets
                # everything else itself (the page's role may write no more than it needs).
                "UPDATE recap_jobs SET tries = tries + 1, state = 'queued' "
                + where
                + " AND status = 'draft' AND state IN ('ready', 'failed') AND tries < %s",
                (*args, MAX_TRIES),
            )
        else:
            raise RecapError("not_found")
        if not cur.rowcount:
            raise RecapError("not_allowed")
        *_, row = await _one(conn, ref, session)
    return _view(row)
