"""The website's "Still in the party?" (#4895, party knowledge slice 6a, web half; Supervisor's
ruling on slice 6): "Okthandom is marked dead. Mark Okthandom as no longer in the party?", answered
from the Questions page as in Discord. One database door (`dmbot_web_leave_questions`, migration
0572) lists the DM's waiting questions by character names and ids only, and answers NULL to anyone
who is not a DM of the campaign. The answers (`leave-yes`, `leave-no`) go through the one queue door
like every website action; the bot runs the store's own `staying_answer_left` and
`staying_answer_stays`, so "Left the party" is one change with an Undo (the existing `join-undo`,
which asks the question again) and another DM's earlier answer comes back as `gone`. "Later" is the
page's own and writes nothing. Names, ids and counts only: no word of the story."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")
ANSWERS = {"left": "leave-yes", "stays": "leave-no"}


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{groups: [{group, kind, characters: [{id, name}]}]}`: each message's characters (A to Z),
    and which question it is (`died`: marked dead, `leave`: a goodbye). `not_allowed` for anyone who
    is not a DM of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_leave_questions(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {
        "groups": [
            {
                "group": str(g["group"]),
                "kind": str(g["kind"]),
                "characters": [
                    {"id": str(c["id"]), "name": str(c["name"])} for c in g["characters"]
                ],
            }
            for g in got
        ]
    }


async def queue_answer(db: Database, session: Session, ref: str, body: object) -> str:
    """One character's answer: `{"character": <32 hex>, "answer": "left" | "stays"}`. The id of
    the answer the page polls at `.../web/answers/{id}`; "left" carries its change number."""
    character = body.get("character") if isinstance(body, dict) else None
    answer = body.get("answer") if isinstance(body, dict) else None
    if not isinstance(character, str) or not _ID.fullmatch(character) or answer not in ANSWERS:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action=ANSWERS[str(answer)], target="", text=character,
        flag=False,
    )  # fmt: skip
