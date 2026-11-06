"""How many campaigns a person owns, and whether their plan has room for another (#437
part 2c; docs/PLAN.md, "Plans and pricing").

Campaigns are isolated per server, so "how many does this person own, in every server" is
read from `owner_campaigns`: two ids per row, kept in step with `campaigns` by a trigger
(dmbot.schema, 0034), counted by a function that answers only for the person set. This is
the one place that counts: the start check, the hand-over and take-over checks, and the
restore check all ask here.
"Owned" counts the campaigns that are not paused (#957): a paused one takes no place.
"""

from __future__ import annotations

from dataclasses import dataclass

from dmbot import entitlements
from dmbot.db import Conn


@dataclass(frozen=True, slots=True)
class Room:
    """What a person may own and what they own. `cap` None: no limit. `works`: the plan
    works at all (a plan that has ended has no room, whatever the numbers)."""

    works: bool
    cap: int | None
    owned: int
    can_change_plan: bool = False

    def fits(self, extra: int = 1) -> bool:
        """May they own `extra` more campaigns? (A campaign handed over, taken on or
        restored is one more; one they already own and start is zero more.)"""
        return self.works and (self.cap is None or self.owned + extra <= self.cap)


async def owned_count(conn: Conn, user_id: int) -> int:
    """How many campaigns this person owns, in every server. Inside a transaction the
    caller already has open (the bot's meter door, or the website's own: the function is
    callable from both)."""
    return await entitlements.owned_campaigns(conn, user_id)


async def room(conn: Conn, user_id: int, now: int) -> Room:
    """Their plan's campaign cap against what they own now."""
    access = await entitlements.effective(conn, user_id, now)
    return Room(
        access.works,
        access.campaign_cap,
        await owned_count(conn, user_id),
        can_change_plan=access.kind == "paid",
    )


async def has_room_for_one_more(conn: Conn, user_id: int, now: int) -> bool:
    """The campaign store's free-slot check when plans are enforced (CampaignStore's
    `has_free_slot`): handing over, taking on and restoring each make one more."""
    # One at a time per person, until this transaction ends: two hand-overs (or a hand-over
    # and a restore) to someone one short of their cap each count before the other has
    # written, and both would pass. The campaign lock is taken first by every caller, and
    # this is the only lock after it, so they cannot wait on each other.
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"dmbot.owner_slots:{user_id}",)
    )
    return (await room(conn, user_id, now)).fits(1)
