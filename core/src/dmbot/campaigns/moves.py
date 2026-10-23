"""Moving a campaign to another server when its new owner accepted a hand-over by email there
(#2532 slice C; docs/PLAN.md, "Hand-over by email", points 3 and 7).

The website only records the wish (`move_to_guild_id` on the offer). This is the bot's side. A move
is **one change in one transaction**: the campaign's row changes server and every row that hangs
off it follows (migration 0215 makes the foreign keys carry the update), so nothing is read back
and written again, nothing can be left behind, and a failure at any point leaves the campaign
exactly where it was. The rules, in order of importance:

- **Nothing is lost.** Every table that belongs to the campaign moves, through the foreign keys
  (`tests/test_campaign_move_cascade.py` fails for a key that does not cascade, and the move test
  compares every table found in the schema, not a hand-kept list). What does not carry over is
  what is only about the old server: its channels and messages, which `/dmbot start` makes again.
- **One move at a time, on a campaign nobody is using.** The offer carries a short lease
  (`move_claimed_at`), so two sweeps, or two bot processes, cannot both move it. Inside the
  transaction the campaign is locked `FOR UPDATE` (as `delete` does, so even a child-row insert
  waits), the offer is read again and must still be open, for this server and this run's lease,
  and a running session, a recap job a worker holds or has due for audio, an import being read or
  a session still winding down stops the move: the offer opens again and the person who accepted
  is told to try again in a few minutes.
- **The write is gated by the move itself.** No SECURITY DEFINER door: the bot already holds the
  campaign's rows, so migration 0215's `campaign_move` policy lets that one campaign's row change
  server only while this transaction has set the two settings naming it and its target. Every
  check above shares that transaction, which a door could not do.
- **Recap audio is copied before and removed after.** The files are not in the database; they are
  copied to the new server's folder before the move; after the commit a second pass copies any
  file finished meanwhile, and only then are the old ones removed. A failure in between leaves the
  old files where they were.
- **Players are asked again.** Consent records belong to the voices and the server; they are not
  moved, and counts of names said are kept only for who agreed in the new server.
- **Not yet:** a campaign that has published a rule set is not moved (#2551), and the website
  refuses it before anyone accepts. This is the same check, as the last word under the lock.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from psycopg import sql

from dmbot import campaign_cap
from dmbot.campaigns.models import HandoverOffer
from dmbot.campaigns.store import CampaignStore, _to_offer
from dmbot.db import Conn, Database
from dmbot.logs import log_context
from dmbot.recaps.store import AUDIO_MAX_TRIES

log = logging.getLogger(__name__)

LEASE_S = 600  # a move that did not finish in this long is taken up again
STUCK_S = 3600  # an offer that has waited this long for its move is opened again
Result = Literal["moved", "reopened", "ended", "skipped"]

# What the new owner is told when a move does not happen (the old owner is told only that it did
# not, never why: a reason can be about the new owner's plan).
SHARES_RULES = (
    "This campaign shares a rule set, so it can't move to another server yet. "
    "It can still be handed over on this server."
)
CHANGED_HANDS = "The campaign changed hands while the move was waiting."
NO_PAID_ROOM = "Your plan has no room for one more campaign, so the move did not happen."
SESSION_RUNNING = "A session is running. Try again after it ends."
BUSY = "DMbot is still finishing the last session. Try again in a few minutes."
STUCK = "DMbot couldn't reach the other server in time, so the move did not happen."
COULD_NOT = "DMbot couldn't move it this time."


# Tables a worker takes rows of with a claim or a lease: locked for the move, so a claim made after
# the move's checks waits (the campaign's own row lock does not stop their updates).
LOCKED_TABLES = (
    "recap_jobs",
    "session_imports",
    "name_actions",
    "name_answers",
    "recap_requests",
    "secret_exposures",
    "setting_actions",
)
# Of those, the ones that say 'working' while a worker has a row.
WORKED_TABLES = (
    "session_imports",
    "name_actions",
    "name_answers",
    "recap_requests",
    "setting_actions",
)


class AudioFolders(Protocol):
    """Recap audio on the server's disk (`recaps.audio.files.AudioFiles`)."""

    def copy_campaign(self, from_guild: int, to_guild: int, campaign_id: str) -> None: ...

    def catch_up(self, from_guild: int, to_guild: int, campaign_id: str) -> None: ...

    def forget_campaign(self, guild_id: int, campaign_id: str) -> None: ...


class Refused(Exception):
    """The move cannot happen now; the message is for the new owner, in plain words."""


class Ended(Refused):
    """The move can never happen (the campaign changed hands): the offer ends instead of opening
    again, and only the person who accepted is told."""


@dataclass(frozen=True, slots=True)
class Moved:
    """What was done, for the two people to be told (best effort, after it is all over)."""

    result: Result
    offer: HandoverOffer
    campaign_name: str
    reason: str = ""  # for the new owner only


Told = Callable[[Moved], Awaitable[None]]
Consenting = Callable[[int], Awaitable[frozenset[int]]]


class HandoverMover:
    def __init__(
        self,
        store: CampaignStore,
        *,
        clock: Callable[[], float],
        consenting: Consenting | None = None,
        audio: AudioFolders | None = None,
        forgotten: Callable[[str], None] | None = None,
        busy: Callable[[int, str], bool] | None = None,
        told: Told | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._consenting = consenting
        self._audio = audio
        self._forgotten = forgotten  # what the bot keeps by campaign id alone
        # True while the bot is still winding a session of this server's campaign down (its
        # after-session work runs in this process; the database cannot see it).
        self._busy = busy or (lambda guild_id, campaign_id: False)
        self.told = told  # set by whoever can reach the two people (the bot's SiteOffers)

    @property
    def _db(self) -> Database:  # read when used: the store is built first
        return self._store.db

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
        """Do one move. Never raises; a failure leaves the campaign where it was."""
        now = int(self._clock())
        offer = await self._take(guild_id, offer_id, now)
        if offer is None:
            return "skipped"  # not waiting any more, or another sweep has it
        target = offer.move_to_guild_id
        assert target is not None
        with log_context(guild_id=guild_id, campaign_id=offer.campaign_id):
            try:
                moved = await self._run(offer, target, now)
            except _Changed:
                return "skipped"  # ended, taken back or opened again since the lease
            except Ended as ended:
                moved = await self._end(offer, str(ended))
            except Refused as refusal:
                moved = await self._reopen(offer, str(refusal))
            except Exception as exc:  # the type only: an error can quote a row
                log.warning("A campaign move failed: %s", type(exc).__name__)
                moved = await self._reopen(offer, COULD_NOT)
        if self.told is not None and moved.result != "skipped":
            try:
                await self.told(moved)
            except Exception as exc:
                log.warning("A campaign move could not be told: %s", type(exc).__name__)
        return moved.result

    async def reopen_stuck(self, guild_id: int) -> int:
        """Open again the offers whose move has waited too long (the target server became
        unreachable, say the bot left it), so the owner is not left with an offer that is neither
        answered nor ended. Returns how many."""
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM campaign_handover_offers WHERE guild_id = %s AND status = 'open'"
                " AND move_to_guild_id IS NOT NULL AND move_asked_at < %s"
                " AND (move_claimed_at IS NULL OR move_claimed_at < %s)",
                (guild_id, now - STUCK_S, now - LEASE_S),
            )
            rows = await cur.fetchall()
        for row in rows:
            moved = await self._reopen(_to_offer(row), STUCK)
            if self.told is not None:
                try:
                    await self.told(moved)
                except Exception as exc:
                    log.warning("A campaign move could not be told: %s", type(exc).__name__)
        return len(rows)

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

    async def _run(self, offer: HandoverOffer, target: int, now: int) -> Moved:
        g, c = offer.guild_id, offer.campaign_id
        consenting = await self._consenting(target) if self._consenting else frozenset()
        copied = False
        try:
            if self._audio is not None:
                self._audio.copy_campaign(g, target, c)  # before the move: the old files stay
                copied = True
            async with self._db.guild(g) as conn:
                name = await self._move_in(conn, offer, target, now, consenting)
        except BaseException:
            if copied and self._audio is not None:
                self._audio.forget_campaign(target, c)  # (the campaign did not arrive: no use)
            raise
        # Committed. A file written in the old folder after the first copy (a recap's audio that
        # was finishing) is copied across now; only then do the old files go. A failure here leaves
        # harmless leftovers.
        if self._audio is not None:
            self._audio.catch_up(g, target, c)
            self._audio.forget_campaign(g, c)
        if self._forgotten is not None:
            self._forgotten(c)
        return Moved("moved", offer, name)

    async def _move_in(
        self,
        conn: Conn,
        offer: HandoverOffer,
        target: int,
        now: int,
        consenting: frozenset[int],
    ) -> str:
        g, c = offer.guild_id, offer.campaign_id
        if self._busy(g, c):
            raise Refused(BUSY)
        # The lock first, as `delete` takes it: FOR UPDATE also makes a child-row insert wait.
        cur = await conn.execute(
            "SELECT owner_user_id FROM campaigns WHERE guild_id = %s AND id = %s FOR UPDATE",
            (g, c),
        )
        row = await cur.fetchone()
        if row is None:
            raise Refused(CHANGED_HANDS)  # deleted meanwhile; the offer went with it
        # The offer, read again under the lock: it must still be this run's to carry out.
        cur = await conn.execute(
            "SELECT status, move_to_guild_id, move_claimed_at, to_user_id"
            " FROM campaign_handover_offers WHERE guild_id = %s AND id = %s FOR UPDATE",
            (g, offer.id),
        )
        again = await cur.fetchone()
        if (
            again is None
            or again["status"] != "open"
            or again["move_to_guild_id"] != target
            or again["move_claimed_at"] != offer.move_claimed_at
        ):
            raise _Changed
        if int(row["owner_user_id"]) != offer.from_user_id:
            raise Ended(CHANGED_HANDS)
        cur = await conn.execute(
            "SELECT 1 FROM active_sessions WHERE guild_id = %s AND campaign_id = %s", (g, c)
        )
        if await cur.fetchone() is not None:
            raise Refused(SESSION_RUNNING)
        # Work that writes to the campaign without holding its row: a recap job a worker holds or
        # has due for audio, an import being read, a website request being answered. Their rows
        # are locked first, so a worker that takes a lease after the check below waits for this
        # transaction, and then finds the row gone from the old server and takes nothing.
        for table in LOCKED_TABLES:
            await conn.execute(
                sql.SQL(
                    "SELECT 1 FROM {} WHERE guild_id = %s AND campaign_id = %s FOR UPDATE"
                ).format(sql.Identifier(table)),
                (g, c),
            )
        cur = await conn.execute(
            "SELECT 1 FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
            " AND (leased_to > %s OR (state = 'ready' AND audio = 'queued' AND audio_tries < %s"
            " AND status IN ('draft', 'posting', 'posted') AND audio_next_at <= %s)) LIMIT 1",
            (g, c, now, AUDIO_MAX_TRIES, now),
        )
        if await cur.fetchone() is not None:
            raise Refused(BUSY)
        for table in WORKED_TABLES:
            cur = await conn.execute(
                sql.SQL(
                    "SELECT 1 FROM {} WHERE guild_id = %s AND campaign_id = %s"
                    " AND state = 'working' LIMIT 1"
                ).format(sql.Identifier(table)),
                (g, c),
            )
            if await cur.fetchone() is not None:
                raise Refused(BUSY)
        cur = await conn.execute(
            "SELECT 1 FROM rule_sets WHERE guild_id = %s AND campaign_id = %s"
            " AND status = 'shared' LIMIT 1",
            (g, c),
        )
        if await cur.fetchone() is not None:
            raise Refused(SHARES_RULES)
        # The plan is checked now, not when they accepted: it may have lapsed since.
        if not await campaign_cap.has_paid_room_for_one_more(conn, offer.to_user_id, now):
            raise Refused(NO_PAID_ROOM)
        # The offer has done its work and goes (it is keyed to the campaign, which leaves).
        await conn.execute(
            "DELETE FROM campaign_handover_offers WHERE guild_id = %s AND id = %s", (g, offer.id)
        )
        return await self._store.move_in(
            conn,
            g,
            target,
            c,
            new_owner=offer.to_user_id,
            old_owner=offer.from_user_id,
            consenting=consenting,
        )

    async def _end(self, offer: HandoverOffer, reason: str) -> Moved:
        """The move can never happen: the offer ends (it is no one's to answer any more)."""
        async with self._db.guild(offer.guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET status = 'withdrawn', move_claimed_at = NULL,"
                " decided_at = %s WHERE guild_id = %s AND id = %s AND status = 'open'",
                (int(self._clock()), offer.guild_id, offer.id),
            )
            cur = await conn.execute(
                "SELECT name FROM campaigns WHERE guild_id = %s AND id = %s",
                (offer.guild_id, offer.campaign_id),
            )
            row = await cur.fetchone()
        return Moved("ended", offer, "" if row is None else str(row["name"]), reason)

    async def _reopen(self, offer: HandoverOffer, reason: str, name: str = "") -> Moved:
        """The move did not happen: the offer is open again for the owner to see, with nothing
        claimed, and the campaign is as it was. The reason is kept for the person who accepted
        (`move_failed_reason`, shown on their page) and is not told to the owner."""
        async with self._db.guild(offer.guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET move_to_guild_id = NULL,"
                " move_claimed_at = NULL, to_user_id = 0, to_name = '',"
                " move_failed_for = %s, move_failed_reason = %s"
                " WHERE guild_id = %s AND id = %s AND status = 'open'",
                (offer.to_user_id, reason, offer.guild_id, offer.id),
            )
            if not name:  # for the notices
                cur = await conn.execute(
                    "SELECT name FROM campaigns WHERE guild_id = %s AND id = %s",
                    (offer.guild_id, offer.campaign_id),
                )
                row = await cur.fetchone()
                name = "" if row is None else str(row["name"])
        return Moved("reopened", offer, name, reason)


class _Changed(Exception):
    """The offer was ended, taken back or reopened between the lease and the lock: nothing to do."""
