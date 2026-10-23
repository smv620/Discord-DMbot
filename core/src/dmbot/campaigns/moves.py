"""Moving a campaign to another server when its new owner accepted a hand-over by email there
(#2532 slice C; docs/PLAN.md, "Hand-over by email", points 3 and 7).

The website only records the wish (`move_to_guild_id` on the offer). This is the bot's side: like
loading a backup into the target server, then removing the old copy. The rules, in order of
importance:

- **A campaign is never lost.** The old copy goes only after the new copy exists and matches it
  (the same sections with the same row counts), and in the same transaction that holds the old
  campaign's lock. Any failure before that leaves the old copy whole and opens the offer again with
  a plain reason. A failure after the new copy was made and recorded is retried: the copy is
  reused, never made twice.
- **One move at a time.** The offer carries a short lease (`move_claimed_at`), so two sweeps, or
  two bot processes, cannot both move it. The old campaign row is locked for the whole move
  (`FOR NO KEY UPDATE`, as every memory write takes it), so nothing writes to it meanwhile and the
  copy is not stale.
- **Everything stays per Discord account.** Consent records are not copied (as in a restore): they
  belong to the voices that were recorded. Channels do not carry over; the new owner picks them
  with `/dmbot start`.
- **Not yet:** a campaign that published a shared rule set is not moved (the set's rows are keyed
  to the old server and other tables follow it, and stopping the sharing would strand them). The
  offer opens again with that reason, until the set can travel too with its followers kept.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from dmbot.campaigns.models import CampaignError, HandoverOffer
from dmbot.campaigns.store import CampaignStore, _to_offer
from dmbot.logs import log_context

log = logging.getLogger(__name__)

LEASE_S = 600  # a move that did not finish in this long is taken up again
Result = Literal["moved", "reopened", "skipped"]

SHARES_RULES = (
    "This campaign shares a rule set, so it can't move to another server yet. It can still be "
    "handed over on this server."
)
CHANGED_HANDS = "The campaign changed hands while the move was waiting."
COPY_FAILED = "DMbot couldn't make a complete copy in the other server."


@dataclass(frozen=True, slots=True)
class Moved:
    """What was done, for the two people to be told (best effort, after it is all over)."""

    result: Result
    offer: HandoverOffer
    campaign_name: str
    new_campaign_id: str | None = None
    reason: str = ""


Told = Callable[[Moved], Awaitable[None]]


# Sections whose rows legitimately differ in the copy: the DMs (the new owner is added, as in any
# restore).
_DIFFERENT_BY_DESIGN = frozenset({"dms"})


def _shape(data: dict[str, Any]) -> dict[str, int]:
    """What a backup holds, as section names and row counts: two copies of one campaign agree."""
    return {
        name: len(rows) if isinstance(rows, list | tuple | dict) else 1
        for name, rows in data["sections"].items()
        if name not in _DIFFERENT_BY_DESIGN
    }


class HandoverMover:
    def __init__(
        self, store: CampaignStore, *, clock: Callable[[], float], told: Told | None = None
    ) -> None:
        self._store = store
        self._db = store.db
        self._clock = clock
        self.told = told  # set by whoever can reach the two people (the bot's SiteOffers)

    async def waiting(self, guild_id: int) -> list[int]:
        """The offers of this server that wait for their move (ids, oldest first)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id FROM campaign_handover_offers WHERE guild_id = %s AND status = 'open'"
                " AND move_to_guild_id IS NOT NULL ORDER BY id",
                (guild_id,),
            )
            return [int(row["id"]) for row in await cur.fetchall()]

    async def move(self, guild_id: int, offer_id: int) -> Result:
        """Do one move. Never raises: a failure leaves the old campaign whole (see the module)."""
        now = int(self._clock())
        offer = await self._take(guild_id, offer_id, now)
        if offer is None:
            return "skipped"  # not waiting any more, or another sweep has it
        target = offer.move_to_guild_id
        assert target is not None
        with log_context(guild_id=guild_id, campaign_id=offer.campaign_id):
            try:
                moved = await self._run(offer, target)
            except Exception as exc:  # the type only: an error can quote a row
                log.warning("A campaign move failed: %s", type(exc).__name__)
                moved = await self._reopen(offer, COPY_FAILED)
        if self.told is not None:
            try:
                await self.told(moved)
            except Exception as exc:
                log.warning("A campaign move could not be told: %s", type(exc).__name__)
        return moved.result

    # ---- steps ---------------------------------------------------------------

    async def _take(self, guild_id: int, offer_id: int, now: int) -> HandoverOffer | None:
        """Take the lease on the move: None when it is not waiting or someone else holds it."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE campaign_handover_offers SET move_claimed_at = %s WHERE guild_id = %s"
                " AND id = %s AND status = 'open' AND move_to_guild_id IS NOT NULL"
                " AND (move_claimed_at IS NULL OR move_claimed_at < %s) RETURNING *",
                (now, guild_id, offer_id, now - LEASE_S),
            )
            row = await cur.fetchone()
        return None if row is None else _to_offer(row)

    async def _run(self, offer: HandoverOffer, target: int) -> Moved:
        store = self._store
        g, c = offer.guild_id, offer.campaign_id
        async with self._db.guild(g) as conn:
            # Held to the end of this transaction: nothing writes to the campaign meanwhile.
            # NO KEY UPDATE does not block the offer row's own updates (their foreign key check),
            # and it is what every memory write takes, so they wait for it.
            cur = await conn.execute(
                "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s FOR NO KEY UPDATE",
                (g, c),
            )
            if await cur.fetchone() is None:
                return Moved("skipped", offer, "")  # deleted meanwhile: the offer went with it
            campaign = await store.get(g, c)
            if campaign is None:
                return Moved("skipped", offer, "")
            if campaign.owner_user_id != offer.from_user_id:
                return await self._reopen(offer, CHANGED_HANDS, campaign.name)
            cur = await conn.execute(
                "SELECT 1 FROM rule_sets WHERE guild_id = %s AND campaign_id = %s LIMIT 1", (g, c)
            )
            if await cur.fetchone() is not None:
                return await self._reopen(offer, SHARES_RULES, campaign.name)
            old = await store.export(g, c)
            new_id = offer.moved_campaign_id
            if new_id is not None and await store.get(target, new_id) is None:
                new_id = None  # the recorded copy is gone: make it again
            if new_id is None:
                try:
                    copy = await store.import_backup(target, old, offer.to_user_id)
                except CampaignError as exc:  # plan, slot, name: plain words for the owner
                    return await self._reopen(offer, str(exc), campaign.name)
                new_id = copy.id
                await self._record_copy(offer, new_id)
            try:
                await store.add_dm(target, new_id, offer.from_user_id)  # the old owner stays a DM
                copied = await store.export(target, new_id)
                if _shape(copied) != _shape(old):
                    raise CampaignError(COPY_FAILED)
            except Exception:
                await self._drop_copy(target, new_id)
                await self._forget_copy(offer)
                raise
            # The new copy is whole: only now does the old one go, in this transaction, and the
            # offer with it (it is keyed to the campaign). A failure here rolls all of it back.
            try:
                await store.delete_in(conn, g, c)
            except Exception:
                # Rolled back with this transaction: the old campaign is whole. The copy is taken
                # back so there is one campaign, and the offer opens again. If the copy cannot be
                # taken back either, it stays recorded and the next sweep takes the move up again.
                if await self._drop_copy(target, new_id):
                    raise
                await self._release_lease(offer)
                return Moved("skipped", offer, campaign.name, new_id)
        return Moved("moved", offer, campaign.name, new_id)

    async def _record_copy(self, offer: HandoverOffer, new_id: str) -> None:
        async with self._db.guild(offer.guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET moved_campaign_id = %s"
                " WHERE guild_id = %s AND id = %s",
                (new_id, offer.guild_id, offer.id),
            )

    async def _forget_copy(self, offer: HandoverOffer) -> None:
        async with self._db.guild(offer.guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET moved_campaign_id = NULL"
                " WHERE guild_id = %s AND id = %s",
                (offer.guild_id, offer.id),
            )

    async def _drop_copy(self, target: int, new_id: str) -> bool:
        """Take a copy back. False when that did not work (it is then left, and logged)."""
        try:
            await self._store.delete(target, new_id)
        except Exception as exc:
            log.warning("A half-made campaign copy could not be removed: %s", type(exc).__name__)
            return False
        return True

    async def _release_lease(self, offer: HandoverOffer) -> None:
        async with self._db.guild(offer.guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET move_claimed_at = NULL"
                " WHERE guild_id = %s AND id = %s",
                (offer.guild_id, offer.id),
            )

    async def _reopen(self, offer: HandoverOffer, reason: str, name: str = "") -> Moved:
        """The move did not happen: the offer is open again for the owner to see, with nothing
        claimed, and the old campaign is as it was."""
        async with self._db.guild(offer.guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET move_to_guild_id = NULL,"
                " moved_campaign_id = NULL, move_claimed_at = NULL, to_user_id = 0, to_name = ''"
                " WHERE guild_id = %s AND id = %s AND status = 'open'",
                (offer.guild_id, offer.id),
            )
        return Moved("reopened", offer, name, None, reason)
