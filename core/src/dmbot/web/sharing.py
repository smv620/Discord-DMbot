"""The Shared rules page (#2460 part 2): the house-rules sets and homebrew packs a person's
campaigns publish and follow, read-only.

One database door (`dmbot_web_shared_rules`, schema.py) answers for a whole server in one read, and
only for the campaigns the person is a DM of: the people who can change sharing in Discord. A
player sees nothing here. The page says whether a share code is on but never shows it (the code is
the way in to the set, so it is read in Discord). Changing a set stays in Discord until the website
can write.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.me import MAX_GUILDS
from dmbot.web.recaps import _scoped
from dmbot.web.sessions import Session

# The page's names for the two kinds; the tables say house_rules and homebrew.
_KINDS = {"house_rules": "house-rules", "homebrew": "homebrew"}


def _kind(raw: Any) -> str:
    return _KINDS.get(str(raw), "homebrew")


def _published(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": _kind(item.get("kind")),
        "name": str(item["name"]),
        "version": int(item["version"]),
        "shared": bool(item["shared"]),
        "hasCode": bool(item["hasCode"]),
    }


def _followed(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": _kind(item.get("kind")),
        "name": str(item["name"]),
        "publisher": str(item["publisher"]),
        "version": int(item["version"]),
        "updating": bool(item["updating"]),
    }


async def list_sharing(db: Database, session: Session) -> list[dict[str, Any]]:
    """The campaigns this person runs that share or follow rules, in every server of the session's
    list: its address, name, server's name, what it publishes and what it follows."""
    out: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        for guild in session.guilds[:MAX_GUILDS]:
            await _scoped(conn, int(guild.id))
            cur = await conn.execute("SELECT dmbot_web_shared_rules(%s) AS found", (int(guild.id),))
            row = await cur.fetchone()
            for item in (row["found"] if row else None) or []:
                out.append(
                    {
                        "ref": f"{guild.id}.{item['id']}",
                        "name": str(item["name"]),
                        "serverName": guild.name,
                        "publishes": [_published(p) for p in item["publishes"]],
                        "follows": [_followed(f) for f in item["follows"]],
                    }
                )
    return out
