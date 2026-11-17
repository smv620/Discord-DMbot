"""The 🤫 Known or secret? questions on the website (#4598, the web half of #4524 slice 2), for a
campaign's DM only. One database door (`dmbot_web_secret_questions`, migration 0527) answers NULL
to anyone else, so a player or a stranger gets "not allowed" and never a question. This module
words each question as Discord's card does (the words are `web/secret_question_words.py`, kept
equal to `ui/secret_questions.py`'s by a test: the website may not import the Discord layer) and
queues the three changes (`secret-keep`, `secret-make`, `secret-undo`) through the one queue
door, as the languages' are: the bot applies each through the store call Discord's card makes
(`answer_secret_question`, `undo`), so a make is one change and one Undo. A 🌍 World read's
question is not here: it is answered one upload at a time, in Discord. The label is the DM's own
upload or session label; nothing here is a transcript's words."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory import link_words
from dmbot.web import relationships
from dmbot.web import secret_question_words as words
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")


def _connection(r: dict[str, Any]) -> str:
    """A connection in words, "Ash Morrow works for Master Orlen" (plain text: the page escapes)."""
    said = link_words.link_words(
        str(r["predicate"]), str(r["detail"]), True, str(r.get("subjectType") or ""), feeling=True
    )
    return f"{r['subject']} {said} {r['object']}"


def shape(row: dict[str, Any]) -> dict[str, Any]:
    """One question as the page draws it: `id`, `kind`, `heading` (the question in a line), the
    `source` line, and `keep` and `make` (each button's words and what it does). A gone item says
    so and both answers only clear it, as Discord's card does."""
    item = row.get("item")
    kind = str(row["kind"])
    label = str(row.get("label") or "")
    out: dict[str, Any] = {"id": str(row["id"]), "kind": kind}
    out["source"] = words.source(label)
    if not isinstance(item, dict):
        out.update(words.gone(kind))
        return out
    table = item["table"]
    if table == "entity":
        thing, what = str(item["name"]), "clue" if item.get("type") == "clue" else "name"
        owner = ""
    elif table == "alias":
        thing, what, owner = str(item["text"]), "other name", str(item["owner"])
    else:
        thing, what, owner = _connection(item), "connection", ""
    new = _connection(row["new"]) if isinstance(row.get("new"), dict) else ""
    knowers = [str(k) for k in row.get("knowers") or []]
    out.update(words.card(kind, thing, what, owner, new, knowers, bool(row.get("limits"))))
    return out


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{questions: [...]}`, each as `shape` words it, oldest first. `not_allowed` for anyone who
    is not a DM of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_secret_questions(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return {"questions": [shape(r) for r in got]}


async def change(db: Database, session: Session, ref: str, body: object) -> str:
    """Queue one answer and give its id (polled at `.../web/answers/{answer}`): `{action: "keep",
    question}` (the answer that changes nothing), `{action: "make", question}` (the one that
    changes something) or `{action: "undo", batch}` (the change number a make's answer gave, as
    text). The shape is checked here before anything is queued; the bot checks everything again."""
    data = body if isinstance(body, dict) else {}
    action = data.get("action")
    if action in ("keep", "make"):
        question = data.get("question")
        if not isinstance(question, str) or not _ID.fullmatch(question):
            raise RecapError("bad_action")
        text, queued = question, f"secret-{action}"
    elif action == "undo":
        batch = data.get("batch")
        if not isinstance(batch, str) or not _BATCH.fullmatch(batch):
            raise RecapError("bad_action")
        text, queued = batch, "secret-undo"
    else:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action=queued, target="", text=text, flag=False
    )
