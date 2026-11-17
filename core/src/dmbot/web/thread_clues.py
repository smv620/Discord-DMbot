"""A story thread's clues on the website (#4625, the web half of quests slice 6a), for a campaign's
DM only. One database door (`dmbot_web_thread_clues`, migration 0533) answers NULL to anyone else:
the tie of a clue to a thread, its "false lead" word and the count of clues found are the DM's
alone (Supervisor's condition on #4624), so a player or a stranger gets "not allowed" and no clue.
This module words the count as `thread_clues.found_text` does (no percentage, no total DMbot
doesn't know) and queues the four changes (`clue-add`, `clue-word`, `clue-off`, `clue-undo`)
through the one queue door: the bot applies each through the same store calls as Discord's 🔎 Clues
screen, so each is one change and one Undo. "Found" is the door's own reading of who-knows rows,
and Discord's is wider (the test lists each difference): the page never says "found" where Discord
says "not found yet". Words and ids only."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory import thread_clues as clues_mod
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")
NAME_MAX = 100  # a clue's name, as the store's own limit for an entry's name


async def read(db: Database, session: Session, ref: str, thread: str) -> dict[str, Any]:
    """`{count, clues: [{id, name, falseLead, found}]}` for one thread, A to Z; `count` is the
    DM's words ("2 of 3 clues found (1 is a false lead). Not yet found: 1.") or "" with no clue
    tied. `not_allowed` for anyone who is not a DM of the campaign, `not_found` for an entry that
    is not in it."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.fullmatch(thread):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_thread_clues(%s, %s, %s) AS r", (guild_id, campaign_id, thread)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got.get("status") != "ok":
        raise RecapError("not_found")
    rows = got.get("clues") or []
    tied = [
        clues_mod.ThreadClue(
            str(r["id"]), "", str(r["name"]), bool(r["falseLead"]), bool(r["found"])
        )
        for r in rows
    ]
    return {
        "count": clues_mod.found_text(tied),
        "clues": [
            {
                "id": str(r["id"]),
                "name": str(r["name"]),
                "falseLead": bool(r["falseLead"]),
                "found": bool(r["found"]),
            }
            for r in rows
        ],
    }


async def change(db: Database, session: Session, ref: str, thread: str, body: object) -> str:
    """Queue one change to this thread's clues and give the answer's id (polled at
    `.../web/answers/{answer}`): `{action: "add", name}` (the campaign's clue of that name, else a
    new one hidden from the players), `{action: "word", clue, falseLead}`, `{action: "off", clue}`
    (take it off this thread) or `{action: "undo", batch}` (the change number an answer gave, as
    text). The page's words are checked here before anything is queued; the bot checks again."""
    data = body if isinstance(body, dict) else {}
    if not _ID.fullmatch(thread):
        raise RecapError("not_found")
    action = data.get("action")
    if action == "add":
        name = data.get("name")
        if not isinstance(name, str):
            raise RecapError("bad_action")
        name = " ".join(name.split())
        if not 1 <= len(name) <= NAME_MAX or not name.isprintable():
            raise RecapError("bad_action")
        text, queued = f"{thread} {name}", "clue-add"
    elif action in ("word", "off"):
        clue = data.get("clue")
        if not isinstance(clue, str) or not _ID.fullmatch(clue):
            raise RecapError("bad_action")
        if action == "word":
            lead = data.get("falseLead")
            if not isinstance(lead, bool):
                raise RecapError("bad_action")
            text, queued = f"{thread} {clue} {'false' if lead else 'true'}", "clue-word"
        else:
            text, queued = f"{thread} {clue}", "clue-off"
    elif action == "undo":
        batch = data.get("batch")
        if not isinstance(batch, str) or not _BATCH.fullmatch(batch):
            raise RecapError("bad_action")
        text, queued = batch, "clue-undo"
    else:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action=queued, target="", text=text, flag=False
    )
