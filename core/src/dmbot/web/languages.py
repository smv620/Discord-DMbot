"""A character's languages on the website (#4271, #4229 slice 1b, web half), for a campaign's DM
only. One database door (`dmbot_web_languages`, migration 0519) answers NULL to anyone else, so a
player or a stranger gets "not allowed" and never a language. This module shapes the door's answer
and queues the four changes (`lang-add`, `lang-remove`, `lang-check`, `lang-undo`) through the one
queue door, as the thread card's changes are: the bot applies each through the same store calls as
Discord's 🗣️ Languages screen, so each is one change and one Undo. A character with a sheet keeps its
languages on the sheet: the page only shows them. Words and ids only; a language's name is the DM's
own and is shown to that DM and nobody else."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory.models import STORY_LANGUAGE_MAX
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")


async def read(db: Database, session: Session, ref: str, entity: str) -> dict[str, Any]:
    """`{hasSheet, languages: [{id?, name, source}]}`: `source` is `sheet` (read only; no id), `dm`
    (the DM's word) or `read` (DMbot found it in the DM's notes and the DM has not checked it).
    `not_allowed` for anyone who is not a DM of the campaign, `not_found` for an entry that is not
    in it."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.fullmatch(entity):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_languages(%s, %s, %s) AS r", (guild_id, campaign_id, entity)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got.get("status") != "ok":
        raise RecapError("not_found")
    out: list[dict[str, Any]] = []
    for r in got.get("languages") or []:
        row: dict[str, Any] = {"name": str(r["name"]), "source": str(r["source"])}
        if r.get("id") is not None:
            row["id"] = str(r["id"])
        out.append(row)
    return {"hasSheet": bool(got.get("hasSheet")), "languages": out}


async def change(db: Database, session: Session, ref: str, entity: str, body: object) -> str:
    """Queue one change to this character's languages and give the answer's id (polled at
    `.../web/answers/{answer}`): `{action: "add", name}`, `{action: "remove", language}`,
    `{action: "check"}` (the ones DMbot found are right) or `{action: "undo", batch}` (the change
    number a change's answer gave, as text). The page's words are checked here before anything is
    queued; the bot checks everything again."""
    data = body if isinstance(body, dict) else {}
    if not _ID.fullmatch(entity):
        raise RecapError("not_found")
    action = data.get("action")
    if action == "add":
        name = data.get("name")
        if not isinstance(name, str):
            raise RecapError("bad_action")
        name = " ".join(name.split())
        if not 1 <= len(name) <= STORY_LANGUAGE_MAX or not name.isprintable():
            raise RecapError("bad_action")
        text, queued = f"{entity} {name}", "lang-add"
    elif action == "remove":
        language = data.get("language")
        if not isinstance(language, str) or not _ID.fullmatch(language):
            raise RecapError("bad_action")
        text, queued = language, "lang-remove"
    elif action == "check":
        text, queued = entity, "lang-check"
    elif action == "undo":
        batch = data.get("batch")
        if not isinstance(batch, str) or not _BATCH.fullmatch(batch):
            raise RecapError("bad_action")
        text, queued = batch, "lang-undo"
    else:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action=queued, target="", text=text, flag=False
    )
