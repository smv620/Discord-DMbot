"""The website's join question (#4778 part b, party knowledge slice 2b; Supervisor's ruling on
#4537): "Mira has joined the party. What does Mira know about what came before?", answered from
the page as in Discord. One database door (`dmbot_web_join_questions`, migration 0554) lists the
DM's waiting groups, by character names only, and answers NULL to anyone who is not a DM of the
campaign. The answers (`join-everyone`, `join-party`) and the Undo (`join-undo`) go through the
one queue door like every website action; the bot runs the store's own calls, so each is one
change and one Undo, and another DM's earlier answer comes back as `gone`. Names, ids and counts
only: no word of the story."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

GROUP = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")
ANSWERS = {"everyone": "join-everyone", "party": "join-party"}


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{groups: [{group, answer, names, kind}]}`: each message's characters (A to Z), which
    question it is (`join`: new to the party, `back`: a character coming back, whose words differ,
    #4791) and whether it still waits (`waiting`) or was answered "Only what everyone knows"
    (`everyone`, which "What the party knows" may still follow). `not_allowed` for anyone who is
    not a DM of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_join_questions(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {
        "groups": [
            {
                "group": str(g["group"]),
                "answer": str(g["answer"]),
                "kind": str(g.get("kind", "join")),
                "names": [str(n) for n in g["names"]],
            }
            for g in got
        ]
    }


async def queue_answer(db: Database, session: Session, ref: str, body: object) -> str:
    """The DM's answer: `{"group": <32 hex>, "answer": "everyone" | "party"}`. The id of the answer
    the page polls at `.../web/answers/{id}`; a "party" answer carries its change number."""
    group = body.get("group") if isinstance(body, dict) else None
    answer = body.get("answer") if isinstance(body, dict) else None
    if not isinstance(group, str) or not GROUP.fullmatch(group) or answer not in ANSWERS:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action=ANSWERS[str(answer)], target="", text=group,
        flag=False,
    )  # fmt: skip


async def queue_undo(db: Database, session: Session, ref: str, batch: str) -> str:
    """↩ Undo of a "What the party knows" by the change number its answer carried. The bot reads
    the number again in this DM's own done answers before it undoes anything."""
    if not _BATCH.fullmatch(batch):
        raise RecapError("not_found")
    return await relationships._queue(
        db, session, ref, entity=None, action="join-undo", target="", text=batch, flag=False
    )
