"""What a DM types on the recap page to fix a recap (#2113): queued here, sorted by the bot, polled
by the page. The website has no AI key, so these only talk to the database's doors (a DM of the
campaign, checked there). The words are the DM's own about one recap: held in the request row for
at most an hour, never logged, never returned to anyone but the DM who sent them.
"""

from __future__ import annotations

import json
from typing import Any

from dmbot.db import Database
from dmbot.recaps.store import MAX_CORRECTIONS, MAX_TRIES
from dmbot.recaps.style import MAX_NOTES
from dmbot.web import recaps
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session

MAX_TEXT = 1000  # the form limit the Discord path uses too
MAX_IDS = 20


def _request_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 32
        or not all(c in "0123456789abcdef" for c in value)
    ):
        raise RecapError("not_found")
    return value


async def send_instructions(
    db: Database, session: Session, ref: str, text: object
) -> dict[str, Any]:
    """Queue the DM's words for the bot to sort. Nothing is stored about the recap."""
    if not isinstance(text, str) or not text.strip():
        raise RecapError("bad_text")
    if len(text) > MAX_TEXT:
        raise RecapError("too_big")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await recaps._one(conn, ref, session, marks=False)
        cur = await conn.execute(
            "SELECT dmbot_recap_request_add(%s, %s, %s, %s) AS r", (gid, cid, at, text)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_found")  # not a DM: nothing is said about the recap
    if got["status"] != "ok":
        raise RecapError(got["status"])
    return {"requestId": got["id"]}


async def read_instructions(
    db: Database, session: Session, ref: str, request_id: object
) -> dict[str, Any]:
    """Where the request is: waiting, working, done (with the points) or failed (with why)."""
    rid = _request_id(request_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await recaps._one(conn, ref, session, marks=False)
        cur = await conn.execute(
            "SELECT dmbot_recap_request_read(%s, %s, %s, %s) AS r", (gid, cid, at, rid)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_found")
    out: dict[str, Any] = {"state": got["state"]}
    if got["state"] == "done":
        out["points"] = got["answer"]
    if got["state"] == "failed":
        out["error"] = got["error"] or "sort_failed"
    return out


def _ids(value: object) -> list[str]:
    if value is None:
        return []
    if (
        not isinstance(value, list)
        or len(value) > MAX_IDS
        or not all(isinstance(v, str) and len(v) <= 8 for v in value)
    ):
        raise RecapError("bad_choice")
    return list(dict.fromkeys(value))


async def rewrite(db: Database, session: Session, ref: str, body: object) -> dict[str, Any]:
    """ "Rewrite with these": the points the DM kept go to the recap's next try and the standing
    notes, the recap goes back to the queue with one more try, and its approval ends. Only a
    request of this DM's own that is done."""
    if not isinstance(body, dict):
        raise RecapError("bad_choice")
    rid = _request_id(body.get("requestId"))
    keep_c, keep_s = _ids(body.get("corrections")), _ids(body.get("style"))
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await recaps._one(conn, ref, session, marks=False)
        cur = await conn.execute(
            "SELECT dmbot_recap_rewrite(%s, %s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (gid, cid, at, rid, keep_c, keep_s, MAX_TRIES, MAX_CORRECTIONS, MAX_NOTES),
        )
        got = (await cur.fetchone() or {}).get("r")
        if got is None:
            raise RecapError("not_found")
        if got["status"] != "ok":
            raise RecapError(got["status"])
        *_, row = await recaps._one(conn, ref, session)
    return {
        "recap": recaps._view(row),
        "triesLeft": recaps._view(row)["triesLeft"],
        "needsApproval": True,
    }


def never_logged(payload: Any) -> str:
    """For tests: the JSON of an answer, to look for words in a log."""
    return json.dumps(payload)
