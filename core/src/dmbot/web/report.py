"""One session's after-session report on the website (#2661; docs/STORY_MEMORY.md 9a): the minor
contradictions PlotBot found, what the party has wrong sorted as disguised, misunderstood or
false, and the DM's answers to each. For the campaign's DMs alone: a player, a peek or anyone whose
campaign it is not gets `not_allowed`, and nothing of it is ever in a players' view.

The website's database role has no right on the memory tables, so everything comes from one door
(`dmbot_web_report`, schema.py). It sends ids, DMbot's own words (a connection's two names and its
relationship) and numbers; evidence is its kind, its session and whether it still stands (a link
whose words were removed is a placeholder), never a line, who said it or a quote. This module only
puts those into the shapes the page was built against (agreed on #2661): the readable line for a
connection, the session numbers (the transcripts page's own), the view's name, the counts, and the
shared progress shape while a report is being made.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory.link_words import link_words
from dmbot.memory.report_store import STALLED_S
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped, session_facts
from dmbot.web.sessions import Session
from dmbot.web.transcripts import numbered_sessions

SORT_ORDER = ("disguised", "misunderstood", "false")
NOT_SO = "That is not so."  # what the DM's view says where it denies the connection
UNIT = {
    "grading": "items",
    "sorting": "items",
}  # what `done` and `total` count at a stage (reading has no count)
STATES = ("none", "making", "ready", "failed")


def _parse_at(at: object) -> int:
    """A session's start in Unix seconds: plain digits, a real time, never anything else."""
    if not isinstance(at, int) or isinstance(at, bool) or not 1 <= at <= 99_999_999_999:
        raise RecapError("not_found")
    return at


def _who(who: dict[str, Any] | None) -> str:
    """Whose view: "The DM's view", "The party", or the character's own name."""
    kind = str((who or {}).get("kind") or "")
    if kind == "truth":
        return "The DM's view"
    if kind == "party":
        return "The party"
    return str((who or {}).get("name") or "A character")


def _words(rel: dict[str, Any]) -> str:
    """A connection as one plain line, "Aldric serves the Ashen Crown"."""
    said = link_words(
        str(rel["predicate"]), str(rel.get("detail") or ""), bool(rel.get("directional"))
    )
    return f"{rel['subject']} {said} {rel['object']}"


def _evidence(
    rel: dict[str, Any], numbers: dict[int, int], fallback: int | None
) -> list[dict[str, Any]]:
    out = []
    for e in rel.get("evidence", []):
        at = e.get("at")
        number = numbers.get(int(at)) if at is not None else None
        out.append(
            {
                "kind": e["kind"],
                "session": number if number is not None else (fallback or 0),
                "available": bool(e["available"]),
            }
        )
    return out


def _answer(a: dict[str, Any] | None) -> dict[str, Any] | None:
    return None if a is None else {"action": a["action"], "at": int(a["at"])}


def _reminded(item: dict[str, Any], numbers: dict[int, int]) -> int | None:
    start = item.get("remindFrom")
    return numbers.get(int(start)) if start is not None else None


def _progress(run: dict[str, Any], now: int) -> dict[str, Any]:
    """The shared wait shape: stage and counts and times, never a word. A run that has not moved for
    longer than a stalled worker is given is `stalled` (the page says it may be slow first)."""
    state = str(run["state"])
    if state == "failed":
        shown = "failed"
    elif now - int(run["updatedAt"]) > STALLED_S:
        shown = "stalled"
    else:
        shown = "running"
    stage = str(run["stage"])
    return {
        "stage": stage,
        "done": int(run["done"]) if stage in UNIT else None,
        "total": int(run["total"]) if stage in UNIT else None,
        "unit": UNIT.get(stage),
        "startedAt": int(run["startedAt"]),
        "updatedAt": int(run["updatedAt"]),
        "serverNow": now,
        "state": shown,
        "reason": str(run["reason"]) or None if state == "failed" else None,
    }


def shape(got: dict[str, Any], at: int, listed: list[dict[str, Any]]) -> dict[str, Any]:
    """The door's answer made into the page's report."""
    numbers = {int(s["startedAt"]): int(s["number"]) for s in listed}
    number, _seconds = session_facts(listed, at)
    now = int(got["now"])
    contradictions = []
    for c in got["contradictions"]:
        if not c.get("first") or not c.get("second"):
            continue  # (a connection gone since: one bad row never takes the report down)
        view = _who(c.get("who"))
        stated = []
        for side in (c["first"], c["second"]):
            stated.append(
                {
                    "text": _words(side),
                    "view": view,
                    "session": numbers.get(int(side["beliefAt"])) if side.get("beliefAt") else None,
                    "evidence": _evidence(side, numbers, number),
                }
            )
        contradictions.append(
            {
                "id": c["key"],
                "reason": c["reason"],
                "gameTimeKnown": not c["timesUnknown"],
                "first": stated[0],
                "second": stated[1],
                "answer": _answer(c["answer"]),
                "remindedFrom": _reminded(c, numbers),
            }
        )
    by_sort: dict[str, list[dict[str, Any]]] = {s: [] for s in SORT_ORDER}
    for d in got["differences"]:
        if d["sort"] not in by_sort:
            continue
        believes = d.get("believes")
        if not believes:
            continue
        truth = d.get("truth")
        by_sort[d["sort"]].append(
            {
                "id": d["key"],
                "reason": d["reason"],
                "view": _who(d.get("who")),
                "believes": _words(believes),
                "truth": _words(truth) if truth else NOT_SO,
                "evidence": _evidence(believes, numbers, number),
                "answer": _answer(d["answer"]),
                "open": d.get("open"),
                "remindedFrom": _reminded(d, numbers),
            }
        )
    items = [*contradictions, *(i for s in SORT_ORDER for i in by_sort[s])]
    run = got.get("run")
    if run is not None and run["state"] in STATES:
        state = str(run["state"])
    else:
        state = "ready" if items else "none"
    report: dict[str, Any] = {
        "session": {"at": at, "number": number},
        "state": state,
        "progress": _progress(run, now) if run and state in ("making", "failed") else None,
        "counts": {
            "contradictions": len(contradictions),
            **{s: len(by_sort[s]) for s in SORT_ORDER},
            # a reminded item has come back: it is open again
            "open": sum(1 for i in items if i["answer"] is None or i["remindedFrom"] is not None),
            "answered": sum(
                1 for i in items if i["answer"] is not None and i["remindedFrom"] is None
            ),
        },
        "contradictions": contradictions,
        "differences": by_sort,
        # Found but never sorted or graded (the call limit stopped the run): counted, never read as
        # "nothing found". A ready report with a reason of `left_for_next_time` says the same.
        "notSorted": int(got.get("notSorted") or 0),
        "leftForNextTime": bool(
            run and run["state"] == "ready" and run["reason"] == "left_for_next_time"
        ),
    }
    return report


async def read_report(db: Database, session: Session, ref: str, at: object) -> dict[str, Any]:
    """The report for the session that began at `at` (Unix seconds). DM only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    when = _parse_at(at)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
            (guild_id, campaign_id, session.user_id),
        )
        if await cur.fetchone() is None:
            raise RecapError("not_allowed")  # a player, or a campaign that is not theirs
        listed = await numbered_sessions(conn, guild_id, campaign_id)
        cur = await conn.execute(
            "SELECT dmbot_web_report(%s, %s, %s) AS r", (guild_id, campaign_id, when)
        )
        got = (await cur.fetchone() or {}).get("r")
        cur = await conn.execute(
            "SELECT dmbot_web_report_request(%s, %s, %s) AS r", (guild_id, campaign_id, when)
        )
        asked = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    return with_request(shape(got, when, listed), asked, int(got["now"]))


ACTIONS = ("intended", "not_a_problem", "remind", "fix", "undo")
ITEM_KEY = re.compile(r"[0-9a-f]{32}")


async def answer_item(
    db: Database, session: Session, ref: str, at: object, body: object
) -> dict[str, Any]:
    """The DM's answer to one item: `{id, action}`, action one of `intended`, `not_a_problem`,
    `remind`, `fix` or `undo`. Written at once by the database door, which checks the caller is a DM
    of the campaign and that the item is in this session's report of this campaign (a key from
    anywhere else is `gone`, and so is a second answer: the page reads the report again). `fix`
    writes nothing (the page opens the Story graph); `undo` puts the item back to open. Returns
    `{answer}`: the answer as it now stands, or null. Bookkeeping only: nothing in the story
    changes here, so there is no bot round trip (Supervisor, #2661)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    when = _parse_at(at)
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    key, action = body.get("id"), body.get("action")
    if action not in ACTIONS:
        raise RecapError("bad_action")
    if not isinstance(key, str) or not ITEM_KEY.fullmatch(key):
        raise RecapError("gone")  # (a key that is no item's: the same plain answer as any other)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_report_answer(%s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, when, key, action),
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError("gone" if got["status"] == "gone" else "bad_action")
    return {"answer": got["answer"]}


def with_request(report: dict[str, Any], asked: dict[str, Any] | None, now: int) -> dict[str, Any]:
    """Put the DM's "make it again" request into the report (#2661). The run the bot writes says how
    far the making has got once it starts; before that, a request that is asked for and not taken
    yet is a report being made at the stage `waiting`, so the page shows the wait at once and the
    button cannot be pressed twice. A request the bot gave up on, with no report to show, is a
    failed one. A run that is already making (or ready after the request) is left as it is."""
    if asked is None or report["state"] == "making":
        return report
    state, created, updated = str(asked["state"]), int(asked["createdAt"]), int(asked["updatedAt"])
    if state in ("waiting", "working"):
        shown = "stalled" if now - updated > STALLED_S else "running"
        report["state"] = "making"
        report["progress"] = {
            "stage": "waiting",
            "done": None,
            "total": None,
            "unit": None,
            "startedAt": created,
            "updatedAt": min(max(updated, created), max(now, created)),
            "serverNow": now,
            "state": shown,
            "reason": "stalled" if shown == "stalled" else None,
        }
    elif state == "failed" and report["state"] in ("none", "failed"):
        report["state"] = "failed"
        report["progress"] = {
            "stage": "waiting",
            "done": None,
            "total": None,
            "unit": None,
            "startedAt": created,
            "updatedAt": min(max(updated, created), max(now, created)),
            "serverNow": now,
            "state": "failed",
            "reason": "failed",
        }
    return report


async def make_again(db: Database, session: Session, ref: str, at: object) -> dict[str, Any]:
    """Ask the bot to make this session's report (again): queued like the other choices, then the
    report is read back as it now stands (`making`, at the stage `waiting`). DM only. The session
    has to be one of the campaign's (`not_found` otherwise). A report being made now is `busy`
    (nothing is queued; the page reads the report), and five requests an hour for a campaign
    is `too_soon`."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    when = _parse_at(at)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
            (guild_id, campaign_id, session.user_id),
        )
        if await cur.fetchone() is None:
            raise RecapError("not_allowed")
        listed = await numbered_sessions(conn, guild_id, campaign_id)
        if not any(int(s["startedAt"]) == when for s in listed):
            raise RecapError("not_found")
        cur = await conn.execute(
            "SELECT dmbot_web_report_make(%s, %s, %s) AS r", (guild_id, campaign_id, when)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] not in ("ok", "busy"):
        raise RecapError(str(got["status"]))
    return await read_report(db, session, ref, when)
