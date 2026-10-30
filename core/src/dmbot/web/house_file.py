"""The House-rules file from the website (#2985, slice 1): a DM connects or changes the linked
file, stops using it, asks DMbot to compare it with the campaign's rules now, or uploads a text
file once.

The website's role has no grant on the linked file (a share link can be private), so this only
checks the signed-in DM, passes the request to its door (`dmbot_web_house_file_add`, migration
0295) and reads the state back (`dmbot_web_house_file_read`). The bot applies the queued request
through the same calls as Discord's 📄 House-rules file buttons and then blanks the address and the
text. What comes back is whether a file is connected, the SITE it is on (never the address) and a
request's stage and counts: never a link, a file's words or a rule. Accepting or reviewing the
changes a compare finds stays in Discord for now.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

ACTIONS = ("link", "unlink", "check", "upload")
# The upload limit is the house-rules reader's (200,000 characters, refused unread) and the
# migration's TEXT_MAX; the link limit is Discord's (rules/house_file_link.LINK_MAX, which this
# module does not import so web code stays light). Tests keep them in step.
TEXT_MAX = 200_000
LINK_MAX = 2000


def _lone_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in value)


def _check(body: object) -> tuple[str, str, str]:
    """(action, link, text) or the refusal word: never a guess about what a stray field meant."""
    if not isinstance(body, dict):
        raise RecapError("bad_text")
    action = body.get("action")
    link, text = body.get("link", ""), body.get("text", "")
    if action not in ACTIONS or not isinstance(link, str) or not isinstance(text, str):
        raise RecapError("bad_text")
    if len(text) > TEXT_MAX or len(link) > LINK_MAX:
        raise RecapError("too_long")
    # Postgres text cannot hold a NUL and psycopg cannot send a lone surrogate: refuse, not a 500.
    if any("\x00" in v or _lone_surrogate(v) for v in (link, text)):
        raise RecapError("bad_text")
    return str(action), link.strip(), text.strip()


async def add(db: Database, session: Session, ref: str, body: object) -> dict[str, Any]:
    """Queue a request; the result is the file's state with that request's progress. `busy` while
    one is open (the page reads that one), `too_soon` past ten an hour, `bad_text` for words the
    door refuses. DM only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    action, link, text = _check(body)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_house_file_add(%s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, action, link, text),
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return await read(db, session, ref, str(got["id"]))


async def read(db: Database, session: Session, ref: str, request: str | None) -> dict[str, Any]:
    """Whether a file is connected, the site it is on and one request's progress (or the newest of
    the last day when `request` is None; `"request": null` when there is none). DM only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if request is not None and (len(request) != 32 or request.strip("0123456789abcdef")):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_house_file_read(%s, %s, %s) AS r", (guild_id, campaign_id, request)
        )
        got = (await cur.fetchone() or {}).get("r")
        if got is None:
            raise RecapError("not_allowed")
    if got.get("missing"):
        raise RecapError("not_found")
    return {
        "linked": bool(got["linked"]),
        "site": str(got["site"]),
        "request": got["request"],
        "serverNow": got["serverNow"],
    }
