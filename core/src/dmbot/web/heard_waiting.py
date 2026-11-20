"""The website's "Did they mean…?" (#4841 part B, slice B1): the questions DMbot kept for the DM
after a session, and the pick of a name or "Keep as heard". One door (`dmbot_web_heard_waiting`,
migration 0563, DM only) gives each open question's heard word, who said it and the names that are
safe to offer; the answer goes through the one queue door and the bot applies it with the function
Discord's buttons call (`DMBot.answer_waiting_coded`), so no rule is copied. The heard word is a
word from a transcript, shown to the DM as Discord shows it; the line itself never leaves."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_QUESTION = re.compile(r"^[0-9]{1,15}$")


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{total, questions: [{id, heard, speaker, options: [{index, id, name}]}]}`, oldest first,
    at most 50; `speaker` is null when no player character is known for them. `not_allowed` for
    anyone who is not a DM of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_heard_waiting(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {
        "total": int(got["total"]),
        "questions": [
            {
                "id": str(q["id"]),
                "heard": str(q["heard"]),
                "speaker": None if q["speaker"] is None else str(q["speaker"]),
                "options": [
                    {"index": int(o["index"]), "id": str(o["id"]), "name": str(o["name"])}
                    for o in q["options"]
                ],
            }
            for q in got["questions"]
        ],
    }


async def queue_pick(db: Database, session: Session, ref: str, body: object) -> str:
    """A name picked: `{"question": <id>, "index": 0..2}`. The id of the answer the page polls."""
    question = body.get("question") if isinstance(body, dict) else None
    index = body.get("index") if isinstance(body, dict) else None
    if not (isinstance(question, str) and _QUESTION.fullmatch(question)):
        raise RecapError("bad_action")
    if not (isinstance(index, int) and not isinstance(index, bool) and 0 <= index <= 2):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="heard-pick", target="",
        text=f"{question} {index}", flag=False,
    )  # fmt: skip


async def queue_keep(db: Database, session: Session, ref: str, body: object) -> str:
    """ "Keep as heard": `{"question": <id>}`."""
    question = body.get("question") if isinstance(body, dict) else None
    if not (isinstance(question, str) and _QUESTION.fullmatch(question)):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="heard-keep", target="", text=question, flag=False
    )


TYPED_SHOWN_MAX = 84  # what the queue's text limit leaves after the id; the bot says "too long"


async def queue_type(db: Database, session: Session, ref: str, body: object) -> str:
    """ "Type it…": `{"question": <id>, "name": <what the DM typed>}`. Only the shape is checked
    here (a text, at most what the queue can hold); whether it is a name DMbot can use is the
    bot's own check, which answers with a reason the page words. The typed words go to the queue
    and nowhere else: not a log, not an answer."""
    question = body.get("question") if isinstance(body, dict) else None
    typed = body.get("name") if isinstance(body, dict) else None
    if not (isinstance(question, str) and _QUESTION.fullmatch(question)):
        raise RecapError("bad_action")
    if not (isinstance(typed, str) and len(typed) <= TYPED_SHOWN_MAX):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="heard-type", target="",
        text=f"{question} {typed}", flag=False,
    )  # fmt: skip
