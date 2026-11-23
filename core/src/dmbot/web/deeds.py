"""The website's "Deeds the party did" (#3591, core half): the deeds DMbot noticed in a session,
answered from the page as Discord's deed card does (`ui/deed_card.py`). One database door
(`dmbot_web_deeds_waiting`, migration 0604) lists the DM's waiting deeds, only those whose every
speaker is recorded now, and answers NULL to anyone who is not a DM of the campaign. The answers
(`deed-record`, `deed-not`, `deed-undo`) go through the one queue door like every website action;
the bot applies them with the function Discord's buttons call (`deed_card.apply_answer`), so no
rule is copied, and the answer comes back as a short code, never a word. The deed's words (who did
what, whom it mattered to) are the model's own telling, shown to the DM alone: never logged, and
never a transcript line, a speaker or a session."""

from __future__ import annotations

import re
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.db import Database
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")
ACTIONS = {"record": "deed-record", "not": "deed-not"}


async def read(
    db: Database,
    session: Session,
    ref: str,
    *,
    outside_engine: str | None,
    deeds_on: bool = True,
) -> dict[str, Any]:
    """`{total, deeds: [{id, doer, text, viewers, effect, kind}]}`, oldest first, at most 50, only
    deeds whose speakers are all recorded now (under the current wording and, while an outside
    company hears the voices, for that company: the bot's rule). `not_allowed` for anyone who is
    not a DM of the campaign. With the deeds switch off (`DMBOT_STORY_DEEDS`, the bot's own gate)
    nothing is listed, so the page and the Questions count never disagree (Supervisor's note on
    #5114); the plan half of the bot's rule (`deeds_on`) is the bot's to check and is checked again
    when the DM answers."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not deeds_on:
        return {"total": 0, "deeds": []}
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_deeds_waiting(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, TERMS_VERSION, outside_engine),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {
        "total": int(got["total"]),
        "deeds": [
            {
                "id": str(d["id"]),
                "doer": str(d["doer"]),
                "text": str(d["text"]),
                "viewers": [str(v) for v in d["viewers"]],
                "effect": str(d["effect"]),
                "kind": str(d["kind"]),
            }
            for d in got["deeds"]
        ],
    }


async def queue_answer(db: Database, session: Session, ref: str, body: object) -> str:
    """The DM's answer: `{"choice": "record" | "not", "deed": <32 hex>}` or `{"choice": "undo",
    "batch": <the change number a recorded answer gave>}`. The id of the answer the page polls at
    `.../web/answers/{id}`; a recorded one carries its change number."""
    choice = body.get("choice") if isinstance(body, dict) else None
    if choice == "undo":
        batch = body.get("batch") if isinstance(body, dict) else None
        if not (isinstance(batch, int) and not isinstance(batch, bool)) or not _BATCH.fullmatch(
            str(batch)
        ):
            raise RecapError("bad_action")
        action, text = "deed-undo", str(batch)
    elif choice in ACTIONS:
        deed = body.get("deed") if isinstance(body, dict) else None
        if not isinstance(deed, str) or not _ID.fullmatch(deed):
            raise RecapError("bad_action")
        action, text = ACTIONS[str(choice)], deed
    else:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action=action, target="", text=text, flag=False
    )
