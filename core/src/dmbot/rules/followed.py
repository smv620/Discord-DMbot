"""House rules a campaign follows from a shared set (#1190), as rules the advisors can read.

Own house rules come first, then the followed set's: the precedence in CLAUDE.md puts a
campaign's own rules above everything it follows. Each followed rule carries its set's name
and version so every answer that uses it can cite where it came from ("Red Table house
rules, v4"). The copy is the follower's own; nothing here reads another campaign.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dmbot.rules.house import HouseRule

if TYPE_CHECKING:
    from dmbot.rules.rule_sets import Follow, RuleSetStore

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FollowedRule(HouseRule):
    set_name: str = ""
    set_version: int = 0

    @property
    def cite(self) -> str:
        return cite(self.set_name, self.set_version)


def cite(name: str, version: int) -> str:
    return f"{name} house rules, v{version}"


def from_follow(follow: Follow, campaign_id: str) -> list[FollowedRule]:
    """The followed set's rules, oldest first. Content was validated on the way in; a row
    that still doesn't fit is skipped rather than failing the whole answer."""
    rules = follow.content.get("rules")
    out: list[FollowedRule] = []
    if not isinstance(rules, list):
        return out
    for item in rules:
        try:
            out.append(
                FollowedRule(
                    number=int(item["number"]),
                    campaign_id=campaign_id,
                    rule=str(item["rule"]),
                    supersedes=str(item["instead"]) if item.get("instead") else None,
                    scenario=None,
                    session_id=None,
                    created_by=0,
                    created_at=follow.followed_at,
                    updated_at=follow.followed_at,
                    set_name=follow.name,
                    set_version=follow.version,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda r: r.number)


async def followed_rules(
    sets: RuleSetStore | None, guild_id: int, campaign_id: str
) -> list[FollowedRule]:
    """What this campaign follows; none if nothing is followed or it can't be read (the
    campaign's own rules must still be answered)."""
    if sets is None:
        return []
    try:
        follows = await sets.following(guild_id, campaign_id)
    except Exception:
        log.exception("Couldn't read the followed house rules")
        return []
    out: list[FollowedRule] = []
    for follow in follows:
        if follow.kind == "house_rules":
            out.extend(from_follow(follow, campaign_id))
    return out


def own_first(own: Sequence[HouseRule], followed: Sequence[HouseRule]) -> list[HouseRule]:
    """The campaign's own rules, then rules merged in from a set (marked `from_set`), then the
    set it follows: the order in which they win. A merged rule is an ordinary house rule in
    every other way, but a rule the campaign wrote itself always comes before it."""
    mine = [r for r in own if not getattr(r, "from_set", None)]
    merged = [r for r in own if getattr(r, "from_set", None)]
    return [*mine, *merged, *followed]
