"""The website's story threads DMbot found in an upload (#4457, quests slice 1, the website half),
for a DM of the campaign only. One database door (`dmbot_web_threads`, migration 0505) answers NULL
to anyone else, so a player or a stranger gets "not allowed" and never a suggestion. This module
shapes the door's suggestions and queues the three actions (`thread-keep`, `thread-not`,
`thread-undo`) through the one queue door, as the World notes do: the bot applies each through the
same store calls as Discord's 📜 card, so a keep is one change and one Undo. Words and ids only:
the quote is the DM's own lore, shown to that DM and nobody else."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory.thread_read import ReadBranch, ReadPart
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

SUGGESTION_ID = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")
NAME_MAX = 100  # as Discord's Rename
PEOPLE_MAX = 25  # what the door's shape allows in one keep
LINES_MAX = 50  # the lines of one suggestion a keep may leave on (the door's shape)


def _line(r: dict[str, Any]) -> dict[str, Any]:
    """One line of a suggestion as Discord's card words it (`ui/thread_questions.line_for`), by the
    names the door gave: `{words, what, branch}`. No entry id is ever here: the names are stood in
    for by throwaway keys, so the card's own wording is used and not copied."""
    from dmbot.ui.thread_questions import line_for

    names: dict[str, str] = {}
    waiting: set[str] = set()

    def stand_in(key: str, who: Any) -> str | None:
        if not isinstance(who, dict) or not who.get("name"):
            return None
        names[key] = str(who["name"])
        if who.get("waiting"):
            waiting.add(key)
        return key

    line: ReadPart | ReadBranch
    if r.get("branch"):
        people = []
        for k, p in enumerate(r.get("people") or []):
            key = stand_in(f"b{k}", p)
            if key is not None:
                people.append((str(p["part"]), key))
        line = ReadBranch(
            "", str(r.get("name") or ""), "", "", str(r.get("state") or ""), tuple(people)
        )
    else:
        line = ReadPart(
            str(r.get("kind") or ""), str(r.get("words") or ""), str(r.get("state") or ""), "",
            stand_in("w", r.get("who")), str(r.get("due") or ""), str(r.get("terms") or ""),
            str(r.get("limit") or ""),
        )  # fmt: skip
    words, what = line_for(line, names, frozenset(waiting))
    return {"words": words, "what": what, "branch": bool(r.get("branch"))}


def _suggestion(r: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "kind": str(r["kind"]),
        "name": str(r["name"]),
        "aim": str(r.get("aim") or ""),
        "quote": str(r.get("quote") or ""),
        "upload": str(r.get("upload") or ""),
        "people": [
            {"index": int(p["index"]), "role": str(p["role"]), "name": str(p["name"])}
            for p in r.get("people") or []
        ],
        # what is in it, by position (a keep names the ones left on); with no entry ids
        "lines": [_line(line) for line in lines],
    }


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{suggestions: [...]}`, the cards of Discord's 📜 Story threads, oldest first.
    `not_allowed` for anyone who is not a DM of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute("SELECT dmbot_web_threads(%s, %s) AS r", (guild_id, campaign_id))
        found = await cur.fetchone()
        cur = await conn.execute(
            "SELECT dmbot_web_thread_lines(%s, %s) AS r", (guild_id, campaign_id)
        )
        lined = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    by_id = {
        str(s["id"]): list(s.get("lines") or [])
        for s in ((lined["r"] if lined and lined["r"] else {}) or {}).get("suggestions", [])
    }
    return {
        "suggestions": [_suggestion(r, by_id.get(str(r["id"]), [])) for r in got["suggestions"]]
    }


async def queue_keep(db: Database, session: Session, ref: str, body: object) -> str:
    """✅ Keep it: the suggestion, the places (in its own list) of the people left on, and the DM's
    own name for it if they renamed it."""
    data = body if isinstance(body, dict) else {}
    sid, on, name = data.get("id"), data.get("on"), data.get("name")
    lines = data.get("lines")
    if lines is not None and (
        not isinstance(lines, list)
        or len(lines) > LINES_MAX
        or not all(isinstance(i, int) and not isinstance(i, bool) and 0 <= i <= 99 for i in lines)
        or len(set(lines)) != len(lines)
    ):
        raise RecapError("bad_action")
    if (
        not isinstance(sid, str)
        or not SUGGESTION_ID.fullmatch(sid)
        or not isinstance(on, list)
        or len(on) > PEOPLE_MAX
        or not all(isinstance(i, int) and not isinstance(i, bool) and 0 <= i <= 99 for i in on)
        or len(set(on)) != len(on)
    ):
        raise RecapError("bad_action")
    text = f"{sid} {','.join(str(i) for i in sorted(on)) if on else '-'}"
    if lines is not None:  # (a page that shows the lines says which are left on; none says `-`)
        text += f" +{','.join(str(i) for i in sorted(lines)) if lines else '-'}"
    if name is not None:
        if not isinstance(name, str):
            raise RecapError("bad_action")
        name = " ".join(name.split())
        if len(name) > NAME_MAX or not name.isprintable():
            raise RecapError("bad_action")
        if name:
            text += f" {name}"
    return await relationships._queue(
        db, session, ref, entity=None, action="thread-keep", target="", text=text, flag=False
    )


async def queue_not(db: Database, session: Session, ref: str, body: object) -> str:
    """🚫 Not a story thread: one suggestion, never offered again."""
    sid = body.get("id") if isinstance(body, dict) else None
    if not isinstance(sid, str) or not SUGGESTION_ID.fullmatch(sid):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="thread-not", target="", text=sid, flag=False
    )


async def queue_undo(db: Database, session: Session, ref: str, body: object) -> str:
    """↩ Undo: of a keep by the change number its answer carried (`keep`), or of a 🚫 by the
    suggestion's id (`not`). The bot reads a keep's number again among this DM's own done keeps."""
    data = body if isinstance(body, dict) else {}
    keep, turned = data.get("keep"), data.get("not")
    if isinstance(keep, str) and turned is None and _BATCH.fullmatch(keep):
        text = f"keep {keep}"
    elif isinstance(turned, str) and keep is None and SUGGESTION_ID.fullmatch(turned):
        text = f"not {turned}"
    else:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="thread-undo", target="", text=text, flag=False
    )
