"""The website's Plot threads panel (#3696, slice 1): the campaign's story threads as Discord's
threads card shows them, read only, for the DM. One database door (`dmbot_web_plot_threads`,
migration 0576, DM only) gives each thread's state rows, the facts that touch it, the stalled count
(`dmbot_thread_quiet`, 0575, the one the bot's after-session line calls too) and the session
numbers. Everything else is `memory/threads.py`, the code Discord uses: the state words, the
history lines, the roles, the order and `is_stalled`, so no rule is copied and "stalled" is worked
out here, never stored. Names, ids, states, counts and session numbers only."""

from __future__ import annotations

import os
import re
from typing import Any

from dmbot.db import Database
from dmbot.memory import threads
from dmbot.memory.models import THREAD_TYPES, State
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


def stalled_after() -> int:
    """How many quiet sessions make a live thread stalled: `DMBOT_THREAD_STALLED_SESSIONS` as the
    bot reads it (a whole number from 1 up), else the default."""
    raw = os.environ.get("DMBOT_THREAD_STALLED_SESSIONS", "").strip()
    # (the same test as `config.py`: ASCII digits only, so a stray digit like "²" is no number)
    ok = raw.isascii() and raw.isdigit() and int(raw) >= 1
    return int(raw) if ok else threads.STALLED_AFTER


_ID = re.compile(r"^[0-9a-f]{32}$")
ROLE_MAX = 60  # the card's own limit on "Their part"
NAME_MAX = 100  # a thread's or an event's name, as the card's forms
DUE_MAX = 120  # a promise's due note, as the card's form


async def read(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{after, total, stalledCount, stalledLine, threads: [...]}` as described on #3696.
    `not_allowed` for anyone who is not a DM of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_plot_threads(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return shape(got, stalled_after())


def shape(got: dict[str, Any], after: int) -> dict[str, Any]:
    """The door's rows as the panel reads them (pure: no database)."""
    numbers = {int(k): int(v) for k, v in got["numbers"].items()}
    names: dict[str, str] = {str(k): str(v) for k, v in got["names"].items()}
    history: dict[str, list[State]] = {}
    for s in got["states"]:
        history.setdefault(str(s["entity"]), []).append(
            State(
                "",
                str(s["entity"]),
                str(s["condition"]),
                s["fromSession"],
                "",
                int(s["createdAt"]),
                s["closedAt"],
            )
        )
    out: list[dict[str, Any]] = []
    for t in got["threads"]:
        tid, kind = str(t["id"]), str(t["type"])
        rows = history.get(tid, [])
        current = next((r for r in reversed(rows) if r.closed_at is None), None)
        now = current.condition if current is not None else None
        quiet = int(got["quiet"].get(tid, [0, 0])[1])
        facts = [f for f in got["facts"] if str(f["subject"]) in names]
        mine = [f for f in facts if str(f["object"]) == tid and f["predicate"] != "led_to"]

        def named(ids: list[str]) -> list[dict[str, str]]:
            return [{"id": i, "name": names[i]} for i in ids]

        out.append(
            {
                "id": tid,
                "name": str(t["name"]),
                "kind": kind,
                "state": now,
                "stateWords": threads.words_for(kind).get(now or "") if now else None,
                "history": threads.history_lines(rows, numbers, kind),
                "involved": [
                    {
                        "id": str(f["subject"]),
                        "name": names[str(f["subject"])],
                        "role": str(f["detail"] or ""),
                    }
                    for f in mine
                    if f["predicate"] == "in_thread"
                ],
                "movers": [
                    {
                        "id": str(f["subject"]),
                        "name": names[str(f["subject"])],
                        "endsIt": f["predicate"] == "resolves",
                        "session": numbers.get(f["fromSession"]) if f["fromSession"] else None,
                    }
                    for f in mine
                    if f["predicate"] in ("advances", "resolves")
                ],
                "cameFrom": named(
                    [
                        str(f["subject"])
                        for f in facts
                        if f["predicate"] == "led_to" and str(f["object"]) == tid
                    ]
                ),
                "ledTo": named(
                    [
                        str(f["object"])
                        for f in facts
                        if f["predicate"] == "led_to"
                        and str(f["subject"]) == tid
                        and str(f["object"]) in names
                    ]
                ),
                "promise": (
                    {
                        "by": [
                            names[str(f["subject"])]
                            for f in mine
                            if f["predicate"] == "promised_by"
                        ],
                        "to": [
                            names[str(f["subject"])]
                            for f in mine
                            if f["predicate"] == "promised_to"
                        ],
                        "due": str(t["description"] or ""),
                    }
                    if kind == threads.PROMISE
                    else None
                ),
                "quiet": quiet,
                "stalled": threads.is_stalled(now, quiet, after),
            }
        )
    out.sort(key=lambda x: threads.play_key(x["state"], x["quiet"], x["name"], x["id"]))
    stalled = [x for x in out if x["stalled"]]
    promises = sum(1 for x in stalled if x["kind"] == threads.PROMISE)
    return {
        "after": after,
        "total": int(got["total"]),
        "stalledCount": len(stalled),
        "stalledLine": threads.stalled_line(len(stalled), after, promises) if stalled else None,
        "threads": out,
    }


async def queue_state(db: Database, session: Session, ref: str, body: object) -> str:
    """Change one thread's state (#3696 slice 2a): `{"thread": <32 hex>, "state": <a state word>}`,
    or `{"thread": ..., "state": "check"}` for "Checked, still going". The id of the answer the page
    polls at `.../web/answers/{id}`. The bot runs the card's own store calls; the page has no Undo,
    as the card has none."""
    thread = body.get("thread") if isinstance(body, dict) else None
    state = body.get("state") if isinstance(body, dict) else None
    if not isinstance(thread, str) or not _ID.fullmatch(thread):
        raise RecapError("bad_action")
    if state == "check":
        action, text = "thread-check", thread
    elif state in threads.ALL_STATES:
        action, text = "thread-state", f"{thread} {state}"
    else:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action=action, target="", text=text, flag=False
    )  # fmt: skip


async def queue_involve(db: Database, session: Session, ref: str, body: object) -> str:
    """Say who or what has a part in a thread (#3696 slice 2b): `{"thread": <32 hex>, "who":
    <32 hex>, "role": <their part, optional, at most 60 characters>}`. The id of the answer the
    page polls at `.../web/answers/{id}`. The bot runs the card's own `involve` and checks both
    entries again."""
    thread = body.get("thread") if isinstance(body, dict) else None
    who = body.get("who") if isinstance(body, dict) else None
    role = body.get("role", "") if isinstance(body, dict) else None
    if not isinstance(thread, str) or not _ID.fullmatch(thread):
        raise RecapError("bad_action")
    if not isinstance(who, str) or not _ID.fullmatch(who) or who == thread:
        raise RecapError("bad_action")
    if not isinstance(role, str):
        raise RecapError("bad_action")
    part = " ".join(role.split())  # (the card collapses spaces the same way, then counts)
    if len(part) > ROLE_MAX:  # (an empty part is kept as it is, as the card's "(optional)" says)
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="thread-involve", target="",
        text=f"{thread} {who} {part}".strip(), flag=False,
    )  # fmt: skip


async def queue_new(db: Database, session: Session, ref: str, body: object) -> str:
    """Start a thread or a promise (#3696 slice 2c): `{"kind": <thread kind>, "state": <a state
    word>, "name": <1 to 100 characters>}`. The id of the answer the page polls at
    `.../web/answers/{id}`; a done answer carries the new thread's id."""
    kind = body.get("kind") if isinstance(body, dict) else None
    state = body.get("state") if isinstance(body, dict) else None
    name = body.get("name") if isinstance(body, dict) else None
    if kind not in THREAD_TYPES or state not in threads.ALL_STATES or not isinstance(name, str):
        raise RecapError("bad_action")
    clean = " ".join(name.split())
    if not clean or len(clean) > NAME_MAX:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="thread-new", target="",
        text=f"{kind} {state} {clean}", flag=False,
    )  # fmt: skip


async def queue_due(db: Database, session: Session, ref: str, body: object) -> str:
    """A promise's due note (#3696 slice 2c): `{"promise": <32 hex>, "due": <at most 120
    characters, empty clears it>}`."""
    promise = body.get("promise") if isinstance(body, dict) else None
    due = body.get("due", "") if isinstance(body, dict) else None
    if not isinstance(promise, str) or not _ID.fullmatch(promise) or not isinstance(due, str):
        raise RecapError("bad_action")
    note = " ".join(due.split())
    if len(note) > DUE_MAX:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="thread-due", target="",
        text=f"{promise} {note}".strip(), flag=False,
    )  # fmt: skip


async def queue_party(db: Database, session: Session, ref: str, body: object) -> str:
    """Who made a promise, or who it was made to (#3696 slice 2c): `{"promise": <32 hex>, "side":
    "by" | "to", "who": <32 hex>}`."""
    promise = body.get("promise") if isinstance(body, dict) else None
    side = body.get("side") if isinstance(body, dict) else None
    who = body.get("who") if isinstance(body, dict) else None
    if (
        not isinstance(promise, str) or not _ID.fullmatch(promise)
        or not isinstance(who, str) or not _ID.fullmatch(who) or side not in ("by", "to")
    ):  # fmt: skip
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="thread-party", target="",
        text=f"{promise} {side} {who}", flag=False,
    )  # fmt: skip


async def queue_link(db: Database, session: Session, ref: str, body: object) -> str:
    """Link an event to a thread (#3696 slice 2d): `{"thread": <32 hex>, "how": "moved" | "ended",
    "event": <32 hex>}`, or `"newEvent": <a name, 1 to 100 characters>` in place of `event` to make
    the event on the spot, as the card does. The id of the answer the page polls at
    `.../web/answers/{id}`."""
    thread = body.get("thread") if isinstance(body, dict) else None
    how = body.get("how") if isinstance(body, dict) else None
    event = body.get("event") if isinstance(body, dict) else None
    fresh = body.get("newEvent") if isinstance(body, dict) else None
    if not isinstance(thread, str) or not _ID.fullmatch(thread) or how not in ("moved", "ended"):
        raise RecapError("bad_action")
    if isinstance(event, str) and fresh is None and _ID.fullmatch(event):
        which = event
    elif isinstance(fresh, str) and event is None:
        name = " ".join(fresh.split())
        if not name or len(name) > NAME_MAX:
            raise RecapError("bad_action")
        which = f"new {name}"
    else:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="thread-link", target="",
        text=f"{thread} {how} {which}", flag=False,
    )  # fmt: skip


async def queue_led_to(db: Database, session: Session, ref: str, body: object) -> str:
    """Say what one thing led to (#3696 slice 2d): `{"from": <32 hex>, "to": <32 hex>}`, a clue,
    an event or a thread that led to a thread or an event."""
    source = body.get("from") if isinstance(body, dict) else None
    target = body.get("to") if isinstance(body, dict) else None
    if (
        not isinstance(source, str) or not _ID.fullmatch(source)
        or not isinstance(target, str) or not _ID.fullmatch(target) or source == target
    ):  # fmt: skip
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="thread-led-to", target="",
        text=f"{source} {target}", flag=False,
    )  # fmt: skip
