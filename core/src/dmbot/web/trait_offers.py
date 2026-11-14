"""The lineage and class questions waiting for the DM, for the website (#3946, the web half of
#3926): what the lore said about a character the campaign already has, and what DMbot has for them
now. One database door (`dmbot_web_trait_offers`, 0410) answers it for a DM of the campaign; anyone
else gets "not found". The quote and the upload's label are the DM's own words: DM only."""

from __future__ import annotations

import re
from typing import Any

from dmbot import progress
from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


async def waiting(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{offers: [...]}`: each with the character (`entity`, `who`), the trait (`kind` species or
    class, `name`, `known`, `edition`), what DMbot has now (`current` lineage or null, `classes`),
    the upload's `upload` label and the reader's `quote`. Empty when nothing waits."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_trait_offers(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return {"offers": list(got)}


# ---- the DM's answer: queued for the bot, read back by polling (0411) ----------------

_ID = re.compile(r"^[0-9a-f]{32}$")
_ENTITY = re.compile(r"^[0-9a-f]{32}$")
KINDS = ("species", "class")
ANSWERS = ("keep", "never")  # Yes, change it / No, leave it


async def answer(
    db: Database, session: Session, ref: str, entity: object, kind: object, key: object, how: object
) -> str:
    """Queue the DM's answer to one question and return its id. `how` is `keep` (Yes, change it)
    or `never` (No, leave it). Refusals: `bad_value` for a shape that is wrong, `not_found` (not a
    DM, or no such question waiting) and `too_soon` (60 a minute for the campaign). A second press
    on a question already being answered is given that answer's id."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if (
        not isinstance(entity, str)
        or not _ENTITY.match(entity)
        or kind not in KINDS
        or not isinstance(key, str)
        or not 1 <= len(key) <= 100
        or how not in ANSWERS
    ):
        raise RecapError("bad_value")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_trait_answer_add(%s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, entity, kind, key, how),
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
    failed, `batch` (the change a Yes made, for Undo) and the shared progress; counts and times
    only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.match(answer_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_trait_answer_read(%s, %s, %s) AS r",
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
        "batch": got["batch"],
        "progress": progress.shape(
            stage,
            started_at=created,
            updated_at=taken if state != "waiting" and taken else created,
            now=int(got["now"]),
            reason=(str(got["error"]) or "answer_failed") if stage == "failed" else None,
        ),
    }
