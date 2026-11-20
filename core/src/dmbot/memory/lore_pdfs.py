"""A lore PDF sent from the website, counted before the DM's yes (#4762, step 2; Supervisor's
ruling on the issue): the bot's half of `lore-pdf-estimate`.

The website stages the file (`web/lore_pdf.py`, migration 0545) and queues the action with the
staged file's id. This module checks that the file is still the one staged, reads it from the
staging bucket, counts its pages and pictures through the one document reader Discord's upload
uses (supplied by the bot, because that reader belongs to the Discord side), prices the read with
Discord's own function (`credits.lore_and_pictures`) and writes only three numbers back (`pages`,
`pictures`, `estimate_usd`). **Nothing is sent to an AI**, and the pictures the reader takes out
go into its private temporary folder and are removed when it ends (the reader owns that). A staged
object that is gone (the bucket's 1-day rule) answers "gone". Counts only are ever logged.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from dmbot import credits
from dmbot.db import Database
from dmbot.pictures.bucket import BucketError

if TYPE_CHECKING:
    from dmbot.memory.web_actions import Action, Outcome

log = logging.getLogger(__name__)

EXPIRED = "expired"  # the staged file is gone: "That file expired before DMbot could count it."
ROW_KEEP_S = 86400  # a row (and its object, if the bucket's own rule missed it) goes after a day


class StagingBucket(Protocol):
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Counted:
    """What the reader found in a PDF: its text's length in characters (what the lore read is
    priced by), its pages, and the pictures it took out (past the limits: `over`, not counted)."""

    chars: int
    pages: int
    pictures: int
    over: int = 0


# Reads one PDF's bytes: DocumentError (plain words for the DM) for a file it cannot use.
CountFn = Callable[[bytes], Awaitable[Counted]]


@dataclass(frozen=True, slots=True)
class Staged:
    id: str
    object_key: str
    size_bytes: int
    state: str
    sha256: str = ""  # the file's own hash: what the right-to-use record carries (#4762 step 3)


class StagedPdfs:
    """The bot's reads and writes of `lore_pdf_staged`: one row for each campaign."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, guild_id: int, campaign_id: str) -> Staged | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, object_key, size_bytes, state, sha256 FROM lore_pdf_staged"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Staged(
            str(row["id"]),
            str(row["object_key"]),
            int(row["size_bytes"]),
            row["state"],
            str(row["sha256"]),
        )

    async def drop(self, guild_id: int, campaign_id: str, staged_id: str) -> None:
        """Delete this file's row (a later file's is never touched: the id must match)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM lore_pdf_staged WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, staged_id),
            )

    async def mark(
        self,
        guild_id: int,
        campaign_id: str,
        staged_id: str,
        state: str,
        *,
        now: int,
        counted: tuple[int, int, float] | None = None,
        only_from: tuple[str, ...] | None = None,
    ) -> bool:
        """Move the row to `state` (and write the three numbers) when it is still this file's, and,
        with `only_from`, still in one of those states (so a count never writes over a read that
        began meanwhile). False when another file replaced it or the state moved on."""
        pages, pictures, usd = counted if counted is not None else (None, None, None)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE lore_pdf_staged SET state = %s, updated_at = %s,"
                " pages = COALESCE(%s, pages), pictures = COALESCE(%s, pictures),"
                " estimate_usd = COALESCE(%s, estimate_usd)"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                " AND (%s::text[] IS NULL OR state = ANY(%s::text[]))",
                (
                    state,
                    now,
                    pages,
                    pictures,
                    usd,
                    guild_id,
                    campaign_id,
                    staged_id,
                    list(only_from) if only_from is not None else None,
                    list(only_from) if only_from is not None else None,
                ),
            )
            return bool(cur.rowcount == 1)

    async def sweep(self, guild_id: int, *, now: int) -> list[str]:
        """Delete the rows older than a day and give back their object keys, for the caller to
        delete (the bucket's own rule usually has them already). A row being read is spared for
        two days: past that its read ended without clearing it."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM lore_pdf_staged WHERE guild_id = %s AND created_at < %s"
                " AND (state <> 'reading' OR created_at < %s) RETURNING object_key",
                (guild_id, now - ROW_KEEP_S, now - 2 * ROW_KEEP_S),
            )
            return [str(r["object_key"]) for r in await cur.fetchall()]


@dataclass(frozen=True, slots=True)
class LorePdfs:
    """What the bot needs to count a staged PDF: the staging bucket, the table and the reader. A
    missing piece makes every `lore-pdf-estimate` fail (and be sent again)."""

    staging: StagingBucket | None
    staged: StagedPdfs | None
    count: CountFn | None
    max_bytes: int


def ref_of(staged_id: str) -> str:
    """The staged file's public name: the website never sees its id (it is the object's key in the
    bucket), only this one-way short hash of it, which the bot compares with the row's own."""
    return hashlib.sha256(staged_id.encode()).hexdigest()[:32]


def usd_of(credit_count: int) -> float:
    """Dollars for a number of credits (a credit is a thousandth of a dollar)."""
    return credit_count * credits.MICRO_PER_CREDIT / 1_000_000


async def estimate(
    lore: LorePdfs | None, guild_id: int, campaign_id: str, *, a: Action, now: int
) -> Outcome:
    """Count the staged PDF whose `ref_of` is `a.text` and write the numbers back. Words of failure
    go in `result_other` (plain, never the file's), the outcome word in `error`."""
    from dmbot.memory.name_documents import DocumentError
    from dmbot.memory.web_actions import BAD_TARGET, FAILED, GONE, Outcome

    if lore is None or lore.staging is None or lore.staged is None or lore.count is None:
        return Outcome(error=FAILED)
    row = await lore.staged.get(guild_id, campaign_id)
    if row is None or ref_of(row.id) != a.text:
        return Outcome(error=GONE)  # (another file replaced it, or "Never mind")
    if row.state == "reading":
        return Outcome(error=BAD_TARGET)  # (a read is on: nothing to count again)
    if row.size_bytes > lore.max_bytes:
        await lore.staged.mark(guild_id, campaign_id, row.id, "failed", now=now)
        return Outcome(error=BAD_TARGET)
    started = await lore.staged.mark(
        guild_id, campaign_id, row.id, "estimating", now=now,
        only_from=("staged", "estimating", "estimated", "failed"),
    )  # fmt: skip
    if not started:
        return Outcome(error=BAD_TARGET)  # (a read began, or the file was replaced, meanwhile)
    try:
        try:
            raw = await lore.staging.get(row.object_key)
        except BucketError:
            await lore.staged.mark(guild_id, campaign_id, row.id, "failed", now=now)
            return Outcome(error=GONE, result_other=EXPIRED)
        if len(raw) > lore.max_bytes:  # (the bot checks the bytes themselves, not the row's word)
            await lore.staged.mark(guild_id, campaign_id, row.id, "failed", now=now)
            return Outcome(error=BAD_TARGET)
        found = await lore.count(raw)
    except DocumentError as exc:
        await lore.staged.mark(guild_id, campaign_id, row.id, "failed", now=now)
        return Outcome(error=BAD_TARGET, result_other=str(exc)[:300])
    except BaseException:
        # (any other failure, the bucket's included: back to "staged", so the page never waits on
        # an "estimating" nothing is doing; the action is sent again)
        await lore.staged.mark(
            guild_id, campaign_id, row.id, "staged", now=now, only_from=("estimating",)
        )
        raise
    cost = credits.lore_and_pictures(found.chars, found.pictures)
    kept = await lore.staged.mark(
        guild_id, campaign_id, row.id, "estimated", now=now, only_from=("estimating",),
        counted=(found.pages, found.pictures, usd_of(cost.credits)),
    )  # fmt: skip
    if not kept:
        return Outcome(error=GONE)  # (replaced while it was counted)
    log.info("A staged lore PDF was counted: %s pages, %s pictures", found.pages, found.pictures)
    return Outcome(result_id=row.id, counts=f"pages:{found.pages} pictures:{found.pictures}")
