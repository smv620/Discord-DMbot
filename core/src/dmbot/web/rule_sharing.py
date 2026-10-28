"""A campaign's shared and followed house rules, for the website's House rules page (#2783; the
shared-rule-sets row of the parity table on #2554; docs/PLAN.md, "Shared rulesets").

Read only here: what the campaign shares (its set, its version, whether a share code is on), what
it follows, and the sets other campaigns share in the same server, picked from a list. The
website's role cannot read the rule tables, so one DM-only database door (`dmbot_web_rule_sharing`)
answers; publishing, following and stopping go through the queue the bot applies, with the same
`RuleSetStore` calls and checks as Discord. Only rules cross, so the answer holds names, versions
and counts, never a rule's words; and a share code is never shown here (they are read in Discord,
#2460). `canShare` comes from the owner's plan in `plan_rules`, the check Discord makes.

A campaign is named by `<server>.<campaign>`, as for the other pages.
"""

from __future__ import annotations

import re
import time
from typing import Any

from dmbot import plan_rules, progress
from dmbot.db import Database
from dmbot.entitlements import plan_grant_access
from dmbot.rules import rule_sets
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

ACTIONS = frozenset(
    {"publish", "stop_sharing", "look_code", "follow_here", "follow_code", "unfollow"}
)
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
# What a failed choice says, as the code the page turns into words; anything else is "failed".
REASONS = {"failed"}
NAME_MAX = 60


def shape(got: dict[str, Any], *, can_share: bool) -> dict[str, Any]:
    """The door's answer as the page's: what the campaign publishes (or null), what it follows,
    the sets it could follow here, how many house rules it could share, and whether its plan lets
    it share."""
    mine = got["published"]
    return {
        "published": None
        if mine is None
        else {
            "name": str(mine["name"]),
            "shared": bool(mine["shared"]),
            "version": int(mine["version"] or 0),
            "hasCode": bool(mine["hasCode"]),
        },
        "following": [
            {
                "name": str(f["name"]),
                "publisher": str(f["publisher"]),
                "version": int(f["version"]),
                "updating": bool(f["updating"]),
            }
            for f in got["following"]
        ],
        "here": [
            {
                "setId": str(h["setId"]),
                "name": str(h["name"]),
                "publisher": str(h["publisher"]),
                "version": int(h["version"]),
                "rules": int(h["rules"]),
            }
            for h in got["here"]
        ],
        "ruleCount": int(got["ruleCount"]),
        "canShare": can_share,
    }


async def _can_share(db: Database, owner: str | None, now: int, *, enforce_plans: bool) -> bool:
    """Whether the campaign's plan lets it share house rules: the owner's plan, the check Discord
    makes (`plan_rules` "share"). A campaign with no owner has no plan."""
    if not enforce_plans:
        return True
    if owner is None or not owner.isdecimal():
        return False
    _plan, _grant, access = await plan_grant_access(db, int(owner), now)
    return plan_rules.allowed(plan_rules.RULE_OF["share"], access)


async def read_sharing(
    db: Database, session: Session, ref: str, *, now: int, enforce_plans: bool
) -> dict[str, Any]:
    """The campaign's sharing for a DM of it. A server the person is not in is "not found";
    anyone else, a player included, is "not allowed"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_rule_sharing(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    can_share = await _can_share(db, got.get("owner"), now, enforce_plans=enforce_plans)
    return shape(got, can_share=can_share)


def _choice(body: object) -> tuple[str, str]:
    """The action and its argument from the request, with the code as typed made plain (any case,
    spaces or dashes, as Discord reads it). The door checks the argument's shape again."""
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    action, arg = body.get("action"), body.get("arg", "")
    if not isinstance(action, str) or action not in ACTIONS:
        raise RecapError("bad_action")
    if not isinstance(arg, str) or len(arg) > 200:
        raise RecapError("bad_arg")
    if action in ("look_code", "follow_code"):
        arg = rule_sets.clean_code(arg)
    elif action == "publish":
        arg = arg.strip()
        if not 1 <= len(arg) <= NAME_MAX:
            raise RecapError("bad_arg")
    elif action in ("stop_sharing", "unfollow"):
        arg = ""
    return action, arg


async def queue_action(
    db: Database, session: Session, ref: str, body: object, *, now: int, enforce_plans: bool
) -> str:
    """Queue one sharing choice of the signed-in DM for the bot and return its id. A server the
    person is not in is "not found"; a member who is not a DM of the campaign is "not allowed".
    Sharing needs a plan that allows it, which is said here ("plan") as Discord says it; the bot
    checks again, with the caps and the guess limit, when it applies the choice."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    action, arg = _choice(body)
    if action == "publish":
        mine = await read_sharing(db, session, ref, now=now, enforce_plans=enforce_plans)
        if not mine["canShare"]:
            raise RecapError("plan")
        if mine["ruleCount"] == 0:
            raise RecapError("no_rules")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_rule_action_add(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, action, arg),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


async def read_action(
    db: Database, session: Session, ref: str, answer: str, *, now: int | None = None
) -> dict[str, Any]:
    """Where one of the caller's own choices stands, in the shared progress shape, with the words
    Discord would say (`note`) once it is done or has failed. Nobody else's choice."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not ID_PATTERN.match(answer):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_rule_action_read(%s, %s, %s) AS r", (guild_id, campaign_id, answer)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    reason = got["error"] or None
    shaped = progress.shape(
        got["stage"],
        started_at=int(got["createdAt"]),
        updated_at=int(got["updatedAt"]),
        now=int(time.time()) if now is None else now,
        reason=reason if reason in REASONS else ("failed" if reason else None),
    )
    return {**shaped, "action": got["action"], "error": got["error"], "note": got["note"]}
