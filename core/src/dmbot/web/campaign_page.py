"""One campaign's own page on the website (#2416): `GET /me/campaigns/{ref}`.

The page needs the campaign's name for its heading and whether the person runs it, to decide which
links to show. A DM also gets the campaign's current choices (who can see the DM screen, how much
DMbot says, transcript retention, how many game masters, paused) for the page's Settings group
(#2461); a player gets none of that. A DM of the campaign (its owner or a co-DM) is read through
the same row-level security as `GET /me`; anyone else in the campaign's server is a player, and the
name comes from the page's own small door (`dmbot_web_campaign_card`), which answers only for a
server in this session's own list. A
server the person is not in, a campaign that is not there and another server's campaign all give
the same "not found". Nothing here reads a secret, a story or a transcript line.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dmbot.db import Database
from dmbot.recaps.notify import parse_ref
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


def _parse(ref: str) -> tuple[int, str]:
    parsed = parse_ref(f"{ref}.0")
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


async def read_campaign(
    db: Database,
    session: Session,
    ref: str,
    *,
    imports_for: Callable[[int], bool] = lambda guild_id: False,
) -> dict[str, Any]:
    """The campaign's name, its server's name, the person's role in it ("owner", "co-dm" or
    "player") and whether importing works in its server. Raises `not_found` for anyone not in the
    server and for a campaign that isn't there."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    server_name = next((g.name for g in session.guilds if int(g.id) == guild_id), "")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT c.name, c.owner_user_id, c.dm_screen_visibility, c.dm_screen_level,"
            " c.transcript_keep_days, c.paused,"
            " (SELECT count(*) FROM campaign_dms m WHERE m.guild_id = c.guild_id"
            "    AND m.campaign_id = c.id) AS dm_count"
            " FROM campaigns c"
            " JOIN campaign_dms d ON d.campaign_id = c.id AND d.guild_id = c.guild_id"
            " WHERE c.guild_id = %s AND c.id = %s AND d.user_id = %s",
            (guild_id, campaign_id, session.user_id),
        )
        mine = await cur.fetchone()
        if mine is not None:
            name = mine["name"]
            role = "owner" if mine["owner_user_id"] == session.user_id else "co-dm"
            # The campaign's current choices, for the page's Settings group (#2461): the same ones
            # ⚙️ Settings shows. Only a DM reads the campaign's row, so only a DM gets them.
            settings: dict[str, Any] | None = {
                "visibility": str(mine["dm_screen_visibility"]),
                "level": str(mine["dm_screen_level"]),
                "keepDays": mine["transcript_keep_days"],
                "paused": bool(mine["paused"]),
                "gameMasters": int(mine["dm_count"]),
            }
        else:
            settings = None
            # Not a DM of it: a player if the campaign's own small door knows it in this server (it
            # answers for the server's members only), else it isn't there.
            cur = await conn.execute(
                "SELECT dmbot_web_campaign_card(%s, %s) AS found", (guild_id, campaign_id)
            )
            found = (await cur.fetchone() or {}).get("found")
            if found is None:
                raise RecapError("not_found")
            name = found["name"]
            role = "player"
    page: dict[str, Any] = {
        "ref": f"{guild_id}.{campaign_id}",
        "name": name,
        "serverName": server_name,
        "role": role,
        "importsOn": bool(imports_for(guild_id)),
    }
    if settings is not None:
        page["settings"] = settings
    return page
