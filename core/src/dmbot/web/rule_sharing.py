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

from typing import Any

from dmbot import plan_rules
from dmbot.db import Database
from dmbot.entitlements import plan_grant_access
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


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
