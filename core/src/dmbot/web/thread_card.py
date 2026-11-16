"""The website's card for one kept story thread (#4476, quests slice 2, the website half), for a
DM of the campaign only: its steps, what is in the way, rewards, help, time limits and branches, as
Discord's 🧵 "📋 Steps, rewards and more" reads them. One database door (`dmbot_web_thread_card`,
migration 0511) answers NULL to anyone else, so a player or a stranger gets "not allowed" and
never a part. What is worked out ("50 gold still owed", "1 of 2 done") is worked out here with the
one set of rules the bot uses (`thread_parts.owed`, `done_count`), never stored. Words, ids and
counts only: a part's words are the DM's own, shown to that DM."""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory import thread_parts as tp
from dmbot.memory import threads
from dmbot.memory.models import CONFIRMED
from dmbot.web import relationships
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


_ID = re.compile(r"^[0-9a-f]{32}$")
_WORD = re.compile(r"^[a-z_]{1,20}$")
_KINDS = ("objective", "complication", "reward", "reward_given", "help", "time_limit")
_FIELDS = ("words", "words_story", "terms", "who", "about", "under", "due", "limit")
_BATCH = re.compile(r"^[0-9]{1,15}$")


def _text(value: object, most: int = 200) -> str:
    """One line of the DM's own words, trimmed, 1 to `most` characters, or `bad_action`."""
    if not isinstance(value, str):
        raise RecapError("bad_action")
    words = " ".join(value.split())
    if not words or len(words) > most or not words.isprintable():
        raise RecapError("bad_action")
    return words


def _id_or_none(value: object) -> str:
    """A 32-hex id, or `-` for none (also None)."""
    if value is None or value == "":
        return "-"
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RecapError("bad_action")
    return value


def _choice(value: object, allowed: tuple[str, ...]) -> str:
    if value is None or value == "":
        return "-"
    if not isinstance(value, str) or value not in allowed:
        raise RecapError("bad_action")
    return value


async def change(db: Database, session: Session, ref: str, thread_id: str, body: object) -> str:
    """Queue one change to a kept thread's parts or branches for the bot (#4476): `add`, `fix`,
    `state`, `show`, `remove`, `branch-move`, `branch-not` or `undo`. Shapes are checked here as
    the queue door checks them again (migration 0514); the bot applies it through the store, so
    each is one change and one Undo. The answer's id is polled like the other website actions."""
    data = body if isinstance(body, dict) else {}
    action = data.get("action")
    if not _ID.fullmatch(thread_id):
        raise RecapError("not_found")
    if action == "add":
        kind = _choice(data.get("kind"), _KINDS)
        if kind == "-":
            raise RecapError("bad_action")
        state = data.get("state")
        if state not in (None, "") and not (isinstance(state, str) and _WORD.fullmatch(state)):
            raise RecapError("bad_action")
        text = " ".join(
            (
                thread_id, kind, str(state) if state else "-",
                _choice(data.get("due"), ("up_front", "afterwards")),
                _choice(data.get("limit"), ("soft", "hard")),
                _id_or_none(data.get("who")), _id_or_none(data.get("about")),
                _id_or_none(data.get("under")), _text(data.get("words")),
            )
        )  # fmt: skip
        name = "part-add"
    elif action == "fix":
        field = data.get("field")
        part = data.get("part")
        if not isinstance(part, str) or not _ID.fullmatch(part) or field not in _FIELDS:
            raise RecapError("bad_action")
        if field in ("words", "words_story", "terms"):
            value = "-" if field == "terms" and data.get("value") in (None, "") else None
            value = value or _text(data.get("value"))
        elif field in ("who", "about", "under"):
            value = _id_or_none(data.get("value"))
        elif field == "due":
            value = _choice(data.get("value"), ("up_front", "afterwards"))
        else:
            value = _choice(data.get("value"), ("soft", "hard"))
        text, name = f"{part} {field} {value}", "part-fix"
    elif action in ("state", "show", "remove"):
        part = data.get("part")
        if not isinstance(part, str) or not _ID.fullmatch(part):
            raise RecapError("bad_action")
        if action == "state":
            word = data.get("state")
            if not isinstance(word, str) or not _WORD.fullmatch(word):
                raise RecapError("bad_action")
            text = f"{part} {word}"
        elif action == "show":
            if not isinstance(data.get("shown"), bool):
                raise RecapError("bad_action")
            text = f"{part} {'1' if data['shown'] else '0'}"
        else:
            text = part
        name = f"part-{action}"
    elif action == "branch-move":
        bigger = data.get("bigger")
        if not isinstance(bigger, str) or not _ID.fullmatch(bigger) or bigger == thread_id:
            raise RecapError("bad_action")
        text = f"{thread_id} {bigger}" + (" move" if data.get("move") is True else "")
        name = "branch-move"
    elif action == "branch-not":
        text, name = thread_id, "branch-not"
    elif action == "undo":
        batch = data.get("batch")
        if not isinstance(batch, str) or not _BATCH.fullmatch(batch):
            raise RecapError("bad_action")
        text, name = batch, "part-undo"
    else:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action=name, target="", text=text, flag=False
    )
