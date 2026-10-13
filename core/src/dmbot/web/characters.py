"""`GET /me/characters`: the signed-in person's own character sheets, read-only (#1164).

Read exactly as the data export reads them (export.read_sheet_rows): one server at a time, the
servers in this session's list only, the person's own rows only, under the website's role.
Never another player's sheet, the DM's notes or a server outside the list. Changing a sheet
stays in Discord.

Known gap: the bot shows a sheet only while the character is still the person's (not removed,
merged or given to someone else). The website's role cannot read the memory tables to check, so a
sheet of a character since removed or handed on can still be listed here until the bot cleans it
up.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.export import _iso, read_sheet_rows
from dmbot.web.me import MAX_GUILDS
from dmbot.web.sessions import Session


def class_line(snapshot: dict[str, Any] | None) -> str | None:
    """ "Ranger 3 / Druid 3", or None when the sheet has no classes (a link not read yet)."""
    classes = [
        f"{c['name']} {c['level']}"
        for c in (snapshot or {}).get("classes", [])
        if isinstance(c, dict) and c.get("name") and c.get("level")
    ]
    return " / ".join(classes) or None


async def build_characters(db: Database, session: Session) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        for guild in session.guilds[:MAX_GUILDS]:
            await conn.execute(
                "SELECT set_config('dmbot.guild_id', %s, true)", (str(int(guild.id)),)
            )
            for row in await read_sheet_rows(conn, guild.id, session.user_id):
                snapshot = row["sheet"] if isinstance(row["sheet"], dict) else None
                level = (snapshot or {}).get("level")
                cards.append(
                    {
                        "serverName": guild.name,
                        "campaign": row["campaign"],
                        # A sheet that is linked but not read yet has no name to show.
                        "name": (snapshot or {}).get("name"),
                        "classLine": class_line(snapshot),
                        "level": level or None,
                        "maxHp": (snapshot or {}).get("max_hp"),
                        "ac": (snapshot or {}).get("ac"),
                        "sheetLink": row["url"],
                        "typedIn": row["source"] == "typed",
                        "readAt": _iso(row["fetched_at"]),
                    }
                )
    return cards
