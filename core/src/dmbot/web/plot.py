"""PlotBot's check of a recap, for the DM's recap page (#2122, docs/PLAN.md, "PlotBot checks
every recap before it is accepted").

Two reads through the database's doors, and the Approve gate. The issues belong to one text of one
recap and to its DMs alone: the doors hold every call to a DM of the campaign (anyone else gets
"not found", as for the recap itself), the website's role has no right to the tables, and nothing
here is ever on a shared link or in a player's view. Nothing logs a word of a recap.

PlotBot advises and the DM decides: "leave it as is" is always an answer, and a check that cannot
run (the switch is off, or the text waited too long for the bot) never holds Approve.
"""

from __future__ import annotations

from typing import Any

from dmbot.web.recaps import RecapError, _one
from dmbot.web.sessions import Session

# How long a text may wait for its first check before the page is told "unavailable" (the bot
# may be off): PlotBot never holds a DM up for long.
PLOT_WAIT_S = 1800
ANSWERS = ("fix", "changed", "fine", "leave")
NOTE_MAX = 500


def _view(raw: dict[str, Any], *, on: bool) -> dict[str, Any]:
    """The page's shape from the door's answer. `version`: the accepted version this text is, or the
    next one it would become."""
    state = raw["state"] if on else "unavailable"
    versions = list(raw["versions"])
    newest = versions[-1]["version"] if versions else 0
    open_n = int(raw["open"]) if state == "ready" else 0
    return {
        "state": state,
        "approveBlocked": open_n > 0,
        "version": newest + (0 if raw.get("approvedCurrent") else 1),
        "textHash": raw["hash"],
        "issues": raw["issues"] if state == "ready" else [],
        "versions": versions,
    }


async def read_plot(db: Any, session: Session, ref: str, *, on: bool) -> dict[str, Any]:
    """The recap's plot check as the page shows it. Not a DM: `RecapError("not_found")`."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session, marks=False)
        if not on:
            return _view(
                {
                    "state": "unavailable",
                    "hash": "",
                    "issues": [],
                    "versions": [],
                    "open": 0,
                },
                on=False,
            )
        cur = await conn.execute(
            "SELECT dmbot_plot_read(%s, %s, %s, %s) AS r", (gid, cid, at, PLOT_WAIT_S)
        )
        raw = (await cur.fetchone() or {}).get("r")
    if raw is None:
        raise RecapError("not_found")
    return _view(raw, on=True)


async def answer_issue(
    db: Any, session: Session, ref: str, issue: str, kind: str, note: str
) -> dict[str, Any]:
    """The DM's answer to one issue: fixed, the story changed, not a problem (with their note,
    which keeps the same issue from being raised again), or leave it as is. The updated issue.
    A text changed since the check answers `stale` (the page reloads: a new check has its own
    issues)."""
    if kind not in ANSWERS or not isinstance(note, str) or len(note) > NOTE_MAX:
        raise RecapError("bad_answer")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session, marks=False)
        cur = await conn.execute(
            "SELECT dmbot_plot_answer(%s, %s, %s, %s, %s, %s) AS r",
            (gid, cid, at, issue[:64], kind, note.strip()),
        )
        outcome = (await cur.fetchone() or {}).get("r")
        if outcome in ("bad", "stale"):
            raise RecapError("bad_answer" if outcome == "bad" else "stale")
        if outcome != "ok":
            raise RecapError("not_found")
        cur = await conn.execute(
            "SELECT dmbot_plot_read(%s, %s, %s, %s) AS r", (gid, cid, at, PLOT_WAIT_S)
        )
        raw = (await cur.fetchone() or {}).get("r")
    found = next((i for i in (raw or {}).get("issues", []) if i["id"] == issue), None)
    if found is None:
        raise RecapError("not_found")
    return dict(found)


CHAT_MAX = 1000  # the form limit the Discord path uses too
PROPOSAL_ACTIONS = ("save", "decline")


async def send_chat(db: Any, session: Session, ref: str, issue: str, text: str) -> dict[str, Any]:
    """The DM's message about one issue (#2123): queued for the bot, which answers on a second
    row; the page polls `read_chat`. The words are never logged. Refusals: `bad_text` (empty or
    over the limit), `not_found` (not a DM, or no such issue), `stale` (the recap changed since the
    check), `busy` (an answer is on its way), `too_soon` (only this issue's talk: it is long
    enough, so answer it or leave it as it is; never the hourly cap) and `hour_full` (the
    campaign's DMs have sent thirty messages this hour, on any issue: try again later)."""
    if not isinstance(text, str) or not text.strip() or len(text) > CHAT_MAX:
        raise RecapError("bad_text")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session, marks=False)
        cur = await conn.execute(
            "SELECT dmbot_plot_chat_send(%s, %s, %s, %s, %s) AS r",
            (gid, cid, at, issue[:64], text),
        )
        raw = (await cur.fetchone() or {}).get("r")
    if raw is None:
        raise RecapError("not_found")
    status = raw["status"]
    if status != "ok":
        raise RecapError(
            {"too_many": "too_soon", "hour_full": "hour_full", "busy": "busy"}.get(status, status)
        )
    return {"id": raw["id"]}


async def read_chat(db: Any, session: Session, ref: str, issue: str) -> dict[str, Any]:
    """The DM's own talk about one issue, oldest first, with PlotBot's proposals and where each
    stands (open, saving, saved, declined, failed). Someone else's talk is never returned."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session, marks=False)
        cur = await conn.execute(
            "SELECT dmbot_plot_chat_read(%s, %s, %s, %s) AS r", (gid, cid, at, issue[:64])
        )
        raw = (await cur.fetchone() or {}).get("r")
    if raw is None:
        raise RecapError("not_found")
    return dict(raw)


async def answer_proposal(
    db: Any, session: Session, ref: str, issue: str, proposal: str, action: str
) -> dict[str, Any]:
    """Save or Not that, on one proposal. Not that closes it and writes nothing. Save only
    queues it: the bot makes the change as the DM's own fact, so the proposal reads `saving` until
    it is `saved`. The updated proposal. One no longer open is refused (`not_allowed`)."""
    if action not in PROPOSAL_ACTIONS:
        raise RecapError("bad_answer")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session, marks=False)
        cur = await conn.execute(
            "SELECT dmbot_plot_proposal(%s, %s, %s, %s, %s, %s) AS r",
            (gid, cid, at, issue[:64], proposal[:64], action),
        )
        outcome = (await cur.fetchone() or {}).get("r")
        if outcome == "bad":
            raise RecapError("bad_answer")
        if outcome == "done_already":
            raise RecapError("not_allowed")
        if outcome == "stale":
            raise RecapError("stale")
        if outcome != "ok":
            raise RecapError("not_found")
        cur = await conn.execute(
            "SELECT dmbot_plot_chat_read(%s, %s, %s, %s) AS r", (gid, cid, at, issue[:64])
        )
        raw = (await cur.fetchone() or {}).get("r")
    for m in (raw or {}).get("messages", []):
        for p in m["proposals"]:
            if p["id"] == proposal:
                return {"messageId": m["id"], **p}
    raise RecapError("not_found")
