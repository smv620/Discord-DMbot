"""A campaign's homebrew on the website (#1525): read-only, for anyone in the server, with the
download of the same file Discord gives, for the entries the DM let the players know.

Two database doors (`dmbot_web_homebrew`, `dmbot_web_homebrew_campaigns`, schema.py) hand over ONLY
the entries marked "players may know"; a hidden entry never leaves the database, not even for the
campaign's DM, who reads those on the DM screen (CLAUDE.md, DM authority). The bot does not gate
homebrew by plan, so neither does the site. The DM adds and changes entries in Discord.

The page groups entries by kind, in the bot's order of kinds (`KINDS`), each by name as the bot
lists them. The download is `homebrew_file.write` over those entries and `homebrew_file.filename`,
so it is byte-equal to the file the bot would write for them: its own pick of the entries, as the
bot's Download button sends every entry, hidden ones included, to the DM only.

The campaign's own entries only: a followed pack's entries (#1527) are not on the page or in
the file.

Never shown: who wrote an entry, its id, or whether it came from a book. A campaign is named by
`<server>.<campaign>`, as for the house-rules page.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.recaps.notify import parse_ref
from dmbot.rules import homebrew_file
from dmbot.rules.homebrew import KINDS, Homebrew
from dmbot.web.me import MAX_GUILDS
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


def _parse(ref: str) -> tuple[int, str]:
    parsed = parse_ref(f"{ref}.0")
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


async def list_campaigns(db: Database, session: Session) -> list[dict[str, Any]]:
    """The campaigns with at least one shown entry, in every server of this session's list."""
    out: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        for guild in session.guilds[:MAX_GUILDS]:
            await _scoped(conn, int(guild.id))
            cur = await conn.execute(
                "SELECT dmbot_web_homebrew_campaigns(%s) AS found", (int(guild.id),)
            )
            row = await cur.fetchone()
            for item in (row["found"] if row else None) or []:
                out.append(
                    {
                        "ref": f"{guild.id}.{item['id']}",
                        "name": item["name"],
                        "serverName": guild.name,
                        "entries": int(item["entries"]),
                    }
                )
    return out


async def _read(db: Database, session: Session, ref: str) -> tuple[str, dict[str, Any]]:
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_homebrew(%s, %s) AS found", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    if found is None:
        raise RecapError("not_found")
    return campaign_id, found


def _kind_order(entry: dict[str, Any]) -> int:
    return KINDS.index(entry["kind"]) if entry["kind"] in KINDS else len(KINDS)


async def read_homebrew(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The shown entries, grouped by kind (the bot's order of kinds), each kind by name. Anyone
    outside the server, and a campaign that is not there, get "not found"."""
    _c, found = await _read(db, session, ref)
    entries = sorted(found["entries"], key=_kind_order)  # stable: names stay in the door's order
    return {
        "name": found["name"],
        "fileName": homebrew_file.filename(found["name"]),
        "entries": [
            {
                "kind": e["kind"],
                "name": e["name"],
                "summary": e["summary"],
                "text": e["text"],
            }
            for e in entries
        ],
    }


async def render(db: Database, session: Session, ref: str) -> tuple[str, str]:
    """(the file's name, the file's text) for the shown entries. No shown entry, no file."""
    campaign_id, found = await _read(db, session, ref)
    if not found["entries"]:
        raise RecapError("not_found")
    entries = [
        Homebrew(
            id=0,
            campaign_id=campaign_id,
            kind=e["kind"],
            name=e["name"],
            text=e["text"],
            summary=e["summary"],
            players_may_know=True,
            created_by=0,
            created_at=0,
            updated_at=int(e["updatedAt"]),
            version=int(e["version"]),
        )
        for e in found["entries"]
    ]
    return homebrew_file.filename(found["name"]), homebrew_file.write(found["name"], entries)
