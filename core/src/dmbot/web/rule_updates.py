"""The website's "House rule updates" (#4841 part C, slice C1): the newer version of the house-rules
set a campaign follows, as the same lines Discord's update note shows. One door
(`dmbot_web_rule_update`, migration 0561) gives the two versions, DM only; the changes are worked
out here by `rules.set_diff`, the code Discord uses, so no diff rule is copied anywhere. Only rules
and relationship definitions are read, never a story word. The page does not take the update:
DMbot takes it at a session's start, as on Discord."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.rules import set_diff
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

MAX_LINES = 50  # how many changes one answer lists; the rest are counted ("and 12 more")


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{update: null | {name, publisher, from, to, words, lines, more}}`: `words` is core's count
    ("3 rules and 1 relationship"), `lines` core's one line for each change (rules first, then
    relationship definitions), `more` how many were left out. `not_allowed` for anyone who is not a
    DM of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_rule_update(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    update = got["update"]
    if update is None:
        return {"update": None}
    rules = set_diff.diff(update["mine"], update["latest"])
    defs = set_diff.diff_definitions(update["mine"], update["latest"])
    lines = [set_diff.describe(c) for c in rules] + [set_diff.describe_definition(d) for d in defs]
    return {
        "update": {
            "name": str(update["name"]),
            "publisher": str(update["publisher"]),
            "from": int(update["from"]),
            "to": int(update["to"]),
            "words": set_diff.count_words(rules, defs),
            "lines": lines[:MAX_LINES],
            "more": max(0, len(lines) - MAX_LINES),
        }
    }
