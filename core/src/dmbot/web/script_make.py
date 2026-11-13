"""Make a session's script from the website (#3899): the offer, the press and the wait.

A session's theatrical script is made after a live session by DMbot's own question in Discord, and
by itself for new sessions (#3901); this is the game master's way to make one for a session that has
none (made before then, or while scripts were off). The AI key is the bot's, never the web
container's (#2419), so a press only queues a row (`dmbot_web_script_make_add`, migration 0401)
that the bot answers with the same job as Discord's button (`transcript.theatre_job`), and the page
polls the row for the stage and the part it is on. Here: the checks the web side can make before
queuing, and the answers.

Who may press: a DM of the campaign, for a session of that campaign (anyone else gets `not_found`
or `not_allowed`, as the script's download). Nothing is queued when scripts are off on this server
(`script_off`: `DMBOT_THEATRE_ON`), when the owner's plan has no story memory (`script_plan`, the
gate Discord's button has) or when the session already has a script (`script_already`). A second
press while one is going is given the running job's id, so a reload or a second tab never queues a
second AI charge. Counts and words of progress only: never a line of the session.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot import plan_rules, progress
from dmbot.db import Database
from dmbot.transcript.theatre_job import estimate_line
from dmbot.web.names import _parse
from dmbot.web.recap_choices import owner_access
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session
from dmbot.web.transcripts import _sessions

_ID = re.compile(r"^[0-9a-f]{32}$")


async def _session_of(
    db: Database, session: Session, ref: str, number: int, *, now: int, enforce_plans: bool
) -> tuple[int, str, str, int]:
    """(server, campaign, the session's id, its stored line count) for session `number` of the
    campaign, or `not_found`."""
    guild_id, campaign_id, found = await _sessions(
        db, session, ref, now=now, enforce_plans=enforce_plans
    )
    wanted = next((s for s in found["sessions"] if int(s["number"]) == number), None)
    if wanted is None:
        raise RecapError("not_found")
    return guild_id, campaign_id, str(wanted["id"]), int(wanted.get("lines") or 0)


async def _gate(
    db: Database,
    session: Session,
    guild_id: int,
    campaign_id: str,
    *,
    theatre_on: bool,
    now: int,
    enforce_plans: bool,
) -> str | None:
    """Why a script cannot be made now (`off` or `plan`), or None when it can."""
    if not theatre_on:
        return "off"
    if enforce_plans:
        access = await owner_access(db, session, guild_id, campaign_id, now)
        if not plan_rules.allowed(plan_rules.RULE_OF["story"], access):
            return "plan"
    return None


async def offer(
    db: Database,
    session: Session,
    ref: str,
    number: int,
    *,
    theatre_on: bool,
    now: int,
    enforce_plans: bool,
) -> dict[str, Any]:
    """Where the session's script stands, for a DM: `state` (`none` offers the button, `exists`,
    `running`), the cost line Discord's question gives, whether it `allowed` now and `why` not
    (`plan` or `off`), the id of the run in `running`."""
    guild_id, campaign_id, sid, lines = await _session_of(
        db, session, ref, number, now=now, enforce_plans=enforce_plans
    )
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_script_make_state(%s, %s, %s) AS r", (guild_id, campaign_id, sid)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    why = await _gate(
        db,
        session,
        guild_id,
        campaign_id,
        theatre_on=theatre_on,
        now=now,
        enforce_plans=enforce_plans,
    )
    # A script DMbot is making by itself (the automatic script, Discord's button) counts as running
    # too, so the page waits for it instead of offering a second press (`auto`, 0406).
    running_now = bool(got["running"] or got.get("auto"))
    state = "exists" if got["exists"] else "running" if running_now else "none"
    return {
        "state": state,
        "cost": estimate_line(lines) if state == "none" and why is None else None,
        "allowed": why is None or state == "exists",
        "why": None if state == "exists" else why,
        "running": got["running"],
    }


async def queue(
    db: Database,
    session: Session,
    ref: str,
    number: int,
    *,
    theatre_on: bool,
    now: int,
    enforce_plans: bool,
) -> str:
    """Queue one press and return the id to poll. Refusals: `not_found`, `not_allowed` (not a DM),
    `script_off`, `script_plan`, `script_already` and `too_soon` (the door's 10 a minute for the
    campaign)."""
    guild_id, campaign_id, sid, _lines = await _session_of(
        db, session, ref, number, now=now, enforce_plans=enforce_plans
    )
    why = await _gate(
        db,
        session,
        guild_id,
        campaign_id,
        theatre_on=theatre_on,
        now=now,
        enforce_plans=enforce_plans,
    )
    # A closed gate queues nothing: the refusal comes before any row is made (a game master whose
    # session already has a script is still told so by the offer, which reads without queuing).
    if why is not None:
        raise RecapError(f"script_{why}")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_script_make_add(%s, %s, %s) AS r", (guild_id, campaign_id, sid)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] == "already":
        raise RecapError("script_already")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


async def read(db: Database, session: Session, ref: str, make: str) -> dict[str, Any]:
    """Where one press stands, for any DM of the campaign: `state` (waiting, working, done, failed),
    the `error` word when it failed, and the shared `progress` (the part it is on of how many)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not _ID.match(make):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_script_make_read(%s, %s, %s) AS r", (guild_id, campaign_id, make)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_found")
    state = str(got["state"])
    created = int(got["createdAt"])
    taken = int(got["claimedAt"]) // 1000  # the queue's own stamp is milliseconds
    of = int(got["of"])
    part = int(got["part"])
    return {
        "state": state,
        "error": got["error"],
        "progress": progress.shape(
            {"waiting": "waiting", "working": "working", "done": "done"}.get(state, "failed"),
            started_at=created,
            updated_at=taken if state != "waiting" and taken else created,
            now=int(got["now"]),
            done=part if of > 0 else None,
            total=of if of > 0 else None,
            unit="parts" if of > 0 else None,
            reason=(str(got["error"]) or "script_failed") if state == "failed" else None,
        ),
    }
