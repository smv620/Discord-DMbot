"""The website's "Met but not named" list (#4084, the web half of #4045): the creatures and people
the party met at the table that nobody named, for the DM only. One database door
(`dmbot_web_met_list`, 0437) lists them after the consent re-check; the DM's three answers (give
them a name, say they are a name the campaign has, remove) are queued for the bot on the
name-action queue, and the page polls `all_names.read_action`. Only the words, the count and the
session's date leave here: never a line's time or who spoke it."""

from __future__ import annotations

import re
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")
ACTIONS = {"name": "met-name", "join": "met-join", "remove": "met-remove"}


async def listed(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{items: [{id, what, count, sessionStartedAt}]}`, newest session first. `count` 1 is one
    being, 0 is "several". Anyone who is not a DM of the campaign is refused (`not_allowed`)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_met_list(%s, %s, %s) AS r", (guild_id, campaign_id, TERMS_VERSION)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {
        "items": [
            {
                "id": str(m["id"]),
                "what": str(m["what"]),
                "count": int(m["count"]),
                "sessionStartedAt": None
                if m["sessionStartedAt"] is None
                else int(m["sessionStartedAt"]),
            }
            for m in got
        ]
    }


def _arguments(body: object) -> tuple[str, str, str]:
    """`{action: name | join | remove, name?, target?}` as the door's (action, target, text)."""
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    action = ACTIONS.get(str(body.get("action")))
    if action is None:
        raise RecapError("bad_action")
    name, target = body.get("name"), body.get("target")
    if action == "met-name":
        if not isinstance(name, str) or not name.strip() or target is not None:
            raise RecapError("bad_name")
        return action, "", name
    if name is not None:
        raise RecapError("bad_action")
    if action == "met-join":
        if not isinstance(target, str) or not _ID.fullmatch(target):
            raise RecapError("bad_target")
        return action, target, ""
    if target is not None:
        raise RecapError("bad_action")
    return action, "", ""


async def queue(db: Database, session: Session, ref: str, met_id: str, body: object) -> str:
    """Queue one answer about one row of the list and return its id (polled like any name
    action). Refusals: `not_found` (no such row here), `not_allowed` (not a DM), `bad_action`,
    `bad_name`, `bad_target` (the entry to join is not a confirmed name) and `too_soon`."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.fullmatch(met_id):
        raise RecapError("not_found")
    action, target, text = _arguments(body)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, met_id, action, "", target, text, False),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError("bad_name" if got["status"] == "bad_text" else str(got["status"]))
    return str(got["id"])
