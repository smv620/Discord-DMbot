"""Upload campaign lore from the website (#3022, #2985): a DM pastes a text, gives a text file's
words or a link, says whose backstory it is and how it is known, and confirms the right to use it.

The website's role cannot call the AI or touch the memory tables, so this only checks the signed-in
DM, passes the upload to its door (`dmbot_web_lore_add`, migration 0288) and reads the progress
back (`dmbot_web_lore_read`). The bot reads the queued row, runs the same read as Discord's upload
and then blanks the words. The text is the DM's own and is never echoed back: progress is stages and
counts only (CLAUDE.md, "Every wait shows that DMbot is still working").
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

SOURCES = ("paste", "file", "link")
CHOICES = ("world", "spoilers", "smart")
# The same limits as Discord's upload form (ui/name_lists.py); web code never imports `ui`, which
# needs discord.py, so a test keeps the two in step.
PASTE_MAX = 4000
LINK_MAX = 2000


def _lone_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in value)


def _check(body: object) -> dict[str, Any]:
    """The body's fields, or `bad_text`: never a guess about what a stray field meant."""
    if not isinstance(body, dict):
        raise RecapError("bad_text")
    source, choice = body.get("source"), body.get("choice")
    text, link, who = body.get("text", ""), body.get("link", ""), body.get("speaker", "")
    if source not in SOURCES or choice not in CHOICES:
        raise RecapError("bad_text")
    if not all(isinstance(v, str) for v in (text, link, who)):
        raise RecapError("bad_text")
    if len(text) > PASTE_MAX or len(link) > LINK_MAX:
        raise RecapError("too_long")
    # Postgres text cannot hold a NUL and psycopg cannot send a lone surrogate: refuse, not a 500.
    if any("\x00" in v or _lone_surrogate(v) for v in (text, link, who)):
        raise RecapError("bad_text")
    if not isinstance(body.get("rights", False), bool):
        raise RecapError("bad_text")
    return {
        "source": source,
        "choice": choice,
        "text": text.strip(),
        "link": link.strip(),
        "speaker": who,
        "rights": body.get("rights", False),
    }


async def add(db: Database, session: Session, ref: str, body: object) -> dict[str, Any]:
    """Queue an upload; the result is its id and where it stands. `needs_confirm` until the DM
    ticked the right-to-use box (who and when are recorded by the door), `busy` while one is
    open (the page reads that one), `too_soon` past five an hour, `bad_speaker` for a name that is
    not a confirmed character of this campaign. DM only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    f = _check(body)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_lore_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (
                guild_id,
                campaign_id,
                f["source"],
                f["text"],
                f["link"],
                f["speaker"],
                f["choice"],
                f["rights"],
            ),
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return await read(db, session, ref, str(got["id"]))


async def read(db: Database, session: Session, ref: str, upload: str | None) -> dict[str, Any]:
    """One upload's progress (or the newest of the last day when `upload` is None): `{"upload":
    null}` when there is none. DM only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if upload is not None and (len(upload) != 32 or upload.strip("0123456789abcdef")):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_lore_read(%s, %s, %s) AS r", (guild_id, campaign_id, upload)
        )
        got = (await cur.fetchone() or {}).get("r")
        if got is None:
            cur = await conn.execute(
                "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s "
                "AND user_id = %s",
                (guild_id, campaign_id, session.user_id),
            )
            if await cur.fetchone() is None:
                raise RecapError("not_allowed")
            if upload is not None:
                raise RecapError("not_found")
    return {"upload": got}
