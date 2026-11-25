"""What DMbot thinks a waiting name is, for the website's one-name page (#3602, slice 3): the
card of one name to check (`name_checks.read_checks`) with DMbot's ranked answers beside it, each
a ready answer the page sends back unchanged, as the Discord card's buttons do. The DM's alone: the
door (`dmbot_web_who_answers`) gives nothing to anyone else, a hidden entry may be named in an
answer because only a DM can ask, and nothing here reads a transcript. The label is the one
Discord's card gives an answer ("a.k.a. Ilse, a character", "new character"), worked out in one
place (`label_of`) so the two screens read the same.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.memory import who_top
from dmbot.recaps import name_answers
from dmbot.web import name_checks
from dmbot.web.recaps import RecapError, _scoped
from dmbot.web.sessions import Session

CONFIDENCE_WORDS = who_top.CONFIDENCE_WORDS
kind_word = who_top.kind_word


def label_of(row: dict[str, Any]) -> str:
    """The words on one answer: another name for an entry that is already in the story, or a new
    entry of a kind (worked out in `who_top.label`, so Discord's card says the same)."""
    if row["pick"] == "new":
        return who_top.label(new_kind=str(row.get("newKind") or ""))
    return who_top.label(
        name=str(row["targetName"]),
        kind=str(row["targetKind"]),
        tie=row.get("targetTie"),
        detail=str(row.get("targetDetail") or ""),
    )


def answer_of(row: dict[str, Any]) -> dict[str, str]:
    """The answer the page sends for it: the same body `name_checks.answer` takes."""
    if row["pick"] == "new":
        return {"action": "confirm", "kind": str(row["newKind"])}
    return {"action": "same", "known": str(row["targetId"])}


def answers_by_name(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The door's rows as each name's ranked answers, best first, with their confidence in words."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["id"]), []).append(
            {
                "label": label_of(r),
                "answer": answer_of(r),
                "confidence": str(r["confidence"]),
                "confidenceWords": CONFIDENCE_WORDS.get(str(r["confidence"]), ""),
            }
        )
    return out


async def read_answers(
    db: Database, session: Session, ref: str, ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """DMbot's ranked answers for these waiting names of the recap's campaign. A DM only; anyone
    else gets "not found". Reads the door and nothing else."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, _at = await name_checks._recap_or_other(conn, ref, session)
        await _scoped(conn, gid)
        cur = await conn.execute(
            "SELECT dmbot_web_who_answers(%s, %s, %s) AS r", (gid, cid, sorted(set(ids)))
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_found")
    return answers_by_name(list(got["answers"]))


async def read_one(
    db: Database, session: Session, ref: str, entity_id: str, *, outside_engine: str | None = None
) -> dict[str, Any]:
    """One name to check on its own page: its card from the recap's list, DMbot's answers for it
    (an empty list when it has none yet), and the kinds a new entry can be made as. A name that is
    not waiting in this recap is "not found" (the recap's list holds suggestions only; a person the
    story never named is on the Questions page, read with `read_answers`)."""
    ent = name_checks._entity_id(entity_id)
    listed = await name_checks.read_checks(db, session, ref, outside_engine=outside_engine)
    card = next((n for n in listed["names"] if str(n.get("id")) == ent), None)
    if card is None:
        raise RecapError("not_found")
    given = await read_answers(db, session, ref, [ent])
    # (the card and the answers are two reads: a name answered between them shows a card with no
    # answers, and the page's next read finds it gone)
    return {
        "places": [],
        "morePlaces": 0,
        "scene": None,  # (the scene door joins in when the scenes are built, #3596)
        "answering": False,  # (an answer queued for this name is read with the answer's own door)
        **card,
        "answers": given.get(ent, []),
        "kinds": name_answers.kinds(),
    }
