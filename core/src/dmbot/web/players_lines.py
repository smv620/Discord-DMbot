"""The line for the players of a name waiting to be checked, read for the website (#3833).

A lore upload shared with everyone fills each suggested name's line for the players (#2967); the
Discord card shows it as "Players know: ..." so the DM sees what confirming also confirms. The only
door that reads it is `dmbot_web_players_lines` (DM of the campaign only, entries still waiting).
This module asks it for the cards a page is about to show and attaches `playersText` to each card
that has a line, so a card with none has no key and draws nothing: DMbot never makes one up. The
text is shown whole, as written, and is never logged.
"""

from __future__ import annotations

from typing import Any

MAX_IDS = 200  # as the database's door


async def read(conn: Any, guild_id: int, campaign_id: str, ids: list[str]) -> dict[str, str]:
    """The lines of these entries, by entry id. Empty for anyone who is not a DM of the campaign
    (the door answers nothing) and for entries with no line. `conn` is a user connection already
    scoped to the guild (`recaps._scoped`)."""
    wanted = [i for i in ids if isinstance(i, str)]
    found: dict[str, str] = {}
    for start in range(0, len(wanted), MAX_IDS):
        cur = await conn.execute(
            "SELECT dmbot_web_players_lines(%s, %s, %s) AS r",
            (guild_id, campaign_id, wanted[start : start + MAX_IDS]),
        )
        got = (await cur.fetchone() or {}).get("r")
        lines = got.get("lines") if isinstance(got, dict) else None
        if not isinstance(lines, dict):
            return {}  # (not a DM: the check is the same for every slice, so nothing is kept)
        found.update({str(k): str(v) for k, v in lines.items() if isinstance(v, str) and v.strip()})
    return found


async def attach(conn: Any, guild_id: int, campaign_id: str, rows: list[dict[str, Any]]) -> None:
    """Give each of `rows` (dicts with an `id`) its `playersText` when it has a line."""
    lines = await read(conn, guild_id, campaign_id, [str(r["id"]) for r in rows])
    for row in rows:
        text = lines.get(str(row["id"]))
        if text:
            row["playersText"] = text
