"""The campaign's names on the website, with the narrator's "said as" spelling (#1451, on #1419).

The DM reads how the narrator will say things on the recap page, so that is where they can fix a
spelling ("Kah-zeth" for Ka'zeth). The website's database role has no right on the memory tables
(secret names live there), so both the read and the write go through two database doors
(`dmbot_web_names`, `dmbot_web_set_said_as`, schema.py). They answer only a DM of the campaign, in a
server on this session's list, and never return or accept a secret name or a person at the table.
The write is logged like every other memory change, so Undo on the Discord name card still works.

A campaign is named by `<server>.<campaign>` (the recap's address without its last part).
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.recaps.notify import parse_ref
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

SAID_AS_MAX = 80  # the same limit as the bot's MemoryStore.set_said_as and the database's check
ALIAS_ID_LENGTH = 32


def _parse(ref: str) -> tuple[int, str]:
    """(server, campaign) from `<server>.<campaign>`, or a "not found". The campaign half goes
    through the recap address's own checks (ASCII, letters and digits, bounded)."""
    parsed = parse_ref(f"{ref}.0")
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


def _alias_id(text: str) -> str:
    if len(text) != ALIAS_ID_LENGTH or not set(text) <= set("0123456789abcdef"):
        raise RecapError("not_found")
    return text


async def list_names(db: Database, session: Session, ref: str) -> list[dict[str, Any]]:
    """The campaign's confirmed public names, for a DM of it. Anyone else gets "not found"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute("SELECT dmbot_web_names(%s, %s) AS names", (guild_id, campaign_id))
        found = await cur.fetchone()
    names = found["names"] if found else None
    if names is None:
        raise RecapError("not_found")
    return list(names)


async def set_said_as(
    db: Database, session: Session, ref: str, alias_id: str, spelling: str | None
) -> None:
    """Set or clear how the narrator says one name. The database door holds it to the rules."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    alias = _alias_id(alias_id)
    # The bot's own rule first (MemoryStore.set_said_as): tidy spaces, up to 80 printable letters.
    # This also keeps a NUL, which the database driver refuses, from becoming a server error.
    clean = " ".join((spelling or "").split()) or None
    if clean is not None and (len(clean) > SAID_AS_MAX or not clean.isprintable()):
        raise RecapError("bad_text")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_set_said_as(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, alias, clean),
        )
        found = await cur.fetchone()
    outcome = found["r"] if found else "not_allowed"
    if outcome == "ok":
        return
    # Not this campaign's DM, no such name and a secret name are all told as "not found": nobody
    # learns that a secret name with that id exists. Only a too-long spelling is its own answer.
    raise RecapError("bad_text" if outcome == "too_long" else "not_found")
