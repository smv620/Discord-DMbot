"""The website's card for one kept story thread (#4476, quests slice 2, the website half), for a
DM of the campaign only: its steps, what is in the way, rewards, help, time limits and branches, as
Discord's 🧵 "📋 Steps, rewards and more" reads them. One database door (`dmbot_web_thread_card`,
migration 0511) answers NULL to anyone else, so a player or a stranger gets "not allowed" and
never a part. What is worked out ("50 gold still owed", "1 of 2 done") is worked out here with the
one set of rules the bot uses (`thread_parts.owed`, `done_count`), never stored. Words, ids and
counts only: a part's words are the DM's own, shown to that DM."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.memory import thread_parts as tp
from dmbot.memory import threads
from dmbot.memory.models import CONFIRMED
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


def _person(raw: Any) -> dict[str, str] | None:
    return None if not raw else {"id": str(raw["id"]), "name": str(raw["name"])}


def _part(thread: str, r: dict[str, Any]) -> tp.Part:
    """The door's row as the bot's own `Part`, so the counts use its rules."""
    amount = r.get("amount")
    return tp.Part(
        str(r["id"]), thread, str(r["kind"]), str(r["words"]), str(r["state"]), r.get("underId"),
        (r.get("who") or {}).get("id"), (r.get("about") or {}).get("id"),
        None if amount is None else float(amount), str(r.get("unit") or ""), None,
        str(r.get("due") or ""), str(r.get("terms") or ""), str(r.get("limitKind") or ""),
        CONFIRMED, str(r.get("source") or ""), bool(r.get("tableKnows")), None, None,
    )  # fmt: skip


async def read(db: Database, session: Session, ref: str, thread_id: str) -> dict[str, Any]:
    """The thread's card: its standing parts by kind with what is owed on each reward promised, how
    many objectives are done, and its branches. `not_allowed` for anyone who is not a DM of the
    campaign, `not_found` for a thread that is not a kept entry."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if len(thread_id) != 32 or not all(c in "0123456789abcdef" for c in thread_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_thread_card(%s, %s, %s) AS r", (guild_id, campaign_id, thread_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got.get("status") != "ok":
        raise RecapError("not_found")
    rows: list[dict[str, Any]] = got.get("parts") or []
    parts = [_part(thread_id, r) for r in rows]
    by_id = {p.id: p for p in parts}
    given = [p for p in parts if p.kind == tp.REWARD_GIVEN]
    done, total = tp.done_count(parts)
    out: list[dict[str, Any]] = []
    for r in rows:
        part = by_id[str(r["id"])]
        owed = tp.owed(part, given) if part.kind == tp.REWARD else None
        out.append(
            {
                "id": str(r["id"]),
                "kind": str(r["kind"]),
                "words": str(r["words"]),
                "state": str(r["state"]),
                "underId": r.get("underId"),
                "who": _person(r.get("who")),
                "about": _person(r.get("about")),
                "amount": part.amount,
                "unit": part.unit,
                "owed": owed,
                "due": part.due,
                "terms": part.terms,
                "limitKind": part.limit_kind,
                "dated": r.get("dueMinute") is not None,
                "fromRead": r.get("source") != "dm",
                "tableKnows": part.table_knows,
            }
        )
    thread = got["thread"]
    kind_type = str(thread.get("type") or "")
    state = str(thread.get("state") or "")
    # The giver first, then the other parts in the order Discord's list knows them, then the rest.
    order = {role: i for i, role in enumerate(threads.ROLES)}
    # (The door already lists them by name; the sort is stable, so within a role they stay A to Z.
    # A person and the entry it was merged into both on the thread show once, as one.)
    seen: set[tuple[str, str]] = set()
    unique = []
    for p in got.get("people") or []:
        key = (str(p["id"]), str(p.get("role") or ""))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    people = sorted(unique, key=lambda p: order.get(str(p.get("role") or ""), len(order)))
    return {
        "thread": {
            "id": str(thread["id"]),
            "name": str(thread["name"]),
            "kind": kind_type.replace("_", " ").capitalize(),
            "state": state,
            "stateWords": threads.words_for(kind_type).get(state, ""),
            "description": str(thread.get("description") or ""),
        },
        "people": [
            {"id": str(p["id"]), "name": str(p["name"]), "role": str(p.get("role") or "")}
            for p in people
        ],
        "waiting": int(got.get("waiting") or 0),
        "done": done,
        "of": total,
        "parts": out,
        "bigger": _person(got.get("bigger")),
        "branches": [p for p in (_person(b) for b in got.get("branches") or []) if p is not None],
    }
