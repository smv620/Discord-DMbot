"""The website's "Names that look alike" (#5259, Discord's 🔗 Look-alikes): the groups of
confirmed names that may be one name written several ways, for a DM of the campaign.

The database door (`dmbot_web_look_alike_names`, migration 0623) gives the names and the pairs the
DM said are "Not the same"; the groups come from the bot's own pure finder (`memory/lookalikes.py`),
so the two places list the same groups and the Questions count agrees with the list. The kind is
Discord's short word. Counts and names only, nothing logged.
"""

from __future__ import annotations

import asyncio
from typing import Any

from dmbot.db import Database
from dmbot.memory.lookalikes import Name, family_of
from dmbot.memory.lookalikes import find as find_look_alikes
from dmbot.memory.name_words import KIND_SHORT, kind_key
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


async def read_groups(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{groups: [[{id, name, kind, heard}, …], …], count}`, most heard first in each group, as
    Discord shows them. Anyone who is not a DM of the campaign (or in the server) gets "not
    found"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_look_alike_names(%s, %s) AS r", (guild_id, campaign_id)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_found")
    kinds: dict[str, str] = {}
    names: list[Name] = []
    for row in got["names"]:
        key = kind_key(str(row["type"]), row.get("role"))
        kinds[str(row["id"])] = KIND_SHORT.get(key, "other")
        names.append(
            Name(str(row["id"]), str(row["name"]), int(row["heard"]), family_of(row["type"]))
        )
    apart = {(str(a), str(b)) for a, b in got["apart"]}
    # Off the event loop: a campaign of thousands of names takes a moment (as the bot does it).
    groups = await asyncio.to_thread(lambda: find_look_alikes(names, apart=apart))
    return {
        "groups": [
            [
                {"id": n.id, "name": n.name, "kind": kinds.get(n.id, "other"), "heard": n.mentions}
                for n in group
            ]
            for group in groups
        ],
        "count": len(groups),
    }
