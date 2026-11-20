"""The website's pack updates (#4841 part C, the packs slice): the newer versions of the homebrew
packs a campaign follows, and the updates DMbot already took, as the same lines Discord's update
note shows. One door (`dmbot_web_pack_updates`, migration 0566) gives the copies, DM only; the
changes are worked out here by `rules.pack_diff`, the code Discord uses, so no diff rule is copied.
A damaged copy is left out, as the bot's `pending_pack_updates` does. Only a pack's own entries
are read, never a story word."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.rules import pack_diff
from dmbot.rules.pack_content import BadPack, valid_content
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

# The door returns whole copies (at most five packs, two copies each): the lines are worked out here
# from all of an entry's words, so nothing is cut before the diff.
MAX_LINES = 50  # how many changes one pack lists; the rest are counted ("and 12 more")


def _lines(old: Any, new: Any) -> dict[str, Any] | None:
    """`{words, lines, more}` for what differs between two copies, or None for a damaged one."""
    try:
        valid_content(old)  # (only the copy the campaign has, as `pending_pack_updates` does)
    except BadPack:
        return None
    changes = pack_diff.diff(old, new)
    lines = [pack_diff.describe(c) for c in changes]
    return {
        "words": pack_diff.count_words(changes),
        "lines": lines[:MAX_LINES],
        "more": max(0, len(lines) - MAX_LINES),
    }


def _item(row: dict[str, Any], old_key: str, new_key: str) -> dict[str, Any] | None:
    lines = _lines(row[old_key], row[new_key])
    if lines is None:
        return None
    return {
        "setId": str(row["setId"]),
        "name": str(row["name"]),
        "publisher": str(row["publisher"]),
        "from": int(row["from"]),
        "to": int(row["to"]),
        **lines,
    }


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{waiting: [...], taken: [...]}`, one item per followed pack (at most five): `{setId, name,
    publisher, from, to, words, lines, more}`. `not_allowed` for anyone who is not a DM."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_pack_updates(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    waiting = [_item(r, "mine", "latest") for r in got["waiting"]]
    taken = [_item(r, "before", "after") for r in got["taken"]]
    return {
        "waiting": [i for i in waiting if i is not None],
        "taken": [i for i in taken if i is not None],
    }


_PACK = re.compile(r"^[0-9a-f]{32}$")


async def queue_undo_last(db: Database, session: Session, ref: str, body: object) -> str:
    """↩️ Undo of the update DMbot took on a pack: `{"setId": <the pack's id>, "version": <the
    version now, as shown>}`."""
    set_id = body.get("setId") if isinstance(body, dict) else None
    version = body.get("version") if isinstance(body, dict) else None
    if not (isinstance(set_id, str) and _PACK.fullmatch(set_id)):
        raise RecapError("bad_action")
    if not (isinstance(version, int) and not isinstance(version, bool) and 0 <= version < 10**9):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="pack-update-undo-last", target="",
        text=f"{set_id} {version}", flag=False,
    )  # fmt: skip


PICK_MAX = 50  # changes one pick can name; the bot's limit too (`web_actions.PICK_MAX`)
_TAKE = re.compile(r"^[0-9]{1,15}$")


async def queue_pick(db: Database, session: Session, ref: str, body: object) -> str:
    """ "Save my picks" on a pack: `{"setId", "version": <the version shown>, "kept": [<index>…]}`:
    the indexes in the shown list the DM keeps; the rest are left out, which ends the follow and
    copies the pack into the campaign's own homebrew. The id of the answer the page polls."""
    set_id = body.get("setId") if isinstance(body, dict) else None
    version = body.get("version") if isinstance(body, dict) else None
    kept = body.get("kept") if isinstance(body, dict) else None
    if not (isinstance(set_id, str) and _PACK.fullmatch(set_id)):
        raise RecapError("bad_action")
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
        db, session, ref, entity=None, action="pack-update-pick", target="",
        text=f"{set_id} {version} {mask:x}", flag=False,
    )  # fmt: skip


async def queue_undo(db: Database, session: Session, ref: str, take: str) -> str:
    """↩️ Undo of a pack pick, by the id its answer carried (this DM's own, the newest, a day)."""
    if not _TAKE.fullmatch(take):
        raise RecapError("not_found")
    return await relationships._queue(
        db, session, ref, entity=None, action="pack-update-undo", target="", text=take,
        flag=False,
    )  # fmt: skip
