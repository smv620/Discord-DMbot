"""The website's "who is this Discord ID?" (#4225, point 5): a person picker takes a typed id; the
page asks, the bot looks the member up in the server and answers, and the page shows who it is
before the id is used. Two doors (`dmbot_web_member_lookup_add` / `_read`): the page never reaches
Discord itself. Nothing here is written into a campaign."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.memory.store import INT64_MAX
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

ID_MIN_DIGITS, ID_MAX_DIGITS = 17, 20


def clean_id(raw: object) -> int:
    """The typed id as a number: 17 to 20 digits and within a Discord id's range, else
    `bad_id` (the page says "That isn't a Discord ID. It's a long number.")."""
    text = str(raw).strip() if isinstance(raw, (str, int)) and not isinstance(raw, bool) else ""
    if not (text.isascii() and text.isdecimal() and ID_MIN_DIGITS <= len(text) <= ID_MAX_DIGITS):
        raise RecapError("bad_id")
    number = int(text)
    if not 0 < number <= INT64_MAX:
        raise RecapError("bad_id")
    return number


async def ask(db: Database, session: Session, guild_id: int, raw: object) -> str:
    """Queue one question; the id the page polls."""
    number = clean_id(raw)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_member_lookup_add(%s, %s) AS found", (guild_id, number)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    if not isinstance(found, dict):
        raise RecapError("not_found")
    if found.get("status") != "ok":
        raise RecapError("too_soon" if found.get("status") == "too_soon" else "bad_id")
    return str(found["id"])


async def read(db: Database, session: Session, guild_id: int, lookup_id: str) -> dict[str, Any]:
    """Where one question stands: state, outcome, the name once found, whether they agreed."""
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_member_lookup_read(%s, %s) AS found", (guild_id, lookup_id)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    if not isinstance(found, dict):
        raise RecapError("not_found")
    return found
