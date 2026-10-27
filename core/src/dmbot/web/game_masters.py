"""A campaign's game masters (co-DMs) for the website's Settings tab (#2768; the "co-DMs" row of the
parity table on #2554).

Read only here: who the campaign's game masters are, and who can be offered as one more. The
website's role cannot read the campaign tables, so two DM-only database doors give them
(`dmbot_web_game_masters`, `dmbot_web_game_master_candidates`; schema.py). Adding and removing
goes through the settings queue and is applied by the bot with the same store calls as Discord's
👑 Game masters (`dm_screen/game_masters.py`), so the rules cannot drift: who may change the list,
the limit (`MAX_DMS`), the plan holder who stays, the last game master who stays.

Only a first name for each person leaves, the one the campaign or the server already shows its DMs;
nobody who never agreed to be recorded is offered as a candidate, and no free-typed account reaches
the queue. A campaign is named by `<server>.<campaign>`, as for the other pages.
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.store import MAX_DMS
from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

# What a game master shows as when nothing knows their name: Discord's own word for it
# (`dm_screen/game_masters.UNREACHABLE`, kept here so the website does not import the bot's Discord
# code).
NAMELESS = "A game master"
SEARCH_MAX = 60  # characters of a search; longer is cut, never refused


def shape(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The door's list as the page's: each game master with whether their plan runs the campaign
    (so cannot be removed) and whether it is the person looking (who may leave)."""
    return {
        "max": MAX_DMS,
        "gameMasters": [
            {
                "userId": str(r["userId"]),
                "name": str(r["name"]) or NAMELESS,
                "plan": bool(r["plan"]),
                "you": bool(r["you"]),
            }
            for r in rows
        ],
    }


async def read_game_masters(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The campaign's game masters, for a DM of it. A server the person is not in is "not found";
    anyone else, a player included, is "not allowed"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_game_masters(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return shape(got)


async def read_candidates(db: Database, session: Session, ref: str, q: str) -> dict[str, Any]:
    """Who a DM can offer as a new game master, searched by name: the campaign's own people and the
    server's members who agreed to be recorded, not game masters already, at most 25."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_game_master_candidates(%s, %s, %s) AS r",
            (guild_id, campaign_id, q.strip()[:SEARCH_MAX]),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {"people": [{"userId": str(p["userId"]), "name": str(p["name"])} for p in got]}
