"""The bot's side of the website's Upload lore queue (#3022): claim an upload, report where the read
is, finish it.

The website cannot call the AI or touch the memory tables, so a DM's lore (pasted, a file's text or
a link) is queued as a row in `lore_uploads` (migration 0288). This module takes the oldest waiting
row of a server, keeps it alive while it is read, and ends it. The text and the link are the DM's
own words and are never kept: `finish` blanks them in the very statement that marks the row done or
failed, and the sweeps blank what nobody took. Only counts and stage words are ever written back,
and nothing from a document is logged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from dmbot.db import Database
from dmbot.memory.models import NEEDS_A_LOOK

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # a claim quiet this long belongs to a bot that stopped (as in 0288's doors)
NOT_TAKEN_AFTER_S = 1800  # nobody took it in half an hour: it ends as `not_taken`
KEEP_FOR_S = 86400  # nothing stays longer than a day

# The stages the page shows, as 0288 names them.
READING = "reading"
FINDING_NAMES = "finding_names"
FINDING_LINKS = "finding_links"
DONE = "done"

# Why an upload failed: one word each, for the page to turn into plain ones.
NOT_ALLOWED = "not_allowed"  # the sender or the confirmer is not a DM of the campaign any more
TOO_LONG = "too_long"
NO_AI = "no_ai"  # the AI is off, the plan has no AI, or today's reads are used
LINK_FAILED = "link_failed"  # the link could not be opened or held nothing readable
NOTHING_FOUND = "nothing_found"
OUT_OF_TIME = "ran_out_of_time"
NOT_TAKEN = "not_taken"
FAILED = "failed"  # a bug: the row must never stay "working" for it


FOUND_IDS_MAX = 2500  # ids kept for "Add all" (the column holds 120 kB; an id is about 36 bytes)


def _capped(found: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    """The kinds with their ids, the biggest kinds first, cut at `FOUND_IDS_MAX` ids in all. A
    read this large still adds every name one by one in Check new names."""
    room = FOUND_IDS_MAX
    kept: dict[str, list[str]] = {}
    # (the kinds Add all never covers come last, so a flood of ideas cannot crowd the rest out)
    for kind, ids in sorted(
        found.items(), key=lambda item: (item[0] in NEEDS_A_LOOK, -len(item[1]))
    ):
        if room <= 0:
            break
        kept[kind] = list(ids[:room])
        room -= len(kept[kind])
    return kept


@dataclass(frozen=True, slots=True)
class Claim:
    """An upload the bot holds. `claimed_at` (milliseconds) is the claim: only the worker that
    holds the current one may write."""

    id: str
    campaign_id: str
    user_id: int
    source: str
    text: str
    link: str
    speaker: str
    choice: str
    rights_by: int
    claimed_at: int


class LoreQueue:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def sweep(self, guild_id: int) -> None:
        """What the website's doors also do, so a DM's words never stay just because the DM did
        not come back: a claim quiet for ten minutes goes back to waiting, an upload nobody took in
        half an hour fails as `not_taken` with its text and link blanked, and rows older than a day
        go."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM lore_uploads WHERE guild_id = %s AND created_at < %s",
                (guild_id, now - KEEP_FOR_S),
            )
            await conn.execute(
                "UPDATE lore_uploads SET state = 'waiting', claimed_at = 0, updated_at = %s"
                " WHERE guild_id = %s AND state = 'working' AND updated_at < %s",
                (now, guild_id, now - RETAKE_AFTER_S),
            )
            await conn.execute(
                "UPDATE lore_uploads SET state = 'failed', error = %s, text = '', link = '',"
                # (by `updated_at`, as the doors do: a claim put back because the reader was busy
                # has been tried lately and is not "never taken")
                " updated_at = %s WHERE guild_id = %s AND state = 'waiting' AND updated_at < %s",
                (NOT_TAKEN, now, guild_id, now - NOT_TAKEN_AFTER_S),
            )

    async def claim_next(self, guild_id: int) -> Claim | None:
        """The oldest waiting upload of this server, marked as being worked on. One at a time per
        campaign is the table's own rule (one open upload a campaign)."""
        now = self._now()
        claimed = int(now * 1000)  # milliseconds, so two claims of one row never match
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE lore_uploads SET state = 'working', stage = %s, claimed_at = %s,"
                " updated_at = %s, done = 0, total = 0 WHERE (guild_id, id) = (SELECT guild_id, id"
                " FROM lore_uploads WHERE guild_id = %s AND state = 'waiting' ORDER BY seq"
                " LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id, source, text, link, speaker, choice,"
                " rights_by",
                (READING, claimed, int(now), guild_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Claim(
            row["id"], row["campaign_id"], int(row["user_id"]), row["source"], row["text"],
            row["link"], row["speaker"], row["choice"], int(row["rights_by"]), claimed,
        )  # fmt: skip

    async def progress(
        self,
        guild_id: int,
        claim: Claim,
        stage: str,
        done: int = 0,
        total: int = 0,
        words: int = 0,
        calls: int = 0,
    ) -> bool:
        """Where the read is: the stage and the counts, and the time (which also keeps the claim
        alive). Counts and words only, never a word of the document. `words` and `calls`: what the
        read has handled so far (#3534), kept as the most it has reported (a count never goes back).
        False: not its row any more."""
        total = max(total, 0)
        done = min(max(done, 0), total) if total else 0
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE lore_uploads SET stage = %s, done = %s, total = %s, updated_at = %s,"
                " words_done = GREATEST(words_done, %s), calls_made = GREATEST(calls_made, %s)"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    stage,
                    done,
                    total,
                    int(self._now()),
                    max(words, 0),
                    max(calls, 0),
                    guild_id,
                    claim.id,
                    claim.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def release(self, guild_id: int, claim: Claim) -> bool:
        """Put the claim back to waiting (the server's reader is busy right now): the next pass
        takes it again."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE lore_uploads SET state = 'waiting', stage = 'waiting', claimed_at = 0,"
                " updated_at = %s WHERE guild_id = %s AND id = %s AND state = 'working'"
                " AND claimed_at = %s",
                (int(self._now()), guild_id, claim.id, claim.claimed_at),
            )
            return bool(cur.rowcount)

    async def finish(
        self,
        guild_id: int,
        claim: Claim,
        *,
        error: str = "",
        names: int = 0,
        links: int = 0,
        found: Mapping[str, Sequence[str]] | None = None,
    ) -> bool:
        """Done, or failed with a word. The text and the link are blanked in this same statement,
        whichever way it ends. `found`: the ids of the names this reading proposed, kind by kind,
        kept with the row for the website's "Add all" (#3207); ids only, capped so the row's size
        limit is never hit. Only the worker that holds the claim can write. False: not its row
        any more."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE lore_uploads SET state = %s, stage = %s, error = %s, names_found = %s,"
                " links_found = %s, found = %s, text = '', link = '', updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if error else "done",
                    DONE,
                    error,
                    max(names, 0),
                    max(links, 0),
                    Jsonb({} if error else _capped(found or {})),
                    int(self._now()),
                    guild_id,
                    claim.id,
                    claim.claimed_at,
                ),
            )
            return bool(cur.rowcount)
