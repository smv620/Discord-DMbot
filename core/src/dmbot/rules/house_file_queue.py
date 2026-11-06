"""The bot's side of the website's House-rules file queue (#3087): claim a request, report the
stage, finish it.

The website cannot fetch a share link, call the AI or touch the house rules, so a DM's request
(connect or change the file, stop using it, compare now, upload one text file) is queued as a row
in `web_house_file_actions` (migration 0295). This module takes the oldest waiting row of a server,
keeps it alive while it runs, and ends it. The link and the file's words are the DM's own and are
never kept: `finish` blanks them in the very statement that marks the row done or failed, and the
sweeps blank what nobody took. Only stage words and counts are written back (how many rules to add,
change or remove; never a rule's words), and nothing from the file or the link is logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from dmbot.db import Database

RETAKE_AFTER_S = 600  # a claim quiet this long belongs to a bot that stopped (as in 0295's doors)
NOT_TAKEN_AFTER_S = 1800  # nobody took it in half an hour: it ends as `not_taken`
KEEP_FOR_S = 86400  # nothing stays longer than a day

# The actions the website queues.
LINK, UNLINK, CHECK, UPLOAD = "link", "unlink", "check", "upload"

# The stages the page shows.
FETCHING = "fetching"
READING = "reading"
COMPARING = "comparing"
COMPARED = "compared"
DONE = "done"

# Why a request failed: one word each, for the page to turn into plain ones.
UNREADABLE = "unreadable"  # the file could not be fetched or read
BAD_LINK = "bad_link"  # not an address DMbot can open, or there is no linked file to check
TOO_BIG = "too_big"
NO_RULES = "no_rules"  # a file with no readable rule is never compared
BUSY = "busy"  # a compare is already running
TOO_OFTEN = "too_often"  # just compared
DAILY_LIMIT = "daily_limit"
NO_AI = "no_ai"  # the plan cannot read files that are not in the numbered format
NO_OFFER = "no_offer"  # the offer could not be posted on the DM screen
NOT_DM = "not_dm"  # the sender is not a DM of the campaign any more
FAILED = "failed"  # a bug: the row must never stay "working" for it


@dataclass(frozen=True, slots=True)
class Claim:
    """A request the bot holds. `claimed_at` (milliseconds) is the claim: only the worker that
    holds the current one may write. `link` and `text` are the DM's own words."""

    id: str
    campaign_id: str
    user_id: int
    action: str
    link: str
    text: str
    claimed_at: int


@dataclass(frozen=True, slots=True)
class Counts:
    to_add: int = 0
    to_change: int = 0
    to_remove: int = 0
    rules_read: int = 0


class HouseFileQueue:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def sweep(self, guild_id: int) -> None:
        """What the website's doors also do, so a DM's words never stay just because the DM did
        not come back: a claim quiet for ten minutes goes back to waiting, a request nobody took
        in half an hour fails as `not_taken` with its link and text blanked, and rows older than a
        day go."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM web_house_file_actions WHERE guild_id = %s AND created_at < %s",
                (guild_id, now - KEEP_FOR_S),
            )
            await conn.execute(
                "UPDATE web_house_file_actions SET state = 'waiting', claimed_at = 0,"
                " updated_at = %s WHERE guild_id = %s AND state = 'working' AND updated_at < %s",
                (now, guild_id, now - RETAKE_AFTER_S),
            )
            await conn.execute(
                "UPDATE web_house_file_actions SET state = 'failed', error = 'not_taken',"
                " link = '', text = '', updated_at = %s"
                " WHERE guild_id = %s AND state = 'waiting' AND updated_at < %s",
                (now, guild_id, now - NOT_TAKEN_AFTER_S),
            )

    async def claim_next(self, guild_id: int) -> Claim | None:
        """The oldest waiting request of this server, marked as being worked on. One at a time
        per campaign is the table's own rule (one open request a campaign)."""
        now = self._now()
        claimed = int(now * 1000)  # milliseconds, so two claims of one row never match
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_house_file_actions SET state = 'working', stage = %s, claimed_at = %s,"
                " updated_at = %s WHERE (guild_id, id) = (SELECT guild_id, id"
                " FROM web_house_file_actions WHERE guild_id = %s AND state = 'waiting'"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id, action, link, text",
                (FETCHING, claimed, int(now), guild_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Claim(
            row["id"], row["campaign_id"], int(row["user_id"]), row["action"],
            row["link"], row["text"], claimed,
        )  # fmt: skip

    async def progress(self, guild_id: int, claim: Claim, stage: str) -> bool:
        """Where the request is: the stage and the time (which also keeps the claim alive). False:
        not its row any more."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_house_file_actions SET stage = %s, updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (stage, int(self._now()), guild_id, claim.id, claim.claimed_at),
            )
            return bool(cur.rowcount)

    async def release(self, guild_id: int, claim: Claim) -> bool:
        """Put the claim back to waiting: the next pass takes it again."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_house_file_actions SET state = 'waiting', stage = 'waiting',"
                " claimed_at = 0, updated_at = %s WHERE guild_id = %s AND id = %s"
                " AND state = 'working' AND claimed_at = %s",
                (int(self._now()), guild_id, claim.id, claim.claimed_at),
            )
            return bool(cur.rowcount)

    async def finish(
        self,
        guild_id: int,
        claim: Claim,
        *,
        error: str = "",
        stage: str = DONE,
        counts: Counts = Counts(),  # noqa: B008 (frozen: safe to share)
    ) -> bool:
        """Done, or failed with a word. The link and the text are blanked in this same statement,
        whichever way it ends. Only the worker that holds the claim can write. False: not its row
        any more."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_house_file_actions SET state = %s, stage = %s, error = %s,"
                " to_add = %s, to_change = %s, to_remove = %s, rules_read = %s,"
                " link = '', text = '', updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if error else "done",
                    stage,
                    error,
                    max(counts.to_add, 0),
                    max(counts.to_change, 0),
                    max(counts.to_remove, 0),
                    max(counts.rules_read, 0),
                    int(self._now()),
                    guild_id,
                    claim.id,
                    claim.claimed_at,
                ),
            )
            return bool(cur.rowcount)
