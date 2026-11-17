"""The "Progress to check" lines on the website (#4616, the web half of #4522 quests slice 5b),
for a campaign's DM only. One database door (`dmbot_web_thread_progress`, migration 0528) answers
NULL to anyone else, so a player or a stranger gets "not allowed" and never a line; it lists a line
only while every speaker it quotes is recorded now (the rule of 0518). This module words each line
as Discord's 🧵 card does (`web/progress_line_words.py`, held equal by a test), groups them by
thread and queues the three changes (`progress-keep`, `progress-none`, `progress-undo`) through the
one queue door: the bot applies each through the same store calls as Discord's card, so a keep is
one change and one Undo. Words, dates and counts only: a line keeps no transcript words."""

from __future__ import annotations

import re
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.db import Database
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.progress_line_words import line_text
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

LINE_ID = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")
# the lines a thread shows and one answer covers, as Discord's card (a press answers what is seen)
SHOWN = 25


async def read(
    db: Database, session: Session, ref: str, *, outside_engine: str | None = None
) -> dict[str, Any]:
    """`{threads: [{id, name, kind, lines: [{id, text}], more, sessions, sessionAt}]}` in the order
    the lines were found; each thread shows at most `SHOWN` lines and says how many more wait
    (`more`; they show once these are answered). `sessionAt` is the newest session's date (Unix
    seconds) when one session heard them all, and `sessions` how many were. `not_allowed` for
    anyone who is not a DM of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_thread_progress(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, TERMS_VERSION, outside_engine),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    order: list[str] = []
    by_thread: dict[str, list[dict[str, Any]]] = {}
    for row in got:
        key = str(row["threadId"])
        if key not in by_thread:
            order.append(key)
        by_thread.setdefault(key, []).append(row)
    threads = []
    for key in order:
        rows = by_thread[key]
        shown = rows[:SHOWN]
        heard = {str(r["session"]) for r in shown}
        dates = [int(r["sessionAt"]) for r in shown if r.get("sessionAt") is not None]
        threads.append(
            {
                "id": key,
                "name": str(rows[0]["thread"]),
                "kind": str(rows[0]["threadType"]),
                "lines": [{"id": str(r["id"]), "text": line_text(r)} for r in shown],
                "more": len(rows) - len(shown),
                "sessions": len(heard),
                "sessionAt": max(dates) if len(heard) == 1 and dates else None,
            }
        )
    return {"threads": threads}


def _ids(value: object, *, empty: bool) -> list[str]:
    """A list of line ids from the page: 1 to 25 distinct ones (or none, when `empty`)."""
    if not isinstance(value, list) or len(value) > SHOWN:
        raise RecapError("bad_action")
    if not value and not empty:
        raise RecapError("bad_action")
    if not all(isinstance(v, str) and LINE_ID.fullmatch(v) for v in value):
        raise RecapError("bad_action")
    if len(set(value)) != len(value):
        raise RecapError("bad_action")
    return [str(v) for v in value]


async def change(db: Database, session: Session, ref: str, body: object) -> str:
    """Queue one answer and give its id (polled at `.../web/answers/{answer}`):
    `{action: "keep", on: [line ids], off: [line ids]}` (✅ Keep these: the ticked lines are kept
    and the unticked turned down, in one change), `{action: "none", lines: [ids]}` (🚫 None
    happened), `{action: "undo", batch}` (take back a keep: the change number its answer gave, as
    text) or `{action: "undo", lines: [ids]}` (put turned-down lines back to waiting). Shapes are
    checked here before anything is queued; the bot checks everything again."""
    data = body if isinstance(body, dict) else {}
    action = data.get("action")
    if action == "keep":
        on = _ids(data.get("on"), empty=True)
        off = _ids(data.get("off"), empty=True)
        if (not on and not off) or len(on) + len(off) > SHOWN or set(on) & set(off):
            raise RecapError("bad_action")
        text, queued = f"{','.join(on) or '-'} {','.join(off) or '-'}", "progress-keep"
    elif action == "none":
        text, queued = ",".join(_ids(data.get("lines"), empty=False)), "progress-none"
    elif action == "undo":
        batch = data.get("batch")
        if batch is not None:
            if not isinstance(batch, str) or not _BATCH.fullmatch(batch):
                raise RecapError("bad_action")
            text = f"keep {batch}"
        else:
            text = "none " + ",".join(_ids(data.get("lines"), empty=False))
        queued = "progress-undo"
    else:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action=queued, target="", text=text, flag=False
    )
