"""The "Does Claude still have it?" questions for the website (#4122, the web half of #4115):
two holders of a one-of-a-kind item, and the DM's answer. The bot writes the list
(`hand_over_answer.refresh`) and applies the answers; the page reads one door
(`dmbot_web_hand_over_questions`, 0424) and queues answers through another. DMs only: anyone else
gets "not found". Ids and the names the DM sees, never a word of a transcript."""

from __future__ import annotations

import re
from typing import Any

from dmbot import progress
from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")
ANSWERS = ("no", "both", "undo")  # No, not any more / Yes, both have it / ↩ Undo that


async def waiting(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{questions: [...]}`, oldest first: each with the item (`item`, `itemName`), who had it
    first (`older`, `olderName`: the one asked about), who has it now (`newer`, `newerName`), the
    tie (`predicate`, owns or carries) and the two ties' ids (`olderTie`, `newerTie`, which name
    the question in an answer). Empty when nothing waits."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_hand_over_questions(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return {"questions": list(got)}


async def answer(
    db: Database,
    session: Session,
    ref: str,
    older: object,
    newer: object,
    how: object,
    undoes: object = None,
) -> str:
    """Queue the DM's answer to one question and return its id. `how` is `no` (No, not any more),
    `both` (Yes, both have it) or `undo` (↩ Undo that: `undoes` is the id of the `no` it takes
    back). Refusals: `bad_value` for a shape that is wrong, `not_found` (not a DM, or no such
    question or answer) and `too_soon` (60 a minute for the campaign). A second press on a question
    already being answered is given that answer's id."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if (
        not isinstance(older, str)
        or not _ID.match(older)
        or not isinstance(newer, str)
        or not _ID.match(newer)
        or how not in ANSWERS
        or (how == "undo") != isinstance(undoes, str)
        or (isinstance(undoes, str) and not _ID.match(undoes))
    ):
        raise RecapError("bad_value")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_hand_over_answer_add(%s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, older, newer, how, undoes),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


async def read_answer(db: Database, session: Session, ref: str, answer_id: str) -> dict[str, Any]:
    """Where one answer stands: `state` (waiting, working, done, failed), the `error` word when it
    failed (`gone`, `not-allowed`, `too-late`, `changed`, `failed`, `lost`), `undoable` (a `no`
    that is done: the page offers ↩ Undo that) and the shared progress; counts and times only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.match(answer_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_hand_over_answer_read(%s, %s, %s) AS r",
            (guild_id, campaign_id, answer_id),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    created = int(got["createdAt"])
    taken = int(got["claimedAt"]) // 1000  # the queue's own stamp is milliseconds
    state = str(got["state"])
    stage = {"waiting": "waiting", "working": "working", "done": "done"}.get(state, "failed")
    return {
        "state": state,
        "error": got["error"],
        "action": got["action"],
        "undoable": bool(got["undoable"]),
        "progress": progress.shape(
            stage,
            started_at=created,
            updated_at=taken if state != "waiting" and taken else created,
            now=int(got["now"]),
            reason=(str(got["error"]) or "answer_failed") if stage == "failed" else None,
        ),
    }
