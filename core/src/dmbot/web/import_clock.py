"""The import page's clock offer (#3199): where an imported session ended in game time, and the
DM's "Set it" or "Not now". The offers come from a DM-only database door
(`dmbot_web_import_clocks`), which works out the DM check itself; the choice is queued like any
name action (`clock-set`, `clock-skip`, see `all_names`) and applied by the bot, which re-checks
everything (the DM, the clock, the split party, that the clock is still behind it). The page reads
the offer's `state` again to see how it ended. A label, a state and a time: never a word of the
session.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Conn, Database
from dmbot.timebot import clock as game
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

CHOICES = {"set": "clock-set", "not-now": "clock-skip"}  # what the page sends -> the queued action


async def read_rows(
    conn: Conn, guild_id: int, campaign_id: str, import_id: str
) -> list[dict[str, Any]]:
    """The offers of one import as the database door gives them; none for anyone who is not a DM
    of the campaign."""
    cur = await conn.execute(
        "SELECT dmbot_web_import_clocks(%s, %s, %s) AS r", (guild_id, campaign_id, import_id)
    )
    found = (await cur.fetchone() or {}).get("r")
    return list(found) if isinstance(found, list) else []


def shown(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the page reads for a session: the state, the game time as DMbot words it (the page
    never formats game time) and when the state last changed. None: no offer for this session."""
    if row is None:
        return None
    label = game.short_label(int(row["minute"]))
    out: dict[str, Any] = {
        "state": row["state"],
        "label": label,
        "updatedAt": int(row["updatedAt"]),
    }
    if row["state"] == "set":
        out["setTo"] = label
    return out


async def choose(db: Database, session: Session, ref: str, import_id: str, body: object) -> str:
    """Queue the DM's answer to one session's offer and return the answer's id. The offer must
    still be waiting (the database door checks it again); a session with no offer, or one already
    answered or replaced, is "not found"; a member who is not a DM of the campaign, "not a DM"."""
    if not isinstance(body, dict):
        raise RecapError("bad_choice")
    file, choice = body.get("file"), body.get("choice")
    if not isinstance(file, str) or not 1 <= len(file) <= 200 or choice not in CHOICES:
        raise RecapError("bad_choice")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_import_clocks(%s, %s, %s) AS r", (guild_id, campaign_id, import_id)
        )
        rows = (await cur.fetchone() or {}).get("r")
        if not isinstance(rows, list):
            raise RecapError("not_a_dm")
        # (a file name is one session of an import: the import page refuses two with one name)
        mine = next((r for r in rows if r["file"] == file and r["state"] == "offered"), None)
        if mine is None:
            raise RecapError("not_found")
        cur = await conn.execute(
            "SELECT dmbot_web_name_action_add(%s, %s, NULL, %s, '', %s, '', false) AS r",
            (guild_id, campaign_id, CHOICES[str(choice)], mine["sessionId"]),
        )
        added = (await cur.fetchone() or {}).get("r")
    if added is None:
        raise RecapError("not_a_dm")
    if added["status"] != "ok":
        raise RecapError(str(added["status"]))
    return str(added["id"])
