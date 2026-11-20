"""The website's "House rule updates" (#4841 part C, slice C1): the newer version of the house-rules
set a campaign follows, as the same lines Discord's update note shows. One door
(`dmbot_web_rule_update`, migration 0561) gives the two versions, DM only; the changes are worked
out here by `rules.set_diff`, the code Discord uses, so no diff rule is copied anywhere. Only rules
and relationship definitions are read, never a story word. The page does not take the update:
DMbot takes it at a session's start, as on Discord."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.rules import set_diff
from dmbot.web import relationships
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


async def read_taken(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{taken: null | {name, publisher, from, to, words, lines, more}}`: the update DMbot already
    took on the followed set (what Discord's note says, with its Undo), worked out like `read`
    from the copy before and the copy now. `not_allowed` for anyone who is not a DM."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_rule_taken(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    taken = got["taken"]
    if taken is None:
        return {"taken": None}
    rules = set_diff.diff(taken["before"], taken["after"])
    defs = set_diff.diff_definitions(taken["before"], taken["after"])
    lines = [set_diff.describe(c) for c in rules] + [set_diff.describe_definition(d) for d in defs]
    return {
        "taken": {
            "name": str(taken["name"]),
            "publisher": str(taken["publisher"]),
            "from": int(taken["from"]),
            "to": int(taken["to"]),
            "words": set_diff.count_words(rules, defs),
            "lines": lines[:MAX_LINES],
            "more": max(0, len(lines) - MAX_LINES),
        }
    }


PICK_MAX = 50  # changes one pick can name; the bot's limit too (`web_actions.PICK_MAX`)
_TAKE = re.compile(r"^[0-9]{1,15}$")


async def queue_pick(db: Database, session: Session, ref: str, body: object) -> str:
    """ "Save my picks": `{"version": <the version shown>, "kept": [<index>, …]}`: the indexes in
    the shown list (rule changes first, then relationship definitions) the DM keeps; the rest are
    left out, which ends the follow and keeps the campaign's own copy. The id of the answer the
    page polls."""
    version = body.get("version") if isinstance(body, dict) else None
    kept = body.get("kept") if isinstance(body, dict) else None
    if not (isinstance(version, int) and not isinstance(version, bool) and 0 <= version < 10**9):
        raise RecapError("bad_action")
    if not isinstance(kept, list) or len(kept) > PICK_MAX:
        raise RecapError("bad_action")
    mask = 0
    for i in kept:
        if not (isinstance(i, int) and not isinstance(i, bool) and 0 <= i < PICK_MAX):
            raise RecapError("bad_action")
        mask |= 1 << i
    return await relationships._queue(
        db, session, ref, entity=None, action="rules-update-pick", target="",
        text=f"{version} {mask:x}", flag=False,
    )  # fmt: skip


async def queue_undo(db: Database, session: Session, ref: str, take: str) -> str:
    """↩️ Undo of a pick, by the id its answer carried (this DM's own, the newest, for a day)."""
    if not _TAKE.fullmatch(take):
        raise RecapError("not_found")
    return await relationships._queue(
        db, session, ref, entity=None, action="rules-update-undo", target="", text=take,
        flag=False,
    )  # fmt: skip


async def queue_undo_last(db: Database, session: Session, ref: str, body: object) -> str:
    """↩️ Undo of the update DMbot took: `{"version": <the version now, as shown>}`."""
    version = body.get("version") if isinstance(body, dict) else None
    if not (isinstance(version, int) and not isinstance(version, bool) and 0 <= version < 10**9):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="rules-update-undo-last", target="",
        text=str(version), flag=False,
    )  # fmt: skip
