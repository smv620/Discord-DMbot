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
from dmbot.memory import tags, who_top
from dmbot.memory import who_ranking as ranking
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


RANKED_NAMES = 20  # names whose guesses are filled in one read: the page asks for one
DETAIL_ENTRIES = 20  # entries whose detail is read in one go (the door's own limit)


def _ranked_for(
    asking: dict[str, Any],
    candidates: list[ranking.Candidate],
    seen: set[str],
    room: int,
) -> list[ranking.Candidate]:
    """The entries `who_ranking.rank` finds for one waiting name, best first, as Discord's card
    fills them (`who_top.top_for`): none already offered, at most `room`, ranked by the same code.
    A hidden entry is never a candidate here (the door leaves it out: the page's own answer door
    refuses it), so what is ranked is the same code run on what the page may offer."""
    ask = ranking.Ask(str(asking["name"]), str(asking["kind"]))
    found: list[ranking.Candidate] = []
    for r in ranking.rank(ask, candidates, include_hidden=False):
        if len(found) >= room:
            break
        if r.candidate.id in seen:
            continue
        seen.add(r.candidate.id)
        found.append(r.candidate)
    return found


def _detail(entry: dict[str, Any] | None, name: str, summaries: bool) -> str:
    """An entry's most significant detail, worded by the function Discord's label uses."""
    if not entry:
        return ""
    ties = [
        tags.TagTie(
            str(t["predicate"]),
            str(t["other"]),
            bool(t["outgoing"]),
            confirmed=True,
            detail=str(t.get("detail") or ""),
        )
        for t in entry.get("ties", [])
    ]
    summary = entry.get("summary") if summaries else None
    return who_top.detail_of(
        name, summary if isinstance(summary, str) else None, tags.detail_for(ties)
    )


async def read_answers(
    db: Database, session: Session, ref: str, ids: list[str], *, card_summary: bool = False
) -> dict[str, list[dict[str, Any]]]:
    """DMbot's ranked answers for these waiting names of the recap's campaign, as Discord's card
    gives them (#5264): the AI's stored answers first, then the entries `who_ranking.rank` finds
    by code, at most five in all, each with its most significant detail (the card's sentence while
    `card_summary` is on, else its strongest tie, else its kind). A DM only; anyone else gets "not
    found". Reads three doors and nothing else; ids, names, kinds and words, never a line."""
    wanted = sorted(set(ids))
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, _at = await name_checks._recap_or_other(conn, ref, session)
        await _scoped(conn, gid)
        cur = await conn.execute(
            "SELECT dmbot_web_who_answers(%s, %s, %s) AS r", (gid, cid, wanted)
        )
        got = (await cur.fetchone() or {}).get("r")
        if got is None:
            raise RecapError("not_found")
        rows = [dict(r) for r in got["answers"]]
        stored: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            stored.setdefault(str(r["id"]), []).append(r)
        asking: list[dict[str, Any]] = []
        candidates: list[ranking.Candidate] = []
        ranked_in: dict[str, Any] = {}
        # Only the names with room for a guess are asked about: when every one already has five
        # stored answers, the candidates (up to 5,000 entries) are not read at all.
        roomy = [n for n in wanted[:RANKED_NAMES] if len(stored.get(n, [])) < who_top.TOP]
        if roomy:
            cur = await conn.execute(
                "SELECT dmbot_web_who_candidates(%s, %s, %s) AS r", (gid, cid, roomy)
            )
            ranked_in = (await cur.fetchone() or {}).get("r") or {}
            asking = list(ranked_in.get("asking", []))
            candidates = [
                ranking.Candidate(
                    str(c["id"]),
                    str(c["kind"]),
                    tuple(dict.fromkeys([str(c["name"]), *map(str, c["names"])])),
                )
                for c in ranked_in.get("candidates", [])
            ]
        names = {str(c["id"]): str(c["name"]) for c in ranked_in.get("candidates", [])}
        chosen: dict[str, list[dict[str, Any]]] = {}
        for ask in asking:
            mine = stored.get(str(ask["id"]), [])[: who_top.TOP]
            seen = {str(r["targetId"]) for r in mine if r.get("targetId")}
            room = who_top.TOP - len(mine)
            extra = _ranked_for(ask, candidates, seen, room) if room > 0 else []
            chosen[str(ask["id"])] = [
                *mine,
                *(
                    {
                        "id": str(ask["id"]),
                        "pick": "entity",
                        "targetId": c.id,
                        "targetName": names.get(c.id, c.names[0]),
                        "targetKind": c.kind,
                        "confidence": "",
                    }
                    for c in extra
                ),
            ]
        for key, mine in stored.items():  # (a name the door did not list as asking: as stored)
            chosen.setdefault(key, mine[: who_top.TOP])
        ordered: list[str] = []
        for rows_of in chosen.values():
            for r in rows_of:
                if r.get("targetId") and str(r["targetId"]) not in ordered:
                    ordered.append(str(r["targetId"]))
        details: dict[str, Any] = {}
        if ordered:
            cur = await conn.execute(
                "SELECT dmbot_web_who_details(%s, %s, %s) AS r",
                (gid, cid, ordered[:DETAIL_ENTRIES]),
            )
            details = (await cur.fetchone() or {}).get("r") or {}
    flat: list[dict[str, Any]] = []
    for rows_of in chosen.values():
        for r in rows_of:
            if r.get("targetId"):
                r["targetDetail"] = _detail(
                    details.get(str(r["targetId"])), str(r["targetName"]), card_summary
                )
            flat.append(r)
    return answers_by_name(flat)


async def read_one(
    db: Database,
    session: Session,
    ref: str,
    entity_id: str,
    *,
    outside_engine: str | None = None,
    card_summary: bool = False,
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
    given = await read_answers(db, session, ref, [ent], card_summary=card_summary)
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
