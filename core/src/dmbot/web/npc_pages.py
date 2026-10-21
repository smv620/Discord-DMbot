"""An NPC's page and a person's private notes on it (#2265; docs/PLAN.md, "Every NPC opens its own
page, with private notes").

Anyone in the campaign's Discord server (the session's own server list, like the transcripts) may
open the page. It shows what the NPC list in Discord shows the players, taken from what the same
pass wrote when it listed the entry (`npc_roster`, see `dm_screen/npc_pages.py`): never a secret
name, a clue, a condition, a thread, the DM's note or anything from the adventure plan. The
campaign's DMs also get their own note on the entry (DM-only, as in Discord). The posted recaps that
mention the name are listed too: the table has already seen them.

My notes: a private box for each person and each NPC (5,000 characters). Four database doors, and
only they, reach the tables (the website's role has no grant): each answers the caller's own rows.
Nobody else reads a note, not the DM. A note is never logged, never in a transcript, a prompt, the
story memory or a backup, and the doors work by server and campaign. A person downloads all of their
own notes in a campaign, or deletes them.

A campaign is named by `<server>.<campaign>`, as for the recap and transcript pages; an NPC by its
entry id.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.recaps.notify import parse_ref
from dmbot.web.recaps import RecapError, _scoped
from dmbot.web.sessions import Session

_ENTRY = re.compile(r"^[0-9a-f]{32}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
NOTE_MAX = 5000


def _parse(ref: str) -> tuple[int, str]:
    parsed = parse_ref(f"{ref}.0")
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


def _member(session: Session, guild_id: int) -> None:
    """Only the people in the campaign's server (this session's own list). Anyone else is told so
    plainly: the page says "This page is for the people in that Discord server."."""
    if guild_id not in {int(g.id) for g in session.guilds}:
        raise RecapError("not_in_server")


def _entry(entry: str) -> str:
    if not _ENTRY.match(entry):
        raise RecapError("not_found")
    return entry


async def read_page(db: Database, session: Session, ref: str, entry: str) -> dict[str, Any]:
    """One NPC's page: the players' view, the posted recaps that mention it, the caller's own note
    and (for a DM of the campaign) the DM's note. 'not_in_server' for someone outside the server,
    'not_found' for an entry that is not listed (a secret one, an unconfirmed one, another
    campaign's)."""
    guild_id, campaign_id = _parse(ref)
    entry = _entry(entry)
    _member(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_npc_page(%s, %s, %s) AS found", (guild_id, campaign_id, entry)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    if found is None:
        raise RecapError("not_in_server")
    if found.get("status") != "ok":
        raise RecapError("not_found")
    view = found["view"]
    return {
        "id": view["id"],
        "name": view["name"],
        "kind": view["kind"],
        "isGroup": bool(view["isGroup"]),
        "alsoCalled": view["alsoCalled"],
        "groups": view["groups"],
        "groupsMore": int(view["groupsMore"]),
        "ties": view["ties"],
        "tiesMore": int(view["tiesMore"]),
        "recaps": [
            {
                "ref": f"{guild_id}.{campaign_id}.{int(r['sessionAt'])}",
                "title": r["title"],
                "day": r["day"],
            }
            for r in found["recaps"]
        ],
        "myNote": found["myNote"],
        "isDm": bool(found["isDm"]),
        # Only a DM of the campaign is given this key's value; everyone else gets null.
        "dmNote": found["dmNote"] if found["isDm"] else None,
    }


async def save_note(db: Database, session: Session, ref: str, entry: str, body: object) -> None:
    """Save the caller's note on a listed NPC (an empty note clears it)."""
    guild_id, campaign_id = _parse(ref)
    entry = _entry(entry)
    _member(session, guild_id)
    if not isinstance(body, str) or len(body) > NOTE_MAX * 2 or _CONTROL.search(body):
        raise RecapError("bad_text")  # (the door checks again; a NUL never reaches the database)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_npc_note_put(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, entry, body),
        )
        row = await cur.fetchone()
    code = row["r"] if row else None
    if code is None:
        raise RecapError("not_in_server")
    if code != "ok":
        raise RecapError(str(code))


async def my_notes(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """All of the caller's own notes in one campaign, for the download, including those on names no
    longer listed."""
    guild_id, campaign_id = _parse(ref)
    _member(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_npc_notes_mine(%s, %s) AS found", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    if found is None:
        raise RecapError("not_in_server")
    return {
        "notes": [
            {
                "id": n["id"],
                "name": n["name"],
                "body": n["body"],
                "updatedAt": int(n["updatedAt"]),
                "listed": bool(n["listed"]),
            }
            for n in found
        ]
    }


async def delete_my_notes(db: Database, session: Session, ref: str) -> int:
    """Delete all of the caller's own notes in one campaign. How many were deleted."""
    guild_id, campaign_id = _parse(ref)
    _member(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_npc_notes_delete(%s, %s) AS n", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
    gone = row["n"] if row else None
    if gone is None:
        raise RecapError("not_in_server")
    return int(gone)
