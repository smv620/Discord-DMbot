"""The scene of a question, read for the website (#3644, website half of #3596).

DMbot keeps a short AI summary of the scene a waiting name or an unnamed person came up in
(`memory_scenes`, kept until the question is answered). The only door that reads it is
`dmbot_web_scenes`: it gives nothing to anyone who is not a DM of the campaign, and only for a
question still open. This module asks that door for the rows a page is about to show, at most
`MAX_IDS` at a time, and attaches `scene` to each row that has one, so a page with no scene has no
key and draws nothing. The text is plain text for the page to show as it is; it is never logged.
"""

from __future__ import annotations

from typing import Any

MAX_IDS = 200  # as the database's door
ENTITY = "entity"  # the scene of an entry (a suggested name, or an unnamed person)


async def read(conn: Any, guild_id: int, campaign_id: str, ids: list[str]) -> dict[str, str]:
    """The scenes of these entries, by entry id. Empty for anyone who is not a DM of the campaign
    (the door says so by answering nothing), and for entries with no scene or an answered question.
    `conn` is a user connection already scoped to the guild (`recaps._scoped`)."""
    wanted = [i for i in ids if isinstance(i, str)][:MAX_IDS]
    if not wanted:
        return {}
    cur = await conn.execute(
        "SELECT dmbot_web_scenes(%s, %s, %s, %s) AS r", (guild_id, campaign_id, ENTITY, wanted)
    )
    got = (await cur.fetchone() or {}).get("r")
    scenes = got.get("scenes") if isinstance(got, dict) else None
    if not isinstance(scenes, dict):
        return {}
    return {str(k): str(v) for k, v in scenes.items() if isinstance(v, str) and v.strip()}


async def attach(
    conn: Any, guild_id: int, campaign_id: str, rows: list[dict[str, Any]], *, only: Any = None
) -> None:
    """Give each of `rows` (dicts with an `id`) its `scene` when it has one. `only` picks the rows
    to ask for (a list page asks only for its unnamed people); every other row is left as it is."""
    asked = [r for r in rows if only is None or only(r)]
    scenes = await read(conn, guild_id, campaign_id, [str(r["id"]) for r in asked])
    for row in asked:
        text = scenes.get(str(row["id"]))
        if text:
            row["scene"] = text
